"""Реестр плагинов.

Плагины находятся по entry points и загружаются по одному: ошибка импорта, несовместимая
версия API или неправильный класс помечают неисправным только этот плагин, остальные
работают (ARCHITECTURE.md, раздел 4.3).
"""

from __future__ import annotations

import traceback
from collections.abc import Iterable
from importlib.metadata import EntryPoint, entry_points
from typing import Any, TypeVar, cast

from autogenerator.contracts import (
    ENTRY_POINT_GROUPS,
    PLUGIN_API_VERSION,
    AgenError,
    AggregationPlugin,
    BlockPlugin,
    ErrorCode,
    ParamsPlugin,
    Plugin,
    PluginInfo,
    PluginKind,
    PluginManifest,
    PluginStatus,
    ReaderPlugin,
    StepPlugin,
    WindowPlugin,
)

BASE_CLASSES: dict[PluginKind, type[Plugin]] = {
    PluginKind.READER: ReaderPlugin,
    PluginKind.STEP: StepPlugin,
    PluginKind.WINDOW: WindowPlugin,
    PluginKind.AGGREGATION: AggregationPlugin,
    PluginKind.BLOCK: BlockPlugin,
}

P = TypeVar("P", bound=Plugin)


def api_compatible(plugin_version: str, host_version: str = PLUGIN_API_VERSION) -> bool:
    """Совместимы ли версии API. Для 0.x несовместимо изменение второго числа, дальше — первого."""
    try:
        p = [int(x) for x in plugin_version.split(".")]
        h = [int(x) for x in host_version.split(".")]
    except ValueError:
        return False
    if h[0] == 0:
        return p[:2] == h[:2]
    return p[0] == h[0]


class PluginRegistry:
    """Загруженные плагины по видам и именам, плюс записи о неисправных."""

    def __init__(self) -> None:
        self._plugins: dict[PluginKind, dict[str, Plugin]] = {k: {} for k in PluginKind}
        self._infos: list[PluginInfo] = []

    # --- загрузка ----------------------------------------------------------------

    @classmethod
    def discover(cls, kinds: Iterable[PluginKind] | None = None) -> PluginRegistry:
        """Найти и загрузить установленные плагины указанных видов (по умолчанию — все)."""
        reg = cls()
        for kind in kinds or list(PluginKind):
            for ep in sorted(entry_points(group=ENTRY_POINT_GROUPS[kind]), key=lambda e: e.name):
                reg._load_entry_point(kind, ep)
        return reg

    @classmethod
    def from_plugins(cls, plugins: Iterable[type[Plugin] | Plugin]) -> PluginRegistry:
        """Реестр из явно переданных классов — для тестов и отладки модуля без установки."""
        reg = cls()
        for p in plugins:
            obj = p() if isinstance(p, type) else p
            reg._register(obj, entry_point=f"{type(obj).__module__}:{type(obj).__name__}")
        return reg

    def _load_entry_point(self, kind: PluginKind, ep: EntryPoint) -> None:
        dist = getattr(ep, "dist", None)
        info = PluginInfo(
            kind=kind,
            name=ep.name,
            entry_point=ep.value,
            distribution=dist.name if dist else None,
            version=dist.version if dist else None,
            status=PluginStatus.ERROR,
        )
        try:
            loaded = ep.load()
            base = BASE_CLASSES[kind]
            if not (isinstance(loaded, type) and issubclass(loaded, base)):
                raise TypeError(f"{ep.value} — не наследник {base.__name__}")
            plugin = loaded()
            if plugin.name != ep.name:
                raise ValueError(f"Имя плагина «{plugin.name}» не совпадает с именем entry point «{ep.name}»")
            if not api_compatible(plugin.api_version):
                raise ValueError(f"Плагин написан для API {plugin.api_version}, приложение даёт {PLUGIN_API_VERSION}")
        except Exception:
            info.error = traceback.format_exc(limit=5)
            self._infos.append(info)
            return
        self._register(plugin, entry_point=ep.value, info=info)

    def _register(self, plugin: Plugin, *, entry_point: str, info: PluginInfo | None = None) -> None:
        kind = plugin.kind
        if plugin.name in self._plugins[kind]:
            prev = self._plugins[kind][plugin.name]
            self._infos.append(
                PluginInfo(
                    kind=kind,
                    name=plugin.name,
                    entry_point=entry_point,
                    status=PluginStatus.ERROR,
                    error=f"Имя уже занято плагином {type(prev).__module__}",
                )
            )
            return
        info = info or PluginInfo(kind=kind, name=plugin.name, entry_point=entry_point, status=PluginStatus.OK)
        info.status = PluginStatus.OK
        info.title = plugin.title
        if isinstance(plugin, ParamsPlugin):
            info.type_version = plugin.type_version
            try:
                info.params_schema = plugin.Params.model_json_schema()
            except Exception as e:  # схема — только для форм; её ошибка не ломает плагин
                info.params_schema = {"error": str(e)}
        self._plugins[kind][plugin.name] = plugin
        self._infos.append(info)

    # --- доступ ------------------------------------------------------------------

    def get(self, kind: PluginKind, name: str, base: type[P] | None = None) -> P:
        plugin = self._plugins[kind].get(name)
        if plugin is None:
            broken = [i for i in self._infos if i.kind == kind and i.name == name]
            if broken:
                raise AgenError(
                    ErrorCode.PLUGIN_BROKEN,
                    f"Плагин {kind} «{name}» не загрузился",
                    details={"error": broken[0].error},
                    hint="Подробности — в `agen modules`.",
                )
            known = ", ".join(sorted(self._plugins[kind])) or "нет"
            raise AgenError(
                ErrorCode.PLUGIN_NOT_FOUND,
                f"Нет плагина {kind} «{name}». Доступны: {known}",
            )
        return cast(P, plugin)

    def has(self, kind: PluginKind, name: str) -> bool:
        return name in self._plugins[kind]

    def names(self, kind: PluginKind) -> list[str]:
        return sorted(self._plugins[kind])

    def all(self, kind: PluginKind) -> list[Any]:
        return [self._plugins[kind][n] for n in sorted(self._plugins[kind])]

    def reader(self, name: str) -> ReaderPlugin:
        return self.get(PluginKind.READER, name, ReaderPlugin)

    def step(self, name: str) -> StepPlugin:
        return self.get(PluginKind.STEP, name, StepPlugin)

    def window(self, name: str) -> WindowPlugin:
        return self.get(PluginKind.WINDOW, name, WindowPlugin)

    def aggregation(self, name: str) -> AggregationPlugin:
        return self.get(PluginKind.AGGREGATION, name, AggregationPlugin)

    def block(self, name: str) -> BlockPlugin:
        return self.get(PluginKind.BLOCK, name, BlockPlugin)

    def manifest(self) -> PluginManifest:
        infos = sorted(self._infos, key=lambda i: (i.kind, i.name, i.status))
        return PluginManifest(plugins=infos)
