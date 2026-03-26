"""X-Ray Agent runs API endpoints."""

from __future__ import annotations

from enum import Enum

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["agents"])


class _StatusFilter(str, Enum):
    active = "active"
    completed = "completed"


@router.get("/agents")
async def list_agents(
    request: Request,
    status: _StatusFilter | None = Query(None, description="Filter by status: active, completed"),
    limit: int = Query(50, ge=1, le=200),
):
    """List all agent runs."""
    store = request.app.state.event_store
    runs = await store.get_agent_runs(status=status.value if status else None, limit=limit)
    return {"runs": runs, "count": len(runs)}


@router.get("/agents/active")
async def list_active_agents(request: Request):
    """Get currently running agents."""
    collector = request.app.state.collector
    active = collector.get_active_runs()
    return {"agents": active, "count": len(active)}


@router.get("/agents/{run_id}")
async def get_agent_detail(request: Request, run_id: str):
    """Get single agent run details."""
    store = request.app.state.event_store
    collector = request.app.state.collector

    detail = await store.get_run_detail(run_id)
    if detail is None:
        # Try to get from active runs in collector
        active = collector.get_active_runs()
        if run_id in active:
            return active[run_id]
        return JSONResponse(status_code=404, content={"error": "Run not found"})
    return detail


@router.get("/agents/{run_id}/events")
async def get_agent_events(
    request: Request,
    run_id: str,
    event_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Get all events for a specific run."""
    store = request.app.state.event_store
    events = await store.query_events(
        run_id=run_id, event_type=event_type, limit=limit, offset=offset
    )
    return {"events": events, "count": len(events)}
