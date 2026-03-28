"""Tests for supervisor API endpoints."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from nanobot.supervisor.app import create_supervisor_app
from nanobot.supervisor.registry import WorkerRegistry


@pytest.fixture
def registry():
    return WorkerRegistry(heartbeat_timeout_s=60.0)


@pytest.fixture
def client(registry):
    app = create_supervisor_app(worker_registry=registry)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Worker endpoints
# ---------------------------------------------------------------------------


def test_register_worker(client):
    resp = client.post("/api/v1/supervisor/workers/register", json={
        "worker_id": "w1",
        "name": "test-worker",
        "capabilities": ["code"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["worker"]["worker_id"] == "w1"
    assert data["worker"]["status"] == "online"


def test_list_workers_empty(client):
    resp = client.get("/api/v1/supervisor/workers")
    assert resp.status_code == 200
    assert resp.json()["workers"] == []


def test_list_workers(client):
    client.post("/api/v1/supervisor/workers/register", json={
        "worker_id": "w1", "name": "a",
    })
    client.post("/api/v1/supervisor/workers/register", json={
        "worker_id": "w2", "name": "b",
    })
    resp = client.get("/api/v1/supervisor/workers")
    assert len(resp.json()["workers"]) == 2


def test_heartbeat(client):
    client.post("/api/v1/supervisor/workers/register", json={
        "worker_id": "w1", "name": "test",
    })
    resp = client.post("/api/v1/supervisor/workers/w1/heartbeat", json={
        "current_task_id": "t1", "status": "busy",
    })
    assert resp.status_code == 200
    assert resp.json()["worker"]["status"] == "busy"


def test_heartbeat_unknown(client):
    resp = client.post("/api/v1/supervisor/workers/unknown/heartbeat", json={})
    assert resp.status_code == 404


def test_get_worker(client):
    client.post("/api/v1/supervisor/workers/register", json={
        "worker_id": "w1", "name": "test",
    })
    resp = client.get("/api/v1/supervisor/workers/w1")
    assert resp.status_code == 200
    assert resp.json()["worker"]["name"] == "test"


def test_unregister_worker(client):
    client.post("/api/v1/supervisor/workers/register", json={
        "worker_id": "w1", "name": "test",
    })
    resp = client.delete("/api/v1/supervisor/workers/w1")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    resp = client.get("/api/v1/supervisor/workers")
    assert len(resp.json()["workers"]) == 0


# ---------------------------------------------------------------------------
# Task endpoints
# ---------------------------------------------------------------------------


def test_create_task(client):
    resp = client.post("/api/v1/supervisor/tasks", json={
        "instruction": "write tests",
        "label": "testing",
        "max_retries": 2,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["task"]["instruction"] == "write tests"
    assert data["task"]["status"] == "pending"
    assert data["task"]["max_retries"] == 2
    assert data["task"]["retry_count"] == 0


def test_create_task_uses_registry_defaults_when_timeout_and_iterations_omitted():
    registry = WorkerRegistry(
        heartbeat_timeout_s=60.0,
        task_default_timeout_s=321.0,
        task_default_max_iterations=12,
    )
    client = TestClient(create_supervisor_app(worker_registry=registry))

    resp = client.post("/api/v1/supervisor/tasks", json={
        "instruction": "write tests",
    })

    assert resp.status_code == 200
    task = resp.json()["task"]
    assert task["timeout_s"] == 321.0
    assert task["max_iterations"] == 12


def test_create_task_explicit_values_override_registry_defaults():
    registry = WorkerRegistry(
        heartbeat_timeout_s=60.0,
        task_default_timeout_s=321.0,
        task_default_max_iterations=12,
    )
    client = TestClient(create_supervisor_app(worker_registry=registry))

    resp = client.post("/api/v1/supervisor/tasks", json={
        "instruction": "write tests",
        "timeout_s": 45.0,
        "max_iterations": 7,
    })

    assert resp.status_code == 200
    task = resp.json()["task"]
    assert task["timeout_s"] == 45.0
    assert task["max_iterations"] == 7


def test_claim_task(client):
    client.post("/api/v1/supervisor/workers/register", json={
        "worker_id": "w1", "name": "test",
    })
    client.post("/api/v1/supervisor/tasks", json={
        "instruction": "do stuff",
    })
    resp = client.post("/api/v1/supervisor/tasks/claim", json={
        "worker_id": "w1",
    })
    assert resp.status_code == 200
    task = resp.json()["task"]
    assert task is not None
    assert task["status"] == "assigned"
    assert task["worker_id"] == "w1"


def test_claim_no_tasks(client):
    client.post("/api/v1/supervisor/workers/register", json={
        "worker_id": "w1", "name": "test",
    })
    resp = client.post("/api/v1/supervisor/tasks/claim", json={
        "worker_id": "w1",
    })
    assert resp.status_code == 200
    assert resp.json()["task"] is None


def test_report_progress(client):
    client.post("/api/v1/supervisor/workers/register", json={
        "worker_id": "w1", "name": "test",
    })
    create_resp = client.post("/api/v1/supervisor/tasks", json={
        "instruction": "do stuff",
    })
    task_id = create_resp.json()["task"]["task_id"]
    client.post("/api/v1/supervisor/tasks/claim", json={"worker_id": "w1"})

    resp = client.post(f"/api/v1/supervisor/tasks/{task_id}/progress", json={
        "worker_id": "w1",
        "iteration": 1,
        "message": "halfway there",
    })
    assert resp.status_code == 200
    assert len(resp.json()["task"]["progress"]) == 1


def test_report_result(client):
    client.post("/api/v1/supervisor/workers/register", json={
        "worker_id": "w1", "name": "test",
    })
    create_resp = client.post("/api/v1/supervisor/tasks", json={
        "instruction": "do stuff",
    })
    task_id = create_resp.json()["task"]["task_id"]
    client.post("/api/v1/supervisor/tasks/claim", json={"worker_id": "w1"})

    resp = client.post(f"/api/v1/supervisor/tasks/{task_id}/result", json={
        "worker_id": "w1",
        "status": "completed",
        "result": "all done",
    })
    assert resp.status_code == 200
    assert resp.json()["task"]["status"] == "completed"
    assert resp.json()["task"]["result"] == "all done"


def test_report_failed_result_requeues_when_retry_budget_remains(client):
    client.post("/api/v1/supervisor/workers/register", json={
        "worker_id": "w1", "name": "test",
    })
    create_resp = client.post("/api/v1/supervisor/tasks", json={
        "instruction": "do stuff",
        "max_retries": 1,
    })
    task_id = create_resp.json()["task"]["task_id"]
    client.post("/api/v1/supervisor/tasks/claim", json={"worker_id": "w1"})

    resp = client.post(f"/api/v1/supervisor/tasks/{task_id}/result", json={
        "worker_id": "w1",
        "status": "failed",
        "error": "transient",
    })
    assert resp.status_code == 200
    assert resp.json()["task"]["status"] == "pending"
    assert resp.json()["task"]["retry_count"] == 1
    assert resp.json()["task"]["last_failed_worker_id"] == "w1"


def test_cancel_task(client):
    create_resp = client.post("/api/v1/supervisor/tasks", json={
        "instruction": "do stuff",
    })
    task_id = create_resp.json()["task"]["task_id"]

    resp = client.post(f"/api/v1/supervisor/tasks/{task_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["task"]["status"] == "cancelled"


def test_list_tasks(client):
    client.post("/api/v1/supervisor/tasks", json={"instruction": "a"})
    client.post("/api/v1/supervisor/tasks", json={"instruction": "b"})

    resp = client.get("/api/v1/supervisor/tasks")
    assert resp.status_code == 200
    assert len(resp.json()["tasks"]) == 2


def test_list_tasks_filter_status(client):
    client.post("/api/v1/supervisor/tasks", json={"instruction": "a"})

    resp = client.get("/api/v1/supervisor/tasks?status=pending")
    assert len(resp.json()["tasks"]) == 1

    resp = client.get("/api/v1/supervisor/tasks?status=completed")
    assert len(resp.json()["tasks"]) == 0


def test_get_task(client):
    create_resp = client.post("/api/v1/supervisor/tasks", json={"instruction": "a"})
    task_id = create_resp.json()["task"]["task_id"]

    resp = client.get(f"/api/v1/supervisor/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["task"]["task_id"] == task_id


def test_get_task_not_found(client):
    resp = client.get("/api/v1/supervisor/tasks/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Plan endpoints
# ---------------------------------------------------------------------------


def test_create_plan(client):
    resp = client.post("/api/v1/supervisor/plans", json={
        "title": "Test Plan",
        "goal": "test",
        "steps": [
            {"index": 0, "instruction": "step 0", "label": "first", "max_retries": 2},
            {"index": 1, "instruction": "step 1", "depends_on": [0]},
        ],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["plan"]["status"] == "draft"
    assert len(data["plan"]["steps"]) == 2
    assert data["plan"]["steps"][0]["max_retries"] == 2


def test_approve_plan(client):
    create_resp = client.post("/api/v1/supervisor/plans", json={
        "title": "Test Plan",
        "goal": "test",
        "steps": [
            {"index": 0, "instruction": "step 0"},
        ],
    })
    plan_id = create_resp.json()["plan"]["plan_id"]

    resp = client.post(f"/api/v1/supervisor/plans/{plan_id}/approve")
    assert resp.status_code == 200
    plan = resp.json()["plan"]
    assert plan["status"] == "executing"
    # Step 0 should have a task
    assert plan["steps"][0]["task_id"] is not None


def test_cancel_plan(client):
    create_resp = client.post("/api/v1/supervisor/plans", json={
        "title": "Test Plan",
        "goal": "test",
        "steps": [{"index": 0, "instruction": "step 0"}],
    })
    plan_id = create_resp.json()["plan"]["plan_id"]

    resp = client.post(f"/api/v1/supervisor/plans/{plan_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["plan"]["status"] == "cancelled"


def test_list_plans(client):
    client.post("/api/v1/supervisor/plans", json={
        "title": "A", "goal": "test", "steps": [{"index": 0, "instruction": "s"}],
    })
    resp = client.get("/api/v1/supervisor/plans")
    assert resp.status_code == 200
    assert len(resp.json()["plans"]) == 1


def test_get_plan(client):
    create_resp = client.post("/api/v1/supervisor/plans", json={
        "title": "A", "goal": "test", "steps": [{"index": 0, "instruction": "s"}],
    })
    plan_id = create_resp.json()["plan"]["plan_id"]

    resp = client.get(f"/api/v1/supervisor/plans/{plan_id}")
    assert resp.status_code == 200
    assert resp.json()["plan"]["plan_id"] == plan_id


def test_get_plan_not_found(client):
    resp = client.get("/api/v1/supervisor/plans/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Integration: full plan lifecycle
# ---------------------------------------------------------------------------


def test_plan_full_lifecycle(client):
    """Test: create plan → approve → claim task → report result → plan completes."""
    # Register worker
    client.post("/api/v1/supervisor/workers/register", json={
        "worker_id": "w1", "name": "test",
    })

    # Create plan with two sequential steps
    create_resp = client.post("/api/v1/supervisor/plans", json={
        "title": "Full Test",
        "goal": "e2e",
        "steps": [
            {"index": 0, "instruction": "step 0", "label": "first"},
            {"index": 1, "instruction": "step 1", "label": "second", "depends_on": [0]},
        ],
    })
    plan_id = create_resp.json()["plan"]["plan_id"]

    # Approve
    client.post(f"/api/v1/supervisor/plans/{plan_id}/approve")

    # Claim step 0's task
    claim_resp = client.post("/api/v1/supervisor/tasks/claim", json={"worker_id": "w1"})
    task_0 = claim_resp.json()["task"]
    assert task_0 is not None

    # Complete step 0
    client.post(f"/api/v1/supervisor/tasks/{task_0['task_id']}/result", json={
        "worker_id": "w1",
        "status": "completed",
        "result": "step 0 done",
    })

    # Now step 1 should have a task
    plan_resp = client.get(f"/api/v1/supervisor/plans/{plan_id}")
    plan_data = plan_resp.json()["plan"]
    assert plan_data["steps"][1]["task_id"] is not None

    # Claim and complete step 1
    claim_resp2 = client.post("/api/v1/supervisor/tasks/claim", json={"worker_id": "w1"})
    task_1 = claim_resp2.json()["task"]
    assert task_1 is not None

    client.post(f"/api/v1/supervisor/tasks/{task_1['task_id']}/result", json={
        "worker_id": "w1",
        "status": "completed",
        "result": "step 1 done",
    })

    # Plan should be completed
    final_plan = client.get(f"/api/v1/supervisor/plans/{plan_id}").json()["plan"]
    assert final_plan["status"] == "completed"
