"""Tests for X-Ray SQLite event store."""

import pytest
import time
from nanobot.xray.events import create_event, EventType
from nanobot.xray.store.sqlite import SQLiteEventStore


@pytest.fixture
async def store():
    """创建内存 SQLite store"""
    s = SQLiteEventStore(":memory:")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_init_creates_tables(store):
    """init() 正确创建表"""
    # 如果 init 没有异常就算通过
    assert store is not None


@pytest.mark.asyncio
async def test_save_and_query_event(store):
    """保存并查询单个事件"""
    event = create_event("run1", EventType.AGENT_START, {"channel": "cli"})
    await store.save_event(event)

    events = await store.query_events(run_id="run1")
    assert len(events) >= 1
    found = events[0]
    assert found["run_id"] == "run1"
    assert found["event_type"] == "agent_start"


@pytest.mark.asyncio
async def test_save_events_batch(store):
    """批量保存事件"""
    events = [
        create_event("run2", EventType.LLM_REQUEST, {"model": "gpt-4"}),
        create_event("run2", EventType.LLM_RESPONSE, {"tokens": 500}),
    ]
    await store.save_events(events)

    results = await store.query_events(run_id="run2")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_query_events_filter_type(store):
    """按事件类型过滤查询"""
    await store.save_event(create_event("run3", EventType.AGENT_START))
    await store.save_event(create_event("run3", EventType.LLM_REQUEST))
    await store.save_event(create_event("run3", EventType.LLM_RESPONSE))

    llm_events = await store.query_events(run_id="run3", event_type=EventType.LLM_REQUEST)
    assert len(llm_events) == 1
    assert llm_events[0]["event_type"] == "llm_request"


@pytest.mark.asyncio
async def test_get_agent_runs(store):
    """获取 agent 运行列表"""
    await store.save_event(create_event("runA", EventType.AGENT_START, {"channel": "telegram"}))
    await store.save_event(create_event("runA", EventType.AGENT_END, {"tools_used": ["web_search"]}))
    await store.save_event(create_event("runB", EventType.AGENT_START, {"channel": "discord"}))

    runs = await store.get_agent_runs()
    assert len(runs) >= 2


@pytest.mark.asyncio
async def test_get_token_usage(store):
    """获取 token 使用统计"""
    await store.save_event(create_event("runT", EventType.LLM_RESPONSE, {
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    }))
    await store.save_event(create_event("runT", EventType.LLM_RESPONSE, {
        "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}
    }))

    usage = await store.get_token_usage(run_id="runT")
    assert isinstance(usage, dict)
    # 验证 token 统计存在
    assert "total_tokens" in usage
    assert usage["total_tokens"] == 450
    assert usage["total_prompt_tokens"] == 300
    assert usage["total_completion_tokens"] == 150


@pytest.mark.asyncio
async def test_cleanup(store):
    """清理旧数据"""
    old_event = create_event("old_run", EventType.AGENT_START)
    old_event.timestamp = time.time() - 100000  # 很久以前
    await store.save_event(old_event)

    new_event = create_event("new_run", EventType.AGENT_START)
    await store.save_event(new_event)

    deleted = await store.cleanup(time.time() - 50000)
    assert deleted >= 1

    # 新事件应该还在
    remaining = await store.query_events(run_id="new_run")
    assert len(remaining) >= 1


@pytest.mark.asyncio
async def test_get_run_detail(store):
    """获取单个运行的详细信息"""
    await store.save_event(create_event("runD", EventType.AGENT_START, {"channel": "cli"}))
    await store.save_event(create_event("runD", EventType.LLM_REQUEST, {"model": "gpt-4"}))
    await store.save_event(create_event("runD", EventType.LLM_RESPONSE, {
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    }))
    await store.save_event(create_event("runD", EventType.AGENT_END, {}))

    detail = await store.get_run_detail("runD")
    assert detail is not None
    assert detail["run_id"] == "runD"
    assert detail["status"] == "completed"
    assert detail["channel"] == "cli"


@pytest.mark.asyncio
async def test_get_run_detail_not_found(store):
    """获取不存在的运行返回 None"""
    detail = await store.get_run_detail("nonexistent")
    assert detail is None


@pytest.mark.asyncio
async def test_save_events_empty_list(store):
    """批量保存空列表不报错"""
    await store.save_events([])  # 应该不抛出异常


@pytest.mark.asyncio
async def test_retain_recent_runs_only():
    """按 run 维度滚动保留最近 N 组事件"""
    s = SQLiteEventStore(":memory:", max_runs=2)
    await s.init()
    try:
        await s.save_event(create_event("run1", EventType.AGENT_START))
        await s.save_event(create_event("run2", EventType.AGENT_START))
        await s.save_event(create_event("run3", EventType.AGENT_START))

        run1_events = await s.query_events(run_id="run1")
        run2_events = await s.query_events(run_id="run2")
        run3_events = await s.query_events(run_id="run3")

        assert len(run1_events) == 0
        assert len(run2_events) >= 1
        assert len(run3_events) >= 1
    finally:
        await s.close()
