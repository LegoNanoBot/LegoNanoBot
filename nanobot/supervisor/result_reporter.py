"""Publish terminal supervisor task results back to the originating channel."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.supervisor.event_sink import SupervisorEventType
from nanobot.supervisor.models import Plan, Task, TaskProgress, TaskStatus
from nanobot.utils.helpers import split_message


@dataclass(slots=True)
class SupervisorResultReporter:
    """Format and publish completed or failed task results to chat channels."""

    bus: MessageBus
    max_message_len: int = 3500
    plan_lookup: Callable[[str], Awaitable[Plan | None]] | None = None

    async def on_task_event(self, task: Task, event_type: str) -> None:
        if task.origin_channel == "cli":
            return

        if event_type == SupervisorEventType.TASK_PROGRESS:
            progress_message = await self._build_progress_message(task)
            if progress_message:
                await self._publish_progress_message(
                    task=task,
                    content=progress_message,
                    scope="task",
                    progress_key=f"task:{task.task_id}",
                )
            return

        if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return

        chunks = self._build_chunks(task)
        total = len(chunks)
        for index, content in enumerate(chunks, start=1):
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=task.origin_channel,
                    chat_id=task.origin_chat_id,
                    content=content,
                    metadata={
                        "_supervisor_result": True,
                        "task_id": task.task_id,
                        "plan_id": task.plan_id,
                        "status": task.status.value,
                        "chunk_index": index,
                        "chunk_total": total,
                        "event_type": event_type,
                    },
                )
            )

        plan_progress = await self._build_plan_progress_message(task)
        if plan_progress:
            await self._publish_progress_message(
                task=task,
                content=plan_progress,
                scope="plan",
                progress_key=f"plan:{task.plan_id}",
            )

    def _build_chunks(self, task: Task) -> list[str]:
        header = self._build_header(task)
        body = self._build_body(task)
        if not body:
            return [header]

        body_chunks = split_message(body, max_len=self.max_message_len)
        if len(body_chunks) == 1:
            return [f"{header}\n\n{body_chunks[0]}"]

        chunks: list[str] = []
        for index, chunk in enumerate(body_chunks, start=1):
            chunks.append(f"{header}\n\nPart {index}/{len(body_chunks)}\n\n{chunk}")
        return chunks

    @staticmethod
    def _build_header(task: Task) -> str:
        if task.status == TaskStatus.COMPLETED:
            title = "## Supervisor Task Completed"
            detail = f"Task `{task.task_id}` finished successfully."
        else:
            title = "## Supervisor Task Failed"
            detail = f"Task `{task.task_id}` failed."

        if task.label:
            detail = f"{detail} Label: {task.label}."
        return f"{title}\n\n{detail}"

    @staticmethod
    def _build_body(task: Task) -> str:
        parts: list[str] = []
        if task.status == TaskStatus.FAILED and task.error:
            parts.append(f"**Error**: {task.error}")

        result = (task.result or "").strip()
        if result:
            if task.status == TaskStatus.FAILED:
                parts.append(f"### Partial Output\n\n{result}")
            else:
                parts.append(result)

        return "\n\n".join(parts)

    async def _build_progress_message(self, task: Task) -> str | None:
        latest = self._latest_progress(task)
        if latest is None:
            return None

        detail = latest.message.strip() or "正在执行..."
        worker = task.worker_id or "unknown"
        plan = await self._get_plan(task.plan_id)
        if plan is not None and task.step_index is not None:
            return (
                f"⏳ 步骤 {task.step_index + 1}/{len(plan.steps)}: "
                f"{detail} (Worker: {worker})"
            )

        return f"⏳ 任务进行中: {detail} (Worker: {worker})"

    async def _build_plan_progress_message(self, task: Task) -> str | None:
        plan = await self._get_plan(task.plan_id)
        if plan is None:
            return None

        completed = sum(1 for step in plan.steps if step.status == TaskStatus.COMPLETED)
        failed = sum(1 for step in plan.steps if step.status == TaskStatus.FAILED)
        total = len(plan.steps)
        message = f"📋 计划进度: {completed}/{total} 步骤完成"
        if failed:
            message = f"{message}，{failed} 步失败"
        return message

    async def _publish_progress_message(
        self,
        *,
        task: Task,
        content: str,
        scope: str,
        progress_key: str,
    ) -> None:
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=task.origin_channel,
                chat_id=task.origin_chat_id,
                content=content,
                metadata={
                    "_progress": True,
                    "_supervisor_progress": True,
                    "task_id": task.task_id,
                    "plan_id": task.plan_id,
                    "status": task.status.value,
                    "progress_scope": scope,
                    "progress_key": progress_key,
                    "progress_mode": "replace",
                },
            )
        )

    async def _get_plan(self, plan_id: str | None) -> Plan | None:
        if plan_id is None or self.plan_lookup is None:
            return None
        return await self.plan_lookup(plan_id)

    @staticmethod
    def _latest_progress(task: Task) -> TaskProgress | None:
        if not task.progress:
            return None
        return task.progress[-1]