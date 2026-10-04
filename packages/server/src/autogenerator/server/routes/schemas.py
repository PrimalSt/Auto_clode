"""JSON Schema моделей для редактора кода (Monaco проверяет и дополняет YAML по схеме)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from autogenerator.contracts import ScenarioSpec, SourceSpec

router = APIRouter(tags=["схемы"])

MODELS: dict[str, type[BaseModel]] = {"scenario": ScenarioSpec, "source": SourceSpec}


@router.get("/api/schemas/{name}")
def schema(name: Literal["scenario", "source"]) -> dict[str, Any]:
    return MODELS[name].model_json_schema()
