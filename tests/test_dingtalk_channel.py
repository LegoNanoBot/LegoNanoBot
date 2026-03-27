import asyncio
from types import SimpleNamespace

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
import nanobot.channels.dingtalk as dingtalk_module
from nanobot.channels.dingtalk import DingTalkChannel, NanobotDingTalkHandler
from nanobot.config.schema import DingTalkConfig


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        json_body: dict | None = None,
        text: str = "{}",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_body = json_body or {}
        self.text = text
        self.headers = headers or {"content-type": "application/json"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http status: {self.status_code}")

    def json(self) -> dict:
        return self._json_body


class _FakeHttp:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def post(self, url: str, json=None, headers=None, files=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "files": files})
        if "media/upload" in url:
            return _FakeResponse(json_body={"errcode": 0, "media_id": "mid123"})
        if "oauth2/accessToken" in url:
            return _FakeResponse(json_body={"accessToken": "token", "expireIn": 7200})
        return _FakeResponse()


@pytest.mark.asyncio
async def test_group_message_keeps_sender_id_and_routes_chat_id() -> None:
    config = DingTalkConfig(client_id="app", client_secret="secret", allow_from=["user1"])
    bus = MessageBus()
    channel = DingTalkChannel(config, bus)

    await channel._on_message(
        "hello",
        sender_id="user1",
        sender_name="Alice",
        conversation_type="2",
        conversation_id="conv123",
    )

    msg = await bus.consume_inbound()
    assert msg.sender_id == "user1"
    assert msg.chat_id == "group:conv123"
    assert msg.metadata["conversation_type"] == "2"


@pytest.mark.asyncio
async def test_group_send_uses_group_messages_api() -> None:
    config = DingTalkConfig(client_id="app", client_secret="secret", allow_from=["*"])
    channel = DingTalkChannel(config, MessageBus())
    channel._http = _FakeHttp()

    ok = await channel._send_batch_message(
        "token",
        "group:conv123",
        "sampleMarkdown",
        {"text": "hello", "title": "Nanobot Reply"},
    )

    assert ok is True
    call = channel._http.calls[0]
    assert call["url"] == "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
    assert call["json"]["openConversationId"] == "conv123"
    assert call["json"]["msgKey"] == "sampleMarkdown"


@pytest.mark.asyncio
async def test_handler_uses_voice_recognition_text_when_text_is_empty(monkeypatch) -> None:
    bus = MessageBus()
    channel = DingTalkChannel(
        DingTalkConfig(client_id="app", client_secret="secret", allow_from=["user1"]),
        bus,
    )
    handler = NanobotDingTalkHandler(channel)

    class _FakeChatbotMessage:
        text = None
        extensions = {"content": {"recognition": "voice transcript"}}
        sender_staff_id = "user1"
        sender_id = "fallback-user"
        sender_nick = "Alice"
        message_type = "audio"

        @staticmethod
        def from_dict(_data):
            return _FakeChatbotMessage()

    monkeypatch.setattr(dingtalk_module, "ChatbotMessage", _FakeChatbotMessage)
    monkeypatch.setattr(dingtalk_module, "AckMessage", SimpleNamespace(STATUS_OK="OK"))

    status, body = await handler.process(
        SimpleNamespace(
            data={
                "conversationType": "2",
                "conversationId": "conv123",
                "text": {"content": ""},
            }
        )
    )

    await asyncio.gather(*list(channel._background_tasks))
    msg = await bus.consume_inbound()

    assert (status, body) == ("OK", "OK")
    assert msg.content == "voice transcript"
    assert msg.sender_id == "user1"
    assert msg.chat_id == "group:conv123"


