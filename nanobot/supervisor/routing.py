"""Supervisor routing strategies for chat message delegation."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.providers.base import LLMProvider
from nanobot.supervisor.models import Task
from nanobot.supervisor.planner import generate_plan


@dataclass(slots=True)
class SupervisorTaskClient:
    """Thin async client for supervisor plan/task creation."""

    base_url: str
    timeout_s: float = 10.0
    http_client: httpx.AsyncClient | None = None
    _base_url: str = field(init=False)

    def __post_init__(self) -> None:
        self._base_url = self.base_url.rstrip("/")

    async def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.http_client is not None:
            resp = await self.http_client.request(method, path, json=payload)
            resp.raise_for_status()
            return resp.json()

        async with httpx.AsyncClient(base_url=self._base_url, timeout=self.timeout_s) as client:
            resp = await client.request(method, path, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def create_task(self, task: Task) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/supervisor/tasks",
            {
                "instruction": task.instruction,
                "label": task.label,
                "context": task.context,
                "plan_id": task.plan_id,
                "step_index": task.step_index,
                "max_iterations": task.max_iterations,
                "max_retries": task.max_retries,
                "timeout_s": task.timeout_s,
                "origin_channel": task.origin_channel,
                "origin_chat_id": task.origin_chat_id,
                "session_key": task.session_key,
            },
        )

    async def create_plan(self, plan: Any) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/supervisor/plans",
            {
                "title": plan.title,
                "goal": plan.goal,
                "steps": [
                    {
                        "index": step.index,
                        "instruction": step.instruction,
                        "label": step.label,
                        "depends_on": step.depends_on,
                        "max_retries": step.max_retries,
                    }
                    for step in plan.steps
                ],
                "origin_channel": plan.origin_channel,
                "origin_chat_id": plan.origin_chat_id,
                "session_key": plan.session_key,
            },
        )

    async def approve_plan(self, plan_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/v1/supervisor/plans/{plan_id}/approve", {})


class RoutingStrategy(ABC):
    """Interface for deciding and handling supervisor delegation."""

    @abstractmethod
    async def should_delegate(self, message: InboundMessage) -> bool:
        """Return True if this strategy wants to delegate the message."""

    @abstractmethod
    async def create_task(self, message: InboundMessage) -> Task | None:
        """Create a Task from the inbound message if applicable."""

    @abstractmethod
    async def handle(self, message: InboundMessage) -> OutboundMessage | None:
        """Handle delegation and return immediate user-facing ack message."""

    async def route(self, message: InboundMessage) -> OutboundMessage | None:
        if not await self.should_delegate(message):
            return None
        return await self.handle(message)


@dataclass(slots=True)
class KeywordRoutingStrategy(RoutingStrategy):
    """Delegate messages based on command keywords like /delegate or /plan."""

    client: SupervisorTaskClient
    provider: LLMProvider
    model: str
    delegate_prefix: str = "/delegate"
    plan_prefix: str = "/plan"

    async def should_delegate(self, message: InboundMessage) -> bool:
        content = message.content.strip().lower()
        return content.startswith(self.delegate_prefix) or content.startswith(self.plan_prefix)

    async def create_task(self, message: InboundMessage) -> Task | None:
        raw = message.content.strip()
        if not raw.lower().startswith(self.delegate_prefix):
            return None
        instruction = raw[len(self.delegate_prefix) :].strip()
        if not instruction:
            return None
        return Task(
            instruction=instruction,
            label="Delegated task",
            origin_channel=message.channel,
            origin_chat_id=message.chat_id,
            session_key=message.session_key,
            max_retries=1,
        )

    async def handle(self, message: InboundMessage) -> OutboundMessage | None:
        content = message.content.strip()

        if content.lower().startswith(self.delegate_prefix):
            task = await self.create_task(message)
            if task is None:
                return OutboundMessage(
                    channel=message.channel,
                    chat_id=message.chat_id,
                    content="Usage: /delegate <instruction>",
                )
            data = await self.client.create_task(task)
            task_id = data.get("task", {}).get("task_id", "unknown")
            return OutboundMessage(
                channel=message.channel,
                chat_id=message.chat_id,
                content=f"Delegated to supervisor as task `{task_id}`. I will report back when it finishes.",
                metadata={"_delegated": True, "task_id": task_id},
            )

        request = content[len(self.plan_prefix) :].strip()
        if not request:
            return OutboundMessage(
                channel=message.channel,
                chat_id=message.chat_id,
                content="Usage: /plan <goal>",
            )

        plan = await generate_plan(
            provider=self.provider,
            model=self.model,
            user_request=request,
            origin_channel=message.channel,
            origin_chat_id=message.chat_id,
            session_key=message.session_key,
        )
        if plan is None:
            fallback = Task(
                instruction=request,
                label="Planner fallback task",
                origin_channel=message.channel,
                origin_chat_id=message.chat_id,
                session_key=message.session_key,
                max_retries=1,
            )
            data = await self.client.create_task(fallback)
            task_id = data.get("task", {}).get("task_id", "unknown")
            return OutboundMessage(
                channel=message.channel,
                chat_id=message.chat_id,
                content=(
                    "Planner judged this as a single-step task. "
                    f"Delegated as task `{task_id}`."
                ),
                metadata={"_delegated": True, "task_id": task_id},
            )

        created = await self.client.create_plan(plan)
        plan_id = created.get("plan", {}).get("plan_id") or plan.plan_id
        await self.client.approve_plan(plan_id)
        return OutboundMessage(
            channel=message.channel,
            chat_id=message.chat_id,
            content=(
                f"Created and approved plan `{plan_id}` with {len(plan.steps)} steps. "
                "Execution has started."
            ),
            metadata={"_delegated": True, "plan_id": plan_id},
        )


@dataclass(slots=True)
class ComplexityRoutingStrategy(RoutingStrategy):
    """Delegate high-complexity requests decided by LLM classification."""

    client: SupervisorTaskClient
    provider: LLMProvider
    model: str

    async def should_delegate(self, message: InboundMessage) -> bool:
        prompt = (
            "You are a request router. Reply with JSON only: "
            '{"delegate": true|false}. Delegate when request likely needs multi-step work, '
            "heavy code changes, or long-running tasks."
        )
        try:
            resp = await self.provider.chat_with_retry(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": message.content},
                ],
                model=self.model,
            )
        except Exception:
            logger.warning("Complexity routing failed to classify; falling back to local execution")
            return False

        try:
            data = json.loads((resp.content or "").strip())
            return bool(data.get("delegate", False))
        except Exception:
            logger.warning("Complexity routing got non-JSON classification; falling back to local execution")
            return False

    async def create_task(self, message: InboundMessage) -> Task | None:
        return Task(
            instruction=message.content,
            label="Complexity-routed task",
            origin_channel=message.channel,
            origin_chat_id=message.chat_id,
            session_key=message.session_key,
            max_retries=1,
        )

    async def handle(self, message: InboundMessage) -> OutboundMessage | None:
        task = await self.create_task(message)
        if task is None:
            return None
        data = await self.client.create_task(task)
        task_id = data.get("task", {}).get("task_id", "unknown")
        return OutboundMessage(
            channel=message.channel,
            chat_id=message.chat_id,
            content=f"Routed to supervisor for execution as task `{task_id}`.",
            metadata={"_delegated": True, "task_id": task_id},
        )


@dataclass(slots=True)
class CompositeRoutingStrategy(RoutingStrategy):
    """Try multiple routing strategies in order."""

    strategies: list[RoutingStrategy]

    async def should_delegate(self, message: InboundMessage) -> bool:
        for strategy in self.strategies:
            if await strategy.should_delegate(message):
                return True
        return False

    async def create_task(self, message: InboundMessage) -> Task | None:
        for strategy in self.strategies:
            if await strategy.should_delegate(message):
                return await strategy.create_task(message)
        return None

    async def handle(self, message: InboundMessage) -> OutboundMessage | None:
        for strategy in self.strategies:
            if await strategy.should_delegate(message):
                return await strategy.handle(message)
        return None
