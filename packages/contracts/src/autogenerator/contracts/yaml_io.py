"""Чтение и запись спецификаций (источники, сценарии) в YAML.

Сценарий — это данные: его можно открыть, поправить руками и положить в git. Все модули
читают YAML одинаково, через эти функции.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .errors import AgenError, ErrorCode


def _yaml() -> YAML:
    y = YAML(typ="safe", pure=True)
    y.default_flow_style = False
    y.allow_unicode = True
    return y


def load_yaml(path: str | Path) -> Any:
    p = Path(path)
    if not p.exists():
        raise AgenError(ErrorCode.FILE_NOT_FOUND, f"Файл не найден: {p}")
    try:
        with p.open(encoding="utf-8") as f:
            return _yaml().load(f)
    except YAMLError as e:
        raise AgenError(ErrorCode.SPEC_INVALID, f"Ошибка в YAML {p.name}: {e}") from e


def loads_yaml(text: str, where: str = "YAML") -> Any:
    try:
        return _yaml().load(text)
    except YAMLError as e:
        raise AgenError(ErrorCode.SPEC_INVALID, f"Ошибка в YAML {where}: {e}") from e


def dump_yaml(data: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def validation_message(e: ValidationError, where: str) -> str:
    """Ошибки Pydantic одним читаемым списком."""
    lines = [f"{where}: ошибки в спецификации"]
    for err in e.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(корень)"
        lines.append(f"  • {loc}: {err['msg']}")
    return "\n".join(lines)


def parse_model[M: BaseModel](model: type[M], data: Any, where: str) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise AgenError(ErrorCode.SPEC_INVALID, validation_message(e, where)) from e


def load_model[M: BaseModel](model: type[M], path: str | Path) -> M:
    return parse_model(model, load_yaml(path), Path(path).name)


def load_model_list[M: BaseModel](model: type[M], path: str | Path) -> list[M]:
    data = load_yaml(path)
    if isinstance(data, dict) and "sources" in data:
        data = data["sources"]
    if not isinstance(data, list):
        raise AgenError(ErrorCode.SPEC_INVALID, f"{Path(path).name}: ожидался список")
    return [parse_model(model, item, f"{Path(path).name}[{i}]") for i, item in enumerate(data)]
