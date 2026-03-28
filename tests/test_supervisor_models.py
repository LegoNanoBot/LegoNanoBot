"""Tests for supervisor domain models."""

from nanobot.supervisor.models import (
    HeartbeatRequest,
    Plan,
    PlanStatus,
    PlanStep,
    Task,
    TaskClaimRequest,
    TaskProgress,
    TaskProgressReport,
    TaskResultReport,
    TaskStatus,
    WorkerInfo,
    WorkerRegisterRequest,
    WorkerStatus,
)


def test_worker_status_values():
    assert WorkerStatus.ONLINE == "online"
    assert WorkerStatus.BUSY == "busy"
    assert WorkerStatus.UNHEALTHY == "unhealthy"
    assert WorkerStatus.OFFLINE == "offline"


def test_task_status_values():
    assert TaskStatus.PENDING == "pending"
    assert TaskStatus.ASSIGNED == "assigned"
    assert TaskStatus.RUNNING == "running"
    assert TaskStatus.COMPLETED == "completed"
    assert TaskStatus.FAILED == "failed"
    assert TaskStatus.CANCELLED == "cancelled"


def test_plan_status_values():
    assert PlanStatus.DRAFT == "draft"
    assert PlanStatus.APPROVED == "approved"
    assert PlanStatus.EXECUTING == "executing"
    assert PlanStatus.COMPLETED == "completed"


def test_worker_info_defaults():
    w = WorkerInfo(worker_id="w1", name="test")
    assert w.worker_id == "w1"
    assert w.name == "test"
    assert w.status == WorkerStatus.ONLINE
    assert w.capabilities == []
    assert w.current_task_id is None
    assert w.registered_at > 0
    assert w.last_heartbeat > 0


def test_task_defaults():
    t = Task(instruction="do something")
    assert t.instruction == "do something"
    assert t.status == TaskStatus.PENDING
    assert t.worker_id is None
    assert t.plan_id is None
    assert len(t.task_id) > 0
    assert t.max_iterations == 30
    assert t.retry_count == 0
    assert t.max_retries == 0
    assert t.last_failed_worker_id is None
    assert t.timeout_s == 600.0
    assert t.progress == []


def test_plan_creation():
    steps = [
        PlanStep(index=0, instruction="step 0", label="first"),
        PlanStep(index=1, instruction="step 1", depends_on=[0]),
    ]
    p = Plan(title="Test Plan", goal="test", steps=steps)
    assert p.status == PlanStatus.DRAFT
    assert len(p.steps) == 2
    assert p.steps[1].depends_on == [0]
    assert p.steps[0].max_retries == 0
    assert p.steps[0].task_id is None


def test_task_progress():
    tp = TaskProgress(iteration=3, message="doing stuff")
    assert tp.iteration == 3
    assert tp.message == "doing stuff"
    assert tp.timestamp > 0


def test_protocol_messages():
    reg = WorkerRegisterRequest(worker_id="w1", name="bob", capabilities=["code"])
    assert reg.worker_id == "w1"
    assert reg.capabilities == ["code"]

    hb = HeartbeatRequest(worker_id="w1", current_task_id="t1")
    assert hb.current_task_id == "t1"

    claim = TaskClaimRequest(worker_id="w1")
    assert claim.capabilities == []

    prog = TaskProgressReport(task_id="t1", worker_id="w1", iteration=2, message="half done")
    assert prog.iteration == 2

    result = TaskResultReport(task_id="t1", worker_id="w1", result="done")
    assert result.status == TaskStatus.COMPLETED
