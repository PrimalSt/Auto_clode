import polars as pl
import pytest

from autogenerator.contracts import AgenError
from autogenerator.engine import SqlTranslator

DF = pl.DataFrame({"a": [10, 0, None], "b": [2, 0, 1], "s": ["Москва", "Казань", None]})


def ev(sql: str) -> list:
    return DF.select(SqlTranslator(DF.columns).expr(sql).alias("r"))["r"].to_list()


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("a / b", [5.0, None, None]),
        ("a + b * 2", [14, 0, None]),
        ("a > 5 AND b = 2", [True, False, False]),  # NULL AND FALSE = FALSE, как в SQL
        ("a IS NULL", [False, False, True]),
        ("s IN ('Москва', 'Тверь')", [True, False, None]),
        ("s LIKE 'Мо%'", [True, False, None]),
        ("a BETWEEN 0 AND 5", [False, True, None]),
        ("CASE WHEN a > 5 THEN 'много' ELSE 'мало' END", ["много", "мало", "мало"]),
        ("COALESCE(a, -1)", [10, 0, -1]),
        ("ROUND(a / 3, 1)", [3.3, 0.0, None]),
        ("LOWER(s) || '!'", ["москва!", "казань!", None]),
        ("NOT (a > 5)", [False, True, None]),
    ],
)
def test_translation(sql, expected):
    assert ev(sql) == expected


def test_columns_in():
    assert SqlTranslator().columns_in("amount / 1.2 + COALESCE(bonus, 0)") == {"amount", "bonus"}


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("a +", "Ошибка в формуле"),
        ("SELECT a FROM t", "не выражение"),
        ("nope > 1", "нет столбца «nope»"),
        ("REGEXP_MATCHES(s, 'x')", "не поддерживается"),
        ("", "Пустая формула"),
    ],
)
def test_errors_are_readable(sql, message):
    with pytest.raises(AgenError, match=message):
        ev(sql)
