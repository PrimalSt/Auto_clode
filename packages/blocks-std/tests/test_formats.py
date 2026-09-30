from datetime import date

import pytest

from autogenerator.blocks_std.formats import (
    NBSP,
    PeriodVars,
    fmt_date,
    fmt_money,
    fmt_number,
    fmt_percent,
    month_name,
    period_label,
)
from autogenerator.contracts import Period


def test_numbers():
    assert fmt_number(1234567.8) == f"1{NBSP}234{NBSP}568"
    assert fmt_number(-1234.5, decimals=1) == f"-1{NBSP}234,5"
    assert fmt_number(26_229_878, scale="million", decimals=1) == "26,2"
    assert fmt_number(26_229_878, scale="million", decimals=1, unit=True) == f"26,2{NBSP}млн"
    assert fmt_number(0.04, sign=True) == "0"
    assert fmt_number(None) is None


def test_percent_and_money():
    assert fmt_percent(0.125) == "12,5%"
    assert fmt_percent(0.083, sign=True) == "+8,3%"
    assert fmt_percent(-0.012, points=True) == f"-1,2{NBSP}п.п."
    assert fmt_money(26_229_878, scale="million", decimals=1) == f"26,2{NBSP}млн{NBSP}₽"
    assert fmt_money(1500) == f"1{NBSP}500{NBSP}₽"
    assert fmt_percent(None) is None


def test_months_and_dates():
    assert month_name(3) == "март"
    assert month_name(date(2026, 3, 1), "gen") == "марта"
    assert month_name(5, "prep", capital=True) == "Мае"
    assert fmt_date(date(2026, 1, 1), "LLL yyyy") == "янв. 2026"
    assert fmt_date(date(2026, 1, 1), "LLLL yyyy") == "январь 2026"
    assert fmt_date(date(2026, 9, 30)) == "30.09.2026"


@pytest.mark.parametrize(
    ("period", "label"),
    [
        ("2026-03", "март 2026"),
        ("2026-Q1", f"I{NBSP}квартал 2026"),
        ("2026", "2026 год"),
        ("2026-03-15", "15 марта 2026"),
        ("2026-03-03..2026-03-19", "3–19 марта 2026"),
        ("2026-02-20..2026-03-10", "20 февраля – 10 марта 2026"),
    ],
)
def test_period_label(period, label):
    assert period_label(Period.parse(period)) == label


def test_period_vars():
    v = PeriodVars(Period.parse("2026-03"))
    assert (v.month, v.month_gen, v.month_prep, v.year, v.quarter) == (
        "март",
        "марта",
        "марте",
        2026,
        1,
    )
    assert v.last_quarter_label == f"I{NBSP}квартал 2026"
    feb = PeriodVars(Period.parse("2026-02"))
    # Квартал ещё не закончился: последний завершённый — IV квартал прошлого года.
    assert (feb.last_quarter, feb.last_quarter_year) == (4, 2025)
    assert str(feb) == "февраль 2026"
