import pytest

from autogenerator.contracts import (
    AgenError,
    ColumnSnapshot,
    DType,
    OverlapPolicy,
    PeriodUnit,
    ReadOptions,
    SchemaSnapshot,
)
from autogenerator.schema import draft_source, suggest_id


def test_suggest_id():
    taken: set[str] = set()
    names = ["Дата заказа", "Номер заказа", "Сумма", "Сумма", "Скидка, %", "Валовая прибыль", "Фамилия", "2025 год"]
    assert [suggest_id(n, taken) for n in names] == [
        "order_date",
        "order_no",
        "amount",
        "amount_2",
        "discount_pct",
        "gross_profit",
        "familiya",
        "c_2025_year",
    ]


def snapshot(*cols: tuple[str, DType, str | None]) -> SchemaSnapshot:
    return SchemaSnapshot(
        path="x.csv",
        format="csv",
        options=ReadOptions(encoding="cp1251", delimiter=";", header_row=3),
        sample_rows=10,
        columns=[ColumnSnapshot(source_name=n, dtype=t, format=f, non_null=10) for n, t, f in cols],
    )


def test_draft_picks_period_column_and_keeps_read_options():
    snap = snapshot(
        ("Дата оплаты", DType.DATE, "%d.%m.%Y"),
        ("Дата заказа", DType.DATE, "%d.%m.%Y"),
        ("Сумма", DType.FLOAT, None),
    )
    spec = draft_source(snap, "sales", "Продажи")
    assert spec.period_column == "payment_date"
    assert [c.id for c in spec.columns] == ["payment_date", "order_date", "amount"]
    assert spec.column("order_date").format == "%d.%m.%Y" and spec.column("amount").format is None
    assert spec.overlap_policy == OverlapPolicy.REPLACE_PERIOD and spec.period_type == PeriodUnit.MONTH
    # Кодировка и разделитель сохраняются, строка шапки ищется в каждом файле заново.
    assert (spec.options.encoding, spec.options.delimiter, spec.options.header_row) == ("cp1251", ";", None)
    explicit = draft_source(snap, "sales", "Продажи", "Дата заказа", explicit=ReadOptions(header_row=3))
    assert explicit.period_column == "order_date" and explicit.options.header_row == 3


def test_draft_without_dates_needs_period_column():
    snap = snapshot(("Месяц", DType.STRING, None), ("Сумма", DType.FLOAT, None))
    with pytest.raises(AgenError, match="--period-column"):
        draft_source(snap, "s", "S")
    spec = draft_source(snap, "s", "S", period_column="Месяц")
    assert spec.column("month").dtype == DType.DATE
    with pytest.raises(AgenError, match="нет столбца"):
        draft_source(snap, "s", "S", period_column="Нет")
