"""Реестр плагинов: обнаружение по entry points, проверка версии API, манифест."""

from .isolated import discover_manifest_isolated
from .registry import PluginRegistry, api_compatible

__all__ = ["PluginRegistry", "api_compatible", "discover_manifest_isolated"]
