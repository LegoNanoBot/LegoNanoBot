"""SSE (Server-Sent Events) broadcast hub for X-Ray."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.xray.events import XRayEvent


class SSEHub:
    """Manages SSE subscriptions and broadcasts events to connected clients."""

    # Evict a subscriber after this many consecutive QueueFull events
    _MAX_CONSECUTIVE_DROPS = 50

    def __init__(self) -> None:
        """Initialize the SSE hub with an empty subscriber registry."""
        self._subscribers: dict[str, asyncio.Queue[XRayEvent]] = {}
        self._drop_counts: dict[str, int] = {}

    def subscribe(self) -> tuple[str, asyncio.Queue[XRayEvent]]:
        """Create a new subscription.

        Returns:
            A tuple of (client_id, queue) for receiving events.
        """
        client_id = str(uuid.uuid4())
        queue: asyncio.Queue[XRayEvent] = asyncio.Queue(maxsize=100)
        self._subscribers[client_id] = queue
        logger.debug(f"SSE client subscribed: {client_id}")
        return client_id, queue

    def unsubscribe(self, client_id: str) -> None:
        """Remove a subscription.

        Args:
            client_id: The client identifier to unsubscribe.
        """
        if client_id in self._subscribers:
            del self._subscribers[client_id]
            self._drop_counts.pop(client_id, None)
            logger.debug(f"SSE client unsubscribed: {client_id}")

    async def broadcast(self, event: XRayEvent) -> None:
        """Push an event to all subscribers.

        Non-blocking: uses put_nowait and skips full queues.
        Evicts subscribers whose queues stay full for too many consecutive events.

        Args:
            event: The event to broadcast.
        """
        stale: list[str] = []
        for client_id, queue in list(self._subscribers.items()):
            try:
                queue.put_nowait(event)
                self._drop_counts.pop(client_id, None)
            except asyncio.QueueFull:
                count = self._drop_counts.get(client_id, 0) + 1
                self._drop_counts[client_id] = count
                if count >= self._MAX_CONSECUTIVE_DROPS:
                    stale.append(client_id)
                    logger.warning(
                        f"SSE client {client_id} evicted after {count} consecutive dropped events"
                    )
                else:
                    logger.warning(f"SSE queue full for client {client_id}, dropping event")
        for client_id in stale:
            self.unsubscribe(client_id)

    @property
    def subscriber_count(self) -> int:
        """Return the number of active subscribers."""
        return len(self._subscribers)