@pytest.mark.asyncio
async def test_handler_extracts_rich_text_when_plain_text_is_empty(monkeypatch) -> None:
    bus = MessageBus()
    channel = DingTalkChannel(
        DingTalkConfig(client_id="app", client_secret="secret", allow_from=["user1"]),
        bus,
    )
    handler = NanobotDingTalkHandler(channel)

    class _FakeChatbotMessage:
        text = None
        extensions = {}
        sender_staff_id = "user1"
        sender_id = "fallback-user"
        sender_nick = "Alice"
        message_type = "richText"

        @staticmethod
        def from_dict(_data):
            return _FakeChatbotMessage()

    monkeypatch.setattr(dingtalk_module, "ChatbotMessage", _FakeChatbotMessage)
    monkeypatch.setattr(dingtalk_module, "AckMessage", SimpleNamespace(STATUS_OK="OK"))

    status, body = await handler.process(
        SimpleNamespace(
            data={
                "conversationType": "2",
                "conversationId": "conv123",
                "text": {"content": ""},
                "content": {
                    "richText": [
                        {"text": "Line 1"},
                        {"children": [{"text": "Line 2"}]},
                    ]
                },
            }
        )
    )

    await asyncio.gather(*list(channel._background_tasks))
    msg = await bus.consume_inbound()

    assert (status, body) == ("OK", "OK")
    assert msg.content == "Line 1\nLine 2"
    assert msg.metadata["message_type"] == "richText"


@pytest.mark.asyncio
async def test_handler_accepts_media_only_message(monkeypatch) -> None:
    bus = MessageBus()
    channel = DingTalkChannel(
        DingTalkConfig(client_id="app", client_secret="secret", allow_from=["user1"]),
        bus,
    )
    handler = NanobotDingTalkHandler(channel)

    class _FakeChatbotMessage:
        text = None
        extensions = {}
        sender_staff_id = "user1"
        sender_id = "fallback-user"
        sender_nick = "Alice"
        message_type = "picture"

        @staticmethod
        def from_dict(_data):
            return _FakeChatbotMessage()

    monkeypatch.setattr(dingtalk_module, "ChatbotMessage", _FakeChatbotMessage)
    monkeypatch.setattr(dingtalk_module, "AckMessage", SimpleNamespace(STATUS_OK="OK"))

    status, body = await handler.process(
        SimpleNamespace(
            data={
                "conversationType": "2",
                "conversationId": "conv123",
                "text": {"content": ""},
                "attachments": [{"downloadUrl": "https://example.com/image.jpg"}],
            }
        )
    )

    await asyncio.gather(*list(channel._background_tasks))
    msg = await bus.consume_inbound()

    assert (status, body) == ("OK", "OK")
    assert msg.content == "[Attachment message]"
    assert msg.media == ["https://example.com/image.jpg"]


@pytest.mark.asyncio
async def test_send_voice_uses_sample_audio_msg_key(monkeypatch, tmp_path) -> None:
    config = DingTalkConfig(client_id="app", client_secret="secret", allow_from=["*"])
    channel = DingTalkChannel(config, MessageBus())
    channel._http = _FakeHttp()

    voice_file = tmp_path / "voice.amr"
    voice_file.write_bytes(b"fake-audio")

    async def _fake_token() -> str:
        return "token"

    monkeypatch.setattr(channel, "_get_access_token", _fake_token)

    await channel.send(
        OutboundMessage(
            channel="dingtalk",
            chat_id="group:conv123",
            content="",
            media=[str(voice_file)],
        )
    )

    send_calls = [
        c
        for c in channel._http.calls
        if c["url"].startswith("https://api.dingtalk.com/v1.0/robot/")
    ]
    assert any(call["json"]["msgKey"] in ("sampleAudio", "sampleVoice") for call in send_calls)


@pytest.mark.asyncio
async def test_send_rich_text_from_metadata() -> None:
    config = DingTalkConfig(client_id="app", client_secret="secret", allow_from=["*"])
    channel = DingTalkChannel(config, MessageBus())
    channel._http = _FakeHttp()

    await channel.send(
        OutboundMessage(
            channel="dingtalk",
            chat_id="group:conv123",
            content="",
            metadata={"dingtalk_rich_text": [{"text": "hello rich"}]},
        )
    )

    send_calls = [
        c
        for c in channel._http.calls
        if c["url"].startswith("https://api.dingtalk.com/v1.0/robot/")
    ]
    assert send_calls[0]["json"]["msgKey"] == "sampleRichText"
