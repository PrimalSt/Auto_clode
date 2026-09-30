"""Источник данных: откуда приходят выгрузки и как их читать (PRD, раздел 6;
ARCHITECTURE.md, раздел 8)."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .periods import PeriodUnit

ID_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")


class DType(StrEnum):
    """Тип столбца источника."""

    STRING = "string"
    INT = "int"
    FLOAT = "float"
    DATE = "date"
    DATETIME = "datetime"
    BOOL = "bool"


class OverlapPolicy(StrEnum):
    """Что делать, если новая загрузка покрывает уже загруженный период (раздел 6.3)."""

    REPLACE_PERIOD = "replace_period"
    MERGE_DEDUPE = "merge_dedupe"
    REPLACE_ALL = "replace_all"
    APPEND = "append"
    ASK = "ask"


class ReadOptions(BaseModel):
    """Параметры чтения файла. Хранятся в источнике, чтобы следующая выгрузка читалась так же."""

    model_config = ConfigDict(extra="forbid")

    encoding: str | None = Field(None, description="utf-8, utf-8-sig или cp1251; пусто — определить")
    delimiter: str | None = Field(None, description="Разделитель CSV; пусто — определить")
    header_row: int = Field(1, ge=1, description="Номер строки заголовков, с единицы")
    sheet: str | int | None = Field(None, description="Лист Excel: имя или номер с нуля")

    def merged(self, other: ReadOptions | None) -> ReadOptions:
        """Заполнить пустые поля значениями из ``other`` (обычно — из автоопределения)."""
        if other is None:
            return self
        data = other.model_dump()
        data.update({k: v for k, v in self.model_dump(exclude_unset=True).items() if v is not None})
        return ReadOptions(**data)


class ColumnSpec(BaseModel):
    """Столбец источника. ``id`` — стабильное латинское имя, на которое ссылается сценарий;
    ``name`` и ``aliases`` — названия этого столбца в файлах выгрузки."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    dtype: DType = DType.STRING
    aliases: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not ID_PATTERN.match(v):
            raise ValueError(f"id столбца «{v}» должен состоять из строчных латинских букв, цифр и «_»")
        return v


class SourceSpec(BaseModel):
    """Настройки источника."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: int = 1
    format: str | None = Field(None, description="csv, xlsx; пусто — определить по файлу")
    options: ReadOptions = Field(default_factory=ReadOptions)
    period_column: str
    period_type: PeriodUnit = PeriodUnit.MONTH
    overlap_policy: OverlapPolicy = OverlapPolicy.REPLACE_PERIOD
    keys: list[str] = Field(default_factory=list)
    columns: list[ColumnSpec]

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not ID_PATTERN.match(v):
            raise ValueError(f"id источника «{v}» должен быть латиницей: строчные буквы, цифры, «_»")
        return v

    @model_validator(mode="after")
    def _check_columns(self) -> SourceSpec:
        ids = [c.id for c in self.columns]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"Повторяются id столбцов: {', '.join(sorted(dupes))}")
        if self.period_column not in ids:
            raise ValueError(f"Столбец периода «{self.period_column}» не описан в columns")
        if self.column(self.period_column).dtype not in (DType.DATE, DType.DATETIME):
            raise ValueError(f"Столбец периода «{self.period_column}» должен иметь тип date или datetime")
        missing_keys = [k for k in self.keys if k not in ids]
        if missing_keys:
            raise ValueError(f"Ключевые столбцы не описаны в columns: {', '.join(missing_keys)}")
        return self

    def column(self, column_id: str) -> ColumnSpec:
        for c in self.columns:
            if c.id == column_id:
                return c
        raise KeyError(column_id)

    @property
    def dtypes(self) -> dict[str, DType]:
        return {c.id: c.dtype for c in self.columns}
