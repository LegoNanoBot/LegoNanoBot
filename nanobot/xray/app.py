"""X-Ray FastAPI application factory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from nanobot.xray.api import agents, config, events, tokens
from nanobot.xray.pages import views as pages_views

if TYPE_CHECKING:
    from nanobot.xray.collector import EventCollector
    from nanobot.xray.sse import SSEHub
    from nanobot.xray.store.base import BaseEventStore


def create_xray_app(
    event_store: "BaseEventStore",
    sse_hub: "SSEHub",
    collector: "EventCollector",
    config_refs: dict[str, Any],
) -> FastAPI:
    """Create and configure X-Ray FastAPI application.

    Args:
        event_store: BaseEventStore instance for persistent storage.
        sse_hub: SSEHub instance for real-time event broadcasting.
        collector: EventCollector instance for event buffering.
        config_refs: Dictionary with references to other components:
            - memory_store: MemoryStore instance
            - skills_loader: SkillsLoader instance
            - tool_registry: ToolRegistry instance
            - workspace: str (workspace path)
            - bot_config: Config instance

    Returns:
        Configured FastAPI application.
    """
    class _UnicodeJSONResponse(JSONResponse):
        """JSONResponse that preserves non-ASCII characters."""

        def render(self, content: Any) -> bytes:
            return json.dumps(
                content, ensure_ascii=False, default=str,
            ).encode("utf-8")

    app = FastAPI(
        title="NanoBot X-Ray",
        description="X-Ray debugging and monitoring interface for NanoBot",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        default_response_class=_UnicodeJSONResponse,
    )

    # Store shared references on app.state
    app.state.event_store = event_store
    app.state.sse_hub = sse_hub
    app.state.collector = collector
    app.state.config_refs = config_refs

    # Mount static files directory
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Configure Jinja2 templates
    templates_dir = Path(__file__).parent / "templates"
    templates_dir.mkdir(exist_ok=True)
    templates = Jinja2Templates(directory=str(templates_dir))

    # Fix Unicode display: ensure Chinese/non-ASCII chars render properly
    templates.env.policies["json.dumps_kwargs"] = {"ensure_ascii": False}

    def _pretty_json(value: Any) -> Markup:
        """Format value as indented JSON with proper Unicode."""
        raw = json.dumps(value, indent=2, ensure_ascii=False, default=str)
        return Markup(raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    templates.env.filters["pretty_json"] = _pretty_json

    app.state.templates = templates

    # Register API routers
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(config.router, prefix="/api/v1")
    app.include_router(tokens.router, prefix="/api/v1")

    # Register SSR page routes (no prefix - serves at root)
    app.include_router(pages_views.router)

    return app
