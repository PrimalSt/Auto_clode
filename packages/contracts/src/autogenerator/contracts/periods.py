"""Периоды и отрезки дат.

Все границы — полуоткрытые интервалы ``[start, end_exclusive)``: ``end_exclusive`` — день
после последнего дня периода (ARCHITECTURE.md, раздел 6.3). Здесь только календарная
арифметика самого типа «период», которая нужна сразу нескольким модулям (history, engine,
окна в steps-std). Правил отчёта здесь нет.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator


class PeriodUnit(StrEnum):
    """Единица периода. ``range`` — произвольный диапазон дат."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    RANGE = "range"


CALENDAR_UNITS = (
    PeriodUnit.DAY,
    PeriodUnit.WEEK,
    PeriodUnit.MONTH,
    PeriodUnit.QUARTER,
    PeriodUnit.YEAR,
)


def add_months(d: date, months: int) -> date:
    """Сдвиг на целое число месяцев; день месяца обрезается до последнего дня."""
    total = d.year * 12 + (d.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    last = _days_in_month(year, month)
    return date(year, month, min(d.day, last))


def _days_in_month(year: int, month: int) -> int:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - timedelta(days=1)).day


def unit_start(d: date, unit: PeriodUnit) -> date:
    """Начало календарной единицы, в которую попадает дата."""
    match unit:
        case PeriodUnit.DAY:
            return d
        case PeriodUnit.WEEK:
            return d - timedelta(days=d.weekday())
        case PeriodUnit.MONTH:
            return d.replace(day=1)
        case PeriodUnit.QUARTER:
            return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
        case PeriodUnit.YEAR:
            return date(d.year, 1, 1)
    raise ValueError(f"У единицы {unit} нет календарного начала")


def unit_shift(d: date, unit: PeriodUnit, n: int) -> date:
    """Сдвиг начала единицы на ``n`` единиц."""
    match unit:
        case PeriodUnit.DAY:
            return d + timedelta(days=n)
        case PeriodUnit.WEEK:
            return d + timedelta(weeks=n)
        case PeriodUnit.MONTH:
            return add_months(d, n)
        case PeriodUnit.QUARTER:
            return add_months(d, 3 * n)
        case PeriodUnit.YEAR:
            return add_months(d, 12 * n)
    raise ValueError(f"Единицу {unit} нельзя сдвинуть календарно")


class DateSpan(BaseModel):
    """Отрезок дат ``[start, end_exclusive)``; ``start=None`` — без нижней границы."""

    model_config = ConfigDict(frozen=True)

    start: date | None
    end_exclusive: date

    def contains(self, d: date) -> bool:
        return (self.start is None or d >= self.start) and d < self.end_exclusive

    def is_empty(self) -> bool:
        return self.start is not None and self.start >= self.end_exclusive


