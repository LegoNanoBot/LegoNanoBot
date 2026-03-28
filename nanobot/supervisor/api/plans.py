"""Supervisor API — plan endpoints (create, approve, cancel, list, detail)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["supervisor-plans"])


# ---------------------------------------------------------------------------
# Pydantic request / response schemas
# ---------------------------------------------------------------------------


class PlanStepBody(BaseModel):
    index: int
    instruction: str
    label: str = ""
    depends_on: list[int] = []
    max_retries: int = 0


class CreatePlanBody(BaseModel):
    title: str
    goal: str
    steps: list[PlanStepBody]
    origin_channel: str = "cli"
    origin_chat_id: str = "direct"
    session_key: str | None = None


def _plan_to_dict(p: Any) -> dict[str, Any]:
    return {
        "plan_id": p.plan_id,
        "title": p.title,
        "goal": p.goal,
        "status": p.status.value if hasattr(p.status, "value") else p.status,
        "steps": [
            {
                "index": s.index,
                "instruction": s.instruction,
                "label": s.label,
                "depends_on": s.depends_on,
                "max_retries": s.max_retries,
                "task_id": s.task_id,
                "status": s.status.value if hasattr(s.status, "value") else s.status,
                "result_summary": s.result_summary,
            }
            for s in p.steps
        ],
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "origin_channel": p.origin_channel,
        "origin_chat_id": p.origin_chat_id,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/supervisor/plans")
async def create_plan(body: CreatePlanBody, request: Request) -> dict[str, Any]:
    from nanobot.supervisor.models import Plan, PlanStep

    registry = request.app.state.worker_registry
    steps = [
        PlanStep(
            index=s.index,
            instruction=s.instruction,
            label=s.label,
            depends_on=list(s.depends_on),
            max_retries=s.max_retries,
        )
        for s in body.steps
    ]
    plan = Plan(
        title=body.title,
        goal=body.goal,
        steps=steps,
        origin_channel=body.origin_channel,
        origin_chat_id=body.origin_chat_id,
        session_key=body.session_key,
    )
    plan = await registry.create_plan(plan)
    return {"ok": True, "plan": _plan_to_dict(plan)}


@router.post("/supervisor/plans/{plan_id}/approve")
async def approve_plan(plan_id: str, request: Request) -> dict[str, Any]:
    registry = request.app.state.worker_registry
    plan = await registry.approve_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"ok": True, "plan": _plan_to_dict(plan)}


@router.post("/supervisor/plans/{plan_id}/cancel")
async def cancel_plan(plan_id: str, request: Request) -> dict[str, Any]:
    registry = request.app.state.worker_registry
    plan = await registry.cancel_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"ok": True, "plan": _plan_to_dict(plan)}


@router.get("/supervisor/plans")
async def list_plans(
    request: Request,
    status: str | None = None,
) -> dict[str, Any]:
    from nanobot.supervisor.models import PlanStatus

    registry = request.app.state.worker_registry
    plan_status = None
    if status:
        try:
            plan_status = PlanStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    plans = await registry.list_plans(status=plan_status)
    return {"plans": [_plan_to_dict(p) for p in plans]}


@router.get("/supervisor/plans/{plan_id}")
async def get_plan(plan_id: str, request: Request) -> dict[str, Any]:
    registry = request.app.state.worker_registry
    plan = await registry.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"plan": _plan_to_dict(plan)}
