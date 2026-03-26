"""Event collector with memory buffer and coordination."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from nanobot.xray.events import EventType, XRayEvent

if TYPE_CHECKING:
    from nanobot.xray.sse import SSEHub


class EventStoreProtocol(Protocol):
    """Structural protocol for persistent event storage backends.

    Note: the ABC lives in ``store.base.BaseEventStore``; this lightweight
    Protocol lets the collector accept *any* object that exposes ``store()``.
    """

    async def store(self, event: XRayEvent) -> None:
        """Persist an event."""
        ...


class EventCollector:
    """Collects X-Ray events with memory buffering, optional persistence, and SSE broadcast."""

    def __init__(self, max_buffer: int = 1000) -> None:
        """Initialize the event collector.

        Args:
            max_buffer: Maximum number of events to keep in the ring buffer.
        """
        self._buffer: deque[XRayEvent] = deque(maxlen=max_buffer)
        self._event_store: EventStoreProtocol | None = None
        self._sse_hub: SSEHub | None = None
        self._active_runs: dict[str, dict[str, Any]] = {}

    def set_store(self, store: EventStoreProtocol) -> None:
        """Set the persistent storage backend.

        Args:
            store: A storage backend implementing BaseEventStore protocol.
        """
        self._event_store = store
        logger.debug("Event store configured")

    def set_sse_hub(self, hub: SSEHub) -> None:
        """Set the SSE broadcast hub.

        Args:
            hub: The SSEHub instance for broadcasting events.
        """
        self._sse_hub = hub
        logger.debug("SSE hub configured")

    async def collect(self, event: XRayEvent) -> None:
        """Collect an event: buffer, persist, and broadcast.

        Args:
            event: The event to collect.
        """
        # Add to ring buffer
        self._buffer.append(event)

        # Track active runs
        self._track_run(event)

        # Persist if store is configured
        if self._event_store is not None:
            try:
                await self._event_store.store(event)
            except Exception as e:
                logger.error(f"Failed to persist event: {e}")

        # Broadcast if SSE hub is configured
        if self._sse_hub is not None:
            try:
                await self._sse_hub.broadcast(event)
            except Exception as e:
                logger.error(f"Failed to broadcast event: {e}")

    def _track_run(self, event: XRayEvent) -> None:
        """Track active agent runs based on AGENT_START/END events.

        Args:
            event: The event to process for run tracking.
        """
        if event.event_type == EventType.AGENT_START:
            self._active_runs[event.run_id] = {
                "start_time": event.timestamp,
                "channel": event.data.get("channel"),
                "status": "running",
                **{k: v for k, v in event.data.items() if k != "channel"},
            }
        elif event.event_type == EventType.AGENT_END:
            if event.run_id in self._active_runs:
                del self._active_runs[event.run_id]
        elif event.event_type == EventType.ERROR:
            if event.run_id in self._active_runs:
                self._active_runs[event.run_id]["status"] = "error"

    def get_recent(self, limit: int = 100) -> list[XRayEvent]:
        """Get the most recent events from the buffer.

        Args:
            limit: Maximum number of events to return.

        Returns:
            List of recent events (newest last).
        """
        events = list(self._buffer)
        return events[-limit:] if len(events) > limit else events

    def get_active_runs(self) -> dict[str, dict[str, Any]]:
        """Get currently tracked agent runs.

        Returns:
            Dictionary mapping run_id to run metadata.
        """
        return dict(self._active_runs)
