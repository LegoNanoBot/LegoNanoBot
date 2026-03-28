"""CLI commands for nanobot."""

import asyncio
import os
import select
import signal
import sys
from pathlib import Path

# Force UTF-8 encoding for Windows console
if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        # Re-open stdout/stderr with UTF-8 encoding
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import typer
from prompt_toolkit import print_formatted_text
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI, HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.application import run_in_terminal
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from nanobot import __logo__, __version__
from nanobot.config.paths import get_workspace_path
from nanobot.config.schema import Config
from nanobot.utils.helpers import sync_workspace_templates

app = typer.Typer(
    name="nanobot",
    help=f"{__logo__} nanobot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    from nanobot.config.paths import get_cli_history_path

    history_file = get_cli_history_path()
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,   # Enter submits (single line mode)
    )


def _make_console() -> Console:
    return Console(file=sys.stdout)


def _render_interactive_ansi(render_fn) -> str:
    """Render Rich output to ANSI so prompt_toolkit can print it safely."""
    ansi_console = Console(
        force_terminal=True,
        color_system=console.color_system or "standard",
        width=console.width,
    )
    with ansi_console.capture() as capture:
        render_fn(ansi_console)
    return capture.get()


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    console = _make_console()
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(f"[cyan]{__logo__} nanobot[/cyan]")
    console.print(body)
    console.print()


async def _print_interactive_line(text: str) -> None:
    """Print async interactive updates with prompt_toolkit-safe Rich styling."""
    def _write() -> None:
        ansi = _render_interactive_ansi(
            lambda c: c.print(f"  [dim]↳ {text}[/dim]")
        )
        print_formatted_text(ANSI(ansi), end="")

    await run_in_terminal(_write)


async def _print_interactive_response(response: str, render_markdown: bool) -> None:
    """Print async interactive replies with prompt_toolkit-safe Rich styling."""
    def _write() -> None:
        content = response or ""
        ansi = _render_interactive_ansi(
            lambda c: (
                c.print(),
                c.print(f"[cyan]{__logo__} nanobot[/cyan]"),
                c.print(Markdown(content) if render_markdown else Text(content)),
                c.print(),
            )
        )
        print_formatted_text(ANSI(ansi), end="")

    await run_in_terminal(_write)


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc



def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """nanobot - Personal AI Assistant."""
    pass


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard():
    """Initialize nanobot configuration and workspace."""
    from nanobot.config.loader import get_config_path, load_config, save_config
    from nanobot.config.schema import Config

    config_path = get_config_path()

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
        if typer.confirm("Overwrite?"):
            config = Config()
            save_config(config)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            config = load_config()
            save_config(config)
            console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        save_config(Config())
        console.print(f"[green]✓[/green] Created config at {config_path}")

    console.print("[dim]Config template now uses `maxTokens` + `contextWindowTokens`; `memoryWindow` is no longer a runtime setting.[/dim]")

    # Create workspace
    workspace = get_workspace_path()

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace}")

    sync_workspace_templates(workspace)

    console.print(f"\n{__logo__} nanobot is ready!")
    console.print("\nNext steps:")
    console.print("  1. Add your API key to [cyan]~/.nanobot/config.json[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print("  2. Chat: [cyan]nanobot agent -m \"Hello!\"[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/nanobot#-chat-apps[/dim]")





