"""Abstract persistence interface for supervisor registry state."""

from __future__ import annotations

from abc import ABC, abstractmethod

from nanobot.supervisor.models import Plan, Task, WorkerInfo


class RegistryStore(ABC):
    """Persistence interface for workers, tasks and plans."""

    async def init(self) -> None:
        """Initialize the backing store if needed."""

    async def close(self) -> None:
        """Close any underlying resources."""

    @abstractmethod
    async def save_worker(self, worker: WorkerInfo) -> None:
        """Persist a worker snapshot."""

    @abstractmethod
    async def load_workers(self) -> list[WorkerInfo]:
        """Load all persisted workers."""

    @abstractmethod
    async def delete_worker(self, worker_id: str) -> None:
        """Delete a worker snapshot."""

    @abstractmethod
    async def save_task(self, task: Task) -> None:
        """Persist a task snapshot."""

    @abstractmethod
    async def load_tasks(self) -> list[Task]:
        """Load all persisted tasks."""

    @abstractmethod
    async def save_plan(self, plan: Plan) -> None:
        """Persist a plan snapshot."""

    @abstractmethod
    async def load_plans(self) -> list[Plan]:
        """Load all persisted plans."""
