"""Что узнали о файле выгрузки: снимок структуры, профиль столбцов, сверка с источником,
результат записи (ARCHITECTURE.md, разделы 6.1 и 6.2)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field

from .sources import DType, ReadOptions


class ValueCount(BaseModel):
    value: str
    count: int


class ColumnProfile(BaseModel):
    """Профиль столбца: пустые, уникальные, минимум, максимум, самые частые значения (F-106).

    ``exact=False`` — профиль посчитан по выборке; точный считается по записанной загрузке.
    Значения — текстом в том виде, в каком их удобно показать (даты — ``ГГГГ-ММ-ДД``).
    """

    rows: int = 0
    nulls: int = 0
    unique: int | None = None
    unique_approx: bool = Field(False, description="Число уникальных — оценка (на миллионах строк)")
    min: str | None = None
    max: str | None = None
    top: list[ValueCount] = Field(default_factory=list, description="До 5 самых частых значений")
    top_skipped: bool = Field(False, description="Почти все значения разные: частые не считались")
    exact: bool = True


class ColumnSnapshot(BaseModel):
    """Столбец файла в том виде, в каком он пришёл."""

    source_name: str = Field(description="Название в файле")
    normalized_name: str = Field("", description="Название после нормализации (заполняет schema)")
    dtype: DType = Field(description="Тип, выведенный по выборке")
    format: str | None = Field(None, description="Формат, по которому распознан тип (например, %d.%m.%Y)")
    non_null: int = 0
    sample: list[str] = Field(default_factory=list, description="Несколько значений как в файле")
    parsed_share: float | None = Field(None, description="Доля непустых значений выборки, которые привелись к типу")
    profile: ColumnProfile | None = None


class SchemaSnapshot(BaseModel):
    """Снимок структуры файла."""

    path: str
    format: str
    options: ReadOptions = Field(description="Параметры, с которыми файл прочитан (найденные и заданные)")
    columns: list[ColumnSnapshot]
    sample_rows: int = Field(description="Сколько строк было в выборке для вывода типов")
    sample_parts: list[str] = Field(
        default_factory=list, description="Откуда взята выборка: начало, середина, конец файла"
    )
    sheets: list[str] = Field(default_factory=list, description="Листы Excel, которые входят в выгрузку")
    file_size: int = 0
    rows_estimate: int | None = Field(None, description="Оценка числа строк (для CSV — по размеру файла)")
    preview: list[list[str | None]] = Field(default_factory=list, description="Первые строки файла как текст")
    notes: list[str] = Field(default_factory=list, description="Что заметили при чтении")

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
    by_pattern: dict[str, str] = Field(
        default_factory=dict,
        description="Столбцы файла, найденные по названию с другим месяцем: название в файле → название в источнике",
    )
    messages: list[str] = Field(default_factory=list, description="Сведения о сверке")
    warnings: list[str] = Field(
        default_factory=list, description="То, что стоит проверить: столбцы без пары, несколько подходящих столбцов"
    )


class CastIssue(BaseModel):
    """Ошибки приведения типа в одном столбце."""

    column: str
    dtype: DType
    errors: int = 0
    examples: list[str] = Field(default_factory=list, description="До 20 значений, которые не привелись")
    first_rows: list[int] = Field(default_factory=list, description="Номера первых строк данных с ошибкой")


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
    empty_rows: int = Field(0, description="Пустые строки файла, которые пропущены")
    months: list[str] = Field(default_factory=list, description="Месяцы, в которые попали строки")
    null_period_rows: int = 0
    period_min: date | None = None
    period_max: date | None = None
    cast_issues: list[CastIssue] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list, description="Замечания читателя: например, у скольких строк отброшены лишние поля"
    )
    status: UploadStatus = UploadStatus.ACTIVE
    review_reasons: list[str] = Field(default_factory=list)
    options: ReadOptions | None = Field(None, description="Параметры, с которыми файл прочитан")
    sheets: list[str] = Field(default_factory=list)
    bytes_written: int = 0
    seconds: float = 0.0
