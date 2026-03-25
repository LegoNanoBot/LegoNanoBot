"""Base channel interface for chat platforms."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.

    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the nanobot message bus.
    """

    name: str = "base"
    display_name: str = "Base"
    transcription_api_key: str = ""
    receipt_message: str = "我收到了请求了, 当前在执行请求有哪些"

    def __init__(self, config: Any, bus: MessageBus):
        """
        Initialize the channel.

        Args:
            config: Channel-specific configuration.
            bus: The message bus for communication.
        """
        self.config = config
        self.bus = bus
        self._running = False
        self.channels_config: Any | None = None

    async def transcribe_audio(self, file_path: str | Path) -> str:
        """Transcribe an audio file via Groq Whisper. Returns empty string on failure."""
        if not self.transcription_api_key:
            return ""
        try:
            from nanobot.providers.transcription import GroqTranscriptionProvider

            provider = GroqTranscriptionProvider(api_key=self.transcription_api_key)
            return await provider.transcribe(file_path)
        except Exception as e:
            logger.warning("{}: audio transcription failed: {}", self.name, e)
            return ""

    @abstractmethod
    async def start(self) -> None:
        """
        Start the channel and begin listening for messages.

        This should be a long-running async task that:
        1. Connects to the chat platform
        2. Listens for incoming messages
        3. Forwards messages to the bus via _handle_message()
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message through this channel.

        Args:
            msg: The message to send.
        """
        pass

    def is_allowed(self, sender_id: str) -> bool:
        """Check if *sender_id* is permitted.  Empty list → deny all; ``"*"`` → allow all."""
        allow_list = getattr(self.config, "allow_from", [])
        if not allow_list:
            logger.warning("{}: allow_from is empty — all access denied", self.name)
            return False
        if "*" in allow_list:
            return True
        return str(sender_id) in allow_list

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        """
        Handle an incoming message from the chat platform.

        This method checks permissions and forwards to the bus.

        Args:
            sender_id: The sender's identifier.
            chat_id: The chat/channel identifier.
            content: Message text content.
            media: Optional list of media URLs.
            metadata: Optional channel-specific metadata.
            session_key: Optional session key override (e.g. thread-scoped sessions).
        """
        if not self.is_allowed(sender_id):
            logger.warning(
                "Access denied for sender {} on channel {}. "
                "Add them to allowFrom list in config to grant access.",
                sender_id, self.name,
            )
            return

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {},
            session_key_override=session_key,
        )

        if self._should_send_task_receipt(content=content, metadata=metadata):
            await self._send_task_receipt(chat_id=str(chat_id), metadata=metadata)

        await self.bus.publish_inbound(msg)

    def _should_send_task_receipt(
        self,
        *,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> bool:
        """Return True when an inbound message should receive a task receipt."""
        options = self._task_receipt_options()
        if not options["enabled"]:
            return False
        text = (content or "").strip()
        if options["skip_empty"] and not text:
            return False
        if options["skip_commands"] and text.startswith("/"):
            return False
        if options["skip_system"] and (metadata or {}).get("_system_message"):
            return False
        return True

    async def _send_task_receipt(
        self,
        *,
        chat_id: str,
        metadata: dict[str, Any] | None,
    ) -> None:
        """Best-effort immediate receipt to acknowledge task processing."""
        try:
            await self.send(
                OutboundMessage(
                    channel=self.name,
                    chat_id=chat_id,
                    content=self._task_receipt_text(),
                    metadata=self._task_receipt_metadata(metadata),
                )
            )
        except Exception as e:
            logger.debug("{}: task receipt send failed: {}", self.name, e)

    def _task_receipt_text(self) -> str:
        """Return the channel receipt text. Override for channel-specific wording."""
        options = self._task_receipt_options()
        return options["message"] or self.receipt_message

    def _task_receipt_metadata(self, metadata: dict[str, Any] | None) -> dict[str, Any]:
        """Build metadata for receipt replies while preserving channel thread context."""
        receipt_meta = dict(metadata or {})
        receipt_meta["_receipt"] = True
        return receipt_meta

    def _task_receipt_options(self) -> dict[str, Any]:
        """Resolve task receipt options from global defaults and channel overrides."""
        global_cfg = self._config_value(self.channels_config, "task_receipt")
        local_cfg = self._config_value(self.config, "task_receipt")
        options = {
            "enabled": self._nested_config_value(global_cfg, "enabled", True),
            "message": self._nested_config_value(global_cfg, "message", self.receipt_message),
            "skip_commands": self._nested_config_value(global_cfg, "skip_commands", True),
            "skip_empty": self._nested_config_value(global_cfg, "skip_empty", True),
            "skip_system": self._nested_config_value(global_cfg, "skip_system", True),
        }
        for key in options:
            value = self._nested_config_value(local_cfg, key, None)
            if value is not None:
                options[key] = value
        return options

    @staticmethod
    def _config_value(config: Any, key: str) -> Any:
        if config is None:
            return None
        if isinstance(config, dict):
            return config.get(key)
        return getattr(config, key, None)

    @classmethod
    def _nested_config_value(cls, config: Any, key: str, default: Any) -> Any:
        value = cls._config_value(config, key)
        return default if value is None else value

    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return self._running
