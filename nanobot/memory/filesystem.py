"""Filesystem-backed memory storage."""

from __future__ import annotations

from pathlib import Path

from nanobot.memory.base import BaseMemoryStore
from nanobot.utils.helpers import ensure_dir


class FilesystemMemoryStore(BaseMemoryStore):
    """Default memory backend using memory/MEMORY.md + memory/HISTORY.md."""

    def __init__(
        self,
        workspace: Path,
        *,
        dir_name: str = "memory",
        memory_file: str = "MEMORY.md",
        history_file: str = "HISTORY.md",
    ):
        self.workspace = workspace
        self.memory_dir = ensure_dir(workspace / dir_name)
        self.memory_file = self.memory_dir / memory_file
        self.history_file = self.memory_dir / history_file

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_identity_lines(self, _workspace: Path) -> list[str]:
        memory_path = self.memory_file
        history_path = self.history_file
        return [
            f"- Long-term memory: {memory_path} (write important facts here)",
            f"- History log: {history_path} (grep-searchable). Each entry starts with [YYYY-MM-DD HH:MM].",
        ]
