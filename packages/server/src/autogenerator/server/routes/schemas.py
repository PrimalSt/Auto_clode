"""JSON Schema сценария и источника для редактора кода (Monaco проверяет и дополняет YAML по
схеме) и для форм конструктора."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter

from autogenerator.contracts import AgenError, SourceSpec

from ..deps import StateDep
from ..editor_schema import scenario_editor_schema

router = APIRouter(tags=["схемы"])


@router.get("/api/schemas/{name}")
def schema(name: Literal["scenario", "source"], state: StateDep) -> dict[str, Any]:
    """Схема сценария — с короткими записями и параметрами шагов, окон и блоков из манифеста
    плагинов (если исполнитель мелких вызовов не запустился — без параметров плагинов)."""
    if name == "source":
        return SourceSpec.model_json_schema()
    try:
        manifest = state.plugin_manifest()
    except AgenError:
        manifest = None
    return scenario_editor_schema(manifest)
