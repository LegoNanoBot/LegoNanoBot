from types import SimpleNamespace

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel


class _DummyChannel(BaseChannel):
    name = "dummy"

    def __init__(self, config: SimpleNamespace, bus: MessageBus):
        super().__init__(config, bus)
        self.sent_messages: list[OutboundMessage] = []
        self.fail_on_send = False

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, msg: OutboundMessage) -> None:
        self.sent_messages.append(msg)
        if self.fail_on_send:
            raise RuntimeError("send failed")
        return None


class _SpyBus(MessageBus):
    def __init__(self):
        super().__init__()
        self.inbound_published: list[InboundMessage] = []

    async def publish_inbound(self, msg: InboundMessage) -> None:
        self.inbound_published.append(msg)
        await super().publish_inbound(msg)


def test_is_allowed_requires_exact_match() -> None:
    channel = _DummyChannel(SimpleNamespace(allow_from=["allow@email.com"]), MessageBus())

    assert channel.is_allowed("allow@email.com") is True
    assert channel.is_allowed("attacker|allow@email.com") is False


@pytest.mark.asyncio
async def test_send_task_receipt_uses_channel_send() -> None:
    bus = _SpyBus()
    channel = _DummyChannel(SimpleNamespace(allow_from=["*"]), bus)

    await channel._send_task_receipt(chat_id="chat-1", metadata={"message_id": 42})

    assert len(channel.sent_messages) == 1
    receipt = channel.sent_messages[0]
    assert receipt.content == "我收到了请求了, 当前在执行请求有哪些"
    assert receipt.metadata["_receipt"] is True


@pytest.mark.asyncio
async def test_handle_message_sends_receipt_before_publish() -> None:
    bus = _SpyBus()
    channel = _DummyChannel(SimpleNamespace(allow_from=["*"]), bus)
    call_order: list[str] = []

    async def _fake_receipt(*, chat_id: str, metadata: dict | None) -> None:
        call_order.append("receipt")

    original_publish = bus.publish_inbound

    async def _record_publish(msg: InboundMessage) -> None:
        call_order.append("publish")
        await original_publish(msg)

    channel._send_task_receipt = _fake_receipt  # type: ignore[method-assign]
    bus.publish_inbound = _record_publish  # type: ignore[method-assign]

    await channel._handle_message(
        sender_id="user-1",
        chat_id="chat-1",
        content="请帮我总结今天待办",
        metadata={"message_id": 42},
    )

    assert call_order == ["receipt", "publish"]
    assert bus.inbound_published[0].content == "请帮我总结今天待办"


@pytest.mark.asyncio
async def test_handle_message_skips_receipt_for_command() -> None:
    bus = _SpyBus()
    channel = _DummyChannel(SimpleNamespace(allow_from=["*"]), bus)

    await channel._handle_message(
        sender_id="user-1",
        chat_id="chat-1",
        content="/stop",
    )

    assert channel.sent_messages == []
    assert len(bus.inbound_published) == 1


@pytest.mark.asyncio
async def test_handle_message_receipt_failure_does_not_block_publish() -> None:
    bus = _SpyBus()
    channel = _DummyChannel(SimpleNamespace(allow_from=["*"]), bus)
    channel.fail_on_send = True

    await channel._handle_message(
        sender_id="user-1",
        chat_id="chat-1",
        content="继续执行任务",
    )

    assert len(bus.inbound_published) == 1


@pytest.mark.asyncio
async def test_handle_message_skips_receipt_for_email_channel() -> None:
    bus = _SpyBus()
    channel = _DummyChannel(SimpleNamespace(allow_from=["*"]), bus)
    channel.channels_config = SimpleNamespace(
        task_receipt=SimpleNamespace(enabled=True, skip_empty=True, skip_commands=True, skip_system=True)
    )
    channel.config = SimpleNamespace(
        allow_from=["*"],
        task_receipt=SimpleNamespace(enabled=False),
    )

    await channel._handle_message(
        sender_id="alice@example.com",
        chat_id="alice@example.com",
        content="请帮我处理这封邮件",
    )

    assert channel.sent_messages == []
    assert len(bus.inbound_published) == 1


@pytest.mark.asyncio
async def test_handle_message_respects_global_receipt_disable() -> None:
    bus = _SpyBus()
    channel = _DummyChannel(SimpleNamespace(allow_from=["*"]), bus)
    channel.channels_config = SimpleNamespace(
        task_receipt=SimpleNamespace(enabled=False, skip_empty=True, skip_commands=True, skip_system=True)
    )

    await channel._handle_message(
        sender_id="user-1",
        chat_id="chat-1",
        content="处理这个任务",
    )

    assert channel.sent_messages == []
    assert len(bus.inbound_published) == 1


@pytest.mark.asyncio
async def test_handle_message_respects_channel_receipt_message_override() -> None:
    bus = _SpyBus()
    channel = _DummyChannel(SimpleNamespace(allow_from=["*"]), bus)
    channel.channels_config = SimpleNamespace(
        task_receipt=SimpleNamespace(
            enabled=True,
            message="默认回执",
            skip_empty=True,
            skip_commands=True,
            skip_system=True,
        )
    )
    channel.config = SimpleNamespace(
        allow_from=["*"],
        task_receipt=SimpleNamespace(message="渠道自定义回执"),
    )

    await channel._handle_message(
        sender_id="user-1",
        chat_id="chat-1",
        content="执行任务",
    )

    assert len(channel.sent_messages) == 1
    assert channel.sent_messages[0].content == "渠道自定义回执"
