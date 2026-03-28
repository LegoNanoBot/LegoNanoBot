"""Integration tests for the Supervisor Gateway system.

Architecture: all-in-process via httpx.ASGITransport — no real network,
no real LLM, no port allocation.  Each test creates a fresh SupervisorApp
and wires worker(s) to it through ASGI, exercising the full lifecycle.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.supervisor.app import create_supervisor_app
from nanobot.supervisor.models import (
    Plan,
    PlanStatus,
    PlanStep,
    Task,
    TaskStatus,
)
from nanobot.supervisor.registry import WorkerRegistry
from nanobot.supervisor.watchdog import WatchdogService
from nanobot.worker.client import SupervisorClient
from nanobot.worker.runner import WorkerRunner


# =====================================================================
#  MockProvider – scriptable LLM responses
# =====================================================================


class MockProvider(LLMProvider):
    """LLM provider that replays a pre-programmed sequence of responses.

    Supports:
    - Plain text responses (str → LLMResponse with content)
    - LLMResponse objects (returned as-is)
    - Exception instances (raised when popped)
    - A default fallback when the queue is exhausted

    ``call_log`` captures every invocation (messages snapshot).
    """

    def __init__(
        self,
        responses: list[str | LLMResponse | BaseException] | None = None,
        default: str = "Done.",
    ) -> None:
        super().__init__()
        self._queue: list[str | LLMResponse | BaseException] = list(responses or [])
        self._default = default
        self.call_log: list[list[dict[str, Any]]] = []

    async def chat(self, messages, tools=None, model=None, **kwargs) -> LLMResponse:
        self.call_log.append(list(messages))
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, LLMResponse):
                return item
            return LLMResponse(content=item)
        return LLMResponse(content=self._default)

    async def chat_with_retry(self, messages, **kwargs) -> LLMResponse:
        # Bypass retry/catch so exceptions propagate to the runner.
        return await self.chat(messages, **kwargs)

    def get_default_model(self) -> str:
        return "mock-model"


# =====================================================================
#  Fixtures
# =====================================================================


@pytest.fixture
def registry():
    """Fresh in-memory WorkerRegistry with short heartbeat timeout."""
    return WorkerRegistry(heartbeat_timeout_s=2.0)


@pytest.fixture
def app(registry):
    """FastAPI supervisor app (no XRay)."""
    return create_supervisor_app(worker_registry=registry)


@pytest.fixture
def asgi_client_factory(app):
    """Factory returning an ``httpx.AsyncClient`` that talks to *app* via ASGI."""

    def _make(worker_id: str = "test") -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    return _make


@pytest.fixture
def supervisor_client_factory(asgi_client_factory):
    """Factory returning a ``SupervisorClient`` wired through ASGI transport."""

    def _make(worker_id: str = "w-test") -> SupervisorClient:
        return SupervisorClient(
            base_url="http://test",
            worker_id=worker_id,
            http_client=asgi_client_factory(worker_id),
        )

    return _make


def _make_runner(
    *,
    worker_id: str,
    workspace: Path,
    provider: MockProvider,
    supervisor_client: SupervisorClient,
    max_iterations: int = 5,
    poll_interval_s: float = 0.05,
    heartbeat_interval_s: float = 0.5,
) -> WorkerRunner:
    """Build a WorkerRunner wired to an in-process supervisor."""
    return WorkerRunner(
        supervisor_url="http://test",          # not actually used
        worker_id=worker_id,
        worker_name=f"test-{worker_id}",
        workspace=workspace,
        provider=provider,
        model="mock-model",
        max_iterations=max_iterations,
        poll_interval_s=poll_interval_s,
        heartbeat_interval_s=heartbeat_interval_s,
        restrict_to_workspace=True,
        supervisor_client=supervisor_client,
    )


# =====================================================================
#  Helpers
# =====================================================================


async def _run_worker_until_idle(runner: WorkerRunner, timeout: float = 5.0) -> None:
    """Run the worker; stop it once the poll loop finds no task twice in a row."""
    # We override _poll_loop to auto-stop after processing available tasks.
    original_poll = runner._poll_loop

    async def _auto_stop_poll() -> None:
        idle_count = 0
        while runner._running:
            try:
                task_data = await runner.client.claim_task()
                if task_data is not None:
                    idle_count = 0
                    await runner._execute_task(task_data)
                else:
                    idle_count += 1
                    if idle_count >= 2:
                        runner._running = False
                        break
                    await asyncio.sleep(runner.poll_interval_s)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    runner._poll_loop = _auto_stop_poll  # type: ignore[assignment]
    await asyncio.wait_for(runner.run(), timeout=timeout)


# =====================================================================
#  Test 1 — Full plan lifecycle (create → approve → worker claims,
#           executes, reports → plan completes)
# =====================================================================


@pytest.mark.asyncio
async def test_full_plan_lifecycle(registry, supervisor_client_factory, tmp_path):
    """End-to-end: plan created → approved → worker polls, executes, reports
    → task completes → plan auto-advances to COMPLETED."""

    # 1. Create a single-step plan
    plan = Plan(
        plan_id="plan-1",
        title="Test plan",
        goal="Run a single step",
        steps=[
            PlanStep(index=0, instruction="Say hello", label="step-0"),
        ],
    )
    await registry.create_plan(plan)
    approved = await registry.approve_plan("plan-1")
    assert approved is not None
    assert approved.status == PlanStatus.EXECUTING

    # A task should have been created for step 0
    tasks = await registry.list_tasks(status=TaskStatus.PENDING)
    assert len(tasks) == 1
    assert tasks[0].plan_id == "plan-1"

    # 2. Fire up a worker
    provider = MockProvider(responses=["Hello from worker!"])
    client = supervisor_client_factory("w-alpha")
    runner = _make_runner(
        worker_id="w-alpha",
        workspace=tmp_path,
        provider=provider,
        supervisor_client=client,
    )

    await _run_worker_until_idle(runner)

    # 3. Verify task completed
    task = tasks[0]
    updated_task = await registry.get_task(task.task_id)
    assert updated_task is not None
    assert updated_task.status == TaskStatus.COMPLETED
    assert updated_task.result == "Hello from worker!"

    # 4. Verify plan completed
    updated_plan = await registry.get_plan("plan-1")
    assert updated_plan is not None
    assert updated_plan.status == PlanStatus.COMPLETED


# =====================================================================
#  Test 2 — Multi-worker contention (2 workers, 1 task)
# =====================================================================


@pytest.mark.asyncio
async def test_multi_worker_contention(registry, supervisor_client_factory, tmp_path):
    """Two workers race to claim a single task; only one gets it."""

    # Create a standalone task
    task = Task(task_id="task-race", instruction="Do something")
    await registry.create_task(task)

    prov_a = MockProvider(responses=["Result A"])
    prov_b = MockProvider(responses=["Result B"])

    client_a = supervisor_client_factory("w-a")
    client_b = supervisor_client_factory("w-b")

    runner_a = _make_runner(
        worker_id="w-a",
        workspace=tmp_path,
        provider=prov_a,
        supervisor_client=client_a,
    )
    runner_b = _make_runner(
        worker_id="w-b",
        workspace=tmp_path,
        provider=prov_b,
        supervisor_client=client_b,
    )

    # Run both concurrently
    await asyncio.gather(
        _run_worker_until_idle(runner_a),
        _run_worker_until_idle(runner_b),
    )

    # Exactly one worker should have completed the task
    t = await registry.get_task("task-race")
    assert t is not None
    assert t.status == TaskStatus.COMPLETED
    assert t.result in ("Result A", "Result B")

    # Only one provider should have been called
    total_calls = len(prov_a.call_log) + len(prov_b.call_log)
    assert total_calls == 1


# =====================================================================
#  Test 3 — Worker drops out, watchdog evicts, another worker takes over
# =====================================================================


@pytest.mark.asyncio
async def test_worker_eviction_and_recovery(registry, supervisor_client_factory, tmp_path):
    """Worker disappears → watchdog evicts → task re-queued → second worker picks up."""

    # Create a task
    task = Task(task_id="task-evict", instruction="Process data")
    await registry.create_task(task)

    # Worker-1 registers and claims but never finishes (simulating crash)
    from nanobot.supervisor.models import WorkerRegisterRequest, TaskClaimRequest

    await registry.register_worker(
        WorkerRegisterRequest(worker_id="w-dead", name="dead-worker")
    )
    claimed = await registry.claim_task(TaskClaimRequest(worker_id="w-dead"))
    assert claimed is not None
    assert claimed.task_id == "task-evict"
    assert claimed.status == TaskStatus.ASSIGNED

    # Simulate heartbeat timeout by backdating last_heartbeat
    w = await registry.get_worker("w-dead")
    assert w is not None
    w.last_heartbeat = time.time() - 999.0   # way past timeout

    # Watchdog scan + evict
    unhealthy = await registry.scan_unhealthy_workers()
    assert len(unhealthy) == 1
    reassigned = await registry.evict_worker("w-dead")
    assert len(reassigned) == 1

    # Task should be back to PENDING
    t = await registry.get_task("task-evict")
    assert t is not None
    assert t.status == TaskStatus.PENDING
    assert t.worker_id is None

    # Worker-2 picks up
    prov = MockProvider(responses=["Recovered result"])
    client = supervisor_client_factory("w-hero")
    runner = _make_runner(
        worker_id="w-hero",
        workspace=tmp_path,
        provider=prov,
        supervisor_client=client,
    )
    await _run_worker_until_idle(runner)

    t2 = await registry.get_task("task-evict")
    assert t2 is not None
    assert t2.status == TaskStatus.COMPLETED
    assert t2.result == "Recovered result"
    assert t2.worker_id == "w-hero"


# =====================================================================
#  Test 4 — Task execution failure
# =====================================================================


@pytest.mark.asyncio
async def test_task_failure_propagates(registry, supervisor_client_factory, tmp_path):
    """An LLM error during execution → task marked FAILED."""

    task = Task(task_id="task-fail", instruction="This will fail")
    await registry.create_task(task)

    # Provider raises on first call
    prov = MockProvider(responses=[RuntimeError("LLM exploded")])
    client = supervisor_client_factory("w-fail")
    runner = _make_runner(
        worker_id="w-fail",
        workspace=tmp_path,
        provider=prov,
        supervisor_client=client,
    )
    await _run_worker_until_idle(runner)

    t = await registry.get_task("task-fail")
    assert t is not None
    assert t.status == TaskStatus.FAILED
    assert "LLM exploded" in (t.error or "")


@pytest.mark.asyncio
async def test_task_retry_moves_to_different_worker(registry, supervisor_client_factory, tmp_path):
    task = Task(task_id="task-retry", instruction="Retry me", max_retries=1)
    await registry.create_task(task)

    standby_client = supervisor_client_factory("w-retry-2")
    await standby_client.register("test-w-retry-2")

    failing_provider = MockProvider(responses=[RuntimeError("temporary failure")])
    worker_one = _make_runner(
        worker_id="w-retry-1",
        workspace=tmp_path,
        provider=failing_provider,
        supervisor_client=supervisor_client_factory("w-retry-1"),
    )
    await _run_worker_until_idle(worker_one)

    after_first_attempt = await registry.get_task("task-retry")
    assert after_first_attempt is not None
    assert after_first_attempt.status == TaskStatus.PENDING
    assert after_first_attempt.retry_count == 1
    assert after_first_attempt.last_failed_worker_id == "w-retry-1"

    second_worker = _make_runner(
        worker_id="w-retry-2",
        workspace=tmp_path,
        provider=MockProvider(responses=["Recovered"]),
        supervisor_client=standby_client,
    )
    await _run_worker_until_idle(second_worker)

    final_task = await registry.get_task("task-retry")
    assert final_task is not None
    assert final_task.status == TaskStatus.COMPLETED
    assert final_task.result == "Recovered"
    assert final_task.last_failed_worker_id is None


@pytest.mark.asyncio
async def test_task_timeout_propagates(registry, supervisor_client_factory, tmp_path):
    """A long-running LLM call should time out and mark the task FAILED."""

    task = Task(task_id="task-timeout", instruction="This will hang", timeout_s=0.05)
    await registry.create_task(task)

    prov = MockProvider(responses=["too slow"])

    async def _slow_chat(messages, tools=None, model=None, **kwargs):
        await asyncio.sleep(0.2)
        return LLMResponse(content="too slow")

    prov.chat = _slow_chat  # type: ignore[method-assign]
    prov.chat_with_retry = _slow_chat  # type: ignore[method-assign]

    client = supervisor_client_factory("w-timeout")
    runner = _make_runner(
        worker_id="w-timeout",
        workspace=tmp_path,
        provider=prov,
        supervisor_client=client,
    )
    await _run_worker_until_idle(runner)

    t = await registry.get_task("task-timeout")
    assert t is not None
    assert t.status == TaskStatus.FAILED
    assert t.error == "task timed out after 0.05s"


@pytest.mark.asyncio
async def test_plan_fails_when_task_fails(registry, supervisor_client_factory, tmp_path):
    """When a plan step's task fails, the plan itself should be marked FAILED."""

    plan = Plan(
        plan_id="plan-fail",
        title="Failing plan",
        goal="Test failure propagation",
        steps=[
            PlanStep(index=0, instruction="Step that will fail", label="boom"),
        ],
    )
    await registry.create_plan(plan)
    await registry.approve_plan("plan-fail")

    tasks = await registry.list_tasks(plan_id="plan-fail")
    assert len(tasks) == 1

    prov = MockProvider(responses=[RuntimeError("kaboom")])
    client = supervisor_client_factory("w-boom")
    runner = _make_runner(
        worker_id="w-boom",
        workspace=tmp_path,
        provider=prov,
        supervisor_client=client,
    )
    await _run_worker_until_idle(runner)

    p = await registry.get_plan("plan-fail")
    assert p is not None
    assert p.status == PlanStatus.FAILED


