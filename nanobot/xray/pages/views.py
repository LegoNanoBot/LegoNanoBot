"""X-Ray SSR page views using Jinja2 templates."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Redirect root to dashboard."""
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard page with overview of agent activity and events."""
    templates = request.app.state.templates
    collector = request.app.state.collector
    store = request.app.state.event_store
    sse_hub = request.app.state.sse_hub

    active_runs = collector.get_active_runs()
    recent_events = collector.get_recent(limit=20)
    token_usage = await store.get_token_usage()
    recent_runs = await store.get_agent_runs(limit=10)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "active_runs": active_runs,
            "recent_events": recent_events,
            "token_usage": token_usage,
            "recent_runs": recent_runs,
            "sse_subscribers": sse_hub.subscriber_count,
            "buffer_size": len(collector._buffer),
            "page": "dashboard",
        },
    )


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    """Agent runs list page."""
    templates = request.app.state.templates
    store = request.app.state.event_store
    collector = request.app.state.collector

    status_filter = request.query_params.get("status")
    runs = await store.get_agent_runs(status=status_filter, limit=100)
    active_runs = collector.get_active_runs()

    return templates.TemplateResponse(
        "agents.html",
        {
            "request": request,
            "runs": runs,
            "active_runs": active_runs,
            "status_filter": status_filter,
            "page": "agents",
        },
    )


@router.get("/agents/{run_id}", response_class=HTMLResponse)
async def agent_detail_page(request: Request, run_id: str):
    """Agent run detail with timeline view."""
    templates = request.app.state.templates
    store = request.app.state.event_store
    collector = request.app.state.collector

    # Get run details
    detail = await store.get_run_detail(run_id)
    if detail is None:
        active = collector.get_active_runs()
        if run_id in active:
            detail = {"run_id": run_id, **active[run_id]}

    # Get all events for this run
    events = await store.query_events(run_id=run_id, limit=500)

    return templates.TemplateResponse(
        "agent_detail.html",
        {
            "request": request,
            "run_id": run_id,
            "detail": detail,
            "events": events,
            "page": "agents",
        },
    )


@router.get("/agents/{run_id}/messages", response_class=HTMLResponse)
async def messages_page(request: Request, run_id: str):
    """LLM conversation viewer for a specific agent run."""
    templates = request.app.state.templates
    store = request.app.state.event_store

    # Get LLM request/response events
    llm_events = []
    for event_type in ["llm_request", "llm_response", "tool_call_start", "tool_call_end"]:
        events = await store.query_events(run_id=run_id, event_type=event_type, limit=200)
        llm_events.extend(events)

    # Sort by timestamp
    llm_events.sort(key=lambda e: e.get("timestamp", 0))

    # Get run detail
    detail = await store.get_run_detail(run_id)

    return templates.TemplateResponse(
        "messages.html",
        {
            "request": request,
            "run_id": run_id,
            "detail": detail,
            "llm_events": llm_events,
            "page": "agents",
        },
    )


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    """Configuration viewer page."""
    templates = request.app.state.templates

    return templates.TemplateResponse(
        "config.html",
        {
            "request": request,
            "page": "config",
        },
    )


@router.get("/tokens", response_class=HTMLResponse)
async def tokens_page(request: Request):
    """Token usage statistics page."""
    templates = request.app.state.templates
    store = request.app.state.event_store

    token_usage = await store.get_token_usage()
    recent_runs = await store.get_agent_runs(limit=20)

    # Calculate per-run token stats
    run_tokens = []
    for run in recent_runs:
        run_usage = await store.get_token_usage(run_id=run.get("run_id"))
        run_tokens.append({
            "run_id": run.get("run_id"),
            "channel": run.get("channel"),
            "started": run.get("start_time"),
            "status": run.get("status"),
            **run_usage,
        })

    return templates.TemplateResponse(
        "tokens.html",
        {
            "request": request,
            "token_usage": token_usage,
            "run_tokens": run_tokens,
            "page": "tokens",
        },
    )
