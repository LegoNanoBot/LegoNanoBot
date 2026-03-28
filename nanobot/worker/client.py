"""Worker HTTP client — communicates with the Supervisor API."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger


class SupervisorClient:
    """HTTP client for worker → supervisor communication.

    All methods are async and use httpx for HTTP/1.1 persistent connections.
    Pass a pre-configured *http_client* (e.g. using ``ASGITransport``) to
    bypass real network traffic in tests.
    """

    def __init__(
        self,
        base_url: str,
        worker_id: str,
        timeout: float = 30.0,
        max_retries: int = 5,
        retry_base_delay_s: float = 0.5,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id
        self.max_retries = max_retries
        self.retry_base_delay_s = retry_base_delay_s
        self._client = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
        )
        self._sleep = asyncio.sleep

    async def close(self) -> None:
        await self._client.aclose()

    async def is_available(self) -> bool:
        try:
            response = await self._client.request("GET", "/api/v1/supervisor/tasks")
            return response.status_code < 500
        except Exception:
            return False

    def _should_retry(self, exc: Exception) -> bool:
        if isinstance(exc, httpx.RequestError):
            return True
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
            return exc.response.status_code in {408, 429, 500, 502, 503, 504}
        return False

    async def _request_with_retry(self, method: str, path: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self._client.request(method, path, **kwargs)
                response.raise_for_status()
                return response
            except Exception as exc:
                last_exc = exc
                if attempt >= self.max_retries or not self._should_retry(exc):
                    raise
                delay = self.retry_base_delay_s * (2 ** (attempt - 1))
                logger.warning(
                    "Supervisor request failed (attempt {}/{}): {}. Retrying in {}s",
                    attempt,
                    self.max_retries,
                    exc,
                    delay,
                )
                await self._sleep(delay)
        assert last_exc is not None
        raise last_exc

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register(self, name: str, capabilities: list[str] | None = None) -> dict[str, Any]:
        resp = await self._request_with_retry(
            "POST",
            "/api/v1/supervisor/workers/register",
            json={
                "worker_id": self.worker_id,
                "name": name,
                "capabilities": capabilities or [],
            },
        )
        return resp.json()

    async def unregister(self) -> None:
        try:
            resp = await self._request_with_retry(
                "DELETE",
                f"/api/v1/supervisor/workers/{self.worker_id}",
            )
        except Exception:
            logger.debug("Failed to cleanly unregister (supervisor may be down)")

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def heartbeat(
        self, current_task_id: str | None = None, status: str = "online"
    ) -> dict[str, Any]:
        resp = await self._request_with_retry(
            "POST",
            f"/api/v1/supervisor/workers/{self.worker_id}/heartbeat",
            json={"current_task_id": current_task_id, "status": status},
        )
        return resp.json()

    # ------------------------------------------------------------------
    # Task operations
    # ------------------------------------------------------------------

    async def claim_task(self, capabilities: list[str] | None = None) -> dict[str, Any] | None:
        """Try to claim a pending task. Returns task dict or None."""
        resp = await self._request_with_retry(
            "POST",
            "/api/v1/supervisor/tasks/claim",
            json={"worker_id": self.worker_id, "capabilities": capabilities or []},
        )
        data = resp.json()
        return data.get("task")

    async def create_task(
        self,
        *,
        instruction: str,
        label: str = "",
        context: str = "",
        plan_id: str | None = None,
        step_index: int | None = None,
        max_iterations: int | None = None,
        max_retries: int = 0,
        timeout_s: float | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> dict[str, Any]:
        resp = await self._request_with_retry(
            "POST",
            "/api/v1/supervisor/tasks",
            json={
                "instruction": instruction,
                "label": label,
                "context": context,
                "plan_id": plan_id,
                "step_index": step_index,
                "max_iterations": max_iterations,
                "max_retries": max_retries,
                "timeout_s": timeout_s,
                "origin_channel": origin_channel,
                "origin_chat_id": origin_chat_id,
                "session_key": session_key,
            },
        )
        return resp.json().get("task", {})

    async def get_task(self, task_id: str) -> dict[str, Any]:
        resp = await self._request_with_retry(
            "GET",
            f"/api/v1/supervisor/tasks/{task_id}",
        )
        return resp.json().get("task", {})

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        resp = await self._request_with_retry(
            "POST",
            f"/api/v1/supervisor/tasks/{task_id}/cancel",
        )
        return resp.json().get("task", {})

    async def wait_for_task(
        self,
        task_id: str,
        *,
        poll_interval_s: float = 1.0,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        start = loop.time()
        terminal_states = {"completed", "failed", "cancelled"}

        while True:
            task = await self.get_task(task_id)
            if task.get("status") in terminal_states:
                return task
            if timeout_s is not None and loop.time() - start >= timeout_s:
                raise TimeoutError(f"Timed out waiting for supervisor task {task_id}")
            await self._sleep(poll_interval_s)

    async def report_progress(
        self,
        task_id: str,
        iteration: int = 0,
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        await self._request_with_retry(
            "POST",
            f"/api/v1/supervisor/tasks/{task_id}/progress",
            json={
                "worker_id": self.worker_id,
                "iteration": iteration,
                "message": message,
                "data": data or {},
            },
        )

    async def report_result(
        self,
        task_id: str,
        status: str = "completed",
        result: str = "",
        error: str | None = None,
    ) -> None:
        await self._request_with_retry(
            "POST",
            f"/api/v1/supervisor/tasks/{task_id}/result",
            json={
                "worker_id": self.worker_id,
                "status": status,
                "result": result,
                "error": error,
            },
        )