# =====================================================================
#  Test 5 — Multi-step plan with dependencies
# =====================================================================


@pytest.mark.asyncio
async def test_multi_step_plan_with_dependencies(
    registry, supervisor_client_factory, tmp_path
):
    """Three-step plan: step 0 → step 1 (depends on 0) → step 2 (depends on 1).
    Workers execute one at a time; plan auto-advances through all steps."""

    plan = Plan(
        plan_id="plan-chain",
        title="Chain plan",
        goal="Sequential execution",
        steps=[
            PlanStep(index=0, instruction="First", label="s0"),
            PlanStep(index=1, instruction="Second", label="s1", depends_on=[0]),
            PlanStep(index=2, instruction="Third", label="s2", depends_on=[1]),
        ],
    )
    await registry.create_plan(plan)
    await registry.approve_plan("plan-chain")

    # Only step 0 should be pending (the others are blocked)
    pending = await registry.list_tasks(status=TaskStatus.PENDING)
    assert len(pending) == 1
    assert pending[0].step_index == 0

    # Worker executes all 3 steps sequentially (poll loop picks up as they unlock)
    prov = MockProvider(responses=["res-0", "res-1", "res-2"])
    client = supervisor_client_factory("w-seq")
    runner = _make_runner(
        worker_id="w-seq",
        workspace=tmp_path,
        provider=prov,
        supervisor_client=client,
    )
    await _run_worker_until_idle(runner, timeout=10.0)

    # All tasks completed
    all_tasks = await registry.list_tasks(plan_id="plan-chain")
    assert all(t.status == TaskStatus.COMPLETED for t in all_tasks), [
        (t.task_id, t.status) for t in all_tasks
    ]

    # Plan completed
    p = await registry.get_plan("plan-chain")
    assert p is not None
    assert p.status == PlanStatus.COMPLETED


