"""Delegate work to the supervisor worker pool."""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.worker.client import SupervisorClient


class DelegateToWorkerTool(Tool):
    """Submit a focused subtask to the remote worker pool."""

    def __init__(self, client: SupervisorClient):
        self._client = client
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._session_key = "cli:direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        self._session_key = f"{channel}:{chat_id}"

    @property
    def name(self) -> str:
        return "delegate_to_worker"

    @property
    def description(self) -> str:
        return (
            "Delegate a subtask to the supervisor worker pool. "
            "Use wait=true when you need the final result before continuing. "
            "Use wait=false to create the task and continue immediately."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "The task to delegate to a worker",
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the delegated task",
                },
                "context": {
                    "type": "string",
                    "description": "Optional extra context for the worker",
                },
                "wait": {
                    "type": "boolean",
                    "description": "Wait for the worker to finish and return the result",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Max seconds to wait when wait=true",
                },
                "poll_interval_s": {
                    "type": "number",
                    "description": "Polling interval while waiting for completion",
                },
                "max_iterations": {
                    "type": "integer",
                    "description": "Optional max tool iterations for the worker task",
                },
                "max_retries": {
                    "type": "integer",
                    "description": "Retry budget for the delegated task",
                },
            },
            "required": ["instruction"],
        }

    async def execute(
        self,
        instruction: str,
        label: str | None = None,
        context: str = "",
        wait: bool = True,
        timeout_s: float = 600.0,
        poll_interval_s: float = 1.0,
        max_iterations: int | None = None,
        max_retries: int = 0,
        **_: Any,
    ) -> str:
        task = await self._client.create_task(
            instruction=instruction,
            label=label or "Delegated worker task",
            context=context,
            max_iterations=max_iterations,
            max_retries=max_retries,
            timeout_s=timeout_s,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            session_key=self._session_key,
        )
        task_id = task.get("task_id", "unknown")

        if not wait:
            return f"Delegated task created with id {task_id}. Continue working and query it later if needed."

        final_task = await self._client.wait_for_task(
            task_id,
            poll_interval_s=poll_interval_s,
            timeout_s=timeout_s,
        )
        status = final_task.get("status")
        if status == "completed":
            return final_task.get("result") or f"Delegated task {task_id} completed with no result."

        error = final_task.get("error") or "unknown error"
        partial = (final_task.get("result") or "").strip()
        if partial:
            return f"Delegated task {task_id} failed: {error}\n\nPartial output:\n{partial}"
        return f"Delegated task {task_id} failed: {error}"