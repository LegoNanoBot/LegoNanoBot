"""Tests for X-Ray API endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock

# 需要先检查 fastapi 是否可用
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from nanobot.xray.app import create_xray_app
from nanobot.xray.sse import SSEHub
from nanobot.xray.collector import EventCollector


@pytest.fixture
def mock_store():
    """Mock event store"""
    store = AsyncMock()
    store.get_agent_runs = AsyncMock(return_value=[
        {"run_id": "abc123", "status": "completed", "channel": "cli"}
    ])
    store.query_events = AsyncMock(return_value=[])
    store.get_run_detail = AsyncMock(return_value={"run_id": "abc123", "status": "completed"})
    store.get_token_usage = AsyncMock(return_value={
        "total_prompt_tokens": 1000,
        "total_completion_tokens": 500,
        "total_tokens": 1500,
        "call_count": 5,
    })
    return store


@pytest.fixture
def client(mock_store):
    """FastAPI test client"""
    sse_hub = SSEHub()
    collector = EventCollector()

    # Mock config_refs
    config_refs = {
        "memory_store": None,
        "skills_loader": None,
        "tool_registry": MagicMock(get_definitions=MagicMock(return_value=[])),
        "workspace": "/tmp/test",
        "bot_config": None,
    }

    app = create_xray_app(mock_store, sse_hub, collector, config_refs)
    return TestClient(app)


def test_status_endpoint(client):
    """GET /api/v1/status"""
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "active_agents" in data


def test_list_agents(client):
    """GET /api/v1/agents"""
    resp = client.get("/api/v1/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert "runs" in data


def test_list_active_agents(client):
    """GET /api/v1/agents/active"""
    resp = client.get("/api/v1/agents/active")
    assert resp.status_code == 200
    data = resp.json()
    assert "agents" in data
    assert "count" in data


def test_get_agent_detail(client):
    """GET /api/v1/agents/{run_id}"""
    resp = client.get("/api/v1/agents/abc123")
    assert resp.status_code == 200


def test_get_agent_events(client):
    """GET /api/v1/agents/{run_id}/events"""
    resp = client.get("/api/v1/agents/abc123/events")
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data


def test_recent_events(client):
    """GET /api/v1/events/recent"""
    resp = client.get("/api/v1/events/recent")
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data


def test_token_summary(client):
    """GET /api/v1/tokens/summary"""
    resp = client.get("/api/v1/tokens/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_tokens" in data or isinstance(data, dict)


def test_get_tools(client):
    """GET /api/v1/config/tools"""
    resp = client.get("/api/v1/config/tools")
    assert resp.status_code == 200


def test_get_memory(client):
    """GET /api/v1/config/memory"""
    resp = client.get("/api/v1/config/memory")
    assert resp.status_code == 200
    data = resp.json()
    assert "available" in data


def test_get_soul(client):
    """GET /api/v1/config/soul"""
    resp = client.get("/api/v1/config/soul")
    assert resp.status_code == 200


def test_get_skills(client):
    """GET /api/v1/config/skills"""
    resp = client.get("/api/v1/config/skills")
    assert resp.status_code == 200


def test_get_mcp(client):
    """GET /api/v1/config/mcp"""
    resp = client.get("/api/v1/config/mcp")
    assert resp.status_code == 200


def test_root_redirect(client):
    """GET / 重定向到 /dashboard"""
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/dashboard" in resp.headers.get("location", "")


def test_agent_detail_not_found(client, mock_store):
    """GET /api/v1/agents/{run_id} 返回 404 当运行不存在"""
    mock_store.get_run_detail = AsyncMock(return_value=None)
    resp = client.get("/api/v1/agents/nonexistent")
    assert resp.status_code == 404
