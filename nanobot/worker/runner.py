"""Worker runner — polls supervisor for tasks and executes them.

The runner is the main event loop for a worker process. It:
1. Registers with the supervisor
2. Sends periodic heartbeats
3. Polls for tasks
4. Executes tasks using a local AgentLoop-style LLM + tool loop
5. Reports progress and results back to the supervisor
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.worker.client import SupervisorClient


class WorkerRunner:
    """Runs the worker poll-execute-report loop."""

    def __init__(
        self,
        *,
        supervisor_url: str,
        worker_id: str | None = None,
        worker_name: str = "worker",
        workspace: Path,
        provider: Any,  # LLMProvider
        model: str,
        max_iterations: int = 30,
        poll_interval_s: float = 3.0,
        heartbeat_interval_s: float = 30.0,
        drain_timeout_s: float = 30.0,
        web_search_config: Any = None,
        web_proxy: str | None = None,
        exec_config: Any = None,
        restrict_to_workspace: bool = False,
        supervisor_client: SupervisorClient | None = None,
    ) -> None:
        self.worker_id = worker_id or f"w-{uuid.uuid4().hex[:8]}"
        self.worker_name = worker_name
        self.workspace = workspace
        self.provider = provider
        self.model = model
        self.max_iterations = max_iterations
        self.poll_interval_s = poll_interval_s
        self.heartbeat_interval_s = heartbeat_interval_s
        self.drain_timeout_s = drain_timeout_s
        self.web_search_config = web_search_config
        self.web_proxy = web_proxy
        self.exec_config = exec_config
        self.restrict_to_workspace = restrict_to_workspace

        self.client = supervisor_client or SupervisorClient(supervisor_url, self.worker_id)
        self._running = False
        self._accepting_tasks = True
        self._current_task_id: str | None = None
        self._shutdown_requested = False
        self._shutdown_event = asyncio.Event()
        self._active_execution_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        """Main loop: register → heartbeat + poll → execute → report."""
        self._running = True
        self._accepting_tasks = True
        self._shutdown_requested = False
        self._shutdown_event = asyncio.Event()
        installed_signals = self._install_signal_handlers()

        try:
            await self._register_until_available()
            if not self._running:
                return

            # Start heartbeat in background
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            try:
                await self._poll_loop()
            finally:
                self._running = False
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
        finally:
            self._running = False
            for sig in installed_signals:
                try:
                    asyncio.get_running_loop().remove_signal_handler(sig)
                except (NotImplementedError, RuntimeError):
                    pass
            await self.client.unregister()
            await self.client.close()
            logger.info("Worker {} shut down", self.worker_id)

    async def stop(self) -> None:
        await self.request_shutdown(reason="stop requested")

    async def request_shutdown(self, *, reason: str) -> None:
        if self._shutdown_requested:
            if self._active_execution_task is not None and not self._active_execution_task.done():
                logger.warning(
                    "Worker {} received repeated shutdown request ({}); interrupting task {}",
                    self.worker_id,
                    reason,
                    self._current_task_id,
                )
                self._active_execution_task.cancel()
            return

        self._shutdown_requested = True
        self._accepting_tasks = False
        self._shutdown_event.set()

        if self._active_execution_task is None or self._active_execution_task.done():
            logger.info("Worker {} shutting down: {}", self.worker_id, reason)
            self._running = False
            return

        logger.info(
            "Worker {} draining task {} before shutdown (reason: {}, timeout: {}s)",
            self.worker_id,
            self._current_task_id,
            reason,
            self.drain_timeout_s,
        )
        asyncio.create_task(self._enforce_drain_timeout())

    def _install_signal_handlers(self) -> list[signal.Signals]:
        installed: list[signal.Signals] = []
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return installed

        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                loop.add_signal_handler(
                    sig,
                    lambda sig_name=sig_name: asyncio.create_task(
                        self.request_shutdown(reason=f"signal {sig_name}")
                    ),
                )
                installed.append(sig)
            except (NotImplementedError, RuntimeError):
                continue
        return installed

    async def _enforce_drain_timeout(self) -> None:
        task = self._active_execution_task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self.drain_timeout_s)
        except asyncio.TimeoutError:
            if self._active_execution_task is not None and not self._active_execution_task.done():
                logger.warning(
                    "Worker {} drain timeout exceeded for task {}; interrupting task",
                    self.worker_id,
                    self._current_task_id,
                )
                self._active_execution_task.cancel()
        except asyncio.CancelledError:
            pass

    async def _register_until_available(self) -> None:
        while self._running:
            try:
                await self.client.register(self.worker_name)
                logger.info("Worker {} registered with supervisor", self.worker_id)
                return
            except Exception as e:
                logger.warning(
                    "Failed to register with supervisor: {}. Retrying in {}s",
                    e,
                    self.poll_interval_s,
                )
                await self._wait_for_shutdown_or_timeout(self.poll_interval_s)

        if self._shutdown_requested:
            logger.info("Worker {} stopped before registration completed", self.worker_id)
            return

        raise RuntimeError("Worker stopped before registration completed")

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                status = "busy" if self._current_task_id else "online"
                await self.client.heartbeat(
                    current_task_id=self._current_task_id,
                    status=status,
                )
            except Exception as e:
                logger.warning("Heartbeat failed: {}", e)
            await asyncio.sleep(self.heartbeat_interval_s)

    async def _poll_loop(self) -> None:
        while self._running and self._accepting_tasks:
            try:
                task_data = await self.client.claim_task()
                if task_data is not None:
                    self._active_execution_task = asyncio.create_task(self._execute_task(task_data))
                    try:
                        await self._active_execution_task
                    finally:
                        self._active_execution_task = None
                        if self._shutdown_requested:
                            self._running = False
                else:
                    await self._wait_for_shutdown_or_timeout(self.poll_interval_s)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Poll loop error: {}", e)
                await self._wait_for_shutdown_or_timeout(self.poll_interval_s)

    async def _wait_for_shutdown_or_timeout(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return

    async def _execute_task(self, task_data: dict[str, Any]) -> None:
        """Execute a claimed task using an LLM + tool loop (similar to SubagentManager)."""
        task_id = task_data["task_id"]
        instruction = task_data["instruction"]
        context = task_data.get("context", "")
        max_iter = task_data.get("max_iterations", self.max_iterations)
        timeout_s = float(task_data.get("timeout_s", 600.0))

        self._current_task_id = task_id
        logger.info("Worker {} executing task {}: {}", self.worker_id, task_id, instruction[:60])

        try:
            final_result = await asyncio.wait_for(
                self._run_task_loop(
                    instruction=instruction,
                    context=context,
                    max_iter=max_iter,
                    task_id=task_id,
                ),
                timeout=timeout_s,
            )

            # Report success
            await self.client.report_result(
                task_id=task_id,
                status="completed",
                result=final_result,
            )
            logger.info("Task {} completed successfully", task_id)

        except asyncio.TimeoutError:
            error_msg = f"task timed out after {timeout_s:g}s"
            logger.error("Task {} failed: {}", task_id, error_msg)
            try:
                await self.client.report_result(
                    task_id=task_id,
                    status="failed",
                    error=error_msg,
                )
            except Exception:
                logger.error("Failed to report task timeout to supervisor")
        except asyncio.CancelledError:
            logger.warning("Task {} interrupted during worker shutdown", task_id)
            raise
        except Exception as e:
            error_msg = str(e)
            logger.error("Task {} failed: {}", task_id, error_msg)
            try:
                await self.client.report_result(
                    task_id=task_id,
                    status="failed",
                    error=error_msg,
                )
            except Exception:
                logger.error("Failed to report task failure to supervisor")
        finally:
            self._current_task_id = None

    async def _run_task_loop(
        self,
        *,
        instruction: str,
        context: str,
        max_iter: int,
        task_id: str,
    ) -> str:
        from nanobot.agent.tools.filesystem import (
            EditFileTool,
            ListDirTool,
            ReadFileTool,
            WriteFileTool,
        )
        from nanobot.agent.tools.registry import ToolRegistry
        from nanobot.agent.tools.shell import ExecTool
        from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
        from nanobot.config.schema import ExecToolConfig, WebSearchConfig
        from nanobot.utils.helpers import build_assistant_message

        tools = ToolRegistry()
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
        tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
        tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
        tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))

        exec_cfg = self.exec_config or ExecToolConfig()
        tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=exec_cfg.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            path_append=exec_cfg.path_append,
        ))

        ws_cfg = self.web_search_config or WebSearchConfig()
        tools.register(WebSearchTool(config=ws_cfg, proxy=self.web_proxy))
        tools.register(WebFetchTool(proxy=self.web_proxy))

        system_prompt = self._build_system_prompt(context)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": instruction},
        ]

        iteration = 0
        final_result: str | None = None

        while iteration < max_iter:
            iteration += 1

            response = await self.provider.chat_with_retry(
                messages=messages,
                tools=tools.get_definitions(),
                model=self.model,
            )

            try:
                progress_msg = ""
                if response.content:
                    progress_msg = response.content[:200]
                elif response.has_tool_calls:
                    tool_names = [tc.name for tc in response.tool_calls]
                    progress_msg = f"Calling tools: {', '.join(tool_names)}"
                await self.client.report_progress(
                    task_id=task_id,
                    iteration=iteration,
                    message=progress_msg,
                )
            except Exception:
                pass

            if response.has_tool_calls:
                tool_call_dicts = [
                    tc.to_openai_tool_call() for tc in response.tool_calls
                ]
                messages.append(build_assistant_message(
                    response.content or "",
                    tool_calls=tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                ))

                for tool_call in response.tool_calls:
                    result = await tools.execute(tool_call.name, tool_call.arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": result,
                    })
            else:
                final_result = response.content
                break

        if final_result is None:
            return "Task completed (max iterations reached without final response)."
        return final_result

    def _build_system_prompt(self, extra_context: str = "") -> str:
        from nanobot.agent.context import ContextBuilder
        from nanobot.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        parts = [
            f"# Worker Agent\n\n{time_ctx}\n\n"
            f"You are a worker agent executing a task assigned by the supervisor.\n"
            f"Stay focused on the assigned task. Complete it thoroughly and return a clear result.\n\n"
            f"## Workspace\n{self.workspace}",
        ]

        if extra_context:
            parts.append(f"## Context\n{extra_context}")

        skills_summary = SkillsLoader(self.workspace).build_skills_summary()
        if skills_summary:
            parts.append(f"## Skills\n\nRead SKILL.md with read_file to use a skill.\n\n{skills_summary}")

        return "\n\n".join(parts)
