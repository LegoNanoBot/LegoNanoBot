"""Event sink abstractions for supervisor domain events.

Keeps supervisor core decoupled from concrete telemetry implementations.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

if TYPE_CHECKING:
    from nanobot.xray.collector import EventCollector


class SupervisorEventType:
    WORKER_REGISTERED = "worker_registered"
    WORKER_HEARTBEAT = "worker_heartbeat"
    WORKER_UNHEALTHY = "worker_unhealthy"
    WORKER_EVICTED = "worker_evicted"
    TASK_CREATED = "task_created"
    TASK_ASSIGNED = "task_assigned"
    TASK_PROGRESS = "task_progress"
    TASK_RETRIED = "task_retried"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    PLAN_CREATED = "plan_created"
    PLAN_APPROVED = "plan_approved"
    PLAN_COMPLETED = "plan_completed"
    PLAN_FAILED = "plan_failed"


class EventSink(Protocol):
    async def emit(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        """Emit one domain event."""


class XRayCollectorEventSink:
    """Adapter from supervisor events to X-Ray collector events."""

    def __init__(self, collector: "EventCollector", emit_timeout_s: float = 0.05) -> None:
        self._collector = collector
        self._emit_timeout_s = emit_timeout_s

    async def emit(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        from nanobot.xray.events import create_event

        event = create_event(run_id, event_type, data)
        try:
            # Bound telemetry latency so state transitions are not blocked indefinitely.
            await asyncio.wait_for(self._collector.collect(event), timeout=self._emit_timeout_s)
        except Exception as e:
            logger.debug("event sink emit skipped: {}", e)
