"""Манифест истории: всё, что нужно знать о загрузках входа, чтобы вычислить действующую
историю, не открывая хранилище метаданных (ARCHITECTURE.md, раздел 6.3)."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from .periods import DateSpan, Period, PeriodUnit
from .snapshot import UploadStatus
from .sources import DType, OverlapPolicy, PeriodFrom, SourceSpec

if TYPE_CHECKING:
    import polars as pl


class UploadRef(BaseModel):
    """Одна загрузка в истории источника."""

    id: str
    seq: int = Field(description="Порядковый номер загрузки: чем больше, тем новее")
    uri: str = Field(description="Папка загрузки с Parquet по месяцам")
    period: Period = Field(description="Объявленный период загрузки")
    rows: int = 0
    status: UploadStatus = UploadStatus.ACTIVE
    original_name: str = ""
    overlap_policy: OverlapPolicy | None = Field(
        None,
        description="Правило пересечения, выбранное для этой загрузки, когда у источника правило «ask»",
    )
    uploaded_at: datetime | None = None


class HistoryManifest(BaseModel):
    """Загрузки одного источника и правила их сложения."""

    source_id: str
    source_version: int = 1
    period_column: str
    period_type: PeriodUnit
    period_from: PeriodFrom = PeriodFrom.COLUMN
    overlap_policy: OverlapPolicy
    keys: list[str] = Field(default_factory=list)
    columns: dict[str, DType] = Field(description="id столбца → тип в источнике")
    uploads: list[UploadRef] = Field(default_factory=list)

    @classmethod
    def for_source(cls, spec: SourceSpec, uploads: list[UploadRef] | None = None) -> HistoryManifest:
        return cls(
            source_id=spec.id,
            source_version=spec.version,
            period_column=spec.period_column,
            period_type=spec.period_type,
            period_from=spec.period_from,
            overlap_policy=spec.overlap_policy,
            keys=spec.keys,
            columns=spec.dtypes,
            uploads=uploads or [],
        )

    @property
    def active_uploads(self) -> list[UploadRef]:
        return sorted((u for u in self.uploads if u.status == UploadStatus.ACTIVE), key=lambda u: u.seq)

    def policy_of(self, upload: UploadRef) -> OverlapPolicy:
        """Правило, по которому загрузка ложится на более ранние: своё (выбранное при «ask»)
        или правило источника."""
        return upload.overlap_policy or self.overlap_policy


class CoverageState(StrEnum):
    """Состояние единицы периода на шкале покрытия."""

    COVERED = "covered"
    GAP = "gap"
    OVERLAP = "overlap"


class CoverageCell(BaseModel):
    """Одна единица шкалы (месяц, неделя, день…): чьи загрузки её покрывают."""

    period: Period
    uploads: list[str] = Field(default_factory=list, description="id активных загрузок, покрывающих единицу")
    state: CoverageState


class CoverageReport(BaseModel):
    """Покрытие истории: объединённые отрезки, пропуски и наложения (экран истории, F-156)."""

    spans: list[DateSpan] = Field(default_factory=list, description="Покрытые отрезки по возрастанию")
    gaps: list[DateSpan] = Field(default_factory=list, description="Пропуски между покрытыми отрезками")
    overlaps: list[DateSpan] = Field(default_factory=list, description="Отрезки, покрытые двумя и более загрузками")
    unit: PeriodUnit = Field(description="Единица шкалы")
    cells: list[CoverageCell] = Field(default_factory=list)


class HistoryProvider(Protocol):
    """Как движок получает историю входов сценария. Реализацию подставляет тот, кто
    собирает модули вместе (``worker``): сам ``engine`` о хранилище и модуле ``history``
    не знает."""

    def period_column(self, input_id: str) -> str: ...

    def columns(self, input_id: str) -> dict[str, DType]: ...

    def scan(
        self,
        input_id: str,
        columns: list[str] | None = None,
        lower: date | None = None,
        upper_exclusive: date | None = None,
    ) -> pl.LazyFrame:
        """Ленивая таблица действующей истории входа.

        ``lower`` — нижняя граница по столбцу периода (если её можно протолкнуть),
        ``upper_exclusive`` — верхняя: история обрезается по концу отчётного периода.
        """
        ...

    def coverage(self, input_id: str) -> list[DateSpan]:
        """Покрытие входа загрузками: объединённые периоды загрузок по возрастанию."""
        ...
