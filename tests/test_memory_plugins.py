from dataclasses import dataclass
from pathlib import Path

from nanobot.config.schema import Config, MemoryPluginConfig
from nanobot.memory.base import BaseMemoryStore
from nanobot.memory.filesystem import FilesystemMemoryStore
from nanobot.memory.registry import create_memory_store


@dataclass
class _FakeEntryPoint:
    name: str
    value: object

    def load(self):
        return self.value


class _FakeEntryPoints(list):
    def select(self, *, group: str):
        if group == "nanobot.memory_factories":
            return self
        return []


class _PluginMemoryStore(BaseMemoryStore):
    def __init__(self):
        self._memory = ""
        self.history: list[str] = []

    def read_long_term(self) -> str:
        return self._memory

    def write_long_term(self, content: str) -> None:
        self._memory = content

    def append_history(self, entry: str) -> None:
        self.history.append(entry)


def test_load_memory_factories_from_entry_points(monkeypatch):
    import nanobot.memory.memory_plugins as memory_plugins

    def _factory(**_kwargs):
        return _PluginMemoryStore()

    monkeypatch.setattr(
        memory_plugins.importlib_metadata,
        "entry_points",
        lambda: _FakeEntryPoints([_FakeEntryPoint("sqlite-memory", _factory)]),
    )

    factories = memory_plugins.load_memory_factories()

    assert "sqlite_memory" in factories
    assert callable(factories["sqlite_memory"])


def test_create_memory_store_uses_plugin_backend(monkeypatch, tmp_path: Path):
    config = Config()
    config.memory.backend = "sqlite-memory"
    config.memory.plugins["sqlite_memory"] = MemoryPluginConfig.model_validate(
        {"dbPath": "memory/nanobot.db"}
    )

    def _factory(*, config, workspace, backend_name, memory_config):
        assert backend_name == "sqlite_memory"
        assert memory_config["dbPath"] == "memory/nanobot.db"
        return _PluginMemoryStore()

    monkeypatch.setattr("nanobot.memory.registry.get_memory_factory", lambda _name: _factory)

    store = create_memory_store(config, tmp_path)

    assert isinstance(store, _PluginMemoryStore)


def test_create_memory_store_fallbacks_to_filesystem_when_plugin_missing(tmp_path: Path):
    config = Config()
    config.memory.backend = "missing_plugin"

    store = create_memory_store(config, tmp_path)

    assert isinstance(store, FilesystemMemoryStore)
