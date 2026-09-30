"""Русские форматы чисел, дат и периодов — общие для текстовых блоков и (на этапе M3) меток
шаблона (F-420).

Числа: ``1 234 567,8`` (разделитель разрядов — неразрывный пробел), проценты ``12,5%``,
процентные пункты ``+1,2 п.п.``, деньги ``1 234 568 ₽`` или ``1,2 млн ₽``. Месяцы — в
именительном, родительном и дательном падеже: «март», «марта», «марту».
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from babel.dates import format_date
from babel.numbers import format_decimal

from autogenerator.contracts import Period, PeriodUnit

LOCALE = "ru_RU"
NO_DATA = "нет данных"
NBSP = "\u00a0"  # неразрывный пробел: «1 234», «12,5 п.п.», «млн ₽» не разрываются

MONTHS = {
    "nom": [
        "январь",
        "февраль",
        "март",
        "апрель",
        "май",
        "июнь",
        "июль",
        "август",
        "сентябрь",
        "октябрь",
        "ноябрь",
        "декабрь",
    ],
    "gen": [
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ],
    "dat": [
        "январю",
        "февралю",
        "марту",
        "апрелю",
        "маю",
        "июню",
        "июлю",
        "августу",
        "сентябрю",
        "октябрю",
        "ноябрю",
        "декабрю",
    ],
    "prep": [
        "январе",
        "феврале",
        "марте",
        "апреле",
        "мае",
        "июне",
        "июле",
        "августе",
        "сентябре",
        "октябре",
        "ноябре",
        "декабре",
    ],
}
Case = Literal["nom", "gen", "dat", "prep"]
ROMAN = ["I", "II", "III", "IV"]
SCALES = {"thousand": (1e3, "тыс."), "million": (1e6, "млн"), "billion": (1e9, "млрд")}
Scale = Literal["thousand", "million", "billion"]


def _pattern(decimals: int) -> str:
    return "#,##0" + ("." + "0" * decimals if decimals > 0 else "")


def fmt_number(
    value: Any,
    decimals: int = 0,
    scale: Scale | None = None,
    sign: bool = False,
    unit: bool = False,
) -> str | None:
    """``1234567.8`` → ``1 234 568``; с ``scale='million', decimals=1`` → ``1,2``
    (``unit=True`` добавит «млн»). ``None`` остаётся ``None``: «нет данных» подставит шаблон."""
    if value is None:
        return None
    v = float(value)
    suffix = ""
    if scale:
        div, word = SCALES[scale]
        v /= div
        suffix = f"{NBSP}{word}" if unit else ""
    text = format_decimal(abs(v), format=_pattern(decimals), locale=LOCALE)
    zero = format_decimal(0, format=_pattern(decimals), locale=LOCALE)
    if v < 0 and text != zero:
        text = "-" + text
    elif sign and v > 0 and text != zero:
        text = "+" + text
    return text + suffix


def fmt_percent(value: Any, decimals: int = 1, sign: bool = False, points: bool = False) -> str | None:
    """Доля ``0.125`` → ``12,5%``; ``points=True`` — процентные пункты ``+1,2 п.п.``."""
    if value is None:
        return None
    text = fmt_number(float(value) * 100, decimals=decimals, sign=sign)
    assert text is not None
    return f"{text}{NBSP}п.п." if points else f"{text}%"


def fmt_money(value: Any, decimals: int = 0, scale: Scale | None = None, currency: str = "₽") -> str | None:
    if value is None:
        return None
    text = fmt_number(value, decimals=decimals, scale=scale, unit=bool(scale))
    return f"{text}{NBSP}{currency}"


def month_name(value: date | datetime | Period | int, case: Case = "nom", capital: bool = False) -> str:
    m = value if isinstance(value, int) else (value.start.month if isinstance(value, Period) else value.month)
    name = MONTHS[case][m - 1]
    return name[:1].upper() + name[1:] if capital else name


def fmt_date(value: Any, pattern: str = "dd.MM.yyyy") -> str | None:
    """Дата по шаблону Babel (CLDR): ``LLL yyyy`` → «янв. 2026», ``LLLL yyyy`` → «январь 2026»."""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return str(value)
    return format_date(value, format=pattern, locale=LOCALE)


def quarter_label(year: int, quarter: int, roman: bool = True) -> str:
    q = ROMAN[quarter - 1] if roman else str(quarter)
    return f"{q}{NBSP}квартал {year}"


def period_label(p: Period) -> str:
    """Подпись периода: «март 2026», «I квартал 2026», «2026 год», «3–19 марта 2026»."""
    match p.unit:
        case PeriodUnit.MONTH:
            return f"{month_name(p.start)} {p.start.year}"
        case PeriodUnit.QUARTER:
            return quarter_label(p.start.year, (p.start.month - 1) // 3 + 1)
        case PeriodUnit.YEAR:
            return f"{p.start.year} год"
        case PeriodUnit.DAY:
            return f"{p.start.day} {month_name(p.start, 'gen')} {p.start.year}"
    a, b = p.start, p.last_day
    if a.year == b.year and a.month == b.month:
        return f"{a.day}–{b.day} {month_name(b, 'gen')} {b.year}"
    if a.year == b.year:
        return f"{a.day} {month_name(a, 'gen')} – {b.day} {month_name(b, 'gen')} {b.year}"
    return f"{fmt_date(a)} – {fmt_date(b)}"


class PeriodVars:
    """Поля ``period`` в тексте блоков (ARCHITECTURE.md, раздел 6.4)."""

    def __init__(self, p: Period):
        self._p = p
        end = p.last_day
        self.label = period_label(p)
        self.start = p.start
        self.end = end
        self.unit = p.unit.value
        self.key = p.key
        self.year = end.year
        self.prev_year = end.year - 1
        self.month = month_name(end)
        self.month_gen = month_name(end, "gen")
        self.month_dat = month_name(end, "dat")
        self.month_prep = month_name(end, "prep")
        self.quarter = (end.month - 1) // 3 + 1
        self.quarter_year = end.year
        self.quarter_label = quarter_label(self.quarter_year, self.quarter)
        # Последний завершённый квартал на конец отчётного периода.
        q_end = date(end.year, 3 * self.quarter, 1)
        complete = p.end_exclusive >= date(q_end.year + (q_end.month == 12), q_end.month % 12 + 1, 1)
        if complete:
            self.last_quarter, self.last_quarter_year = self.quarter, self.quarter_year
        elif self.quarter == 1:
            self.last_quarter, self.last_quarter_year = 4, end.year - 1
        else:
            self.last_quarter, self.last_quarter_year = self.quarter - 1, end.year
        self.last_quarter_label = quarter_label(self.last_quarter_year, self.last_quarter)

    def month_case(self, case: Case = "nom", capital: bool = False) -> str:
        return month_name(self._p.last_day, case, capital)

    def __str__(self) -> str:
        return self.label
