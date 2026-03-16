"""SQLite-backed memory store plugin example."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from nanobot.memory.base import BaseMemoryStore
from nanobot.utils.helpers import ensure_dir


class SQLiteMemoryStore(BaseMemoryStore):
    """Store long-term memory and history log in a SQLite database."""

    def __init__(self, workspace: Path, db_path: str = "memory/memory.sqlite3"):
        self.workspace = workspace
        raw = Path(db_path)
        self.db_file = raw if raw.is_absolute() else (workspace / raw)
        ensure_dir(self.db_file.parent)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_file)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS long_term_memory (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    content TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("INSERT OR IGNORE INTO long_term_memory(id, content) VALUES(1, '')")

    def read_long_term(self) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT content FROM long_term_memory WHERE id = 1").fetchone()
        return row[0] if row else ""

    def write_long_term(self, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE long_term_memory SET content = ? WHERE id = 1",
                (content,),
            )

    def append_history(self, entry: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO history_entries(entry) VALUES(?)",
                (entry.rstrip(),),
            )

    def get_identity_lines(self, _workspace: Path) -> list[str]:
        return [
            f"- Long-term memory: SQLite table long_term_memory (db: {self.db_file})",
            f"- History log: SQLite table history_entries (db: {self.db_file})",
        ]


def create_memory_store(*, config, workspace: Path, backend_name: str, memory_config: dict[str, Any]):
    """Factory entry point for nanobot.memory_factories."""
    db_path = memory_config.get("dbPath") or memory_config.get("db_path") or "memory/memory.sqlite3"
    return SQLiteMemoryStore(workspace=workspace, db_path=db_path)
