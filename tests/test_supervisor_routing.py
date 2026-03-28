from __future__ import annotations

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.supervisor.models import Plan, PlanStep
from nanobot.supervisor.routing import (
    ComplexityRoutingStrategy,
    CompositeRoutingStrategy,
    KeywordRoutingStrategy,
)


class _FakeClient:
    def __init__(self) -> None:
        self.created_tasks: list[dict] = []
        self.created_plans: list[dict] = []
        self.approved_plans: list[str] = []

    async def create_task(self, task):
        self.created_tasks.append({"instruction": task.instruction, "chat_id": task.origin_chat_id})
        return {"task": {"task_id": f"task-{len(self.created_tasks)}"}}

    async def create_plan(self, plan):
        self.created_plans.append({"title": plan.title, "steps": len(plan.steps)})
        return {"plan": {"plan_id": "plan-1"}}

    async def approve_plan(self, plan_id: str):
        self.approved_plans.append(plan_id)
        return {"ok": True}


class _FakeProvider(LLMProvider):
    def __init__(self, content: str) -> None:
        super().__init__()
        self.content = content

    async def chat(self, messages, tools=None, model=None, **kwargs) -> LLMResponse:
        return LLMResponse(content=self.content)

    def get_default_model(self) -> str:
        return "mock-model"


@pytest.mark.asyncio
async def test_keyword_routing_delegate_command_creates_task() -> None:
    client = _FakeClient()
    provider = _FakeProvider('{"delegate": false}')
    strategy = KeywordRoutingStrategy(client=client, provider=provider, model="mock-model")

    msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/delegate do this")
    out = await strategy.route(msg)

    assert out is not None
    assert "task `task-1`" in out.content
    assert client.created_tasks[0]["instruction"] == "do this"


@pytest.mark.asyncio
async def test_keyword_routing_plan_command_creates_and_approves_plan(monkeypatch) -> None:
    client = _FakeClient()
    provider = _FakeProvider('{"delegate": false}')
    strategy = KeywordRoutingStrategy(client=client, provider=provider, model="mock-model")

    async def _fake_generate_plan(**kwargs):
        return Plan(
            title="Demo Plan",
            goal="demo",
            steps=[PlanStep(index=0, instruction="step 0", depends_on=[])],
            origin_channel=kwargs["origin_channel"],
            origin_chat_id=kwargs["origin_chat_id"],
            session_key=kwargs["session_key"],
        )

    monkeypatch.setattr("nanobot.supervisor.routing.generate_plan", _fake_generate_plan)

    msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/plan refactor auth")
    out = await strategy.route(msg)

    assert out is not None
    assert "plan `plan-1`" in out.content
    assert client.created_plans[0]["steps"] == 1
    assert client.approved_plans == ["plan-1"]


@pytest.mark.asyncio
async def test_complexity_routing_delegates_when_llm_returns_true() -> None:
    client = _FakeClient()
    provider = _FakeProvider('{"delegate": true}')
    strategy = ComplexityRoutingStrategy(client=client, provider=provider, model="mock-model")

    msg = InboundMessage(channel="slack", sender_id="u1", chat_id="c1", content="Please migrate 30 modules")
    out = await strategy.route(msg)

    assert out is not None
    assert "task `task-1`" in out.content
    assert client.created_tasks[0]["instruction"] == "Please migrate 30 modules"


@pytest.mark.asyncio
async def test_composite_routing_uses_first_matching_strategy() -> None:
    client = _FakeClient()
    keyword = KeywordRoutingStrategy(client=client, provider=_FakeProvider('{"delegate": false}'), model="mock-model")
    complexity = ComplexityRoutingStrategy(client=client, provider=_FakeProvider('{"delegate": true}'), model="mock-model")
    strategy = CompositeRoutingStrategy(strategies=[keyword, complexity])

    msg = InboundMessage(channel="slack", sender_id="u1", chat_id="c1", content="/delegate quick task")
    out = await strategy.route(msg)

    assert out is not None
    assert client.created_tasks[0]["instruction"] == "quick task"
