from __future__ import annotations

import pytest

from nanobot.bus.queue import MessageBus
from nanobot.supervisor.event_sink import SupervisorEventType
from nanobot.supervisor.models import Plan, PlanStep, Task, TaskProgress, TaskStatus
from nanobot.supervisor.result_reporter import SupervisorResultReporter


@pytest.mark.asyncio
async def test_result_reporter_publishes_completed_task_to_origin_channel() -> None:
    bus = MessageBus()
    reporter = SupervisorResultReporter(bus)
    task = Task(
        task_id="task-1",
        instruction="do work",
        label="Delegated task",
        origin_channel="telegram",
        origin_chat_id="chat-1",
        status=TaskStatus.COMPLETED,
        result="## Summary\n\nAll done.",
    )

    await reporter.on_task_event(task, "task_completed")

    msg = await bus.consume_outbound()
    assert msg.channel == "telegram"
    assert msg.chat_id == "chat-1"
    assert "Supervisor Task Completed" in msg.content
    assert "## Summary" in msg.content
    assert msg.metadata["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_result_reporter_publishes_task_progress_with_merge_metadata() -> None:
    bus = MessageBus()

    async def _lookup_plan(_: str) -> Plan:
        return Plan(
            plan_id="plan-1",
            steps=[
                PlanStep(index=0, instruction="first", status=TaskStatus.COMPLETED),
                PlanStep(index=1, instruction="second", status=TaskStatus.RUNNING),
            ],
        )

    reporter = SupervisorResultReporter(bus, plan_lookup=_lookup_plan)
    task = Task(
        task_id="task-progress",
        instruction="do work",
        plan_id="plan-1",
        step_index=1,
        worker_id="alpha",
        origin_channel="telegram",
        origin_chat_id="chat-1",
        status=TaskStatus.RUNNING,
        progress=[TaskProgress(iteration=2, message="正在分析代码结构...")],
    )

    await reporter.on_task_event(task, SupervisorEventType.TASK_PROGRESS)

    msg = await bus.consume_outbound()
    assert msg.content == "⏳ 步骤 2/2: 正在分析代码结构... (Worker: alpha)"
    assert msg.metadata["_progress"] is True
    assert msg.metadata["progress_scope"] == "task"
    assert msg.metadata["progress_key"] == "task:task-progress"
    assert msg.metadata["progress_mode"] == "replace"


@pytest.mark.asyncio
async def test_result_reporter_publishes_plan_progress_after_task_completion() -> None:
    bus = MessageBus()

    async def _lookup_plan(_: str) -> Plan:
        return Plan(
            plan_id="plan-1",
            steps=[
                PlanStep(index=0, instruction="first", status=TaskStatus.COMPLETED),
                PlanStep(index=1, instruction="second", status=TaskStatus.COMPLETED),
                PlanStep(index=2, instruction="third", status=TaskStatus.PENDING),
            ],
        )

    reporter = SupervisorResultReporter(bus, plan_lookup=_lookup_plan)
    task = Task(
        task_id="task-1",
        instruction="do work",
        plan_id="plan-1",
        origin_channel="telegram",
        origin_chat_id="chat-1",
        status=TaskStatus.COMPLETED,
        result="done",
    )

    await reporter.on_task_event(task, SupervisorEventType.TASK_COMPLETED)

    result_msg = await bus.consume_outbound()
    progress_msg = await bus.consume_outbound()
    assert "Supervisor Task Completed" in result_msg.content
    assert progress_msg.content == "📋 计划进度: 2/3 步骤完成"
    assert progress_msg.metadata["_progress"] is True
    assert progress_msg.metadata["progress_scope"] == "plan"
    assert progress_msg.metadata["progress_key"] == "plan:plan-1"


@pytest.mark.asyncio
async def test_result_reporter_chunks_failed_output() -> None:
    bus = MessageBus()
    reporter = SupervisorResultReporter(bus, max_message_len=80)
    task = Task(
        task_id="task-2",
        instruction="do work",
        origin_channel="slack",
        origin_chat_id="chat-2",
        status=TaskStatus.FAILED,
        error="boom",
        result="line 1\n" * 40,
    )

    await reporter.on_task_event(task, "task_failed")

    first = await bus.consume_outbound()
    second = await bus.consume_outbound()
    assert "Supervisor Task Failed" in first.content
    assert "Part 1/" in first.content
    assert "Partial Output" in first.content
    assert "Part 2/" in second.content
    assert first.metadata["chunk_total"] >= 2


@pytest.mark.asyncio
async def test_result_reporter_skips_cli_origin() -> None:
    bus = MessageBus()
    reporter = SupervisorResultReporter(bus)
    task = Task(
        task_id="task-3",
        instruction="do work",
        origin_channel="cli",
        origin_chat_id="direct",
        status=TaskStatus.COMPLETED,
        result="done",
    )

    await reporter.on_task_event(task, "task_completed")

    assert bus.outbound_size == 0