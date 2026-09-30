from autogenerator.contracts import (
    ColumnSnapshot,
    DType,
    ReadOptions,
    ReconcileStatus,
    SchemaSnapshot,
    SourceSpec,
)
from autogenerator.schema import normalize_name, reconcile

SOURCE = SourceSpec(
    id="s",
    name="S",
    period_column="date",
    columns=[
        {"id": "date", "name": "Дата заказа", "dtype": "date", "aliases": ["Дата"]},
        {"id": "order_no", "name": "Номер заказа", "dtype": "string", "aliases": ["№ заказа"]},
        {"id": "amount", "name": "Сумма", "dtype": "float", "aliases": ["Сумма, руб."]},
        {"id": "manager", "name": "Менеджер", "dtype": "string"},
    ],
)


def snap(*names: str) -> SchemaSnapshot:
    return SchemaSnapshot(
        path="x.csv",
        format="csv",
        options=ReadOptions(),
        sample_rows=0,
        columns=[ColumnSnapshot(source_name=n, dtype=DType.STRING) for n in names],
    )


def test_normalize_name():
    assert normalize_name("  Сумма,  руб. ") == "сумма руб"
    assert normalize_name("Ёмкость") == normalize_name("емкость")
    assert normalize_name("№ заказа") == "no заказа"


def test_renamed_and_reordered_columns():
    res = reconcile(SOURCE, snap("Сумма, руб.", "Лишний", "№ заказа", "дата"), required={"amount"})
    assert res.status == ReconcileStatus.OK
    assert res.mapping == {"Сумма, руб.": "amount", "№ заказа": "order_no", "дата": "date"}
    assert res.missing_optional == ["manager"]
    assert res.unmapped_file_columns == ["Лишний"]


def test_missing_required_blocks():
    res = reconcile(SOURCE, snap("Дата", "Номер заказа"), required={"amount"})
    assert res.status == ReconcileStatus.BLOCKED
    assert res.missing_required == ["amount"]
    assert any("Сумма, руб." in m for m in res.messages)


def test_period_column_is_always_required():
    res = reconcile(SOURCE, snap("Сумма"))
    assert res.missing_required == ["date"]


def test_ambiguous_match_prefers_main_name():
    res = reconcile(SOURCE, snap("Дата", "Дата заказа", "Сумма"))
    assert res.mapping == {"Дата заказа": "date", "Сумма": "amount"}
    assert any("несколько" in m for m in res.messages)
