"""Provider plugin loading helpers."""

from __future__ import annotations

import importlib.metadata as importlib_metadata
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.config.schema import Config, ProviderConfig
    from nanobot.providers.base import LLMProvider

ProviderFactory = Callable[..., "LLMProvider"]


def _iter_entry_points(group: str) -> list[Any]:
    """Return entry points for a group, compatible with Python 3.11+ APIs."""
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return list(entry_points.select(group=group))
    return list(entry_points.get(group, []))


def load_provider_factories() -> dict[str, ProviderFactory]:
    """Load plugin provider factories from ``nanobot.provider_factories`` entry points."""
    factories: dict[str, ProviderFactory] = {}
    for ep in _iter_entry_points("nanobot.provider_factories"):
        try:
            factory = ep.load()
        except Exception as exc:
            logger.warning("Failed loading provider factory plugin {}: {}", ep.name, exc)
            continue

        if not callable(factory):
            logger.warning("Ignore provider factory plugin {}: object is not callable", ep.name)
            continue

        name = ep.name.replace("-", "_")
        if name in factories:
            logger.warning("Ignore provider factory plugin {}: duplicate provider name", name)
            continue
        factories[name] = factory
    return factories


def get_provider_factory(name: str | None) -> ProviderFactory | None:
    """Get provider factory by provider name (normalized to snake_case)."""
    if not name:
        return None
    normalized = name.replace("-", "_")
    return load_provider_factories().get(normalized)


def create_provider_by_factory(
    factory: ProviderFactory,
    *,
    config: "Config",
    model: str,
    provider_name: str | None,
    provider_config: "ProviderConfig | None",
) -> "LLMProvider":
    """Create provider from plugin factory using a stable keyword contract."""
    return factory(
        config=config,
        model=model,
        provider_name=provider_name,
        provider_config=provider_config,
    )


def validate_provider_plugins(config: "Config") -> None:
    """Validate configured plugin providers at startup.

    For each entry in config.providers.plugins, check:
    1. Whether a matching ProviderSpec is loaded (from nanobot.provider_specs)
    2. Whether a matching ProviderFactory is available (from nanobot.provider_factories)
    Log warnings for any misconfigurations.
    """
    from nanobot.providers.registry import find_by_name

    if not config.providers.plugins:
        return

    factories = load_provider_factories()
    for plugin_name, plugin_config in config.providers.plugins.items():
        normalized = plugin_name.replace("-", "_")
        spec = find_by_name(normalized)
        factory = factories.get(normalized)

        if not spec and not factory:
            logger.warning(
                "Provider plugin '{}' is configured but neither spec nor factory found. "
                "Is the plugin installed? (pip install nanobot-provider-{}-plugin)",
                plugin_name,
                plugin_name,
            )
        elif spec and not factory:
            logger.info(
                "{} provider plugin loaded (spec only, will use LiteLLM)",
                spec.label,
            )
        elif factory and not spec:
            logger.info(
                "{} provider plugin loaded (factory only, requires explicit provider setting)",
                plugin_name,
            )
        else:
            logger.info("{} provider plugin loaded (spec + factory)", spec.label)
