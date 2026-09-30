from importlib.metadata import EntryPoint
from typing import Any

import pytest

from autogenerator.contracts import (
    AgenError,
    AggregationPlugin,
    ErrorCode,
    PluginKind,
    PluginStatus,
    StepPlugin,
)
from autogenerator.plugin_host import PluginRegistry, api_compatible


class GoodAgg(AggregationPlugin):
    name = "good"

    def polars_expr(self, column: str | None) -> Any:
        return None

    def sql(self, column: str | None) -> str:
        return "GOOD()"


class OldAgg(GoodAgg):
    name = "old"
    api_version = "0.0"


def test_api_compatibility():
    assert api_compatible("0.1", "0.1")
    assert api_compatible("0.1.5", "0.1")
    assert not api_compatible("0.2", "0.1")
    assert api_compatible("1.3", "1.0")
    assert not api_compatible("2.0", "1.0")
    assert not api_compatible("x", "0.1")


def _ep(name: str, value: str, group: str = "autogenerator.aggregations") -> EntryPoint:
    return EntryPoint(name=name, value=value, group=group)


def test_broken_plugins_are_isolated():
    reg = PluginRegistry()
    reg._load_entry_point(PluginKind.AGGREGATION, _ep("good", f"{__name__}:GoodAgg"))
    reg._load_entry_point(PluginKind.AGGREGATION, _ep("missing", "no_such_module_xyz:Thing"))
    reg._load_entry_point(PluginKind.AGGREGATION, _ep("old", f"{__name__}:OldAgg"))
    reg._load_entry_point(PluginKind.AGGREGATION, _ep("wrong_kind", f"{__name__}:StepPlugin"))
    reg._load_entry_point(PluginKind.AGGREGATION, _ep("renamed", f"{__name__}:GoodAgg"))

    assert reg.names(PluginKind.AGGREGATION) == ["good"]
    manifest = reg.manifest()
    status = {p.name: p.status for p in manifest.plugins}
    assert status == {
        "good": PluginStatus.OK,
        "missing": PluginStatus.ERROR,
        "old": PluginStatus.ERROR,
        "wrong_kind": PluginStatus.ERROR,
        "renamed": PluginStatus.ERROR,
    }
    errors = {p.name: p.error for p in manifest.broken()}
    assert "No module named" in errors["missing"]
    assert "API 0.0" in errors["old"]

    with pytest.raises(AgenError) as e:
        reg.get(PluginKind.AGGREGATION, "missing")
    assert e.value.code == ErrorCode.PLUGIN_BROKEN
    with pytest.raises(AgenError) as e:
        reg.get(PluginKind.AGGREGATION, "nothing")
    assert e.value.code == ErrorCode.PLUGIN_NOT_FOUND


def test_from_plugins_and_duplicate_names():
    reg = PluginRegistry.from_plugins([GoodAgg, GoodAgg()])
    assert reg.aggregation("good").sql(None) == "GOOD()"
    assert len(reg.manifest().broken()) == 1


def test_manifest_has_params_schema():
    class P(StepPlugin):
        name = "p"

        def columns_used(self, params: Any, tools: Any) -> set[str]:
            return set()

        def apply(self, lf: Any, params: Any, ctx: Any) -> Any:
            return lf

    info = PluginRegistry.from_plugins([P]).manifest().plugins[0]
    assert info.params_schema is not None and info.type_version == 1
