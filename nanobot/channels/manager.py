"""Channel manager for coordinating chat channels."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.channels.channel_plugins import create_channel_by_factory, get_channel_factory
from nanobot.config.schema import Config


class ChannelManager:
    """
    Manages chat channels and coordinates message routing.

    Responsibilities:
    - Initialize enabled channels (Telegram, WhatsApp, etc.)
    - Start/stop channels
    - Route outbound messages
    """

    def __init__(self, config: Config, bus: MessageBus):
        self.config = config
        self.bus = bus
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task | None = None

        self._init_channels()

    def _init_channels(self) -> None:
        """Initialize built-in and plugin channels."""
        from nanobot.channels.registry import discover_channel_names, load_channel_class

        groq_key = self.config.providers.groq.api_key

        for modname in discover_channel_names():
            section = getattr(self.config.channels, modname, None)
            if not section or not getattr(section, "enabled", False):
                continue
            try:
                cls = load_channel_class(modname)
                channel = cls(section, self.bus)
                channel.channels_config = self.config.channels
                channel.transcription_api_key = groq_key
                self.channels[modname] = channel
                logger.info("{} channel enabled", cls.display_name)
            except ImportError as e:
                logger.warning("{} channel not available: {}", modname, e)

        for raw_name, section in self.config.channels.plugins.items():
            if not section or not getattr(section, "enabled", False):
                continue

            channel_name = raw_name.replace("-", "_")
            if channel_name in self.channels:
                logger.warning("Ignore channel plugin {}: duplicate channel name", channel_name)
                continue

            factory = get_channel_factory(channel_name)
            if not factory:
                logger.warning("{} channel plugin not available", raw_name)
                continue

            app_config = section.model_dump(by_alias=True)
            try:
                channel = create_channel_by_factory(
                    factory,
                    config=self.config,
                    bus=self.bus,
                    channel_name=channel_name,
                    app_config=app_config,
                )
            except Exception as e:
                logger.warning("{} channel plugin failed to initialize: {}", raw_name, e)
                continue

            if not isinstance(channel, BaseChannel):
                logger.warning(
                    "Ignore channel plugin {}: factory must return BaseChannel",
                    raw_name,
                )
                continue

            channel.transcription_api_key = groq_key
            channel.channels_config = self.config.channels
            self.channels[channel_name] = channel
            logger.info("{} channel enabled", getattr(channel, "display_name", raw_name))

        self._validate_allow_from()

    def _validate_allow_from(self) -> None:
        for name, ch in self.channels.items():
            allow_from = getattr(ch.config, "allow_from", None)
            if allow_from is None and isinstance(ch.config, dict):
                allow_from = ch.config.get("allow_from")
            if allow_from == []:
                raise SystemExit(
                    f'Error: "{name}" has empty allowFrom (denies all). '
                    f'Set ["*"] to allow everyone, or add specific user IDs.'
                )

    async def _start_channel(self, name: str, channel: BaseChannel) -> None:
        """Start a channel and log any exceptions."""
        try:
            await channel.start()
        except Exception as e:
            logger.error("Failed to start channel {}: {}", name, e)

    async def start_all(self) -> None:
        """Start all channels and the outbound dispatcher."""
        if not self.channels:
            logger.warning("No channels enabled")
            return

        # Start outbound dispatcher
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())

        # Start channels
        tasks = []
        for name, channel in self.channels.items():
            logger.info("Starting {} channel...", name)
            tasks.append(asyncio.create_task(self._start_channel(name, channel)))

        # Wait for all to complete (they should run forever)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        """Stop all channels and the dispatcher."""
        logger.info("Stopping all channels...")

        # Stop dispatcher
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        # Stop all channels
        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info("Stopped {} channel", name)
            except Exception as e:
                logger.error("Error stopping {}: {}", name, e)

    async def _dispatch_outbound(self) -> None:
        """Dispatch outbound messages to the appropriate channel."""
        logger.info("Outbound dispatcher started")

        while True:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_outbound(),
                    timeout=1.0
                )

                if msg.metadata.get("_progress"):
                    if msg.metadata.get("_tool_hint") and not self.config.channels.send_tool_hints:
                        continue
                    if not msg.metadata.get("_tool_hint") and not self.config.channels.send_progress:
                        continue

                channel = self.channels.get(msg.channel)
                if channel:
                    try:
                        await channel.send(msg)
                    except Exception as e:
                        logger.error("Error sending to {}: {}", msg.channel, e)
                else:
                    logger.warning("Unknown channel: {}", msg.channel)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_channel(self, name: str) -> BaseChannel | None:
        """Get a channel by name."""
        return self.channels.get(name)

    def get_status(self) -> dict[str, Any]:
        """Get status of all channels."""
        return {
            name: {
                "enabled": True,
                "running": channel.is_running
            }
            for name, channel in self.channels.items()
        }

    @property
    def enabled_channels(self) -> list[str]:
        """Get list of enabled channel names."""
        return list(self.channels.keys())
