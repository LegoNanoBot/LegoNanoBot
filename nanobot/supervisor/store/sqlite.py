"""SQLite-backed registry store for supervisor state."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from enum import Enum
from typing import Any

import aiosqlite
from loguru import logger

from nanobot.supervisor.models import Plan, PlanStatus, PlanStep, Task, TaskProgress, TaskStatus, WorkerInfo, WorkerStatus
from nanobot.supervisor.store.base import RegistryStore

_CREATE_TABLE_SQL = {
    "workers": """
        CREATE TABLE IF NOT EXISTS supervisor_workers (
            worker_id TEXT PRIMARY KEY,
            updated_at REAL NOT NULL,
            payload TEXT NOT NULL
        )
    """,
    "tasks": """
        CREATE TABLE IF NOT EXISTS supervisor_tasks (
            task_id TEXT PRIMARY KEY,
            updated_at REAL NOT NULL,
            payload TEXT NOT NULL
        )
    """,
    "plans": """
        CREATE TABLE IF NOT EXISTS supervisor_plans (
            plan_id TEXT PRIMARY KEY,
            updated_at REAL NOT NULL,
            payload TEXT NOT NULL
        )
    """,
}


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _worker_to_payload(worker: WorkerInfo) -> dict[str, Any]:
    return asdict(worker)


def _task_to_payload(task: Task) -> dict[str, Any]:
    return asdict(task)


def _plan_to_payload(plan: Plan) -> dict[str, Any]:
    return asdict(plan)


def _worker_from_payload(payload: dict[str, Any]) -> WorkerInfo:
    return WorkerInfo(
        worker_id=payload["worker_id"],
        name=payload["name"],
        status=WorkerStatus(payload.get("status", WorkerStatus.ONLINE.value)),
        capabilities=list(payload.get("capabilities", [])),
        registered_at=payload.get("registered_at", 0.0),
        last_heartbeat=payload.get("last_heartbeat", 0.0),
        current_task_id=payload.get("current_task_id"),
        base_url=payload.get("base_url"),
        metadata=dict(payload.get("metadata", {})),
    )


def _task_from_payload(payload: dict[str, Any]) -> Task:
    return Task(
        task_id=payload["task_id"],
        plan_id=payload.get("plan_id"),
        step_index=payload.get("step_index"),
        instruction=payload.get("instruction", ""),
        label=payload.get("label", ""),
        context=payload.get("context", ""),
        status=TaskStatus(payload.get("status", TaskStatus.PENDING.value)),
        worker_id=payload.get("worker_id"),
        assigned_at=payload.get("assigned_at"),
        retry_count=payload.get("retry_count", 0),
        max_retries=payload.get("max_retries", 0),
        last_failed_worker_id=payload.get("last_failed_worker_id"),
        progress=[
            TaskProgress(
                timestamp=item.get("timestamp", 0.0),
                iteration=item.get("iteration", 0),
                message=item.get("message", ""),
                data=dict(item.get("data", {})),
            )
            for item in payload.get("progress", [])
        ],
        result=payload.get("result"),
        error=payload.get("error"),
        created_at=payload.get("created_at", 0.0),
        updated_at=payload.get("updated_at", 0.0),
        max_iterations=payload.get("max_iterations", 30),
        timeout_s=payload.get("timeout_s", 600.0),
        origin_channel=payload.get("origin_channel", "cli"),
        origin_chat_id=payload.get("origin_chat_id", "direct"),
        session_key=payload.get("session_key"),
    )


def _plan_from_payload(payload: dict[str, Any]) -> Plan:
    return Plan(
        plan_id=payload["plan_id"],
        title=payload.get("title", ""),
        goal=payload.get("goal", ""),
        status=PlanStatus(payload.get("status", PlanStatus.DRAFT.value)),
        steps=[
            PlanStep(
                index=item["index"],
                instruction=item.get("instruction", ""),
                label=item.get("label", ""),
                depends_on=list(item.get("depends_on", [])),
                max_retries=item.get("max_retries", 0),
                task_id=item.get("task_id"),
                status=TaskStatus(item.get("status", TaskStatus.PENDING.value)),
                result_summary=item.get("result_summary"),
            )
            for item in payload.get("steps", [])
        ],
        created_at=payload.get("created_at", 0.0),
        updated_at=payload.get("updated_at", 0.0),
        origin_channel=payload.get("origin_channel", "cli"),
        origin_chat_id=payload.get("origin_chat_id", "direct"),
        session_key=payload.get("session_key"),
    )


class SQLiteRegistryStore(RegistryStore):
    """SQLite persistence for supervisor registry state."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        for sql in _CREATE_TABLE_SQL.values():
            await self._db.execute(sql)
        await self._db.commit()
        logger.debug("SQLiteRegistryStore initialized at {}", self._db_path)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.debug("SQLiteRegistryStore connection closed")

    async def save_worker(self, worker: WorkerInfo) -> None:
        await self._upsert(
            table="supervisor_workers",
            key_column="worker_id",
            key=worker.worker_id,
            updated_at=worker.last_heartbeat,
            payload=_worker_to_payload(worker),
        )

    async def load_workers(self) -> list[WorkerInfo]:
        rows = await self._load_rows("SELECT payload FROM supervisor_workers ORDER BY updated_at DESC")
        return [_worker_from_payload(json.loads(row["payload"])) for row in rows]

    async def delete_worker(self, worker_id: str) -> None:
        await self._delete("supervisor_workers", "worker_id", worker_id)

    async def save_task(self, task: Task) -> None:
        await self._upsert(
            table="supervisor_tasks",
            key_column="task_id",
            key=task.task_id,
            updated_at=task.updated_at,
            payload=_task_to_payload(task),
        )

    async def load_tasks(self) -> list[Task]:
        rows = await self._load_rows("SELECT payload FROM supervisor_tasks ORDER BY updated_at DESC")
        return [_task_from_payload(json.loads(row["payload"])) for row in rows]

    async def save_plan(self, plan: Plan) -> None:
        await self._upsert(
            table="supervisor_plans",
            key_column="plan_id",
            key=plan.plan_id,
            updated_at=plan.updated_at,
            payload=_plan_to_payload(plan),
        )

    async def load_plans(self) -> list[Plan]:
        rows = await self._load_rows("SELECT payload FROM supervisor_plans ORDER BY updated_at DESC")
        return [_plan_from_payload(json.loads(row["payload"])) for row in rows]

    async def _upsert(
        self,
        *,
        table: str,
        key_column: str,
        key: str,
        updated_at: float,
        payload: dict[str, Any],
    ) -> None:
        db = self._require_db()
        payload_json = json.dumps(payload, ensure_ascii=False, default=_json_default)
        await db.execute(
            f"""
            INSERT INTO {table} ({key_column}, updated_at, payload)
            VALUES (?, ?, ?)
            ON CONFLICT({key_column}) DO UPDATE SET
                updated_at = excluded.updated_at,
                payload = excluded.payload
            """,
            (key, updated_at, payload_json),
        )
        await db.commit()

    async def _delete(self, table: str, key_column: str, key: str) -> None:
        db = self._require_db()
        await db.execute(f"DELETE FROM {table} WHERE {key_column} = ?", (key,))
        await db.commit()

    async def _load_rows(self, query: str) -> list[aiosqlite.Row]:
        db = self._require_db()
        async with db.execute(query) as cursor:
            return await cursor.fetchall()

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Registry store not initialized. Call init() first.")
        return self._db
