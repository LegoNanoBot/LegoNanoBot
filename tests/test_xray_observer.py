"""Tests for X-Ray observer and SSE hub."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from nanobot.xray.events import create_event, EventType
from nanobot.xray.observer import XRayObserver
from nanobot.xray.sse import SSEHub
from nanobot.xray.collector import EventCollector


def test_sse_hub_subscribe_unsubscribe():
    """SSEHub 订阅和取消订阅"""
    hub = SSEHub()
    assert hub.subscriber_count == 0

    client_id, queue = hub.subscribe()
    assert hub.subscriber_count == 1
    assert isinstance(queue, asyncio.Queue)

    hub.unsubscribe(client_id)
    assert hub.subscriber_count == 0


@pytest.mark.asyncio
async def test_sse_hub_broadcast():
    """SSEHub 广播事件到所有订阅者"""
    hub = SSEHub()
    _, q1 = hub.subscribe()
    _, q2 = hub.subscribe()

    event = create_event("run1", EventType.AGENT_START)
    await hub.broadcast(event)

    assert not q1.empty()
    assert not q2.empty()

    received1 = await q1.get()
    assert received1.run_id == "run1"


def test_sse_hub_unsubscribe_unknown():
    """SSEHub 取消订阅不存在的客户端不报错"""
    hub = SSEHub()
    hub.unsubscribe("nonexistent")  # 应该不抛出异常


def test_collector_buffer():
    """EventCollector 环形缓冲"""
    collector = EventCollector(max_buffer=5)
    # 确认缓冲区初始为空
    recent = collector.get_recent(limit=10)
    assert len(recent) == 0


@pytest.mark.asyncio
async def test_collector_collect():
    """EventCollector 收集事件"""
    collector = EventCollector(max_buffer=10)
    event = create_event("run1", EventType.AGENT_START, {"channel": "cli"})
    await collector.collect(event)

    recent = collector.get_recent(limit=5)
    assert len(recent) == 1
    assert recent[0].run_id == "run1"


@pytest.mark.asyncio
async def test_collector_active_runs():
    """EventCollector 跟踪活跃运行"""
    collector = EventCollector()

    await collector.collect(create_event("runX", EventType.AGENT_START, {"channel": "telegram"}))
    active = collector.get_active_runs()
    assert "runX" in active
    assert active["runX"]["status"] == "running"

    await collector.collect(create_event("runX", EventType.AGENT_END, {}))
    active = collector.get_active_runs()
    assert "runX" not in active  # completed runs are removed to prevent memory leak


@pytest.mark.asyncio
async def test_collector_error_status():
    """EventCollector 跟踪错误状态"""
    collector = EventCollector()

    await collector.collect(create_event("runE", EventType.AGENT_START, {"channel": "cli"}))
    await collector.collect(create_event("runE", EventType.ERROR, {"error": "test error"}))

    active = collector.get_active_runs()
    assert active["runE"]["status"] == "error"


@pytest.mark.asyncio
async def test_collector_buffer_overflow():
    """EventCollector 缓冲区溢出时保留最新事件"""
    collector = EventCollector(max_buffer=3)

    for i in range(5):
        await collector.collect(create_event(f"run{i}", EventType.AGENT_START))

    recent = collector.get_recent(limit=10)
    # 只保留最后3个
    assert len(recent) == 3


@pytest.mark.asyncio
async def test_collector_with_sse_hub():
    """EventCollector 配合 SSEHub 广播"""
    collector = EventCollector()
    hub = SSEHub()
    collector.set_sse_hub(hub)

    _, queue = hub.subscribe()
    event = create_event("run1", EventType.AGENT_START, {"channel": "cli"})
    await collector.collect(event)

    # 事件应该被广播到 SSE 订阅者
    assert not queue.empty()
    received = await queue.get()
    assert received.run_id == "run1"


@pytest.mark.asyncio
async def test_observer_emit():
    """XRayObserver emit 方法"""
    collector = EventCollector()
    observer = XRayObserver(collector)

    await observer.emit("run1", EventType.AGENT_START, {"test": True})

    recent = collector.get_recent(limit=5)
    assert len(recent) == 1


@pytest.mark.asyncio
async def test_observer_on_event():
    """XRayObserver on_event 方法"""
    collector = EventCollector()
    observer = XRayObserver(collector)

    event = create_event("run2", EventType.LLM_REQUEST, {"model": "gpt-4"})
    await observer.on_event(event)

    recent = collector.get_recent(limit=5)
    assert len(recent) == 1
    assert recent[0].event_type == EventType.LLM_REQUEST


@pytest.mark.asyncio
async def test_observer_exception_safety():
    """Observer 异常不抛出"""
    # 创建一个会抛异常的 collector mock
    collector = MagicMock()
    collector.collect = AsyncMock(side_effect=RuntimeError("test error"))

    observer = XRayObserver(collector)
    # 不应抛出异常
    await observer.emit("run1", EventType.ERROR, {"error": "test"})