class Period(BaseModel):
    """Период: календарная единица или произвольный диапазон.

    Для календарных единиц ``start`` совпадает с началом единицы, а ``end_exclusive`` —
    с началом следующей.
    """

    model_config = ConfigDict(frozen=True)

    start: date
    end_exclusive: date
    unit: PeriodUnit

    @model_validator(mode="after")
    def _check(self) -> Period:
        if self.end_exclusive <= self.start:
            raise ValueError("Период пуст: end_exclusive должен быть позже start")
        if self.unit in CALENDAR_UNITS:
            if unit_start(self.start, self.unit) != self.start:
                raise ValueError(f"{self.start} — не начало единицы {self.unit}")
            if unit_shift(self.start, self.unit, 1) != self.end_exclusive:
                raise ValueError("Календарный период должен занимать ровно одну единицу")
        return self

    # --- построение -----------------------------------------------------------------

    @classmethod
    def containing(cls, d: date, unit: PeriodUnit) -> Period:
        """Календарный период, в который попадает дата."""
        start = unit_start(d, unit)
        return cls(start=start, end_exclusive=unit_shift(start, unit, 1), unit=unit)

    @classmethod
    def range(cls, first_day: date, last_day: date) -> Period:
        """Произвольный диапазон по первому и последнему дню включительно."""
        return cls(start=first_day, end_exclusive=last_day + timedelta(days=1), unit=PeriodUnit.RANGE)

    @classmethod
    def parse(cls, text: str) -> Period:
        """Разбор записи периода: ``2026-03``, ``2026-Q1``, ``2026``, ``2026-03-15``,
        ``2026-W12`` или ``2026-03-03..2026-03-19``."""
        t = text.strip()
        if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})\.\.(\d{4})-(\d{2})-(\d{2})", t):
            y1, m1, d1, y2, m2, d2 = map(int, m.groups())
            return cls.range(date(y1, m1, d1), date(y2, m2, d2))
        if m := re.fullmatch(r"(\d{4})-[Qq]([1-4])", t):
            y, q = map(int, m.groups())
            return cls.containing(date(y, 3 * (q - 1) + 1, 1), PeriodUnit.QUARTER)
        if m := re.fullmatch(r"(\d{4})-[Ww](\d{1,2})", t):
            y, w = map(int, m.groups())
            return cls.containing(date.fromisocalendar(y, w, 1), PeriodUnit.WEEK)
        if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t):
            return cls.containing(date(*map(int, m.groups())), PeriodUnit.DAY)
        if m := re.fullmatch(r"(\d{4})-(\d{2})", t):
            y, mo = map(int, m.groups())
            return cls.containing(date(y, mo, 1), PeriodUnit.MONTH)
        if m := re.fullmatch(r"(\d{4})", t):
            return cls.containing(date(int(m.group(1)), 1, 1), PeriodUnit.YEAR)
        raise ValueError(f"Не понял период «{text}». Примеры: 2026-03, 2026-Q1, 2026, 2026-03-03..2026-03-19")

    # --- свойства -----------------------------------------------------------------

    @property
    def last_day(self) -> date:
        return self.end_exclusive - timedelta(days=1)

    @property
    def days(self) -> int:
        return (self.end_exclusive - self.start).days

    @property
    def key(self) -> str:
        """Короткая запись периода, обратная ``parse``."""
        match self.unit:
            case PeriodUnit.YEAR:
                return f"{self.start.year}"
            case PeriodUnit.QUARTER:
                return f"{self.start.year}-Q{(self.start.month - 1) // 3 + 1}"
            case PeriodUnit.MONTH:
                return f"{self.start.year}-{self.start.month:02d}"
            case PeriodUnit.WEEK:
                iso = self.start.isocalendar()
                return f"{iso.year}-W{iso.week:02d}"
            case PeriodUnit.DAY:
                return self.start.isoformat()
        return f"{self.start.isoformat()}..{self.last_day.isoformat()}"

    @property
    def span(self) -> DateSpan:
        return DateSpan(start=self.start, end_exclusive=self.end_exclusive)

    # --- арифметика -----------------------------------------------------------------

    def shift(self, n: int) -> Period:
        """Период на ``n`` единиц раньше (``n < 0``) или позже. У диапазона единица —
        его собственная длина."""
        if self.unit == PeriodUnit.RANGE:
            delta = timedelta(days=self.days * n)
            return Period(start=self.start + delta, end_exclusive=self.end_exclusive + delta, unit=self.unit)
        start = unit_shift(self.start, self.unit, n)
        return Period(start=start, end_exclusive=unit_shift(start, self.unit, 1), unit=self.unit)

    def years_ago(self, years: int = 1) -> Period:
        """Тот же период ``years`` лет назад."""
        if self.unit == PeriodUnit.RANGE:
            return Period(
                start=add_months(self.start, -12 * years),
                end_exclusive=add_months(self.end_exclusive, -12 * years),
                unit=self.unit,
            )
        return Period.containing(add_months(self.start, -12 * years), self.unit)

    def contains(self, d: date) -> bool:
        return self.start <= d < self.end_exclusive