# =====================================================================
#  Test 6 — Tool call round-trip
# =====================================================================


@pytest.mark.asyncio
async def test_tool_call_round_trip(registry, supervisor_client_factory, tmp_path):
    """Worker executes a task where the LLM issues a tool call (list_directory)
    and then produces a final text response."""

    # Write a file so list_directory has something to find
    (tmp_path / "hello.txt").write_text("hi")

    tool_call_response = LLMResponse(
        content=None,
        tool_calls=[
            ToolCallRequest(
                id="tc-1",
                name="list_directory",
                arguments={"path": str(tmp_path)},
            )
        ],
    )
    final_response = LLMResponse(content="I see hello.txt in the directory.")

    task = Task(task_id="task-tool", instruction="List the workspace")
    await registry.create_task(task)

    prov = MockProvider(responses=[tool_call_response, final_response])
    client = supervisor_client_factory("w-tool")
    runner = _make_runner(
        worker_id="w-tool",
        workspace=tmp_path,
        provider=prov,
        supervisor_client=client,
    )
    await _run_worker_until_idle(runner)

    t = await registry.get_task("task-tool")
    assert t is not None
    assert t.status == TaskStatus.COMPLETED
    assert "hello.txt" in (t.result or "")

    # Provider should have been called twice (tool_call + final)
    assert len(prov.call_log) == 2


