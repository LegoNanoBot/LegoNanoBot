"""SQLite-backed event store for X-Ray monitoring."""

from __future__ import annotations

import json
import os
from typing import Any

import aiosqlite
from loguru import logger

from nanobot.xray.events import EventType, XRayEvent
from nanobot.xray.store.base import BaseEventStore

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    timestamp REAL NOT NULL,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'
)
"""

_CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_run_type ON events(run_id, event_type)",
    "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)",
]


class SQLiteEventStore(BaseEventStore):
    """SQLite-backed implementation of X-Ray event store."""

    def __init__(self, db_path: str, max_runs: int = 100) -> None:
        """Initialize the SQLite event store.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = db_path
        self._max_runs = max_runs
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Initialize the database connection and create tables."""
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row

        # Enable WAL mode for better concurrency
        await self._db.execute("PRAGMA journal_mode=WAL")

        # Create table and indexes
        await self._db.execute(_CREATE_TABLE_SQL)
        for index_sql in _CREATE_INDEXES_SQL:
            await self._db.execute(index_sql)

        await self._db.commit()
        logger.debug("SQLiteEventStore initialized at {}", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.debug("SQLiteEventStore connection closed")

    async def save_event(self, event: XRayEvent) -> None:
        """Save a single event to the database."""
        if self._db is None:
            raise RuntimeError("Database not initialized. Call init() first.")

        data_json = json.dumps(event.data, ensure_ascii=False, default=str)
        await self._db.execute(
            "INSERT INTO events (id, timestamp, run_id, event_type, data) VALUES (?, ?, ?, ?, ?)",
            (event.id, event.timestamp, event.run_id, event.event_type, data_json),
        )
        await self._cleanup_excess_runs()
        await self._db.commit()
        logger.trace("Saved event {} (type={})", event.id, event.event_type)

    async def save_events(self, events: list[XRayEvent]) -> None:
        """Save multiple events in batch."""
        if self._db is None:
            raise RuntimeError("Database not initialized. Call init() first.")

        if not events:
            return

        rows = [
            (
                e.id,
                e.timestamp,
                e.run_id,
                e.event_type,
                json.dumps(e.data, ensure_ascii=False, default=str),
            )
            for e in events
        ]
        await self._db.executemany(
            "INSERT INTO events (id, timestamp, run_id, event_type, data) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        await self._cleanup_excess_runs()
        await self._db.commit()
        logger.debug("Batch saved {} events", len(events))

    async def _cleanup_excess_runs(self) -> None:
        """Keep only recent runs by newest event timestamp."""
        if self._db is None or self._max_runs <= 0:
            return

        async with self._db.execute(
            """
            SELECT run_id
            FROM events
            GROUP BY run_id
            ORDER BY MAX(timestamp) DESC
            LIMIT -1 OFFSET ?
            """,
            (self._max_runs,),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return

        stale_run_ids = [row["run_id"] for row in rows]
        placeholders = ",".join("?" for _ in stale_run_ids)
        await self._db.execute(
            f"DELETE FROM events WHERE run_id IN ({placeholders})",
            stale_run_ids,
        )

    async def query_events(
        self,
        run_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Query events with optional filters."""
        if self._db is None:
            raise RuntimeError("Database not initialized. Call init() first.")

        conditions: list[str] = []
        params: list[Any] = []

        if run_id is not None:
            conditions.append("run_id = ?")
            params.append(run_id)
        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT id, timestamp, run_id, event_type, data
            FROM events
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

        return [
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "run_id": row["run_id"],
                "event_type": row["event_type"],
                "data": json.loads(row["data"]),
            }
            for row in rows
        ]

    async def get_agent_runs(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get list of agent runs."""
        if self._db is None:
            raise RuntimeError("Database not initialized. Call init() first.")

        # Build status filter in SQL to avoid post-LIMIT filtering bug.
        # "active" = no matching agent_end event, "completed" = has one.
        if status == "active":
            status_clause = "AND e.timestamp IS NULL"
        elif status == "completed":
            status_clause = "AND e.timestamp IS NOT NULL"
        else:
            status_clause = ""

        query = f"""
            SELECT
                s.run_id,
                s.timestamp AS started_at,
                s.data AS start_data,
                e.timestamp AS ended_at,
                e.data AS end_data
            FROM events s
            LEFT JOIN events e ON s.run_id = e.run_id AND e.event_type = ?
            WHERE s.event_type = ?
            {status_clause}
            ORDER BY s.timestamp DESC
            LIMIT ?
        """

        async with self._db.execute(
            query, (EventType.AGENT_END, EventType.AGENT_START, limit)
        ) as cursor:
            rows = await cursor.fetchall()

        results: list[dict] = []
        for row in rows:
            run_status = "completed" if row["ended_at"] is not None else "active"

            start_data = json.loads(row["start_data"]) if row["start_data"] else {}
            end_data = json.loads(row["end_data"]) if row["end_data"] else {}

            duration_s = None
            if row["ended_at"] is not None:
                duration_s = round(row["ended_at"] - row["started_at"], 3)

            results.append(
                {
                    "run_id": row["run_id"],
                    "start_time": row["started_at"],
                    "end_time": row["ended_at"],
                    "status": run_status,
                    "channel": start_data.get("channel"),
                    "duration": duration_s,
                    "start_data": start_data,
                    "end_data": end_data,
                }
            )

        return results

    async def get_run_detail(self, run_id: str) -> dict | None:
        """Get detailed information for a single agent run."""
        if self._db is None:
            raise RuntimeError("Database not initialized. Call init() first.")

        # Get agent_start event
        async with self._db.execute(
            "SELECT timestamp, data FROM events WHERE run_id = ? AND event_type = ?",
            (run_id, EventType.AGENT_START),
        ) as cursor:
            start_row = await cursor.fetchone()

        if start_row is None:
            return None

        started_at = start_row["timestamp"]
        start_data = json.loads(start_row["data"]) if start_row["data"] else {}

        # Get agent_end event
        async with self._db.execute(
            "SELECT timestamp, data FROM events WHERE run_id = ? AND event_type = ?",
            (run_id, EventType.AGENT_END),
        ) as cursor:
            end_row = await cursor.fetchone()

        ended_at = None
        end_data = {}
        if end_row is not None:
            ended_at = end_row["timestamp"]
            end_data = json.loads(end_row["data"]) if end_row["data"] else {}

        # Count LLM responses and sum tokens
        async with self._db.execute(
            "SELECT data FROM events WHERE run_id = ? AND event_type = ?",
            (run_id, EventType.LLM_RESPONSE),
        ) as cursor:
            llm_data_rows = await cursor.fetchall()

        llm_call_count = len(llm_data_rows)
        total_tokens = 0
        for row in llm_data_rows:
            data = json.loads(row["data"]) if row["data"] else {}
            usage = data.get("usage", {})
            total_tokens += usage.get("total_tokens", 0)

        # Count tool calls
        async with self._db.execute(
            "SELECT COUNT(*) as cnt FROM events WHERE run_id = ? AND event_type = ?",
            (run_id, EventType.TOOL_CALL_START),
        ) as cursor:
            tool_row = await cursor.fetchone()

        tool_call_count = tool_row["cnt"] if tool_row else 0

        run_status = "completed" if ended_at is not None else "active"
        duration_s = None
        if ended_at is not None:
            duration_s = round(ended_at - started_at, 3)

        return {
            "run_id": run_id,
            "start_time": started_at,
            "end_time": ended_at,
            "status": run_status,
            "duration": duration_s,
            "channel": start_data.get("channel"),
            "start_data": start_data,
            "end_data": end_data,
            "llm_call_count": llm_call_count,
            "total_tokens": total_tokens,
            "tool_call_count": tool_call_count,
        }

    async def get_token_usage(self, run_id: str | None = None) -> dict:
        """Get token usage statistics."""
        if self._db is None:
            raise RuntimeError("Database not initialized. Call init() first.")

        conditions = ["event_type = ?"]
        params: list[Any] = [EventType.LLM_RESPONSE]

        if run_id is not None:
            conditions.append("run_id = ?")
            params.append(run_id)

        where_clause = " AND ".join(conditions)
        query = f"SELECT data FROM events WHERE {where_clause}"

        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        call_count = 0

        for row in rows:
            data = json.loads(row["data"]) if row["data"] else {}
            usage = data.get("usage", {})
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)
            total_tokens += usage.get("total_tokens", 0)
            call_count += 1

        return {
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "call_count": call_count,
        }

    async def cleanup(self, before_timestamp: float) -> int:
        """Delete events older than the specified timestamp."""
        if self._db is None:
            raise RuntimeError("Database not initialized. Call init() first.")

        async with self._db.execute(
            "DELETE FROM events WHERE timestamp < ?", (before_timestamp,)
        ) as cursor:
            deleted_count = cursor.rowcount

        await self._db.commit()
        logger.info("Cleaned up {} events before timestamp {}", deleted_count, before_timestamp)
        return deleted_count

    async def store(self, event: XRayEvent) -> None:
        """Alias for save_event, used by EventCollector."""
        await self.save_event(event)
