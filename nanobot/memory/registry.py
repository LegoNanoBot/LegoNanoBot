"""Memory backend registry and factory selection."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.memory.base import BaseMemoryStore
from nanobot.memory.filesystem import FilesystemMemoryStore
from nanobot.memory.memory_plugins import create_memory_by_factory, get_memory_factory

if TYPE_CHECKING:
    from nanobot.config.schema import Config


_BUILTIN_BACKENDS = {"filesystem", "file", "default"}


def _normalize_backend_name(name: str | None) -> str:
    return (name or "filesystem").replace("-", "_")


def _resolve_plugin_config(config: "Config", backend_name: str) -> dict[str, Any]:
    section = config.memory.plugins.get(backend_name)
    if section is not None:
        return section.model_dump(by_alias=True)

    for raw_name, plugin_cfg in config.memory.plugins.items():
        if raw_name.replace("-", "_") == backend_name:
            return plugin_cfg.model_dump(by_alias=True)
    return {}


def create_memory_store(config: "Config", workspace: Path) -> BaseMemoryStore:
    """Create memory backend based on config (built-in filesystem or plugin)."""
    backend_name = _normalize_backend_name(config.memory.backend)

    if backend_name in _BUILTIN_BACKENDS:
        fs = config.memory.filesystem
        return FilesystemMemoryStore(
            workspace,
            dir_name=fs.dir,
            memory_file=fs.memory_file,
            history_file=fs.history_file,
        )

    factory = get_memory_factory(backend_name)
    if not factory:
        logger.warning(
            "Memory backend '{}' not found; fallback to filesystem",
            backend_name,
        )
        fs = config.memory.filesystem
        return FilesystemMemoryStore(
            workspace,
            dir_name=fs.dir,
            memory_file=fs.memory_file,
            history_file=fs.history_file,
        )

    memory_config = _resolve_plugin_config(config, backend_name)
    store = create_memory_by_factory(
        factory,
        config=config,
        workspace=workspace,
        backend_name=backend_name,
        memory_config=memory_config,
    )
    if not isinstance(store, BaseMemoryStore):
        raise TypeError(
            f"Memory plugin '{backend_name}' must return BaseMemoryStore, got {type(store)!r}"
        )
    return store
