"""Persistent storage backends for supervisor registry state."""

from nanobot.supervisor.store.base import RegistryStore

try:
    from nanobot.supervisor.store.sqlite import SQLiteRegistryStore
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    SQLiteRegistryStore = None

__all__ = ["RegistryStore", "SQLiteRegistryStore"]