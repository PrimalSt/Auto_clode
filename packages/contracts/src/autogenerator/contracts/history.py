"""Манифест истории: всё, что нужно знать о загрузках входа, чтобы вычислить действующую
историю, не открывая хранилище метаданных (ARCHITECTURE.md, раздел 6.3)."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from .periods import DateSpan, Period, PeriodUnit
from .snapshot import UploadStatus
from .sources import DType, OverlapPolicy

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


class HistoryManifest(BaseModel):
    """Загрузки одного источника и правила их сложения."""

    source_id: str
    source_version: int = 1
    period_column: str
    period_type: PeriodUnit
    overlap_policy: OverlapPolicy
    keys: list[str] = Field(default_factory=list)
    columns: dict[str, DType] = Field(description="id столбца → тип в источнике")
    uploads: list[UploadRef] = Field(default_factory=list)

    @property
    def active_uploads(self) -> list[UploadRef]:
        return sorted((u for u in self.uploads if u.status == UploadStatus.ACTIVE), key=lambda u: u.seq)


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
