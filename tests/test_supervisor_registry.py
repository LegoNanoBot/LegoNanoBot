"""Tests for supervisor WorkerRegistry."""

import asyncio

import pytest

from nanobot.supervisor.models import (
    HeartbeatRequest,
    Plan,
    PlanStatus,
    PlanStep,
    Task,
    TaskClaimRequest,
    TaskProgressReport,
    TaskResultReport,
    TaskStatus,
    WorkerRegisterRequest,
    WorkerStatus,
)
from nanobot.supervisor.event_sink import SupervisorEventType
from nanobot.supervisor.registry import WorkerRegistry
from nanobot.supervisor.watchdog import WatchdogService


class _FakeEventSink:
    def __init__(self):
        self.events = []

    async def emit(self, run_id, event_type, data):
        self.events.append({
            "run_id": run_id,
            "event_type": event_type,
            "data": data,
        })


@pytest.fixture
def registry():
    return WorkerRegistry(heartbeat_timeout_s=5.0)


@pytest.fixture
def event_collector():
    return _FakeEventSink()


@pytest.fixture
def registry_with_events(event_collector):
    return WorkerRegistry(heartbeat_timeout_s=5.0, event_sink=event_collector)


@pytest.fixture
def _reg_worker(registry):
    """Helper: register a worker and return its info."""
    async def _register(worker_id="w1", name="worker-1"):
        req = WorkerRegisterRequest(worker_id=worker_id, name=name)
        return await registry.register_worker(req)
    return _register


# ---------------------------------------------------------------------------
# Worker tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_worker(registry, _reg_worker):
    w = await _reg_worker()
    assert w.worker_id == "w1"
    assert w.status == WorkerStatus.ONLINE
    workers = await registry.list_workers()
    assert len(workers) == 1


@pytest.mark.asyncio
async def test_heartbeat(registry, _reg_worker):
    await _reg_worker()
    req = HeartbeatRequest(worker_id="w1", current_task_id="t1", status=WorkerStatus.BUSY)
    w = await registry.heartbeat(req)
    assert w is not None
    assert w.current_task_id == "t1"
    assert w.status == WorkerStatus.BUSY


@pytest.mark.asyncio
async def test_heartbeat_unknown_worker(registry):
    req = HeartbeatRequest(worker_id="unknown")
    w = await registry.heartbeat(req)
    assert w is None


@pytest.mark.asyncio
async def test_unregister_worker(registry, _reg_worker):
    await _reg_worker()
    ok = await registry.unregister_worker("w1")
    assert ok is True
    workers = await registry.list_workers()
    assert len(workers) == 0


@pytest.mark.asyncio
async def test_unregister_releases_tasks(registry, _reg_worker):
    await _reg_worker()
    task = Task(instruction="test", status=TaskStatus.ASSIGNED, worker_id="w1")
    await registry.create_task(task)
    await registry.unregister_worker("w1")
    t = await registry.get_task(task.task_id)
    assert t.status == TaskStatus.PENDING
    assert t.worker_id is None


# ---------------------------------------------------------------------------
# Task tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_get_task(registry):
    task = Task(instruction="do work", label="work")
    created = await registry.create_task(task)
    assert created.task_id == task.task_id
    fetched = await registry.get_task(task.task_id)
    assert fetched is not None
    assert fetched.instruction == "do work"


@pytest.mark.asyncio
async def test_claim_task(registry, _reg_worker):
    await _reg_worker()
    task = Task(instruction="do work")
    await registry.create_task(task)

    req = TaskClaimRequest(worker_id="w1")
    claimed = await registry.claim_task(req)
    assert claimed is not None
    assert claimed.status == TaskStatus.ASSIGNED
    assert claimed.worker_id == "w1"


@pytest.mark.asyncio
async def test_claim_no_pending_tasks(registry, _reg_worker):
    await _reg_worker()
    req = TaskClaimRequest(worker_id="w1")
    claimed = await registry.claim_task(req)
    assert claimed is None


@pytest.mark.asyncio
async def test_claim_unknown_worker(registry):
    task = Task(instruction="do work")
    await registry.create_task(task)
    req = TaskClaimRequest(worker_id="unknown")
    claimed = await registry.claim_task(req)
    assert claimed is None


