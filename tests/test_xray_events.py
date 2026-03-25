"""Tests for X-Ray event model."""

import pytest
from nanobot.xray.events import XRayEvent, EventType, create_event, event_to_dict


def test_event_type_constants():
    """所有事件类型常量存在且是字符串"""
    assert EventType.AGENT_START == "agent_start"
    assert EventType.AGENT_END == "agent_end"
    assert EventType.LLM_REQUEST == "llm_request"
    assert EventType.LLM_RESPONSE == "llm_response"
    assert EventType.TOOL_CALL_START == "tool_call_start"
    assert EventType.TOOL_CALL_END == "tool_call_end"
    assert EventType.SUBAGENT_SPAWN == "subagent_spawn"
    assert EventType.SUBAGENT_DONE == "subagent_done"
    assert EventType.MESSAGE_IN == "message_in"
    assert EventType.MESSAGE_OUT == "message_out"
    assert EventType.MEMORY_CONSOLIDATE == "memory_consolidate"
    assert EventType.ERROR == "error"


def test_create_event():
    """create_event 工厂函数生成正确的事件"""
    event = create_event("run123", EventType.AGENT_START, {"channel": "telegram"})
    assert isinstance(event, XRayEvent)
    assert event.run_id == "run123"
    assert event.event_type == EventType.AGENT_START
    assert event.data["channel"] == "telegram"
    assert len(event.id) > 0
    assert event.timestamp > 0


def test_create_event_default_data():
    """create_event 不传 data 时默认空 dict"""
    event = create_event("run123", EventType.ERROR)
    assert event.data == {} or event.data is not None


def test_event_to_dict():
    """event_to_dict 序列化正确"""
    event = create_event("run456", EventType.LLM_RESPONSE, {"tokens": 100})
    d = event_to_dict(event)
    assert isinstance(d, dict)
    assert d["run_id"] == "run456"
    assert d["event_type"] == "llm_response"
    assert d["data"]["tokens"] == 100
    assert "id" in d
    assert "timestamp" in d


def test_create_event_unique_ids():
    """每次 create_event 生成唯一 ID"""
    e1 = create_event("run1", EventType.AGENT_START)
    e2 = create_event("run1", EventType.AGENT_START)
    assert e1.id != e2.id
