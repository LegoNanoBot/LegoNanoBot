"""Tests for provider plugin startup validation."""

import io
from dataclasses import dataclass
from unittest.mock import patch

import pytest
from loguru import logger

from nanobot.config.schema import Config, ProviderConfig
from nanobot.providers.provider_plugins import validate_provider_plugins


@pytest.fixture(autouse=True)
def _enable_nanobot_logging():
    """Re-enable nanobot logging in case a previous test disabled it globally."""
    logger.enable("nanobot")


@dataclass(frozen=True)
class _FakeSpec:
    name: str
    label: str


def test_no_plugins_no_output():
    """Empty plugins dict should produce no log output."""
    output = io.StringIO()
    handler_id = logger.add(output, format="{message}")
    try:
        config = Config()
        validate_provider_plugins(config)
    finally:
        logger.remove(handler_id)
    assert output.getvalue() == ""


def test_warning_when_plugin_not_installed():
    """Configured plugin with no spec and no factory should warn."""
    output = io.StringIO()
    handler_id = logger.add(output, format="{message}")
    try:
        config = Config()
        config.providers.plugins["unknown_provider"] = ProviderConfig(api_key="xxx")

        with (
            patch("nanobot.providers.provider_plugins.load_provider_factories", return_value={}),
            patch("nanobot.providers.registry.find_by_name", return_value=None),
        ):
            validate_provider_plugins(config)
    finally:
        logger.remove(handler_id)

    log_text = output.getvalue()
    assert "neither spec nor factory found" in log_text
    assert "unknown_provider" in log_text


def test_info_spec_only():
    """Plugin with spec but no factory should log info about LiteLLM fallback."""
    output = io.StringIO()
    handler_id = logger.add(output, format="{message}")
    try:
        config = Config()
        config.providers.plugins["demo_provider"] = ProviderConfig(api_key="xxx")
        fake_spec = _FakeSpec(name="demo_provider", label="Demo Provider")

        with (
            patch("nanobot.providers.provider_plugins.load_provider_factories", return_value={}),
            patch("nanobot.providers.registry.find_by_name", return_value=fake_spec),
        ):
            validate_provider_plugins(config)
    finally:
        logger.remove(handler_id)

    log_text = output.getvalue()
    assert "spec only" in log_text
    assert "LiteLLM" in log_text


def test_info_factory_only():
    """Plugin with factory but no spec should log info about explicit setting."""
    output = io.StringIO()
    handler_id = logger.add(output, format="{message}")
    try:
        config = Config()
        config.providers.plugins["demo_provider"] = ProviderConfig(api_key="xxx")
        fake_factory = lambda **kwargs: None

        with (
            patch(
                "nanobot.providers.provider_plugins.load_provider_factories",
                return_value={"demo_provider": fake_factory},
            ),
            patch("nanobot.providers.registry.find_by_name", return_value=None),
        ):
            validate_provider_plugins(config)
    finally:
        logger.remove(handler_id)

    log_text = output.getvalue()
    assert "factory only" in log_text
    assert "explicit provider setting" in log_text


def test_info_spec_and_factory():
    """Plugin with both spec and factory should log success info."""
    output = io.StringIO()
    handler_id = logger.add(output, format="{message}")
    try:
        config = Config()
        config.providers.plugins["demo_provider"] = ProviderConfig(api_key="xxx")
        fake_spec = _FakeSpec(name="demo_provider", label="Demo Provider")
        fake_factory = lambda **kwargs: None

        with (
            patch(
                "nanobot.providers.provider_plugins.load_provider_factories",
                return_value={"demo_provider": fake_factory},
            ),
            patch("nanobot.providers.registry.find_by_name", return_value=fake_spec),
        ):
            validate_provider_plugins(config)
    finally:
        logger.remove(handler_id)

    log_text = output.getvalue()
    assert "spec + factory" in log_text
    assert "Demo Provider" in log_text
