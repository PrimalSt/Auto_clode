"""JSON Schema сценария для редактора кода и конструктора окна.

Схема модели ``ScenarioSpec`` описывает сценарий после разбора: параметры шагов, окон и блоков
в ней — открытый словарь, а короткие записи (``window: quarter_to_date``, ``group_by: [region]``,
``example: 256``) отмечены только ключом ``x-short`` (``contracts.short_form``). Редактору нужно
то, что пишет человек: здесь параметры каждого плагина подставляются по полю ``type``
(проверка и автодополнение), привязки меток получают схему из плагина ``markers``, а узлы с
``x-short`` становятся ``anyOf: [короткая запись, полная]``.
"""

from __future__ import annotations

import copy
from typing import Any

from autogenerator.contracts import SHORT_FORM_KEY, PluginKind, PluginManifest, PluginStatus, ScenarioSpec

# Общие поля узлов с параметрами плагина: они есть у каждого типа.
COMMON = {
    "StepSpec": ("id", "type", "type_version", "enabled"),
    "WindowSpec": ("type",),
    "BlockSpec": ("type", "type_version", "id", "slot", "shape"),
}
KINDS = {"StepSpec": PluginKind.STEP, "WindowSpec": PluginKind.WINDOW, "BlockSpec": PluginKind.BLOCK}


def _rewrite_refs(node: Any, prefix: str) -> Any:
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/$defs/"):
                out[k] = "#/$defs/" + prefix + v.removeprefix("#/$defs/")
            else:
                out[k] = _rewrite_refs(v, prefix)
        return out
    if isinstance(node, list):
        return [_rewrite_refs(v, prefix) for v in node]
    return node


def _embed(params: dict[str, Any], prefix: str, defs: dict[str, Any]) -> dict[str, Any]:
    """Схема параметров плагина без своих ``$defs``: они переезжают в общие под префиксом."""
    params = copy.deepcopy(params)
    for name, sub in params.pop("$defs", {}).items():
        defs[prefix + name] = _rewrite_refs(sub, prefix)
    return _rewrite_refs(params, prefix)


def _expand_short_forms(node: Any) -> Any:
    if isinstance(node, list):
        return [_expand_short_forms(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: _expand_short_forms(v) for k, v in node.items() if k != SHORT_FORM_KEY}
    if SHORT_FORM_KEY in node:
        return {"anyOf": [node[SHORT_FORM_KEY], out]}
    return out


def _params(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Схема параметров плагина, если она построилась: у сломанной вместо неё ``{"error": …}``,
    и параметры такого плагина редактор не проверяет."""
    if not schema or not isinstance(schema.get("properties"), dict):
        return None
    return schema


def _plugin_branches(base: dict[str, Any], kind: PluginKind, manifest: PluginManifest, defs: dict[str, Any], node: str):
    """Ветки ``if type == имя → then параметры`` и список имён для подсказки ``type``."""
    names: list[str] = []
    branches: list[dict[str, Any]] = []
    for p in manifest.plugins:
        if p.kind != kind or p.status != PluginStatus.OK:
            continue
        names.append(p.name)
        schema = _params(p.params_schema)
        if schema is None:
            continue
        params = _embed(schema, f"{node}_{p.name}_", defs)
        props = {k: v for k, v in base["properties"].items() if k in COMMON[node]}
        props.update(params.get("properties", {}))
        then: dict[str, Any] = {"properties": props, "additionalProperties": False}
        if params.get("required"):
            then["required"] = list(params["required"])
        if p.title:
            then["description"] = p.title
        branches.append({"if": {"properties": {"type": {"const": p.name}}, "required": ["type"]}, "then": then})
    return names, branches


def scenario_editor_schema(manifest: PluginManifest | None = None) -> dict[str, Any]:
    """Схема сценария в том виде, в каком его пишут: с короткими записями и, если известен
    манифест плагинов, с параметрами каждого шага, окна и блока."""
    schema = ScenarioSpec.model_json_schema()
    defs: dict[str, Any] = schema["$defs"]

    if manifest is not None:
        for node, kind in KINDS.items():
            base = defs[node]
            names, branches = _plugin_branches(base, kind, manifest, defs, node)
            if names:
                type_prop = dict(base["properties"]["type"])
                type_prop["anyOf"] = [{"enum": names}, {"type": "string"}]
                type_prop.pop("type", None)
                base["properties"]["type"] = type_prop
            if branches:
                base["allOf"] = branches
        markers = next(
            (_params(p.params_schema) for p in manifest.plugins if p.kind == PluginKind.BLOCK and p.name == "markers"),
            None,
        )
        bindings = (markers or {}).get("properties", {}).get("bindings", {})
        if isinstance(bindings, dict) and isinstance(bindings.get("additionalProperties"), dict):
            embedded = _embed(markers or {}, "Marker_", defs)
            binding = embedded["properties"]["bindings"]["additionalProperties"]
            for owner in (schema, defs["SlideSpec"]):
                owner["properties"]["markers"]["additionalProperties"] = binding

    return _expand_short_forms(schema)
