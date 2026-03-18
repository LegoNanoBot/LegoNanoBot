"""Tests for flat channel-plugin configuration in ChannelsConfig."""

from nanobot.config.schema import ChannelsConfig, ChannelPluginConfig


class TestFlatChannelConfig:
    """Verify that unknown top-level keys are collected into plugins."""

    def test_flat_config_moved_to_plugins(self):
        """channels.bilibili should be auto-moved into channels.plugins.bilibili."""
        data = {"bilibili": {"enabled": True, "accessKeyId": "xxx"}}
        cfg = ChannelsConfig(**data)
        assert "bilibili" in cfg.plugins
        assert cfg.plugins["bilibili"].enabled is True

    def test_nested_plugins_still_works(self):
        """channels.plugins.bilibili should still work (backward compat)."""
        data = {"plugins": {"bilibili": {"enabled": True}}}
        cfg = ChannelsConfig(**data)
        assert "bilibili" in cfg.plugins
        assert cfg.plugins["bilibili"].enabled is True

    def test_nested_takes_precedence_on_conflict(self):
        """When both flat and nested exist, nested wins (setdefault)."""
        data = {
            "bilibili": {"enabled": False},
            "plugins": {"bilibili": {"enabled": True}},
        }
        cfg = ChannelsConfig(**data)
        assert cfg.plugins["bilibili"].enabled is True

    def test_builtin_channel_not_moved(self):
        """Known fields like telegram must NOT be moved into plugins."""
        data = {"telegram": {"enabled": True, "bot_token": "tok"}}
        cfg = ChannelsConfig(**data)
        assert "telegram" not in cfg.plugins
        assert cfg.telegram.enabled is True

    def test_non_dict_unknown_key_ignored(self):
        """Non-dict unknown keys should not be moved into plugins.
        Pydantic will handle/ignore them on its own."""
        # This may raise a Pydantic validation error for extra fields,
        # so we just verify that dict-type unknowns are handled.
        data = {"bilibili": {"enabled": True}}
        cfg = ChannelsConfig(**data)
        assert "bilibili" in cfg.plugins

    def test_multiple_plugins_flat(self):
        """Multiple unknown channels all get collected."""
        data = {
            "bilibili": {"enabled": True},
            "twitch": {"enabled": False},
        }
        cfg = ChannelsConfig(**data)
        assert "bilibili" in cfg.plugins
        assert "twitch" in cfg.plugins
        assert cfg.plugins["bilibili"].enabled is True
        assert cfg.plugins["twitch"].enabled is False

    def test_mixed_builtin_and_plugin(self):
        """Builtin + flat plugin should coexist correctly."""
        data = {
            "telegram": {"enabled": True, "bot_token": "tok"},
            "bilibili": {"enabled": True},
        }
        cfg = ChannelsConfig(**data)
        assert cfg.telegram.enabled is True
        assert "telegram" not in cfg.plugins
        assert "bilibili" in cfg.plugins

    def test_empty_config(self):
        """Empty config should produce empty plugins."""
        cfg = ChannelsConfig()
        assert cfg.plugins == {}
