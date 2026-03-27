"""Watchdog service — monitors worker health and evicts dead workers.

Runs as a background asyncio task in the supervisor process. Periodically
scans the WorkerRegistry for workers whose heartbeat has timed out, marks
them unhealthy, and re-queues their assigned tasks.
"""

from __future__ import annotations

import asyncio
from loguru import logger

from nanobot.supervisor.registry import WorkerRegistry


class WatchdogService:
    """Periodic health check for registered workers."""

    def __init__(
        self,
        registry: WorkerRegistry,
        check_interval_s: float = 30.0,
    ) -> None:
        self.registry = registry
        self.check_interval_s = check_interval_s
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("Watchdog started (interval={}s)", self.check_interval_s)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
            logger.info("Watchdog stopped")

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.check_interval_s)
                await self._check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Watchdog error: {}", e)

    async def _check(self) -> None:
        """Scan for unhealthy workers and evict them."""
        unhealthy = await self.registry.scan_unhealthy_workers()
        for worker in unhealthy:
            logger.warning(
                "Worker {} ({}) heartbeat timeout — evicting",
                worker.worker_id,
                worker.name,
            )
            reassigned = await self.registry.evict_worker(
                worker.worker_id,
                reason="heartbeat_timeout",
            )
            if reassigned:
                logger.info(
                    "Re-queued {} tasks from evicted worker {}",
                    len(reassigned),
                    worker.worker_id,
                )
