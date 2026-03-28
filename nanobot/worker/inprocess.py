"""In-process worker wrapper for supervisor development and tests."""

from __future__ import annotations

import httpx

from nanobot.worker.client import SupervisorClient
from nanobot.worker.runner import WorkerRunner


class InProcessWorker:
    """Run a worker loop against a FastAPI app without real HTTP sockets."""

    def __init__(
        self,
        *,
        app,
        supervisor_url: str,
        workspace,
        provider,
        model: str,
        worker_id: str,
        worker_name: str,
        max_iterations: int = 30,
        poll_interval_s: float = 3.0,
        heartbeat_interval_s: float = 30.0,
        drain_timeout_s: float = 30.0,
        web_search_config=None,
        web_proxy: str | None = None,
        exec_config=None,
        restrict_to_workspace: bool = False,
    ) -> None:
        transport = httpx.ASGITransport(app=app)
        http_client = httpx.AsyncClient(transport=transport, base_url=supervisor_url)
        supervisor_client = SupervisorClient(
            supervisor_url,
            worker_id,
            http_client=http_client,
        )
        self.runner = WorkerRunner(
            supervisor_url=supervisor_url,
            worker_id=worker_id,
            worker_name=worker_name,
            workspace=workspace,
            provider=provider,
            model=model,
            max_iterations=max_iterations,
            poll_interval_s=poll_interval_s,
            heartbeat_interval_s=heartbeat_interval_s,
            drain_timeout_s=drain_timeout_s,
            web_search_config=web_search_config,
            web_proxy=web_proxy,
            exec_config=exec_config,
            restrict_to_workspace=restrict_to_workspace,
            supervisor_client=supervisor_client,
        )

    async def run(self) -> None:
        await self.runner.run()

    async def stop(self) -> None:
        await self.runner.stop()