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
        ("LEVENSHTEIN(s, 'x')", "переводится в DuckDB"),
        ("", "Пустая формула"),
    ],
)
def test_errors_are_readable(sql, message):
    with pytest.raises(AgenError, match=message):
        ev(sql)


# --- Сверка с DuckDB: перевод в Polars даёт то же, что DuckDB ---------------------------

from datetime import date, datetime  # noqa: E402
from decimal import Decimal  # noqa: E402

import duckdb  # noqa: E402

from autogenerator.engine.sqlexpr import UnsupportedExpression, to_duckdb  # noqa: E402

TYPED = pl.DataFrame(
    {
        "a": [10, 0, None, -7, 7],
        "b": [2, 0, 1, 2, -2],
        "f": [2.5, -2.5, None, 1.235, 0.0],
        "s": ["Москва", "Казань", None, "a-b-c", "  x "],
        "d": [date(2026, 3, 15), date(2026, 1, 1), None, date(2025, 12, 31), date(2026, 2, 28)],
        "t": [
            datetime(2026, 3, 15, 13, 45, 10),
            datetime(2026, 1, 1),
            None,
            datetime(2025, 12, 31, 23, 59, 59),
            datetime(2026, 2, 28, 0, 0, 1),
        ],
    }
)

FORMULAS = [
    "a / b", "a // b", "a % b", "a + b * 2", "-7 % 3", "round(f)", "round(f, 2)", "round(a / 3, 1)",
    "floor(f)", "ceil(f)", "abs(a)", "sign(a)", "sqrt(abs(f))", "ln(abs(f) + 1)", "log10(abs(f) + 1)",
    "exp(f)", "pow(b, 2)", "greatest(a, b)", "least(a, b)", "nullif(a, 0)", "coalesce(a, b, 0)",
    "CASE WHEN a > 5 THEN 'x' ELSE 'y' END", "CASE a WHEN 10 THEN 1 ELSE 0 END",
    "a IN (10, 7)", "a NOT IN (10, 7)", "a IN (10, NULL)", "s LIKE 'М%'", "s ILIKE 'м%'", "s NOT LIKE 'М%'",
    "a BETWEEN 0 AND 7", "a NOT BETWEEN 0 AND 7", "a IS NULL", "a IS NOT NULL",
    "a IS DISTINCT FROM b", "a IS NOT DISTINCT FROM b",
    "lower(s)", "upper(s)", "length(s)", "trim(s)", "ltrim(s)", "rtrim(s)", "substr(s, 2, 3)", "substring(s, 2)",
    "left(s, 2)", "right(s, 2)", "replace(s, 'а', 'А')", "regexp_matches(s, 'а.')",
    "regexp_replace(s, '[аb]', '_')", "regexp_replace(s, '[аb]', '_', 'g')", "regexp_extract(s, '(\\w)-(\\w)', 2)",
    "split_part(s, '-', 2)", "concat(s, '!', a)", "s || '!'", "contains(s, 'ск')", "starts_with(s, 'Мо')",
    "ends_with(s, 'ань')", "lpad(s, 8, '*')", "rpad(s, 3, '*')",
    "date_trunc('month', d)", "date_trunc('quarter', d)", "date_trunc('week', d)", "date_trunc('day', t)",
    "year(d)", "month(d)", "quarter(d)", "day(d)", "week(d)", "dayofweek(d)", "isodow(d)", "dayofyear(d)",
    "extract(month from d)", "extract(year from t)", "hour(t)",
    "strftime(d, '%Y-%m')", "strftime(t, '%d.%m.%Y %H:%M')",
    "datediff('day', d, DATE '2026-04-01')", "date_diff('month', d, DATE '2026-04-01')",
    "date_diff('year', d, DATE '2026-04-01')", "date_diff('quarter', d, DATE '2026-04-01')",
    "make_date(2026, abs(b) + 1, 1)", "last_day(d)", "d >= '2026-01-01'", "t < '2026-03-01'",
    "d BETWEEN '2026-01-01' AND '2026-02-28'",
    "CAST(f AS INT)", "CAST(a AS VARCHAR)", "TRY_CAST(s AS INT)", "CAST('2026-03-01' AS DATE)", "a::DOUBLE / 4",
    "CAST(d AS TIMESTAMP)", "IF(a > 0, 'pos', 'neg')", "NOT (a > 5)", "a > 5 AND b = 2", "a > 5 OR b = 2",
]  # fmt: skip


def _norm(v):
    if isinstance(v, Decimal):
        v = float(v)
    return round(v, 9) if isinstance(v, float) else v


@pytest.mark.parametrize("sql", FORMULAS)
def test_same_result_as_duckdb(sql):
    try:
        got = TYPED.with_columns(SqlTranslator(dict(TYPED.schema)).expr(sql).alias("r"))["r"].to_list()
    except UnsupportedExpression:
        pytest.skip("формулу считает DuckDB")
    con = duckdb.connect()
    con.register("data", TYPED.to_arrow())
    expected = [r[0] for r in con.execute(f"SELECT {to_duckdb(sql)} FROM data").fetchall()]
    assert [_norm(v) for v in got] == [_norm(v) for v in expected]


def test_all_listed_formulas_are_translated():
    # Сверка выше пропускает непереведённые формулы; здесь — что их среди списка нет.
    tr = SqlTranslator(dict(TYPED.schema))
    assert [f for f in FORMULAS if not tr.supports(f)] == []
