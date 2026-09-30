"""Что узнали о файле выгрузки: снимок структуры, сверка с источником, результат записи
(ARCHITECTURE.md, разделы 6.1 и 6.2)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field

from .sources import DType, ReadOptions


class ColumnSnapshot(BaseModel):
    """Столбец файла в том виде, в каком он пришёл."""

    source_name: str = Field(description="Название в файле")
    normalized_name: str = Field("", description="Название после нормализации (заполняет schema)")
    dtype: DType = Field(description="Тип, выведенный по выборке")
    format: str | None = Field(None, description="Формат, по которому распознан тип (например, %d.%m.%Y)")
    non_null: int = 0
    sample: list[str] = Field(default_factory=list, description="Несколько значений как в файле")


class SchemaSnapshot(BaseModel):
    """Снимок структуры файла."""

    path: str
    format: str
    options: ReadOptions
    columns: list[ColumnSnapshot]
    sample_rows: int = Field(description="Сколько строк было в выборке для вывода типов")

    def names(self) -> list[str]:
        return [c.source_name for c in self.columns]


class ReconcileStatus(StrEnum):
    OK = "ok"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"


class ReconcileResult(BaseModel):
    """Результат сверки структуры файла с источником."""

    status: ReconcileStatus
    mapping: dict[str, str] = Field(default_factory=dict, description="Название в файле → id столбца источника")
    missing_required: list[str] = Field(
        default_factory=list, description="id столбцов, которые нужны сценарию, но не найдены"
    )
    missing_optional: list[str] = Field(
        default_factory=list, description="id столбцов, которых нет, но сценарию они не нужны"
    )
    unmapped_file_columns: list[str] = Field(default_factory=list, description="Столбцы файла, которых нет в источнике")
    messages: list[str] = Field(default_factory=list)


class CastIssue(BaseModel):
    """Ошибки приведения типа в одном столбце."""

    column: str
    dtype: DType
    errors: int = 0
    examples: list[str] = Field(default_factory=list, description="До 20 значений, которые не привелись")


class UploadStatus(StrEnum):
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"
    EXCLUDED = "excluded"


class UploadResult(BaseModel):
    """Итог записи одной выгрузки в Parquet."""

    upload_id: str
    upload_seq: int
    data_uri: str = Field(description="Папка с Parquet, разложенным по месяцам столбца периода")
    rejects_uri: str | None = Field(None, description="Строки с ошибками приведения, как в файле")
    rows: int
    months: list[str] = Field(default_factory=list, description="Месяцы, в которые попали строки")
    null_period_rows: int = 0
    period_min: date | None = None
    period_max: date | None = None
    cast_issues: list[CastIssue] = Field(default_factory=list)
    status: UploadStatus = UploadStatus.ACTIVE
    review_reasons: list[str] = Field(default_factory=list)