@pytest.mark.asyncio
async def test_report_progress(registry, _reg_worker):
    await _reg_worker()
    task = Task(instruction="do work")
    await registry.create_task(task)
    await registry.claim_task(TaskClaimRequest(worker_id="w1"))

    rpt = TaskProgressReport(task_id=task.task_id, worker_id="w1", iteration=1, message="halfway")
    updated = await registry.report_progress(rpt)
    assert updated is not None
    assert len(updated.progress) == 1
    assert updated.progress[0].message == "halfway"
    assert updated.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_report_result(registry, _reg_worker):
    await _reg_worker()
    task = Task(instruction="do work")
    await registry.create_task(task)
    await registry.claim_task(TaskClaimRequest(worker_id="w1"))

    rpt = TaskResultReport(
        task_id=task.task_id,
        worker_id="w1",
        status=TaskStatus.COMPLETED,
        result="done!",
    )
    updated = await registry.report_result(rpt)
    assert updated is not None
    assert updated.status == TaskStatus.COMPLETED
    assert updated.result == "done!"

    # Worker should be freed
    w = await registry.get_worker("w1")
    assert w.status == WorkerStatus.ONLINE
    assert w.current_task_id is None


@pytest.mark.asyncio
async def test_cancel_task(registry, _reg_worker):
    await _reg_worker()
    task = Task(instruction="do work")
    await registry.create_task(task)
    await registry.claim_task(TaskClaimRequest(worker_id="w1"))

    cancelled = await registry.cancel_task(task.task_id)
    assert cancelled.status == TaskStatus.CANCELLED

    w = await registry.get_worker("w1")
    assert w.status == WorkerStatus.ONLINE


@pytest.mark.asyncio
async def test_list_tasks_filter(registry):
    t1 = Task(instruction="a", status=TaskStatus.PENDING)
    t2 = Task(instruction="b", status=TaskStatus.COMPLETED)
    await registry.create_task(t1)
    await registry.create_task(t2)

    pending = await registry.list_tasks(status=TaskStatus.PENDING)
    assert len(pending) == 1
    assert pending[0].task_id == t1.task_id

    all_tasks = await registry.list_tasks()
    assert len(all_tasks) == 2


# ---------------------------------------------------------------------------
# Plan tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_plan(registry):
    steps = [
        PlanStep(index=0, instruction="step 0"),
        PlanStep(index=1, instruction="step 1", depends_on=[0]),
    ]
    plan = Plan(title="Test", goal="test", steps=steps)
    created = await registry.create_plan(plan)
    assert created.plan_id == plan.plan_id
    assert created.status == PlanStatus.DRAFT


@pytest.mark.asyncio
async def test_approve_plan_creates_tasks(registry):
    steps = [
        PlanStep(index=0, instruction="step 0"),
        PlanStep(index=1, instruction="step 1", depends_on=[0]),
    ]
    plan = Plan(title="Test", goal="test", steps=steps)
    await registry.create_plan(plan)

    approved = await registry.approve_plan(plan.plan_id)
    assert approved.status == PlanStatus.EXECUTING

    # Step 0 has no dependencies → task created
    assert approved.steps[0].task_id is not None
    # Step 1 depends on step 0 → no task yet
    assert approved.steps[1].task_id is None

    # Verify task exists
    task = await registry.get_task(approved.steps[0].task_id)
    assert task is not None
    assert task.plan_id == plan.plan_id


@pytest.mark.asyncio
async def test_plan_advances_on_task_completion(registry, _reg_worker):
    await _reg_worker()

    steps = [
        PlanStep(index=0, instruction="step 0"),
        PlanStep(index=1, instruction="step 1", depends_on=[0]),
    ]
    plan = Plan(title="Test", goal="test", steps=steps)
    await registry.create_plan(plan)
    await registry.approve_plan(plan.plan_id)

    # Claim and complete step 0
    claimed = await registry.claim_task(TaskClaimRequest(worker_id="w1"))
    assert claimed is not None

    rpt = TaskResultReport(
        task_id=claimed.task_id,
        worker_id="w1",
        status=TaskStatus.COMPLETED,
        result="step 0 done",
    )
    await registry.report_result(rpt)

    # Now schedule ready steps — step 1 should become available
    await registry._schedule_ready_steps(plan.plan_id)

    updated_plan = await registry.get_plan(plan.plan_id)
    assert updated_plan.steps[1].task_id is not None


@pytest.mark.asyncio
async def test_cancel_plan(registry):
    steps = [PlanStep(index=0, instruction="step 0")]
    plan = Plan(title="Test", goal="test", steps=steps)
    await registry.create_plan(plan)
    await registry.approve_plan(plan.plan_id)

    cancelled = await registry.cancel_plan(plan.plan_id)
    assert cancelled.status == PlanStatus.CANCELLED


# ---------------------------------------------------------------------------
# Health scanning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_unhealthy_workers(registry):
    import time

    req = WorkerRegisterRequest(worker_id="w1", name="old")
    await registry.register_worker(req)

    # Manually set last_heartbeat to the past
    worker = await registry.get_worker("w1")
    worker.last_heartbeat = time.time() - 999

    unhealthy = await registry.scan_unhealthy_workers()
    assert len(unhealthy) == 1
    assert unhealthy[0].status == WorkerStatus.UNHEALTHY


