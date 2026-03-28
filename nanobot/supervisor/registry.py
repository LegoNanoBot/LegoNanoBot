"""Worker registry — tracks connected workers, their tasks and health."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.supervisor.models import (
    HeartbeatRequest,
    Plan,
    PlanStatus,
    PlanStep,
    Task,
    TaskClaimRequest,
    TaskProgressReport,
    TaskResultReport,
    TaskStatus,
    WorkerInfo,
    WorkerRegisterRequest,
    WorkerStatus,
)
from nanobot.supervisor.event_sink import EventSink, SupervisorEventType, XRayCollectorEventSink
from nanobot.supervisor.store.base import RegistryStore

if TYPE_CHECKING:
    from nanobot.xray.collector import EventCollector


class WorkerRegistry:
    """In-memory registry of workers, tasks and plans.

    Thread-safety: all mutations go through a single asyncio.Lock so that
    concurrent FastAPI handlers don't race.
    """

    def __init__(
        self,
        heartbeat_timeout_s: float = 120.0,
        task_default_timeout_s: float = 600.0,
        task_default_max_iterations: int = 30,
        store: RegistryStore | None = None,
        event_sink: "EventSink | None" = None,
        collector: "EventCollector | None" = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._workers: dict[str, WorkerInfo] = {}
        self._tasks: dict[str, Task] = {}
        self._plans: dict[str, Plan] = {}
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.task_default_timeout_s = task_default_timeout_s
        self.task_default_max_iterations = task_default_max_iterations
        self._store = store
        self._event_sink = event_sink or (XRayCollectorEventSink(collector) if collector is not None else None)

    async def restore(self) -> None:
        if self._store is None:
            return

        workers = await self._store.load_workers()
        tasks = await self._store.load_tasks()
        plans = await self._store.load_plans()

        async with self._lock:
            self._workers = {worker.worker_id: worker for worker in workers}
            self._tasks = {task.task_id: task for task in tasks}
            self._plans = {plan.plan_id: plan for plan in plans}

            now = time.time()
            for worker in self._workers.values():
                worker.status = WorkerStatus.OFFLINE
                worker.current_task_id = None
                worker.last_heartbeat = now

            for task in self._tasks.values():
                if task.status in (TaskStatus.ASSIGNED, TaskStatus.RUNNING):
                    task.status = TaskStatus.PENDING
                    task.worker_id = None
                    task.assigned_at = None
                    task.updated_at = now

        await self._persist_all_state()

        for plan in plans:
            if plan.status in (PlanStatus.APPROVED, PlanStatus.EXECUTING):
                await self._schedule_ready_steps(plan.plan_id)

    async def _persist_all_state(self) -> None:
        if self._store is None:
            return
        async with self._lock:
            workers = list(self._workers.values())
            tasks = list(self._tasks.values())
            plans = list(self._plans.values())
        for worker in workers:
            await self._store.save_worker(worker)
        for task in tasks:
            await self._store.save_task(task)
        for plan in plans:
            await self._store.save_plan(plan)

    async def _save_worker(self, worker: WorkerInfo | None) -> None:
        if self._store is None or worker is None:
            return
        await self._store.save_worker(worker)

    async def _delete_worker(self, worker_id: str) -> None:
        if self._store is None:
            return
        await self._store.delete_worker(worker_id)

    async def _save_task(self, task: Task | None) -> None:
        if self._store is None or task is None:
            return
        await self._store.save_task(task)

    async def _save_plan(self, plan: Plan | None) -> None:
        if self._store is None or plan is None:
            return
        await self._store.save_plan(plan)

    async def _emit_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        await self._event_sink.emit(run_id, event_type, data)

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    async def register_worker(self, req: WorkerRegisterRequest) -> WorkerInfo:
        async with self._lock:
            now = time.time()
            worker = WorkerInfo(
                worker_id=req.worker_id,
                name=req.name,
                capabilities=list(req.capabilities),
                base_url=req.base_url,
                registered_at=now,
                last_heartbeat=now,
            )
            self._workers[req.worker_id] = worker
            logger.info("Worker registered: {} ({})", req.worker_id, req.name)
        await self._save_worker(worker)
        await self._emit_event(
            run_id=req.worker_id,
            event_type=SupervisorEventType.WORKER_REGISTERED,
            data={
                "worker_id": req.worker_id,
                "name": req.name,
                "capabilities": list(req.capabilities),
                "base_url": req.base_url,
            },
        )
        return worker

    async def heartbeat(self, req: HeartbeatRequest) -> WorkerInfo | None:
        async with self._lock:
            worker = self._workers.get(req.worker_id)
            if worker is None:
                return None
            worker.last_heartbeat = time.time()
            worker.current_task_id = req.current_task_id
            if req.status != WorkerStatus.OFFLINE:
                worker.status = req.status
        await self._save_worker(worker)
        await self._emit_event(
            run_id=req.worker_id,
            event_type=SupervisorEventType.WORKER_HEARTBEAT,
            data={
                "worker_id": req.worker_id,
                "status": worker.status.value,
                "current_task_id": worker.current_task_id,
                "last_heartbeat": worker.last_heartbeat,
            },
        )
        return worker

    async def unregister_worker(self, worker_id: str) -> bool:
        released_tasks: list[Task] = []
        async with self._lock:
            worker = self._workers.pop(worker_id, None)
            if worker is None:
                return False
            # Release any assigned tasks back to pending
            for task in self._tasks.values():
                if task.worker_id == worker_id and task.status in (
                    TaskStatus.ASSIGNED,
                    TaskStatus.RUNNING,
                ):
                    task.status = TaskStatus.PENDING
                    task.worker_id = None
                    task.assigned_at = None
                    task.updated_at = time.time()
                    released_tasks.append(task)
            logger.info("Worker unregistered: {}", worker_id)
        await self._delete_worker(worker_id)
        for task in released_tasks:
            await self._save_task(task)
        return True

    async def list_workers(self) -> list[WorkerInfo]:
        async with self._lock:
            return list(self._workers.values())

    async def get_worker(self, worker_id: str) -> WorkerInfo | None:
        async with self._lock:
            return self._workers.get(worker_id)

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    async def create_task(self, task: Task) -> Task:
        async with self._lock:
            self._tasks[task.task_id] = task
            logger.info("Task created: {} ({})", task.task_id, task.label or task.instruction[:40])
        await self._save_task(task)
        await self._emit_event(
            run_id=task.task_id,
            event_type=SupervisorEventType.TASK_CREATED,
            data={
                "task_id": task.task_id,
                "plan_id": task.plan_id,
                "step_index": task.step_index,
                "status": task.status.value,
                "worker_id": task.worker_id,
            },
        )
        return task

    def _has_alternative_retry_worker_unlocked(self, failed_worker_id: str) -> bool:
        return any(
            worker.worker_id != failed_worker_id
            and worker.status in (WorkerStatus.ONLINE, WorkerStatus.BUSY)
            for worker in self._workers.values()
        )

    def _apply_failure_policy_unlocked(
        self,
        task: Task,
        *,
        failed_worker_id: str | None,
        error: str | None,
        result: str | None,
        now: float,
    ) -> bool:
        task.error = error
        task.result = result
        task.updated_at = now
        task.last_failed_worker_id = failed_worker_id

        if task.retry_count < task.max_retries:
            task.retry_count += 1
            task.status = TaskStatus.PENDING
            task.worker_id = None
            task.assigned_at = None
            return True

        task.status = TaskStatus.FAILED
        return False

    async def claim_task(self, req: TaskClaimRequest) -> Task | None:
        """Find the oldest pending task the worker can handle and assign it."""
        async with self._lock:
            worker = self._workers.get(req.worker_id)
            if worker is None:
                return None
            avoid_retry_worker = self._has_alternative_retry_worker_unlocked(req.worker_id)
            # FIFO-ish selection without full sort to reduce per-claim overhead.
            task: Task | None = None
            for candidate in self._tasks.values():
                if candidate.status != TaskStatus.PENDING:
                    continue
                if (
                    avoid_retry_worker
                    and candidate.retry_count > 0
                    and candidate.last_failed_worker_id == req.worker_id
                ):
                    continue
                if task is None or candidate.created_at < task.created_at:
                    task = candidate

            if task is None:
                return None

            task.status = TaskStatus.ASSIGNED
            task.worker_id = req.worker_id
            task.assigned_at = time.time()
            task.updated_at = time.time()
            worker.status = WorkerStatus.BUSY
            worker.current_task_id = task.task_id
            logger.info("Task {} claimed by worker {}", task.task_id, req.worker_id)
            claimed_task_id = task.task_id
            claimed_status = task.status.value
            claimed_assigned_at = task.assigned_at
        await self._save_task(task)
        await self._save_worker(worker)
        await self._emit_event(
            run_id=claimed_task_id,
            event_type=SupervisorEventType.TASK_ASSIGNED,
            data={
                "task_id": claimed_task_id,
                "worker_id": req.worker_id,
                "status": claimed_status,
                "assigned_at": claimed_assigned_at,
            },
        )
        return task

    async def report_progress(self, rpt: TaskProgressReport) -> Task | None:
        async with self._lock:
            task = self._tasks.get(rpt.task_id)
            if task is None or task.worker_id != rpt.worker_id:
                return None
            from nanobot.supervisor.models import TaskProgress

            task.progress.append(TaskProgress(
                iteration=rpt.iteration,
                message=rpt.message,
                data=dict(rpt.data),
            ))
            task.status = TaskStatus.RUNNING
            task.updated_at = time.time()
        await self._save_task(task)
        await self._emit_event(
            run_id=rpt.task_id,
            event_type=SupervisorEventType.TASK_PROGRESS,
            data={
                "task_id": rpt.task_id,
                "worker_id": rpt.worker_id,
                "iteration": rpt.iteration,
                "message": rpt.message,
            },
        )
        return task

    async def report_result(self, rpt: TaskResultReport) -> Task | None:
        plan_event_type: str | None = None
        plan_event_payload: dict[str, Any] | None = None
        task_event_type: str | None = None
        async with self._lock:
            task = self._tasks.get(rpt.task_id)
            if task is None or task.worker_id != rpt.worker_id:
                return None
            now = time.time()

            # Free the worker
            worker = self._workers.get(rpt.worker_id)
            if worker:
                worker.status = WorkerStatus.ONLINE
                worker.current_task_id = None

            if rpt.status == TaskStatus.FAILED:
                will_retry = self._apply_failure_policy_unlocked(
                    task,
                    failed_worker_id=rpt.worker_id,
                    error=rpt.error,
                    result=rpt.result or None,
                    now=now,
                )
                if will_retry:
                    logger.info(
                        "Task {} failed on worker {} and will retry ({}/{})",
                        rpt.task_id,
                        rpt.worker_id,
                        task.retry_count,
                        task.max_retries,
                    )
                    task_event_type = SupervisorEventType.TASK_RETRIED
                else:
                    logger.info("Task {} result: {}", rpt.task_id, rpt.status.value)
                    task_event_type = SupervisorEventType.TASK_FAILED
            else:
                task.status = rpt.status
                task.result = rpt.result
                task.error = rpt.error
                task.updated_at = now
                task.last_failed_worker_id = None
                logger.info("Task {} result: {}", rpt.task_id, rpt.status.value)
                task_event_type = SupervisorEventType.TASK_COMPLETED

            # Advance plan if task belongs to one
            if task.plan_id and task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                plan_event_type = await self._advance_plan_unlocked(task.plan_id)
                if plan_event_type is not None:
                    plan = self._plans.get(task.plan_id)
                    if plan is not None:
                        plan_event_payload = {
                            "plan_id": plan.plan_id,
                            "status": plan.status.value,
                        }

            task_status = task.status.value
            task_error = task.error
            task_result = task.result
            persisted_plan = self._plans.get(task.plan_id) if task.plan_id else None

        await self._save_task(task)
        await self._save_worker(worker)
        await self._save_plan(persisted_plan)
        if task_event_type is not None:
            await self._emit_event(
                run_id=rpt.task_id,
                event_type=task_event_type,
                data={
                    "task_id": rpt.task_id,
                    "worker_id": rpt.worker_id,
                    "status": task_status,
                    "error": task_error,
                    "retry_count": task.retry_count,
                    "max_retries": task.max_retries,
                    "last_failed_worker_id": task.last_failed_worker_id,
                    "result_preview": task_result[:500] if task_result else "",
                    "result_len": len(task_result) if task_result else 0,
                },
            )
        if plan_event_type is not None and plan_event_payload is not None:
            await self._emit_event(
                run_id=plan_event_payload["plan_id"],
                event_type=plan_event_type,
                data=plan_event_payload,
            )
        return task

    async def cancel_task(self, task_id: str) -> Task | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                return task
            task.status = TaskStatus.CANCELLED
            task.updated_at = time.time()
            # Free the worker
            if task.worker_id:
                worker = self._workers.get(task.worker_id)
                if worker and worker.current_task_id == task_id:
                    worker.status = WorkerStatus.ONLINE
                    worker.current_task_id = None
        await self._save_task(task)
        if task.worker_id:
            await self._save_worker(worker)
        await self._emit_event(
            run_id=task_id,
            event_type=SupervisorEventType.TASK_CANCELLED,
            data={
                "task_id": task_id,
                "status": task.status.value,
                "worker_id": task.worker_id,
            },
        )
        return task

    async def get_task(self, task_id: str) -> Task | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def list_tasks(
        self,
        status: TaskStatus | None = None,
        plan_id: str | None = None,
    ) -> list[Task]:
        async with self._lock:
            tasks = list(self._tasks.values())
            if status is not None:
                tasks = [t for t in tasks if t.status == status]
            if plan_id is not None:
                tasks = [t for t in tasks if t.plan_id == plan_id]
            return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    # ------------------------------------------------------------------
    # Plans
    # ------------------------------------------------------------------

    async def create_plan(self, plan: Plan) -> Plan:
        async with self._lock:
            self._plans[plan.plan_id] = plan
            logger.info("Plan created: {} ({})", plan.plan_id, plan.title)
        await self._save_plan(plan)
        await self._emit_event(
            run_id=plan.plan_id,
            event_type=SupervisorEventType.PLAN_CREATED,
            data={
                "plan_id": plan.plan_id,
                "title": plan.title,
                "status": plan.status.value,
                "steps": len(plan.steps),
            },
        )
        return plan

    async def get_plan(self, plan_id: str) -> Plan | None:
        async with self._lock:
            return self._plans.get(plan_id)

    async def list_plans(self, status: PlanStatus | None = None) -> list[Plan]:
        async with self._lock:
            plans = list(self._plans.values())
            if status is not None:
                plans = [p for p in plans if p.status == status]
            return sorted(plans, key=lambda p: p.created_at, reverse=True)

    async def approve_plan(self, plan_id: str) -> Plan | None:
        """Approve a draft plan and create tasks for ready steps."""
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None or plan.status != PlanStatus.DRAFT:
                return plan
            plan.status = PlanStatus.APPROVED
            plan.updated_at = time.time()
            plan_steps = len(plan.steps)
        # Create tasks for steps whose dependencies are met (outside lock)
        await self._emit_event(
            run_id=plan_id,
            event_type=SupervisorEventType.PLAN_APPROVED,
            data={
                "plan_id": plan_id,
                "status": PlanStatus.APPROVED.value,
                "steps": plan_steps,
            },
        )
        await self._schedule_ready_steps(plan_id)
        return await self.get_plan(plan_id)

    async def cancel_plan(self, plan_id: str) -> Plan | None:
        affected_tasks: list[Task] = []
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                return None
            plan.status = PlanStatus.CANCELLED
            plan.updated_at = time.time()
            # Cancel all pending/running tasks in this plan
            for task in self._tasks.values():
                if task.plan_id == plan_id and task.status in (
                    TaskStatus.PENDING,
                    TaskStatus.ASSIGNED,
                    TaskStatus.RUNNING,
                ):
                    task.status = TaskStatus.CANCELLED
                    task.updated_at = time.time()
                    affected_tasks.append(task)
        await self._save_plan(plan)
        for task in affected_tasks:
            await self._save_task(task)
        return plan

    async def _schedule_ready_steps(self, plan_id: str) -> None:
        """Create tasks for plan steps whose dependencies are satisfied."""
        created_tasks: list[Task] = []
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None or plan.status not in (PlanStatus.APPROVED, PlanStatus.EXECUTING):
                return

            completed_indices: set[int] = set()
            for step in plan.steps:
                if step.status == TaskStatus.COMPLETED:
                    completed_indices.add(step.index)

            for step in plan.steps:
                if step.task_id is not None or step.status != TaskStatus.PENDING:
                    continue
                if all(dep in completed_indices for dep in step.depends_on):
                    task = Task(
                        plan_id=plan_id,
                        step_index=step.index,
                        instruction=step.instruction,
                        label=step.label or f"Plan {plan_id} step {step.index}",
                        max_iterations=self.task_default_max_iterations,
                        max_retries=step.max_retries,
                        timeout_s=self.task_default_timeout_s,
                        origin_channel=plan.origin_channel,
                        origin_chat_id=plan.origin_chat_id,
                        session_key=plan.session_key,
                    )
                    self._tasks[task.task_id] = task
                    step.task_id = task.task_id
                    created_tasks.append(task)
                    logger.info(
                        "Scheduled step {} of plan {} → task {}",
                        step.index, plan_id, task.task_id,
                    )

            if plan.status == PlanStatus.APPROVED:
                plan.status = PlanStatus.EXECUTING
                plan.updated_at = time.time()
        for task in created_tasks:
            await self._save_task(task)
        await self._save_plan(plan)

    async def _advance_plan_unlocked(self, plan_id: str) -> str | None:
        """Check plan completion after a task finishes. Must be called under _lock."""
        plan = self._plans.get(plan_id)
        if plan is None:
            return None

        # Sync step status from tasks
        for step in plan.steps:
            if step.task_id:
                task = self._tasks.get(step.task_id)
                if task:
                    step.status = task.status
                    if task.result:
                        step.result_summary = task.result[:500]

        all_done = all(
            s.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
            for s in plan.steps
        )
        any_failed = any(s.status == TaskStatus.FAILED for s in plan.steps)

        if any_failed:
            plan.status = PlanStatus.FAILED
            plan.updated_at = time.time()
            return SupervisorEventType.PLAN_FAILED
        elif all_done:
            plan.status = PlanStatus.COMPLETED
            plan.updated_at = time.time()
            return SupervisorEventType.PLAN_COMPLETED
        return None

    # ------------------------------------------------------------------
    # Health scanning
    # ------------------------------------------------------------------

    async def scan_unhealthy_workers(self) -> list[WorkerInfo]:
        """Mark workers whose heartbeat is overdue as unhealthy and return them."""
        now = time.time()
        unhealthy: list[WorkerInfo] = []
        events: list[dict[str, Any]] = []
        async with self._lock:
            for w in self._workers.values():
                if w.status == WorkerStatus.OFFLINE:
                    continue
                if now - w.last_heartbeat > self.heartbeat_timeout_s:
                    w.status = WorkerStatus.UNHEALTHY
                    unhealthy.append(w)
                    events.append({
                        "worker_id": w.worker_id,
                        "status": w.status.value,
                        "last_heartbeat": w.last_heartbeat,
                        "heartbeat_timeout_s": self.heartbeat_timeout_s,
                    })
        for worker in unhealthy:
            await self._save_worker(worker)
        for event_data in events:
            await self._emit_event(
                run_id=event_data["worker_id"],
                event_type=SupervisorEventType.WORKER_UNHEALTHY,
                data=event_data,
            )
        return unhealthy

    async def scan_stale_tasks(self) -> list[Task]:
        """Fail tasks whose assigned runtime exceeds their timeout."""
        now = time.time()
        stale_tasks: list[Task] = []
        task_events: list[dict[str, Any]] = []
        plan_events: list[tuple[str, dict[str, Any]]] = []
        workers_to_save: dict[str, WorkerInfo] = {}
        plans_to_save: dict[str, Plan] = {}
        async with self._lock:
            for task in self._tasks.values():
                if task.status not in (TaskStatus.ASSIGNED, TaskStatus.RUNNING):
                    continue

                started_at = task.assigned_at or task.updated_at or task.created_at
                if now - started_at <= task.timeout_s:
                    continue

                task.status = TaskStatus.FAILED
                task.error = f"task timed out after {task.timeout_s:g}s"
                self._apply_failure_policy_unlocked(
                    task,
                    failed_worker_id=task.worker_id,
                    error=task.error,
                    result=None,
                    now=now,
                )
                stale_tasks.append(task)
                task_events.append({
                    "task_id": task.task_id,
                    "worker_id": task.worker_id,
                    "status": task.status.value,
                    "error": task.error,
                    "retry_count": task.retry_count,
                    "max_retries": task.max_retries,
                    "last_failed_worker_id": task.last_failed_worker_id,
                    "result_preview": "",
                    "result_len": 0,
                })

                if task.worker_id:
                    worker = self._workers.get(task.worker_id)
                    if worker and worker.current_task_id == task.task_id:
                        worker.status = WorkerStatus.ONLINE
                        worker.current_task_id = None
                        workers_to_save[worker.worker_id] = worker

                if task.plan_id and task.status == TaskStatus.FAILED:
                    plan_event_type = await self._advance_plan_unlocked(task.plan_id)
                    if plan_event_type is not None:
                        plan = self._plans.get(task.plan_id)
                        if plan is not None:
                            plans_to_save[plan.plan_id] = plan
                            plan_events.append((
                                plan_event_type,
                                {
                                    "plan_id": plan.plan_id,
                                    "status": plan.status.value,
                                },
                            ))

        for task in stale_tasks:
            await self._save_task(task)
        for worker in workers_to_save.values():
            await self._save_worker(worker)
        for plan in plans_to_save.values():
            await self._save_plan(plan)
        for event_data in task_events:
            await self._emit_event(
                run_id=event_data["task_id"],
                event_type=(
                    SupervisorEventType.TASK_RETRIED
                    if event_data["status"] == TaskStatus.PENDING.value
                    else SupervisorEventType.TASK_FAILED
                ),
                data=event_data,
            )
        for event_type, payload in plan_events:
            await self._emit_event(
                run_id=payload["plan_id"],
                event_type=event_type,
                data=payload,
            )
        return stale_tasks

    async def evict_worker(self, worker_id: str, reason: str | None = None) -> list[Task]:
        """Remove an unhealthy worker and reassign its tasks back to pending."""
        reassigned: list[Task] = []
        async with self._lock:
            worker = self._workers.pop(worker_id, None)
            if worker is None:
                return reassigned
            for task in self._tasks.values():
                if task.worker_id == worker_id and task.status in (
                    TaskStatus.ASSIGNED,
                    TaskStatus.RUNNING,
                ):
                    task.status = TaskStatus.PENDING
                    task.worker_id = None
                    task.assigned_at = None
                    task.updated_at = time.time()
                    reassigned.append(task)
            logger.warning("Evicted worker {} — {} tasks re-queued", worker_id, len(reassigned))
        await self._delete_worker(worker_id)
        for task in reassigned:
            await self._save_task(task)
        await self._emit_event(
            run_id=worker_id,
            event_type=SupervisorEventType.WORKER_EVICTED,
            data={
                "worker_id": worker_id,
                "reason": reason or "unspecified",
                "requeued_tasks": len(reassigned),
            },
        )
        return reassigned