def _make_provider(config: Config):
    """Create the appropriate LLM provider from config."""
    from nanobot.providers.base import GenerationSettings
    from nanobot.providers.openai_codex_provider import OpenAICodexProvider
    from nanobot.providers.azure_openai_provider import AzureOpenAIProvider
    from nanobot.providers.provider_plugins import create_provider_by_factory, get_provider_factory

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)

    # OpenAI Codex (OAuth)
    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        provider = OpenAICodexProvider(default_model=model)
    # Custom: direct OpenAI-compatible endpoint, bypasses LiteLLM
    elif provider_name == "custom":
        from nanobot.providers.custom_provider import CustomProvider
        provider = CustomProvider(
            api_key=p.api_key if p else "no-key",
            api_base=config.get_api_base(model) or "http://localhost:8000/v1",
            default_model=model,
        )
    # Azure OpenAI: direct Azure OpenAI endpoint with deployment name
    elif provider_name == "azure_openai":
        if not p or not p.api_key or not p.api_base:
            console.print("[red]Error: Azure OpenAI requires api_key and api_base.[/red]")
            console.print("Set them in ~/.nanobot/config.json under providers.azure_openai section")
            console.print("Use the model field to specify the deployment name.")
            raise typer.Exit(1)
        provider = AzureOpenAIProvider(
            api_key=p.api_key,
            api_base=p.api_base,
            default_model=model,
        )
    else:
        plugin_factory = get_provider_factory(provider_name)
        if plugin_factory:
            provider = create_provider_by_factory(
                plugin_factory,
                config=config,
                model=model,
                provider_name=provider_name,
                provider_config=p,
            )
        else:
            from nanobot.providers.litellm_provider import LiteLLMProvider
            from nanobot.providers.registry import find_by_name

            spec = find_by_name(provider_name)
            if not model.startswith("bedrock/") and not (p and p.api_key) and not (
                spec and (spec.is_oauth or spec.is_local)
            ):
                console.print("[red]Error: No API key configured.[/red]")
                console.print("Set one in ~/.nanobot/config.json under providers section")
                raise typer.Exit(1)
            provider = LiteLLMProvider(
                api_key=p.api_key if p else None,
                api_base=config.get_api_base(model),
                default_model=model,
                extra_body=p.extra_body if p else None,
                extra_headers=p.extra_headers if p else None,
                provider_name=provider_name,
            )

    defaults = config.agents.defaults
    try:
        provider.generation = GenerationSettings(
            temperature=defaults.temperature,
            max_tokens=defaults.max_tokens,
            reasoning_effort=defaults.reasoning_effort,
        )
    except AttributeError:
        # Some plugin factories may return immutable sentinels/mocks in tests.
        pass
    return provider


def _make_memory_store(config: Config):
    """Create pluggable memory store from config."""
    from nanobot.agent.memory import MemoryStore
    from nanobot.memory.registry import create_memory_store

    backend = create_memory_store(config, config.workspace_path)
    return MemoryStore(config.workspace_path, backend=backend)


def _load_runtime_config(config: str | None = None, workspace: str | None = None) -> Config:
    """Load config and optionally override the active workspace."""
    from nanobot.config.loader import load_config, set_config_path

    config_path = None
    if config:
        config_path = Path(config).expanduser().resolve()
        if not config_path.exists():
            console.print(f"[red]Error: Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")

    loaded = load_config(config_path)
    if workspace:
        loaded.agents.defaults.workspace = workspace
    return loaded