@pytest.mark.asyncio
async def test_evict_worker_requeues_tasks(registry, _reg_worker):
    await _reg_worker()
    task = Task(instruction="test", status=TaskStatus.RUNNING, worker_id="w1")
    await registry.create_task(task)

    reassigned = await registry.evict_worker("w1")
    assert len(reassigned) == 1
    assert reassigned[0].status == TaskStatus.PENDING
    assert reassigned[0].worker_id is None

    # Worker should be gone
    w = await registry.get_worker("w1")
    assert w is None


@pytest.mark.asyncio
async def test_registry_emits_worker_events(registry_with_events, event_collector):
    req = WorkerRegisterRequest(worker_id="w1", name="worker-1")
    await registry_with_events.register_worker(req)
    await registry_with_events.heartbeat(HeartbeatRequest(worker_id="w1", status=WorkerStatus.BUSY))

    assert len(event_collector.events) == 2
    assert event_collector.events[0]["event_type"] == SupervisorEventType.WORKER_REGISTERED
    assert event_collector.events[0]["data"]["worker_id"] == "w1"
    assert event_collector.events[1]["event_type"] == SupervisorEventType.WORKER_HEARTBEAT
    assert event_collector.events[1]["data"]["status"] == WorkerStatus.BUSY.value


@pytest.mark.asyncio
async def test_registry_emits_task_events(registry_with_events, event_collector):
    await registry_with_events.register_worker(WorkerRegisterRequest(worker_id="w1", name="worker-1"))
    task = Task(instruction="do work")
    await registry_with_events.create_task(task)

    claimed = await registry_with_events.claim_task(TaskClaimRequest(worker_id="w1"))
    assert claimed is not None

    await registry_with_events.report_progress(
        TaskProgressReport(task_id=task.task_id, worker_id="w1", iteration=1, message="halfway"),
    )
    await registry_with_events.report_result(
        TaskResultReport(task_id=task.task_id, worker_id="w1", status=TaskStatus.COMPLETED, result="done"),
    )

    event_types = [e["event_type"] for e in event_collector.events]
    assert SupervisorEventType.TASK_CREATED in event_types
    assert SupervisorEventType.TASK_ASSIGNED in event_types
    assert SupervisorEventType.TASK_PROGRESS in event_types
    assert SupervisorEventType.TASK_COMPLETED in event_types


@pytest.mark.asyncio
async def test_registry_emits_plan_events(registry_with_events, event_collector):
    await registry_with_events.register_worker(WorkerRegisterRequest(worker_id="w1", name="worker-1"))
    steps = [PlanStep(index=0, instruction="step 0")]
    plan = Plan(title="Test", goal="test", steps=steps)
    await registry_with_events.create_plan(plan)
    await registry_with_events.approve_plan(plan.plan_id)

    claimed = await registry_with_events.claim_task(TaskClaimRequest(worker_id="w1"))
    assert claimed is not None
    await registry_with_events.report_result(
        TaskResultReport(task_id=claimed.task_id, worker_id="w1", status=TaskStatus.COMPLETED, result="ok"),
    )

    event_types = [e["event_type"] for e in event_collector.events]
    assert SupervisorEventType.PLAN_CREATED in event_types
    assert SupervisorEventType.PLAN_APPROVED in event_types
    assert SupervisorEventType.PLAN_COMPLETED in event_types


@pytest.mark.asyncio
async def test_registry_emits_unhealthy_and_evicted_events(registry_with_events, event_collector):
    await registry_with_events.register_worker(WorkerRegisterRequest(worker_id="w1", name="worker-1"))

    worker = await registry_with_events.get_worker("w1")
    assert worker is not None
    worker.last_heartbeat -= 999

    unhealthy = await registry_with_events.scan_unhealthy_workers()
    assert len(unhealthy) == 1
    await registry_with_events.evict_worker("w1")

    event_types = [e["event_type"] for e in event_collector.events]
    assert SupervisorEventType.WORKER_UNHEALTHY in event_types
    assert SupervisorEventType.WORKER_EVICTED in event_types


@pytest.mark.asyncio
async def test_watchdog_emits_worker_evicted_event(event_collector):
    registry = WorkerRegistry(heartbeat_timeout_s=0.01, event_sink=event_collector)
    await registry.register_worker(WorkerRegisterRequest(worker_id="w1", name="worker-1"))

    worker = await registry.get_worker("w1")
    assert worker is not None
    worker.last_heartbeat -= 999

    watchdog = WatchdogService(registry, check_interval_s=0.01)
    await watchdog.start()
    await asyncio.sleep(0.05)
    watchdog.stop()

    evicted_events = [e for e in event_collector.events if e["event_type"] == SupervisorEventType.WORKER_EVICTED]
    assert len(evicted_events) == 1
    assert evicted_events[0]["data"]["reason"] == "heartbeat_timeout"
