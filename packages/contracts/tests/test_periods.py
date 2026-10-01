from datetime import date

import pytest

from autogenerator.contracts import Period, PeriodUnit
from autogenerator.contracts.periods import add_months, month_of_word


def test_add_months_clamps_day():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 3, 15), -3) == date(2025, 12, 15)


@pytest.mark.parametrize(
    ("text", "start", "end", "unit"),
    [
        ("2026-03", date(2026, 3, 1), date(2026, 4, 1), PeriodUnit.MONTH),
        ("2026-Q1", date(2026, 1, 1), date(2026, 4, 1), PeriodUnit.QUARTER),
        ("2026-q4", date(2026, 10, 1), date(2027, 1, 1), PeriodUnit.QUARTER),
        ("2026", date(2026, 1, 1), date(2027, 1, 1), PeriodUnit.YEAR),
        ("2026-03-15", date(2026, 3, 15), date(2026, 3, 16), PeriodUnit.DAY),
        ("2026-03-03..2026-03-19", date(2026, 3, 3), date(2026, 3, 20), PeriodUnit.RANGE),
    ],
)
def test_parse_and_key_roundtrip(text, start, end, unit):
    p = Period.parse(text)
    assert (p.start, p.end_exclusive, p.unit) == (start, end, unit)
    assert Period.parse(p.key) == p


def test_parse_rejects_garbage():
    with pytest.raises(ValueError, match="Не понял период"):
        Period.parse("март")


def test_calendar_period_must_be_one_unit():
    with pytest.raises(ValueError):
        Period(start=date(2026, 1, 1), end_exclusive=date(2026, 3, 1), unit=PeriodUnit.MONTH)
    with pytest.raises(ValueError):
        Period(start=date(2026, 1, 2), end_exclusive=date(2026, 2, 2), unit=PeriodUnit.MONTH)


def test_shift_and_years_ago():
    march = Period.parse("2026-03")
    assert march.shift(-1).key == "2026-02"
    assert march.shift(-3).key == "2025-12"
    assert march.years_ago().key == "2025-03"
    assert Period.parse("2026-Q1").shift(-1).key == "2025-Q4"


def test_range_shift_keeps_length():
    r = Period.range(date(2026, 3, 3), date(2026, 3, 19))
    prev = r.shift(-1)
    assert prev.days == r.days == 17
    assert prev.end_exclusive == r.start


@pytest.mark.parametrize(
    ("word", "month"),
    [
        ("январь", 1),
        ("Января", 1),
        ("январе", 1),
        ("янв", 1),
        ("марта", 3),
        ("мае", 5),
        ("ноябре", 11),
        ("Jan", 1),
        ("April", 4),
        ("noyabr", 11),
        ("мама", None),
        ("маркетинг", None),
        ("for", None),
    ],
)
def test_month_of_word(word, month):
    assert month_of_word(word) == month
