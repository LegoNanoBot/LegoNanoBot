"""Supervisor API — task endpoints (create, claim, progress, result, cancel)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["supervisor-tasks"])


# ---------------------------------------------------------------------------
# Pydantic request / response schemas
# ---------------------------------------------------------------------------


class CreateTaskBody(BaseModel):
    instruction: str
    label: str = ""
    context: str = ""
    plan_id: str | None = None
    step_index: int | None = None
    max_iterations: int | None = None
    max_retries: int = 0
    timeout_s: float | None = None
    origin_channel: str = "cli"
    origin_chat_id: str = "direct"
    session_key: str | None = None


class ClaimBody(BaseModel):
    worker_id: str
    capabilities: list[str] = []


class ProgressBody(BaseModel):
    worker_id: str
    iteration: int = 0
    message: str = ""
    data: dict[str, Any] = {}


class ResultBody(BaseModel):
    worker_id: str
    status: str = "completed"
    result: str = ""
    error: str | None = None


def _task_to_dict(t: Any) -> dict[str, Any]:
    return {
        "task_id": t.task_id,
        "plan_id": t.plan_id,
        "step_index": t.step_index,
        "instruction": t.instruction,
        "label": t.label,
        "context": t.context,
        "status": t.status.value if hasattr(t.status, "value") else t.status,
        "worker_id": t.worker_id,
        "assigned_at": t.assigned_at,
        "retry_count": t.retry_count,
        "max_retries": t.max_retries,
        "last_failed_worker_id": t.last_failed_worker_id,
        "progress": [
            {
                "timestamp": p.timestamp,
                "iteration": p.iteration,
                "message": p.message,
            }
            for p in t.progress
        ],
        "result": t.result,
        "error": t.error,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "max_iterations": t.max_iterations,
        "timeout_s": t.timeout_s,
        "origin_channel": t.origin_channel,
        "origin_chat_id": t.origin_chat_id,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/supervisor/tasks")
async def create_task(body: CreateTaskBody, request: Request) -> dict[str, Any]:
    from nanobot.supervisor.models import Task

    registry = request.app.state.worker_registry
    task = Task(
        instruction=body.instruction,
        label=body.label,
        context=body.context,
        plan_id=body.plan_id,
        step_index=body.step_index,
        max_iterations=body.max_iterations if body.max_iterations is not None else registry.task_default_max_iterations,
        max_retries=body.max_retries,
        timeout_s=body.timeout_s if body.timeout_s is not None else registry.task_default_timeout_s,
        origin_channel=body.origin_channel,
        origin_chat_id=body.origin_chat_id,
        session_key=body.session_key,
    )
    task = await registry.create_task(task)
    return {"ok": True, "task": _task_to_dict(task)}


@router.post("/supervisor/tasks/claim")
async def claim_task(body: ClaimBody, request: Request) -> dict[str, Any]:
    from nanobot.supervisor.models import TaskClaimRequest

    registry = request.app.state.worker_registry
    req = TaskClaimRequest(worker_id=body.worker_id, capabilities=body.capabilities)
    task = await registry.claim_task(req)
    if task is None:
        return {"ok": True, "task": None}
    return {"ok": True, "task": _task_to_dict(task)}


@router.post("/supervisor/tasks/{task_id}/progress")
async def report_progress(task_id: str, body: ProgressBody, request: Request) -> dict[str, Any]:
    from nanobot.supervisor.models import TaskProgressReport

    registry = request.app.state.worker_registry
    rpt = TaskProgressReport(
        task_id=task_id,
        worker_id=body.worker_id,
        iteration=body.iteration,
        message=body.message,
        data=body.data,
    )
    task = await registry.report_progress(rpt)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found or not owned by this worker")
    return {"ok": True, "task": _task_to_dict(task)}


@router.post("/supervisor/tasks/{task_id}/result")
async def report_result(task_id: str, body: ResultBody, request: Request) -> dict[str, Any]:
    from nanobot.supervisor.models import TaskResultReport, TaskStatus

    registry = request.app.state.worker_registry
    try:
        status = TaskStatus(body.status)
    except ValueError:
        status = TaskStatus.COMPLETED
    rpt = TaskResultReport(
        task_id=task_id,
        worker_id=body.worker_id,
        status=status,
        result=body.result,
        error=body.error,
    )
    task = await registry.report_result(rpt)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found or not owned by this worker")

    # If task is part of a plan, schedule next ready steps
    if task.plan_id:
        await registry._schedule_ready_steps(task.plan_id)

    return {"ok": True, "task": _task_to_dict(task)}


@router.post("/supervisor/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> dict[str, Any]:
    registry = request.app.state.worker_registry
    task = await registry.cancel_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True, "task": _task_to_dict(task)}


@router.get("/supervisor/tasks")
async def list_tasks(
    request: Request,
    status: str | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    from nanobot.supervisor.models import TaskStatus

    registry = request.app.state.worker_registry
    task_status = None
    if status:
        try:
            task_status = TaskStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    tasks = await registry.list_tasks(status=task_status, plan_id=plan_id)
    return {"tasks": [_task_to_dict(t) for t in tasks]}


@router.get("/supervisor/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    registry = request.app.state.worker_registry
    task = await registry.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": _task_to_dict(task)}
