"""Tests for flat provider-plugin configuration in ProvidersConfig."""

from nanobot.config.schema import ProvidersConfig, ProviderConfig


class TestFlatProviderConfig:
    """Verify that unknown top-level keys are collected into plugins."""

    def test_flat_config_moved_to_plugins(self):
        """providers.bailian should be auto-moved into providers.plugins.bailian."""
        data = {"bailian": {"api_key": "xxx"}}
        cfg = ProvidersConfig(**data)
        assert "bailian" in cfg.plugins
        assert cfg.plugins["bailian"].api_key == "xxx"

    def test_nested_plugins_still_works(self):
        """providers.plugins.bailian should still work (backward compat)."""
        data = {"plugins": {"bailian": {"api_key": "xxx"}}}
        cfg = ProvidersConfig(**data)
        assert "bailian" in cfg.plugins
        assert cfg.plugins["bailian"].api_key == "xxx"

    def test_nested_takes_precedence_on_conflict(self):
        """When both flat and nested exist, nested wins (setdefault)."""
        data = {
            "bailian": {"api_key": "flat-key"},
            "plugins": {"bailian": {"api_key": "nested-key"}},
        }
        cfg = ProvidersConfig(**data)
        assert cfg.plugins["bailian"].api_key == "nested-key"

    def test_builtin_provider_not_moved(self):
        """Known fields like openai must NOT be moved into plugins."""
        data = {"openai": {"api_key": "sk-xxx"}}
        cfg = ProvidersConfig(**data)
        assert "openai" not in cfg.plugins
        assert cfg.openai.api_key == "sk-xxx"

    def test_multiple_plugins_flat(self):
        """Multiple unknown providers all get collected."""
        data = {
            "bailian": {"api_key": "key1"},
            "my_custom_llm": {"api_key": "key2"},
        }
        cfg = ProvidersConfig(**data)
        assert "bailian" in cfg.plugins
        assert "my_custom_llm" in cfg.plugins
        assert cfg.plugins["bailian"].api_key == "key1"
        assert cfg.plugins["my_custom_llm"].api_key == "key2"

    def test_mixed_builtin_and_plugin(self):
        """Builtin + flat plugin should coexist correctly."""
        data = {
            "openai": {"api_key": "sk-openai"},
            "bailian": {"api_key": "key-bailian"},
        }
        cfg = ProvidersConfig(**data)
        assert cfg.openai.api_key == "sk-openai"
        assert "openai" not in cfg.plugins
        assert "bailian" in cfg.plugins
        assert cfg.plugins["bailian"].api_key == "key-bailian"

    def test_empty_config(self):
        """Empty config should produce empty plugins."""
        cfg = ProvidersConfig()
        assert cfg.plugins == {}