# =====================================================================
#  Test 7 — Progress reporting
# =====================================================================


@pytest.mark.asyncio
async def test_progress_is_reported(registry, supervisor_client_factory, tmp_path):
    """Progress updates from the worker should be recorded on the task."""

    task = Task(task_id="task-prog", instruction="Multi-step work")
    await registry.create_task(task)

    # Two tool calls then final answer  →  3 LLM calls = 3 progress reports
    tool_resp = LLMResponse(
        content=None,
        tool_calls=[
            ToolCallRequest(id="tc-a", name="list_directory",
                            arguments={"path": str(tmp_path)}),
        ],
    )
    prov = MockProvider(responses=[tool_resp, tool_resp, "All done."])
    client = supervisor_client_factory("w-prog")
    runner = _make_runner(
        worker_id="w-prog",
        workspace=tmp_path,
        provider=prov,
        supervisor_client=client,
    )
    await _run_worker_until_idle(runner)

    t = await registry.get_task("task-prog")
    assert t is not None
    assert t.status == TaskStatus.COMPLETED
    # Should have at least 2 progress entries (best-effort, but ASGI is reliable)
    assert len(t.progress) >= 2


# =====================================================================
#  Test 8 — Watchdog service integration
# =====================================================================


@pytest.mark.asyncio
async def test_watchdog_evicts_dead_worker(registry):
    """Watchdog background loop detects and evicts a dead worker."""

    from nanobot.supervisor.models import WorkerRegisterRequest

    await registry.register_worker(
        WorkerRegisterRequest(worker_id="w-zombie", name="zombie")
    )
    task = Task(task_id="task-z", instruction="zombie task")
    await registry.create_task(task)
    from nanobot.supervisor.models import TaskClaimRequest

    await registry.claim_task(TaskClaimRequest(worker_id="w-zombie"))

    # Backdate heartbeat
    w = await registry.get_worker("w-zombie")
    assert w is not None
    w.last_heartbeat = time.time() - 999.0

    # Run watchdog with very short interval
    wd = WatchdogService(registry, check_interval_s=0.05)
    await wd.start()
    await asyncio.sleep(0.2)
    wd.stop()

    # Worker should be gone
    assert await registry.get_worker("w-zombie") is None

    # Task should be back to PENDING
    t = await registry.get_task("task-z")
    assert t is not None
    assert t.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_watchdog_fails_stale_task(registry, supervisor_client_factory):
    """Watchdog background loop detects timed out tasks and marks them FAILED."""

    client = supervisor_client_factory("w-stale")
    await client.register("stale-worker")

    task = Task(
        task_id="task-stale",
        instruction="stale task",
        timeout_s=0.05,
        status=TaskStatus.RUNNING,
        worker_id="w-stale",
        assigned_at=time.time() - 1.0,
    )
    await registry.create_task(task)

    worker = await registry.get_worker("w-stale")
    assert worker is not None
    worker.status = "busy"  # type: ignore[assignment]
    worker.current_task_id = task.task_id

    wd = WatchdogService(registry, check_interval_s=0.05)
    await wd.start()
    await asyncio.sleep(0.2)
    wd.stop()

    updated_task = await registry.get_task("task-stale")
    assert updated_task is not None
    assert updated_task.status == TaskStatus.FAILED
    assert updated_task.error == "task timed out after 0.05s"

    updated_worker = await registry.get_worker("w-stale")
    assert updated_worker is not None
    assert updated_worker.status == "online"
    assert updated_worker.current_task_id is None

    await client.close()


