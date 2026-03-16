"""Memory plugin loading helpers."""

from __future__ import annotations

import importlib.metadata as importlib_metadata
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.memory.base import BaseMemoryStore

if TYPE_CHECKING:
    from nanobot.config.schema import Config

MemoryFactory = Callable[..., BaseMemoryStore]


def _iter_entry_points(group: str) -> list[Any]:
    """Return entry points for a group, compatible with Python 3.11+ APIs."""
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return list(entry_points.select(group=group))
    return list(entry_points.get(group, []))


def load_memory_factories() -> dict[str, MemoryFactory]:
    """Load plugin memory factories from ``nanobot.memory_factories`` entry points."""
    factories: dict[str, MemoryFactory] = {}
    for ep in _iter_entry_points("nanobot.memory_factories"):
        try:
            factory = ep.load()
        except Exception as exc:
            logger.warning("Failed loading memory factory plugin {}: {}", ep.name, exc)
            continue

        if not callable(factory):
            logger.warning("Ignore memory factory plugin {}: object is not callable", ep.name)
            continue

        name = ep.name.replace("-", "_")
        if name in factories:
            logger.warning("Ignore memory factory plugin {}: duplicate memory backend name", name)
            continue

        factories[name] = factory
    return factories


def get_memory_factory(name: str | None) -> MemoryFactory | None:
    """Get memory factory by backend name (normalized to snake_case)."""
    if not name:
        return None
    normalized = name.replace("-", "_")
    return load_memory_factories().get(normalized)


def create_memory_by_factory(
    factory: MemoryFactory,
    *,
    config: "Config",
    workspace: Path,
    backend_name: str,
    memory_config: dict[str, Any],
) -> BaseMemoryStore:
    """Create memory backend from plugin factory using a stable keyword contract."""
    return factory(
        config=config,
        workspace=workspace,
        backend_name=backend_name,
        memory_config=memory_config,
    )
