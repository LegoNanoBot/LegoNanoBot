"""Abstract memory storage interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BaseMemoryStore(ABC):
    """Storage contract for nanobot long-term memory and history."""

    @abstractmethod
    def read_long_term(self) -> str:
        """Return long-term memory content as markdown text."""

    @abstractmethod
    def write_long_term(self, content: str) -> None:
        """Persist full long-term memory content."""

    @abstractmethod
    def append_history(self, entry: str) -> None:
        """Append one history entry for grep-friendly auditing."""

    def get_memory_context(self) -> str:
        """Build memory context section for system prompt."""
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    def get_identity_lines(self, workspace: Path) -> list[str]:
        """Return identity prompt lines describing memory storage locations."""
        return [
            f"- Long-term memory: managed by {self.__class__.__name__}",
            f"- History log: managed by {self.__class__.__name__}",
        ]