# =====================================================================
#  Test 9 — SupervisorClient via ASGI transport
# =====================================================================


@pytest.mark.asyncio
async def test_supervisor_client_full_lifecycle(registry, supervisor_client_factory):
    """SupervisorClient register → heartbeat → claim → progress → result → unregister."""

    client = supervisor_client_factory("w-cli")

    # Register
    reg_resp = await client.register("test-worker", ["python"])
    assert reg_resp["worker"]["worker_id"] == "w-cli"

    # Heartbeat
    hb_resp = await client.heartbeat()
    assert hb_resp["worker"]["status"] == "online"

    # Create a task via registry directly, then claim via client
    task = Task(task_id="task-cli", instruction="cli test")
    await registry.create_task(task)

    claimed = await client.claim_task(["python"])
    assert claimed is not None
    assert claimed["task_id"] == "task-cli"

    # Progress
    await client.report_progress("task-cli", iteration=1, message="working")

    # Result
    await client.report_result("task-cli", status="completed", result="done!")

    t = await registry.get_task("task-cli")
    assert t is not None
    assert t.status == TaskStatus.COMPLETED

    # Unregister
    await client.unregister()
    assert await registry.get_worker("w-cli") is None

    await client.close()


# =====================================================================
#  Test 10 — Concurrent plan with parallel steps
# =====================================================================