def _print_deprecated_memory_window_notice(config: Config) -> None:
    """Warn when running with old memoryWindow-only config."""
    if config.agents.defaults.should_warn_deprecated_memory_window:
        console.print(
            "[yellow]Hint:[/yellow] Detected deprecated `memoryWindow` without "
            "`contextWindowTokens`. `memoryWindow` is ignored; run "
            "[cyan]nanobot onboard[/cyan] to refresh your config template."
        )


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int | None = typer.Option(None, "--port", "-p", help="Gateway port"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Start the nanobot gateway."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.manager import ChannelManager
    from nanobot.config.paths import get_cron_dir
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronJob
    from nanobot.heartbeat.service import HeartbeatService
    from nanobot.session.manager import SessionManager

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    config = _load_runtime_config(config, workspace)
    _print_deprecated_memory_window_notice(config)
    port = port if port is not None else config.gateway.port

    console.print(f"{__logo__} Starting nanobot gateway on port {port}...")
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    from nanobot.providers.provider_plugins import validate_provider_plugins
    validate_provider_plugins(config)
    provider = _make_provider(config)
    memory_store = _make_memory_store(config)
    session_manager = SessionManager(config.workspace_path)

    # Create cron service first (callback set after agent creation)
    cron_store_path = get_cron_dir() / "jobs.json"
    cron = CronService(cron_store_path)

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        web_search_config=config.tools.web.search,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        memory_store=memory_store,
    )

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        from nanobot.agent.tools.cron import CronTool
        from nanobot.agent.tools.message import MessageTool
        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        # Prevent the agent from scheduling new cron jobs during execution
        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)
        try:
            response = await agent.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        if job.payload.deliver and job.payload.to and response:
            from nanobot.bus.events import OutboundMessage
            await bus.publish_outbound(OutboundMessage(
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to,
                content=response
            ))
        return response
    cron.on_job = on_cron_job

    # Create channel manager
    channels = ChannelManager(config, bus)

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        enabled = set(channels.enabled_channels)
        # Prefer the most recently updated non-internal session on an enabled channel.
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # Fallback keeps prior behavior but remains explicit.
        return "cli", "direct"

    # Create heartbeat service
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        return await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from nanobot.bus.events import OutboundMessage
        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # No external channel available to deliver to
        await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")

    # --- X-Ray monitoring (optional) ---
    xray_server = None
    xray_observer = None
    xray_store = None

    if config.xray.enabled:
        from loguru import logger
        try:
            from nanobot.xray import XRAY_AVAILABLE
            if not XRAY_AVAILABLE:
                logger.warning(
                    "X-Ray is enabled but dependencies are not installed. "
                    "Run: pip install -e '.[xray]'"
                )
            else:
                from nanobot.xray.store.sqlite import SQLiteEventStore
                from nanobot.xray.sse import SSEHub
                from nanobot.xray.collector import EventCollector
                from nanobot.xray.observer import XRayObserver
                from nanobot.xray.app import create_xray_app
                import uvicorn

                # 初始化存储
                db_path = str(config.workspace_path / config.xray.db_path)
                xray_store = SQLiteEventStore(db_path, max_runs=config.xray.retain_runs)
                asyncio.get_event_loop().run_until_complete(xray_store.init())

                # 初始化 SSE 和 Collector
                sse_hub = SSEHub()
                collector = EventCollector()
                collector.set_store(xray_store)
                collector.set_sse_hub(sse_hub)

                # 创建 Observer 并注入
                xray_observer = XRayObserver(collector)
                agent.observer = xray_observer
                agent.xray_capture_full_messages = config.xray.capture_full_messages
                if hasattr(agent, 'tools') and agent.tools:
                    agent.tools.observer = xray_observer
                # 也注入到 subagent manager（如果存在）
                if hasattr(agent, 'subagents') and agent.subagents:
                    agent.subagents.observer = xray_observer

                # 创建 FastAPI 应用
                config_refs = {
                    "memory_store": memory_store,
                    "skills_loader": getattr(agent.context, 'skills', None),
                    "tool_registry": agent.tools,
                    "workspace": str(config.workspace_path),
                    "bot_config": config,
                }
                xray_app = create_xray_app(xray_store, sse_hub, collector, config_refs)

                # 启动 uvicorn 服务器（内嵌到 asyncio loop）
                uvi_config = uvicorn.Config(
                    xray_app,
                    host=config.xray.host,
                    port=config.xray.port,
                    log_level="warning",
                )
                xray_server = uvicorn.Server(uvi_config)

                console.print(
                    f"[green]✓[/green] X-Ray monitoring at http://{config.xray.host}:{config.xray.port}"
                )
        except Exception as e:
            from loguru import logger
            logger.error(f"Failed to start X-Ray: {e}")

    async def run():
        # 定期清理旧数据
        async def _xray_cleanup_loop():
            import time
            while True:
                await asyncio.sleep(3600)  # 每小时
                try:
                    if xray_store:
                        cutoff = time.time() - (config.xray.retention_hours * 3600)
                        deleted = await xray_store.cleanup(cutoff)
                        if deleted > 0:
                            from loguru import logger
                            logger.debug(f"X-Ray cleanup: removed {deleted} old events")
                except Exception as e:
                    from loguru import logger
                    logger.warning(f"X-Ray cleanup error: {e}")

        try:
            await cron.start()
            await heartbeat.start()

            # 启动 X-Ray 服务器和清理任务
            xray_tasks = []
            if xray_server:
                xray_tasks.append(asyncio.create_task(xray_server.serve()))
            if xray_store:
                xray_tasks.append(asyncio.create_task(_xray_cleanup_loop()))

            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            # X-Ray 优雅关闭
            if xray_server:
                xray_server.should_exit = True
            if xray_store:
                await xray_store.close()

            await agent.close_mcp()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()

    asyncio.run(run())




# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show nanobot runtime logs during chat"),
):
    """Interact with the agent directly."""
    from loguru import logger

    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.config.paths import get_cron_dir
    from nanobot.cron.service import CronService

    config = _load_runtime_config(config, workspace)
    _print_deprecated_memory_window_notice(config)
    sync_workspace_templates(config.workspace_path)

    bus = MessageBus()
    provider = _make_provider(config)
    memory_store = _make_memory_store(config)

    # Create cron service for tool usage (no callback needed for CLI unless running)
    cron_store_path = get_cron_dir() / "jobs.json"
    cron = CronService(cron_store_path)

    if logs:
        logger.enable("nanobot")
    else:
        logger.disable("nanobot")

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        web_search_config=config.tools.web.search,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        memory_store=memory_store,
    )

    # Show spinner when logs are off (no output to miss); skip when logs are on
    def _thinking_ctx():
        if logs:
            from contextlib import nullcontext
            return nullcontext()
        # Animated spinner is safe to use with prompt_toolkit input handling
        return console.status("[dim]nanobot is thinking...[/dim]", spinner="dots")

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        console.print(f"  [dim]↳ {content}[/dim]")

    if message:
        # Single message mode — direct call, no bus needed
        async def run_once():
            with _thinking_ctx():
                response = await agent_loop.process_direct(message, session_id, on_progress=_cli_progress)
            _print_agent_response(response, render_markdown=markdown)
            await agent_loop.close_mcp()

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from nanobot.bus.events import InboundMessage
        _init_prompt_session()
        console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        def _handle_signal(signum, frame):
            sig_name = signal.Signals(signum).name
            _restore_terminal()
            console.print(f"\nReceived {sig_name}, goodbye!")
            sys.exit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        # SIGHUP is not available on Windows
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, _handle_signal)
        # Ignore SIGPIPE to prevent silent process termination when writing to closed pipes
        # SIGPIPE is not available on Windows
        if hasattr(signal, 'SIGPIPE'):
            signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        async def run_interactive():
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[str] = []

            async def _consume_outbound():
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass
                            elif ch and not is_tool_hint and not ch.send_progress:
                                pass
                            else:
                                await _print_interactive_line(msg.content)

                        elif not turn_done.is_set():
                            if msg.content:
                                turn_response.append(msg.content)
                            turn_done.set()
                        elif msg.content:
                            await _print_interactive_response(msg.content, render_markdown=markdown)

                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()

                        await bus.publish_inbound(InboundMessage(
                            channel=cli_channel,
                            sender_id="user",
                            chat_id=cli_chat_id,
                            content=user_input,
                        ))

                        with _thinking_ctx():
                            await turn_done.wait()

                        if turn_response:
                            _print_agent_response(turn_response[0], render_markdown=markdown)
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()

        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from nanobot.channels.channel_plugins import get_channel_factory
    from nanobot.channels.registry import discover_channel_names, load_channel_class
    from nanobot.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")

    for modname in sorted(discover_channel_names()):
        section = getattr(config.channels, modname, None)
        enabled = section and getattr(section, "enabled", False)
        try:
            cls = load_channel_class(modname)
            display = cls.display_name
        except ImportError:
            display = modname.title()
        table.add_row(
            display,
            "[green]\u2713[/green]" if enabled else "[dim]\u2717[/dim]",
        )

    for raw_name, section in sorted(config.channels.plugins.items(), key=lambda item: item[0]):
        enabled = bool(section and getattr(section, "enabled", False))
        channel_name = raw_name.replace("-", "_")
        factory = get_channel_factory(channel_name)
        available = factory is not None

        if enabled and not available:
            status = "[yellow]![/yellow]"
        else:
            status = "[green]\u2713[/green]" if enabled else "[dim]\u2717[/dim]"

        table.add_row(
            f"{raw_name} (plugin)",
            status,
        )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    from nanobot.config.paths import get_bridge_install_dir

    user_bridge = get_bridge_install_dir()

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # nanobot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall nanobot")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess

    from nanobot.config.loader import load_config
    from nanobot.config.paths import get_runtime_subdir

    config = load_config()
    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    env = {**os.environ}
    if config.channels.whatsapp.bridge_token:
        env["BRIDGE_TOKEN"] = config.channels.whatsapp.bridge_token
    env["AUTH_DIR"] = str(get_runtime_subdir("whatsapp-auth"))

    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Show nanobot status: config, providers, channels, plugins, supervisor, xray."""
    from nanobot.config.loader import get_config_path, load_config, set_config_path

    if config:
        config_path = Path(config).expanduser().resolve()
        set_config_path(config_path)
    else:
        config_path = get_config_path()

    cfg = load_config(config_path if config else None)
    workspace = cfg.workspace_path

    console.print(f"{__logo__} nanobot Status\n")

    # ── Core ──────────────────────────────────────────────────────────
    table_core = Table(title="Core", show_header=False, box=None, padding=(0, 2))
    table_core.add_column("Key", style="bold")
    table_core.add_column("Value")
    table_core.add_row("Version", __version__)
    table_core.add_row("Config", f"{config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    table_core.add_row("Workspace", f"{workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")
    table_core.add_row("Model", cfg.agents.defaults.model)
    table_core.add_row("Provider", cfg.agents.defaults.provider)
    table_core.add_row("Context window", f"{cfg.agents.defaults.context_window_tokens:,} tokens")
    table_core.add_row("Max tool iterations", str(cfg.agents.defaults.max_tool_iterations))
    console.print(table_core)
    console.print()

    if not config_path.exists():
        console.print("[yellow]Config file not found. Run [cyan]nanobot onboard[/cyan] first.[/yellow]")
        return

    # ── Providers ─────────────────────────────────────────────────────
    from nanobot.providers.registry import BUILTIN_PROVIDERS, PROVIDERS

    table_prov = Table(title="Providers", show_header=True, box=None, padding=(0, 2))
    table_prov.add_column("Provider", style="bold")
    table_prov.add_column("Status")

    for spec in PROVIDERS:
        p = cfg.get_provider_config(spec.name)
        if p is None:
            continue
        if spec.is_oauth:
            table_prov.add_row(spec.label, "[green]✓ (OAuth)[/green]")
        elif spec.is_local:
            if p.api_base:
                table_prov.add_row(spec.label, f"[green]✓[/green] {p.api_base}")
            else:
                table_prov.add_row(spec.label, "[dim]not set[/dim]")
        else:
            has_key = bool(p.api_key)
            table_prov.add_row(spec.label, "[green]✓[/green]" if has_key else "[dim]not set[/dim]")
    console.print(table_prov)
    console.print()

    # ── Channels ──────────────────────────────────────────────────────
    from nanobot.channels.registry import discover_channel_names

    table_chan = Table(title="Channels", show_header=True, box=None, padding=(0, 2))
    table_chan.add_column("Channel", style="bold")
    table_chan.add_column("Status")

    for modname in discover_channel_names():
        section = getattr(cfg.channels, modname, None)
        if section is None:
            continue  # skip internal modules without config
        enabled = bool(getattr(section, "enabled", False))
        table_chan.add_row(modname, "[green]✓ enabled[/green]" if enabled else "[dim]disabled[/dim]")

    # Plugin channels
    for raw_name, section in cfg.channels.plugins.items():
        enabled = bool(section and getattr(section, "enabled", False))
        table_chan.add_row(f"{raw_name} [cyan](plugin)[/cyan]", "[green]✓ enabled[/green]" if enabled else "[dim]disabled[/dim]")

    console.print(table_chan)
    console.print()

    # ── Plugins ───────────────────────────────────────────────────────
    from nanobot.channels.channel_plugins import load_channel_factories
    from nanobot.memory.memory_plugins import load_memory_factories
    from nanobot.providers.provider_plugins import load_provider_factories

    provider_factories = load_provider_factories()
    channel_factories = load_channel_factories()
    memory_factories = load_memory_factories()

    plugin_specs = [s for s in PROVIDERS if s.name not in {b.name for b in BUILTIN_PROVIDERS}]

    has_plugins = plugin_specs or provider_factories or channel_factories or memory_factories

    if has_plugins:
        table_plug = Table(title="Plugins", show_header=True, box=None, padding=(0, 2))
        table_plug.add_column("Type", style="bold")
        table_plug.add_column("Name")

        for spec in plugin_specs:
            table_plug.add_row("Provider spec", spec.label)
        for name in provider_factories:
            table_plug.add_row("Provider factory", name)
        for name in channel_factories:
            table_plug.add_row("Channel factory", name)
        for name in memory_factories:
            table_plug.add_row("Memory factory", name)

        console.print(table_plug)
    else:
        console.print("[dim]Plugins: none installed[/dim]")
    console.print()

    # ── Memory ────────────────────────────────────────────────────────
    table_mem = Table(title="Memory", show_header=False, box=None, padding=(0, 2))
    table_mem.add_column("Key", style="bold")
    table_mem.add_column("Value")
    table_mem.add_row("Backend", cfg.memory.backend)
    if cfg.memory.backend in {"filesystem", "file", "default"}:
        fs = cfg.memory.filesystem
        table_mem.add_row("Directory", fs.dir)
        table_mem.add_row("Memory file", fs.memory_file)
    if cfg.memory.plugins:
        table_mem.add_row("Plugin configs", ", ".join(cfg.memory.plugins.keys()))
    console.print(table_mem)
    console.print()

    # ── MCP Servers ───────────────────────────────────────────────────
    if cfg.tools.mcp_servers:
        table_mcp = Table(title="MCP Servers", show_header=True, box=None, padding=(0, 2))
        table_mcp.add_column("Name", style="bold")
        table_mcp.add_column("Type")
        table_mcp.add_column("Endpoint")

        for name, mcp_cfg in cfg.tools.mcp_servers.items():
            mcp_type = mcp_cfg.type or "auto"
            endpoint = mcp_cfg.url or f"{mcp_cfg.command} {' '.join(mcp_cfg.args)}".strip()
            table_mcp.add_row(name, mcp_type, endpoint or "[dim]—[/dim]")

        console.print(table_mcp)
        console.print()

    # ── Gateway ───────────────────────────────────────────────────────
    table_gw = Table(title="Gateway", show_header=False, box=None, padding=(0, 2))
    table_gw.add_column("Key", style="bold")
    table_gw.add_column("Value")
    table_gw.add_row("Bind", f"{cfg.gateway.host}:{cfg.gateway.port}")
    hb = cfg.gateway.heartbeat
    table_gw.add_row("Heartbeat", f"{'[green]✓[/green]' if hb.enabled else '[dim]disabled[/dim]'} (every {hb.interval_s}s)")
    console.print(table_gw)
    console.print()

    # ── X-Ray ─────────────────────────────────────────────────────────
    table_xray = Table(title="X-Ray", show_header=False, box=None, padding=(0, 2))
    table_xray.add_column("Key", style="bold")
    table_xray.add_column("Value")

    if cfg.xray.enabled:
        table_xray.add_row("Enabled", "[green]✓[/green]")
        table_xray.add_row("Endpoint", f"http://{cfg.xray.host}:{cfg.xray.port}")
        table_xray.add_row("DB path", cfg.xray.db_path)
        table_xray.add_row("Retention", f"{cfg.xray.retention_hours}h")

        # Probe X-Ray availability
        try:
            from nanobot.xray import XRAY_AVAILABLE
            table_xray.add_row("Dependencies", "[green]✓ installed[/green]" if XRAY_AVAILABLE else "[red]✗ missing[/red] (pip install -e '.[xray]')")
        except Exception:
            table_xray.add_row("Dependencies", "[red]✗ import error[/red]")

        # Check if xray is live
        _probe_http(table_xray, f"http://{cfg.xray.host}:{cfg.xray.port}/api/docs", "Service")
    else:
        table_xray.add_row("Enabled", "[dim]disabled[/dim]")

    console.print(table_xray)
    console.print()

    # ── Supervisor ────────────────────────────────────────────────────
    table_sv = Table(title="Supervisor", show_header=False, box=None, padding=(0, 2))
    table_sv.add_column("Key", style="bold")
    table_sv.add_column("Value")

    # Probe default supervisor endpoint
    sv_url = "http://127.0.0.1:9200"
    _probe_http(table_sv, f"{sv_url}/api/v1/supervisor/workers", "Service")

    # Query workers if supervisor is up
    try:
        import httpx
        resp = httpx.get(f"{sv_url}/api/v1/supervisor/workers", timeout=2.0)
        if resp.status_code == 200:
            workers = resp.json()
            if isinstance(workers, list):
                table_sv.add_row("Workers", str(len(workers)))
                for w in workers:
                    w_status = w.get("status", "unknown")
                    w_name = w.get("name", w.get("worker_id", "?"))
                    color = "green" if w_status == "online" else "yellow" if w_status == "busy" else "red"
                    task_info = f" → task {w.get('current_task_id')}" if w.get("current_task_id") else ""
                    table_sv.add_row(f"  {w_name}", f"[{color}]{w_status}[/{color}]{task_info}")
    except Exception:
        pass

    # Query pending/running tasks
    try:
        import httpx
        resp = httpx.get(f"{sv_url}/api/v1/supervisor/tasks", timeout=2.0)
        if resp.status_code == 200:
            tasks = resp.json()
            if isinstance(tasks, list) and tasks:
                by_status: dict[str, int] = {}
                for t in tasks:
                    s = t.get("status", "unknown")
                    by_status[s] = by_status.get(s, 0) + 1
                summary = ", ".join(f"{v} {k}" for k, v in sorted(by_status.items()))
                table_sv.add_row("Tasks", summary)
    except Exception:
        pass

    console.print(table_sv)
    console.print()

    # ── Cron ──────────────────────────────────────────────────────────
    from nanobot.config.paths import get_cron_dir
    from nanobot.cron.service import CronService

    cron_store_path = get_cron_dir() / "jobs.json"
    if cron_store_path.exists():
        cron = CronService(cron_store_path)
        cron_status = cron.status()
        svc_state = "[green]running[/green]" if cron_status.get("enabled") else "[dim]stopped[/dim]"
        console.print(f"[bold]Cron:[/bold]  {cron_status['jobs']} jobs ({svc_state})")
    else:
        console.print("[bold]Cron:[/bold]  [dim]no jobs[/dim]")


def _probe_http(table: Table, url: str, label: str) -> None:
    """Probe an HTTP endpoint and add a row to the table."""
    try:
        import httpx
        resp = httpx.get(url, timeout=2.0)
        if resp.status_code < 500:
            table.add_row(label, f"[green]✓ running[/green] ({url})")
        else:
            table.add_row(label, f"[yellow]⚠ HTTP {resp.status_code}[/yellow] ({url})")
    except Exception:
        table.add_row(label, f"[dim]not running[/dim] ({url})")


# ============================================================================
# OAuth Login
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


_LOGIN_HANDLERS: dict[str, callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn
    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
):
    """Authenticate with an OAuth provider."""
    from nanobot.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@provider_app.command("reload")
def provider_reload():
    """Reload provider plugins for the current nanobot process."""
    from nanobot.providers.provider_plugins import load_provider_factories
    from nanobot.providers.registry import BUILTIN_PROVIDERS, reload_providers

    providers = reload_providers()
    factories = load_provider_factories()

    plugin_specs = [s for s in providers if s.name not in {b.name for b in BUILTIN_PROVIDERS}]
    plugin_names = ", ".join(s.name.replace("_", "-") for s in plugin_specs) or "none"

    console.print(f"{__logo__} Provider Reload\n")
    console.print(
        f"Reloaded providers: [green]{len(providers)}[/green] "
        f"(built-in: {len(BUILTIN_PROVIDERS)}, plugins: {len(plugin_specs)})"
    )
    console.print(f"Plugin providers: {plugin_names}")
    console.print(f"Plugin factories: [green]{len(factories)}[/green]")


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive
        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),
                prompt_fn=lambda s: typer.prompt(s),
            )
        if not (token and token.access):
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]")
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    import asyncio

    console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")

    async def _trigger():
        from litellm import acompletion
        await acompletion(model="github_copilot/gpt-4o", messages=[{"role": "user", "content": "hi"}], max_tokens=1)

    try:
        asyncio.run(_trigger())
        console.print("[green]✓ Authenticated with GitHub Copilot[/green]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# Supervisor Command
# ============================================================================


@app.command()
def supervisor(
    port: int | None = typer.Option(None, "--port", "-p", help="Supervisor API port"),
    host: str | None = typer.Option(None, "--host", help="Supervisor API bind address"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    db_path: str | None = typer.Option(None, "--db", help="Supervisor state database path"),
    heartbeat_timeout: float | None = typer.Option(None, "--heartbeat-timeout", help="Worker heartbeat timeout (seconds)"),
    watchdog_interval: float | None = typer.Option(None, "--watchdog-interval", help="Watchdog scan interval (seconds)"),
):
    """Start the supervisor node (control plane for distributed workers)."""
    from nanobot.supervisor.app import create_supervisor_app
    from nanobot.supervisor.registry import WorkerRegistry
    from nanobot.supervisor.store import SQLiteRegistryStore
    from nanobot.supervisor.watchdog import WatchdogService

    cfg = _load_runtime_config(config, workspace)
    sync_workspace_templates(cfg.workspace_path)

    resolved_host = host or cfg.supervisor.host
    resolved_port = port if port is not None else cfg.supervisor.port
    resolved_heartbeat_timeout = (
        heartbeat_timeout
        if heartbeat_timeout is not None
        else cfg.supervisor.heartbeat_timeout_s
    )
    resolved_watchdog_interval = (
        watchdog_interval
        if watchdog_interval is not None
        else cfg.supervisor.watchdog_interval_s
    )
    resolved_db_path = str((cfg.workspace_path / (db_path or cfg.supervisor.db_path)).resolve())

    console.print(f"{__logo__} Starting supervisor on {resolved_host}:{resolved_port}...")

    # Optional: set up X-Ray stores for aggregated monitoring
    xray_kwargs: dict = {}
    collector = None
    if cfg.xray.enabled:
        try:
            from nanobot.xray import XRAY_AVAILABLE
            if XRAY_AVAILABLE:
                from nanobot.xray.collector import EventCollector
                from nanobot.xray.sse import SSEHub
                from nanobot.xray.store.sqlite import SQLiteEventStore

                db_path = str(cfg.workspace_path / cfg.xray.db_path)
                xray_store = SQLiteEventStore(db_path, max_runs=cfg.xray.retain_runs)
                asyncio.run(xray_store.init())

                sse_hub = SSEHub()
                collector = EventCollector()
                collector.set_store(xray_store)
                collector.set_sse_hub(sse_hub)

                xray_kwargs = {
                    "event_store": xray_store,
                    "sse_hub": sse_hub,
                    "collector": collector,
                    "config_refs": {
                        "workspace": str(cfg.workspace_path),
                        "bot_config": cfg,
                    },
                }
                console.print("[green]✓[/green] X-Ray monitoring enabled")
        except Exception as e:
            console.print(f"[yellow]X-Ray init failed: {e}[/yellow]")

    registry_store = None
    if SQLiteRegistryStore is None:
        console.print("[yellow]Supervisor state store unavailable: install xray extras for SQLite persistence. Falling back to memory.[/yellow]")
    else:
        try:
            registry_store = SQLiteRegistryStore(resolved_db_path)
            asyncio.run(registry_store.init())
            console.print(f"[green]✓[/green] Supervisor state store at {resolved_db_path}")
        except Exception as e:
            registry_store = None
            console.print(f"[yellow]Supervisor state store init failed: {e}. Falling back to memory.[/yellow]")

    registry = WorkerRegistry(
        heartbeat_timeout_s=resolved_heartbeat_timeout,
        task_default_timeout_s=cfg.supervisor.task_default_timeout_s,
        task_default_max_iterations=cfg.supervisor.task_default_max_iterations,
        store=registry_store,
        collector=collector,
    )

    if registry_store is not None:
        asyncio.run(registry.restore())

    supervisor_app = create_supervisor_app(
        worker_registry=registry,
        **xray_kwargs,
    )

    import uvicorn

    uvi_config = uvicorn.Config(
        supervisor_app,
        host=resolved_host,
        port=resolved_port,
        log_level="info",
    )
    server = uvicorn.Server(uvi_config)

    watchdog = WatchdogService(registry, check_interval_s=resolved_watchdog_interval)

    async def _run_supervisor():
        await watchdog.start()
        try:
            await server.serve()
        finally:
            watchdog.stop()
            if registry_store is not None:
                await registry_store.close()
            if "event_store" in xray_kwargs:
                await xray_kwargs["event_store"].close()

    console.print(f"[green]✓[/green] Supervisor API at http://{resolved_host}:{resolved_port}/api/docs")
    asyncio.run(_run_supervisor())


# ============================================================================
# Worker Command
# ============================================================================


@app.command()
def worker(
    supervisor_url: str = typer.Option("http://127.0.0.1:9200", "--supervisor", "-s", help="Supervisor URL"),
    name: str = typer.Option("worker", "--name", "-n", help="Worker display name"),
    worker_id: str | None = typer.Option(None, "--id", help="Worker ID (auto-generated if omitted)"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    poll_interval: float = typer.Option(3.0, "--poll-interval", help="Task poll interval (seconds)"),
):
    """Start a worker node that executes tasks from the supervisor."""
    from nanobot.worker.runner import WorkerRunner

    cfg = _load_runtime_config(config, workspace)
    sync_workspace_templates(cfg.workspace_path)

    provider = _make_provider(cfg)

    runner = WorkerRunner(
        supervisor_url=supervisor_url,
        worker_id=worker_id,
        worker_name=name,
        workspace=cfg.workspace_path,
        provider=provider,
        model=cfg.agents.defaults.model,
        max_iterations=cfg.agents.defaults.max_tool_iterations,
        poll_interval_s=poll_interval,
        web_search_config=cfg.tools.web.search,
        web_proxy=cfg.tools.web.proxy or None,
        exec_config=cfg.tools.exec,
        restrict_to_workspace=cfg.tools.restrict_to_workspace,
    )

    console.print(f"{__logo__} Starting worker '{name}' → {supervisor_url}")
    console.print(f"[dim]Worker ID: {runner.worker_id}[/dim]")

    asyncio.run(runner.run())


if __name__ == "__main__":
    app()
