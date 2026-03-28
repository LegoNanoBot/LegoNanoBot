from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.delegate import DelegateToWorkerTool
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse
from nanobot.supervisor.app import create_supervisor_app
from nanobot.supervisor.models import Task, TaskStatus
from nanobot.supervisor.registry import WorkerRegistry
from nanobot.worker.inprocess import InProcessWorker


class _FakeSupervisorClient:
    def __init__(self, *, available: bool = True, final_task: dict | None = None) -> None:
        self.available = available
        self.final_task = final_task or {"task_id": "remote-1", "status": "completed", "result": "ok"}
        self.created: list[dict] = []
        self.cancelled: list[str] = []
        self.closed = False

    async def is_available(self) -> bool:
        return self.available

    async def create_task(self, **kwargs):
        self.created.append(kwargs)
        return {"task_id": self.final_task.get("task_id", "remote-1")}

    async def wait_for_task(self, task_id: str, *, poll_interval_s: float = 1.0, timeout_s: float | None = None):
        return {**self.final_task, "task_id": task_id}

    async def cancel_task(self, task_id: str):
        self.cancelled.append(task_id)
        return {"task_id": task_id, "status": "cancelled"}

    async def close(self) -> None:
        self.closed = True


class _Provider:
    async def chat_with_retry(self, *args, **kwargs):
        return LLMResponse(content="done")

    def get_default_model(self) -> str:
        return "mock-model"


class _WorkerProvider:
    async def chat_with_retry(self, *args, **kwargs):
        return LLMResponse(content="worker complete")


@pytest.mark.asyncio
async def test_delegate_to_worker_tool_waits_for_result() -> None:
    client = _FakeSupervisorClient(final_task={"task_id": "t-1", "status": "completed", "result": "delegated result"})
    tool = DelegateToWorkerTool(client)
    tool.set_context("cli", "direct")

    result = await tool.execute(instruction="do work", label="demo", wait=True)

    assert result == "delegated result"
    assert client.created[0]["instruction"] == "do work"
    assert client.created[0]["origin_channel"] == "cli"


@pytest.mark.asyncio
async def test_subagent_manager_remote_spawn_announces_back_to_main_agent(tmp_path: Path) -> None:
    bus = MessageBus()
    client = _FakeSupervisorClient(
        final_task={"task_id": "remote-42", "status": "completed", "result": "remote answer"}
    )
    manager = SubagentManager(
        provider=_Provider(),
        workspace=tmp_path,
        bus=bus,
        supervisor_client=client,
        default_mode="auto",
    )

    message = await manager.spawn(
        task="collect facts",
        label="facts",
        origin_channel="test",
        origin_chat_id="chat-1",
        session_key="test:chat-1",
    )

    assert "remote task: remote-42" in message

    inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
    assert inbound.channel == "system"
    assert inbound.chat_id == "test:chat-1"
    assert "remote answer" in inbound.content


@pytest.mark.asyncio
async def test_agent_loop_registers_delegate_tool_when_supervisor_available(tmp_path: Path) -> None:
    bus = MessageBus()
    client = _FakeSupervisorClient(available=True)
    loop = AgentLoop(
        bus=bus,
        provider=_Provider(),
        workspace=tmp_path,
        supervisor_client=client,
    )

    await loop._ensure_supervisor_tools()

    assert loop.tools.has("delegate_to_worker") is True
    assert loop.subagents.default_mode == "auto"

    await loop.close_mcp()
    assert client.closed is True


async def _run_runner_until_idle(worker: InProcessWorker, timeout: float = 5.0) -> None:
    runner = worker.runner

    async def _auto_stop_poll() -> None:
        idle_count = 0
        while runner._running:
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

    runner._poll_loop = _auto_stop_poll  # type: ignore[assignment]
    await asyncio.wait_for(worker.run(), timeout=timeout)


@pytest.mark.asyncio
async def test_inprocess_worker_executes_task_end_to_end(tmp_path: Path) -> None:
    registry = WorkerRegistry(heartbeat_timeout_s=2.0)
    app = create_supervisor_app(worker_registry=registry)
    await registry.create_task(Task(task_id="task-1", instruction="say hi"))

    worker = InProcessWorker(
        app=app,
        supervisor_url="http://test",
        workspace=tmp_path,
        provider=_WorkerProvider(),
        model="mock-model",
        worker_id="inproc-1",
        worker_name="inproc-worker-1",
        poll_interval_s=0.05,
        heartbeat_interval_s=0.2,
        restrict_to_workspace=True,
    )

    await _run_runner_until_idle(worker)

    task = await registry.get_task("task-1")
    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert task.result == "worker complete"