@pytest.mark.asyncio
async def test_parallel_steps_in_plan(registry, supervisor_client_factory, tmp_path):
    """Plan with two independent steps (no dependencies) — both run in parallel."""

    plan = Plan(
        plan_id="plan-par",
        title="Parallel plan",
        goal="Two parallel steps",
        steps=[
            PlanStep(index=0, instruction="Alpha", label="a"),
            PlanStep(index=1, instruction="Beta", label="b"),
            # step 2 depends on both 0 and 1
            PlanStep(index=2, instruction="Merge", label="merge", depends_on=[0, 1]),
        ],
    )
    await registry.create_plan(plan)
    await registry.approve_plan("plan-par")

    # Steps 0 and 1 should both be pending
    pending = await registry.list_tasks(status=TaskStatus.PENDING)
    assert len(pending) == 2

    # Two workers + one worker for the merge step
    prov_a = MockProvider(responses=["alpha-done"])
    prov_b = MockProvider(responses=["beta-done"])

    client_a = supervisor_client_factory("w-pa")
    client_b = supervisor_client_factory("w-pb")

    runner_a = _make_runner(
        worker_id="w-pa", workspace=tmp_path,
        provider=prov_a, supervisor_client=client_a,
    )
    runner_b = _make_runner(
        worker_id="w-pb", workspace=tmp_path,
        provider=prov_b, supervisor_client=client_b,
    )

    # Run both workers plus a third for the merge step.
    # Because of FIFO + speed, one worker may grab multiple tasks.
    # We just need to provide enough responses across all providers.
    prov_c = MockProvider(responses=["merge-done"])
    client_c = supervisor_client_factory("w-pc")
    runner_c = _make_runner(
        worker_id="w-pc", workspace=tmp_path,
        provider=prov_c, supervisor_client=client_c,
    )

    await asyncio.gather(
        _run_worker_until_idle(runner_a),
        _run_worker_until_idle(runner_b),
        _run_worker_until_idle(runner_c),
    )

    # All 3 steps must be completed
    all_tasks = await registry.list_tasks(plan_id="plan-par")
    assert len(all_tasks) == 3
    assert all(t.status == TaskStatus.COMPLETED for t in all_tasks)

    p = await registry.get_plan("plan-par")
    assert p is not None
    assert p.status == PlanStatus.COMPLETED


# =====================================================================
#  Test 11 — Task cancellation mid-flight
# =====================================================================


@pytest.mark.asyncio
async def test_cancel_task_before_claim(registry, supervisor_client_factory, tmp_path):
    """Cancelling a pending task prevents it from being claimed."""

    task = Task(task_id="task-cancel", instruction="never run")
    await registry.create_task(task)

    # Cancel before any worker claims it
    cancelled = await registry.cancel_task("task-cancel")
    assert cancelled is not None
    assert cancelled.status == TaskStatus.CANCELLED

    # Worker tries to claim — nothing available
    prov = MockProvider()
    client = supervisor_client_factory("w-cancel")
    runner = _make_runner(
        worker_id="w-cancel", workspace=tmp_path,
        provider=prov, supervisor_client=client,
    )
    await _run_worker_until_idle(runner)

    # Provider should never have been called
    assert len(prov.call_log) == 0


# =====================================================================
#  Test 12 — Multiple tasks, single worker processes them FIFO
# =====================================================================


@pytest.mark.asyncio
async def test_fifo_task_processing(registry, supervisor_client_factory, tmp_path):
    """Worker processes multiple pending tasks in FIFO order."""

    for i in range(3):
        await registry.create_task(
            Task(task_id=f"fifo-{i}", instruction=f"Task {i}")
        )

    prov = MockProvider(responses=[f"result-{i}" for i in range(3)])
    client = supervisor_client_factory("w-fifo")
    runner = _make_runner(
        worker_id="w-fifo", workspace=tmp_path,
        provider=prov, supervisor_client=client,
    )
    await _run_worker_until_idle(runner, timeout=10.0)

    for i in range(3):
        t = await registry.get_task(f"fifo-{i}")
        assert t is not None
        assert t.status == TaskStatus.COMPLETED
        assert t.result == f"result-{i}"
