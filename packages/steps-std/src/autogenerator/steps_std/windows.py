"""Встроенные окна данных: какой отрезок истории берётся относительно отчётного периода
``P`` (ARCHITECTURE.md, раздел 6.4). Все отрезки — ``[start, end_exclusive)``."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from autogenerator.contracts import DateSpan, Period, PeriodUnit, WindowPlugin
from autogenerator.contracts.periods import unit_start


class ReportPeriodWindow(WindowPlugin):
    name = "report_period"
    title = "Отчётный период"

    def resolve(self, period: Period, params: Any) -> DateSpan:
        return period.span


class PreviousPeriodWindow(WindowPlugin):
    """Предыдущая единица; для произвольного диапазона — такой же длины сразу перед ним."""

    name = "previous_period"
    title = "Предыдущий период"

    def resolve(self, period: Period, params: Any) -> DateSpan:
        return period.shift(-1).span


class SamePeriodLastYearWindow(WindowPlugin):
    name = "same_period_last_year"
    title = "Тот же период год назад"

    def resolve(self, period: Period, params: Any) -> DateSpan:
        return period.years_ago(1).span


class QuarterToDateWindow(WindowPlugin):
    """С начала квартала, в который попадает конец ``P``, по конец ``P``."""

    name = "quarter_to_date"
    title = "С начала квартала"

    def resolve(self, period: Period, params: Any) -> DateSpan:
        start = unit_start(period.last_day, PeriodUnit.QUARTER)
        return DateSpan(start=min(start, period.start), end_exclusive=period.end_exclusive)


class YearToDateWindow(WindowPlugin):
    name = "year_to_date"
    title = "С начала года"

    def resolve(self, period: Period, params: Any) -> DateSpan:
        start = unit_start(period.last_day, PeriodUnit.YEAR)
        return DateSpan(start=min(start, period.start), end_exclusive=period.end_exclusive)


class LastNParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int = Field(ge=1, description="Сколько периодов, включая отчётный")


class LastNWindow(WindowPlugin):
    """``n`` единиц, заканчивая ``P``: ``last_n(6)`` при ``P`` = март 2026 — октябрь 2025 – март 2026."""

    name = "last_n"
    title = "Последние N периодов"
    Params = LastNParams

    def resolve(self, period: Period, params: Any) -> DateSpan:
        return DateSpan(start=period.shift(-(params.n - 1)).start, end_exclusive=period.end_exclusive)


class AllWindow(WindowPlugin):
    name = "all"
    title = "Вся история"

    def resolve(self, period: Period, params: Any) -> DateSpan:
        return DateSpan(start=None, end_exclusive=period.end_exclusive)


class RangeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date = Field(description="Первый день")
    end: date = Field(description="Последний день, включительно")


class RangeWindow(WindowPlugin):
    name = "range"
    title = "Произвольный диапазон"
    Params = RangeParams

    def resolve(self, period: Period, params: Any) -> DateSpan:
        return DateSpan(start=params.start, end_exclusive=params.end + timedelta(days=1))
