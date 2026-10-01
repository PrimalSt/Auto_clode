from datetime import date, datetime

import polars as pl
import pytest

from autogenerator.contracts import DType
from autogenerator.ingest import cast_expr, infer_dtype

NBSP, NNBSP = "\u00a0", "\u202f"


def cast(values: list[str | None], dtype: DType) -> list[object]:
    return (
        pl.DataFrame({"v": values}, schema={"v": pl.String})
        .select(cast_expr(pl.col("v"), dtype).alias("v"))["v"]
        .to_list()
    )


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1 234,56", 1234.56),
        (f"1{NBSP}234{NBSP}567,5", 1234567.5),
        (f"12{NNBSP}000", 12000.0),
        ("(1 500,00)", -1500.0),
        ("\u2212" + "7,5", -7.5),
        ("1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("-0.5", -0.5),
        ("abc", None),
        ("", None),
    ],
)
def test_float(text, value):
    assert cast([text], DType.FLOAT) == [value]


def test_int_rejects_fractions():
    assert cast(["12", "1 000", "2,5"], DType.INT) == [12, 1000, None]


def test_dates_and_datetimes():
    assert cast(["29.09.2026", "2026-09-29", "29.09.2026 14:30", "мусор"], DType.DATE) == [
        date(2026, 9, 29),
        date(2026, 9, 29),
        date(2026, 9, 29),
        None,
    ]
    assert cast(["29.09.2026 14:30", "31.12.2025 0:00:00", "29.09.2026"], DType.DATETIME) == [
        datetime(2026, 9, 29, 14, 30),
        datetime(2025, 12, 31),
        datetime(2026, 9, 29),
    ]


def test_bool():
    assert cast(["да", "Нет", "true", "0", "может"], DType.BOOL) == [True, False, True, False, None]


@pytest.mark.parametrize(
    ("values", "dtype", "fmt"),
    [
        (["1", "2", "30"], DType.INT, None),
        (["1 234,56", "(10,00)", "7"], DType.FLOAT, None),
        (["01.02.2026", "15.02.2026"], DType.DATE, "%d.%m.%Y"),
        (["01.02.2026 10:15", "15.02.2026 00:00"], DType.DATETIME, "%d.%m.%Y %H:%M"),
        (["да", "нет", "Да"], DType.BOOL, "да/нет"),
        (["Москва", "Казань"], DType.STRING, None),
        ([None, " "], DType.STRING, None),
    ],
)
def test_infer(values, dtype, fmt):
    assert infer_dtype(pl.Series(values, dtype=pl.String)) == (dtype, fmt)


def test_infer_tolerates_two_percent_noise():
    values = [str(i) for i in range(99)] + ["н/д"]
    assert infer_dtype(pl.Series(values))[0] == DType.INT
    values = [str(i) for i in range(90)] + ["н/д"] * 10
    assert infer_dtype(pl.Series(values))[0] == DType.STRING


def test_t_f_flags_are_bool():
    assert infer_dtype(pl.Series(["T", "F", None, "T"]))[0] == DType.BOOL
    assert cast(["T", "f", "x"], DType.BOOL) == [True, False, None]


def test_long_integers_are_codes():
    # Номера счетов: 10–12 цифр, а дальше в файле бывают и буквы («12345A678901»).
    assert infer_dtype(pl.Series(["123456789012", "987654321098", "5550001234"]))[0] == DType.STRING
    assert infer_dtype(pl.Series(["123456789012", "15"]))[0] == DType.INT
