"""Загрузка сценария и перевод старых версий формата (``spec_version``)."""

from __future__ import annotations

from typing import Any

from autogenerator.contracts import SPEC_VERSION, AgenError, ErrorCode, ScenarioSpec
from autogenerator.contracts.yaml_io import parse_model


def load_scenario(data: Any, where: str = "сценарий") -> ScenarioSpec:
    """Сценарий из словаря (прочитанного YAML или JSON).

    Версия формата 1 — первая, переводить пока нечего. Параметры отдельных узлов переводит
    сам плагин (``migrate_params``), когда движок разбирает сценарий.
    """
    if not isinstance(data, dict):
        raise AgenError(ErrorCode.SPEC_INVALID, f"{where}: ожидался словарь с полями сценария")
    version = data.get("spec_version", SPEC_VERSION)
    if not isinstance(version, int) or version < 1:
        raise AgenError(ErrorCode.SPEC_INVALID, f"{where}: неверная spec_version «{version}»")
    if version > SPEC_VERSION:
        raise AgenError(
            ErrorCode.SPEC_INVALID,
            f"{where}: формат сценария версии {version} новее приложения (оно знает {SPEC_VERSION})",
            hint="Обновите приложение.",
        )
    return parse_model(ScenarioSpec, data, where)
