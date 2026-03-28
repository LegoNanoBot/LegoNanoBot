"""Subagent manager for background task execution."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ExecToolConfig
from nanobot.providers.base import LLMProvider
from nanobot.utils.helpers import build_assistant_message
from nanobot.worker.client import SupervisorClient

if TYPE_CHECKING:
    from nanobot.xray.observer import XRayObserver


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str | None = None,
        web_search_config: "WebSearchConfig | None" = None,
        web_proxy: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        restrict_to_workspace: bool = False,
        supervisor_client: SupervisorClient | None = None,
        default_mode: str = "local",
    ):
        from nanobot.config.schema import ExecToolConfig, WebSearchConfig

        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.web_search_config = web_search_config or WebSearchConfig()
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.restrict_to_workspace = restrict_to_workspace
        self.supervisor_client = supervisor_client
        self.default_mode = default_mode
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}
        self.observer: XRayObserver | None = None

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        mode: str | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        spawn_mode = mode or self.default_mode
        if spawn_mode not in {"local", "remote", "auto"}:
            raise ValueError(f"Unsupported subagent mode: {spawn_mode}")

        if spawn_mode == "auto":
            if self.supervisor_client is not None and await self.supervisor_client.is_available():
                spawn_mode = "remote"
            else:
                spawn_mode = "local"

        if spawn_mode == "remote":
            if self.supervisor_client is None:
                raise RuntimeError("Supervisor client not configured for remote subagents")
            return await self._spawn_remote(
                task=task,
                label=label,
                origin_channel=origin_channel,
                origin_chat_id=origin_chat_id,
                session_key=session_key,
            )

        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin)
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        if self.observer:
            try:
                await self.observer.emit(
                    run_id=task_id,
                    event_type="subagent_spawn",
                    data={"label": display_label, "task_preview": task[:200], "task_id": task_id}
                )
            except Exception:
                pass

        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _spawn_remote(
        self,
        *,
        task: str,
        label: str | None,
        origin_channel: str,
        origin_chat_id: str,
        session_key: str | None,
    ) -> str:
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}
        remote_task = await self.supervisor_client.create_task(
            instruction=task,
            label=display_label,
            context=self._build_remote_context(display_label),
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            session_key=session_key,
            max_retries=1,
        )
        remote_task_id = remote_task.get("task_id", "unknown")

        bg_task = asyncio.create_task(
            self._poll_remote_subagent(
                local_task_id=task_id,
                remote_task_id=remote_task_id,
                label=display_label,
                task=task,
                origin=origin,
            )
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)
        logger.info("Spawned remote subagent [{}] -> supervisor task {}", task_id, remote_task_id)
        return (
            f"Subagent [{display_label}] delegated to worker pool "
            f"(id: {task_id}, remote task: {remote_task_id}). I'll notify you when it completes."
        )

    async def _poll_remote_subagent(
        self,
        *,
        local_task_id: str,
        remote_task_id: str,
        label: str,
        task: str,
        origin: dict[str, str],
    ) -> None:
        try:
            final_task = await self.supervisor_client.wait_for_task(remote_task_id, poll_interval_s=1.0)
            status = "ok" if final_task.get("status") == "completed" else "error"
            result = final_task.get("result") or ""
            if status != "ok":
                error = final_task.get("error") or "unknown error"
                if result.strip():
                    result = f"Error: {error}\n\nPartial output:\n{result}"
                else:
                    result = f"Error: {error}"
            await self._announce_result(local_task_id, label, task, result, origin, status)
        except asyncio.CancelledError:
            try:
                await self.supervisor_client.cancel_task(remote_task_id)
            except Exception:
                pass
            raise
        except Exception as e:
            await self._announce_result(local_task_id, label, task, f"Error: {e}", origin, "error")

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)
        t0 = time.time()

        try:
            # Build subagent tools (no message tool, no spawn tool)
            tools = ToolRegistry()
            allowed_dir = self.workspace if self.restrict_to_workspace else None
            tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                path_append=self.exec_config.path_append,
            ))
            tools.register(WebSearchTool(config=self.web_search_config, proxy=self.web_proxy))
            tools.register(WebFetchTool(proxy=self.web_proxy))
            
            system_prompt = self._build_subagent_prompt()
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            # Run agent loop (limited iterations)
            max_iterations = 15
            iteration = 0
            final_result: str | None = None

            while iteration < max_iterations:
                iteration += 1

                response = await self.provider.chat_with_retry(
                    messages=messages,
                    tools=tools.get_definitions(),
                    model=self.model,
                )

                if response.has_tool_calls:
                    tool_call_dicts = [
                        tc.to_openai_tool_call()
                        for tc in response.tool_calls
                    ]
                    messages.append(build_assistant_message(
                        response.content or "",
                        tool_calls=tool_call_dicts,
                        reasoning_content=response.reasoning_content,
                        thinking_blocks=response.thinking_blocks,
                    ))

                    # Execute tools
                    for tool_call in response.tool_calls:
                        args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                        logger.debug("Subagent [{}] executing: {} with arguments: {}", task_id, tool_call.name, args_str)
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
                final_result = "Task completed but no final response was generated."

            logger.info("Subagent [{}] completed successfully", task_id)

            if self.observer:
                try:
                    duration_s = time.time() - t0
                    await self.observer.emit(
                        run_id=task_id,
                        event_type="subagent_done",
                        data={"task_id": task_id, "result_preview": final_result[:500] if final_result else "", "duration_s": duration_s, "status": "ok"}
                    )
                except Exception:
                    pass

            await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error("Subagent [{}] failed: {}", task_id, e)

            if self.observer:
                try:
                    duration_s = time.time() - t0
                    await self.observer.emit(
                        run_id=task_id,
                        event_type="subagent_done",
                        data={"task_id": task_id, "result_preview": error_msg[:500], "duration_s": duration_s, "status": "error"}
                    )
                except Exception:
                    pass

            await self._announce_result(task_id, label, task, error_msg, origin, "error")

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = f"""[Subagent '{label}' {status_text}]

Task: {task}

Result:
{result}

Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""

        # Inject as system message to trigger main agent
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])
    
    def _build_subagent_prompt(self) -> str:
        """Build a focused system prompt for the subagent."""
        from nanobot.agent.context import ContextBuilder
        from nanobot.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        parts = [f"""# Subagent

{time_ctx}

You are a subagent spawned by the main agent to complete a specific task.
Stay focused on the assigned task. Your final response will be reported back to the main agent.

## Workspace
{self.workspace}"""]

        skills_summary = SkillsLoader(self.workspace).build_skills_summary()
        if skills_summary:
            parts.append(f"## Skills\n\nRead SKILL.md with read_file to use a skill.\n\n{skills_summary}")

        return "\n\n".join(parts)

    @staticmethod
    def _build_remote_context(label: str) -> str:
        return (
            "This task was delegated by the main agent as a remote subagent. "
            f"Focus on the assigned task and return a concise but complete result. Label: {label}"
        )

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)
