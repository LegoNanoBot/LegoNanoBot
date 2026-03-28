"""Domain models for the Supervisor Gateway."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class WorkerStatus(str, Enum):
    """Lifecycle states for a worker node."""

    ONLINE = "online"
    BUSY = "busy"
    UNHEALTHY = "unhealthy"
    OFFLINE = "offline"


@dataclass
class WorkerInfo:
    """Snapshot of a worker's registration and health."""

    worker_id: str
    name: str
    status: WorkerStatus = WorkerStatus.ONLINE
    capabilities: list[str] = field(default_factory=list)
    registered_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    current_task_id: str | None = None
    base_url: str | None = None  # worker's own XRay URL (read-only mirror)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    """Task lifecycle states."""

    PENDING = "pending"        # waiting for assignment
    ASSIGNED = "assigned"      # claimed by a worker, not yet started
    RUNNING = "running"        # actively being executed
    PAUSED = "paused"          # human-requested pause
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskProgress:
    """A single progress update from a worker."""

    timestamp: float = field(default_factory=time.time)
    iteration: int = 0
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    """A unit of work assigned to a worker."""

    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    plan_id: str | None = None
    step_index: int | None = None

    # Content
    instruction: str = ""
    label: str = ""
    context: str = ""  # extra context passed to the worker

    # Assignment
    status: TaskStatus = TaskStatus.PENDING
    worker_id: str | None = None
    assigned_at: float | None = None
    retry_count: int = 0
    max_retries: int = 0
    last_failed_worker_id: str | None = None

    # Progress
    progress: list[TaskProgress] = field(default_factory=list)
    result: str | None = None
    error: str | None = None

    # Lifecycle
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    max_iterations: int = 30
    timeout_s: float = 600.0  # 10-minute default

    # Origin
    origin_channel: str = "cli"
    origin_chat_id: str = "direct"
    session_key: str | None = None


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


class PlanStatus(str, Enum):
    """Plan lifecycle states."""

    DRAFT = "draft"              # generated, pending human review
    APPROVED = "approved"        # human approved, ready to execute
    EXECUTING = "executing"      # at least one step running
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PlanStep:
    """A single step in an execution plan."""

    index: int
    instruction: str
    label: str = ""
    depends_on: list[int] = field(default_factory=list)
    max_retries: int = 0
    task_id: str | None = None    # populated when a Task is created
    status: TaskStatus = TaskStatus.PENDING
    result_summary: str | None = None


@dataclass
class Plan:
    """A multi-step execution plan."""

    plan_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    title: str = ""
    goal: str = ""
    status: PlanStatus = PlanStatus.DRAFT

    steps: list[PlanStep] = field(default_factory=list)

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # Origin
    origin_channel: str = "cli"
    origin_chat_id: str = "direct"
    session_key: str | None = None


# ---------------------------------------------------------------------------
# Protocol messages (worker ↔ supervisor)
# ---------------------------------------------------------------------------


@dataclass
class WorkerRegisterRequest:
    """Worker → Supervisor: I'm alive, register me."""

    worker_id: str
    name: str
    capabilities: list[str] = field(default_factory=list)
    base_url: str | None = None


@dataclass
class HeartbeatRequest:
    """Worker → Supervisor: periodic keepalive."""

    worker_id: str
    current_task_id: str | None = None
    status: WorkerStatus = WorkerStatus.ONLINE


@dataclass
class TaskClaimRequest:
    """Worker → Supervisor: give me a task."""

    worker_id: str
    capabilities: list[str] = field(default_factory=list)


@dataclass
class TaskProgressReport:
    """Worker → Supervisor: task progress update."""

    task_id: str
    worker_id: str
    iteration: int = 0
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResultReport:
    """Worker → Supervisor: task finished."""

    task_id: str
    worker_id: str
    status: TaskStatus = TaskStatus.COMPLETED
    result: str = ""
    error: str | None = None
