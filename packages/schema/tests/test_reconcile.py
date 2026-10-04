from autogenerator.contracts import (
    ColumnSnapshot,
    DType,
    ReadOptions,
    ReconcileStatus,
    SchemaSnapshot,
    SourceSpec,
)
from autogenerator.schema import normalize_name, reconcile
from autogenerator.schema.reconcile import month_pattern

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
    assert any("Сумма, руб." in m for m in res.warnings)


def test_period_column_is_always_required():
    res = reconcile(SOURCE, snap("Сумма"))
    assert res.missing_required == ["date"]


def test_ambiguous_match_prefers_main_name():
    res = reconcile(SOURCE, snap("Дата", "Дата заказа", "Сумма"))
    assert res.mapping == {"Дата заказа": "date", "Сумма": "amount"}
    assert any("несколько" in m for m in res.warnings)


MONTHLY = SourceSpec(
    id="clients",
    name="Клиенты",
    period_column="period",
    period_from="upload",
    columns=[
        {"id": "period", "name": "Период загрузки", "dtype": "date"},
        {"id": "client", "name": "Клиент"},
        {"id": "bought", "name": "Были ли покупки в январе да/нет", "dtype": "bool"},
        {"id": "bought_3m", "name": "Были ли покупки в ноябре-январе да/нет", "dtype": "bool"},
        {"id": "web", "name": "Покупок в январе через сайт", "dtype": "int"},
    ],
)


def test_month_pattern():
    assert month_pattern("Были ли покупки в ноябре-январе") == "были ли покупки в {месяц} {месяц}"


def test_columns_with_another_month_in_name():
    res = reconcile(
        MONTHLY,
        snap(
            "Клиент",
            "Были ли покупки в апреле да/нет",
            "Были ли покупки в феврале-апреле да/нет",
            "Покупок в апреле через сайт",
        ),
    )
    # Столбца периода в файле нет и не ищется: период задаётся при загрузке.
    assert res.status == ReconcileStatus.OK and not res.warnings
    assert res.mapping == {
        "Клиент": "client",
        "Были ли покупки в апреле да/нет": "bought",
        "Были ли покупки в феврале-апреле да/нет": "bought_3m",
        "Покупок в апреле через сайт": "web",
    }
    assert res.by_pattern["Покупок в апреле через сайт"] == "Покупок в январе через сайт"


def test_month_match_must_be_unique():
    res = reconcile(MONTHLY, snap("Клиент", "Покупок в апреле через сайт", "Покупок в мае через сайт"))
    assert "web" in res.missing_optional
    assert any("несколько" in w for w in res.warnings)


# --- кандидаты для пропавших столбцов ----------------------------------------------

from autogenerator.contracts import ColumnProfile, ValueCount  # noqa: E402
from autogenerator.schema import with_aliases  # noqa: E402

REGIONS = [ValueCount(value=v, count=10) for v in ("Север", "Юг", "Центр")]


def typed(*cols: tuple[str, DType, ColumnProfile | None]) -> SchemaSnapshot:
    return SchemaSnapshot(
        path="x.csv",
        format="csv",
        options=ReadOptions(),
        sample_rows=100,
        columns=[ColumnSnapshot(source_name=n, dtype=t, profile=p) for n, t, p in cols],
    )


def money(lo: float, hi: float) -> ColumnProfile:
    return ColumnProfile(rows=100, nulls=0, min=str(lo), max=str(hi))


SALES = SourceSpec(
    id="s",
    name="S",
    period_column="date",
    columns=[
        {"id": "date", "name": "Дата заказа", "dtype": "date"},
        {"id": "amount", "name": "Сумма", "dtype": "float"},
        {"id": "region", "name": "Регион"},
        {"id": "manager", "name": "Менеджер"},
    ],
)
STATS = {"amount": money(10, 90_000), "region": ColumnProfile(rows=100, top=REGIONS)}


def test_renamed_required_column_is_proposed():
    snap_ = typed(
        ("Дата заказа", DType.DATE, None),
        ("Сумма заказа, ₽", DType.FLOAT, money(5, 120_000)),
        ("Регион", DType.STRING, None),
        ("Менеджер", DType.STRING, None),
    )
    res = reconcile(SALES, snap_, required={"amount"}, value_stats=STATS)
    assert res.status == ReconcileStatus.NEEDS_REVIEW
    assert res.proposed == {"Сумма заказа, ₽": "amount"}
    best = res.candidates["amount"][0]
    assert best.file_name == "Сумма заказа, ₽" and best.score >= 0.6 and best.type_score == 1.0
    assert not res.missing_required and "Сумма заказа, ₽" not in res.unmapped_file_columns


def test_renamed_unused_column_only_warns():
    snap_ = typed(("Дата заказа", DType.DATE, None), ("Сумма", DType.FLOAT, None), ("Менеджер ФИО", DType.STRING, None))
    res = reconcile(SALES, snap_, required={"amount"})
    assert res.status == ReconcileStatus.OK and not res.proposed
    assert "manager" in res.missing_optional
    assert any("похоже, это «Менеджер ФИО»" in w for w in res.warnings)
    # Если неизвестно, какие столбцы нужны сценариям, важен каждый: пару подтверждает пользователь.
    res = reconcile(SALES, snap_, required=None)
    assert res.status == ReconcileStatus.NEEDS_REVIEW and res.proposed == {"Менеджер ФИО": "manager"}


def test_values_help_when_names_differ():
    snap_ = typed(
        ("Дата заказа", DType.DATE, None),
        ("Сумма", DType.FLOAT, None),
        ("Территория", DType.STRING, ColumnProfile(rows=100, top=REGIONS[:2])),
    )
    res = reconcile(SALES, snap_, required={"region"}, value_stats=STATS)
    assert res.proposed == {"Территория": "region"}
    assert res.candidates["region"][0].value_score == 1.0
    # Без профиля прежних загрузок сравниваются только название и тип: кандидат есть, пары нет.
    res = reconcile(SALES, snap_, required={"region"})
    assert res.status == ReconcileStatus.BLOCKED and res.candidates["region"][0].file_name == "Территория"


def test_missing_required_without_candidates_explains_dependents():
    snap_ = typed(("Дата заказа", DType.DATE, None), ("Регион", DType.STRING, None))
    res = reconcile(
        SALES, snap_, required={"amount"}, dependents={"amount": ["сценарий lk, вход sales: набор by_month"]}
    )
    assert res.status == ReconcileStatus.BLOCKED and res.missing_required == ["amount"]
    assert res.dependents == {"amount": ["сценарий lk, вход sales: набор by_month"]}
    assert any("набор by_month" in w for w in res.warnings)


def test_two_equal_candidates_need_a_choice():
    snap_ = typed(
        ("Дата заказа", DType.DATE, None),
        ("Сумма 1", DType.FLOAT, None),
        ("Сумма 2", DType.FLOAT, None),
    )
    res = reconcile(SALES, snap_, required={"amount"})
    assert res.status == ReconcileStatus.NEEDS_REVIEW and not res.proposed
    assert res.missing_required == ["amount"]
    assert [c.file_name for c in res.candidates["amount"]][:2] == ["Сумма 1", "Сумма 2"]


def test_incompatible_type_is_not_proposed():
    snap_ = typed(("Дата заказа", DType.DATE, None), ("Сумма", DType.DATE, None))
    src = SALES.model_copy(
        update={"columns": [SALES.columns[0], SALES.columns[1].model_copy(update={"name": "Сумма итого"})]}
    )
    res = reconcile(src, snap_, required={"amount"})
    assert res.candidates["amount"][0].type_score == 0.0
    assert res.status == ReconcileStatus.BLOCKED


def test_header_only_snapshot_scores_names():
    res = reconcile(SALES, snap("Дата заказа", "Сумма, ₽ без НДС"), required={"amount"})
    cand = res.candidates["amount"][0]
    assert cand.type_score is None and cand.value_score is None and cand.dtype is None
    assert cand.score == cand.name_score


def test_with_aliases_remembers_confirmed_names():
    src = with_aliases(SALES, {"Сумма заказа, ₽": "amount", "Регион": "region", "сумма": "amount"})
    assert src.column("amount").aliases == ["Сумма заказа, ₽"]
    assert src.column("region").aliases == []
    res = reconcile(src, snap("Дата заказа", "Сумма заказа, ₽"), required={"amount"})
    assert res.status == ReconcileStatus.OK and res.mapping["Сумма заказа, ₽"] == "amount"


def test_declined_column_is_not_proposed_again():
    snap_ = typed(
        ("Дата заказа", DType.DATE, None),
        ("Сумма заказа, ₽", DType.FLOAT, money(5, 120_000)),
        ("Регион", DType.STRING, None),
        ("Менеджер", DType.STRING, None),
    )
    res = reconcile(SALES, snap_, value_stats=STATS)
    assert res.status == ReconcileStatus.NEEDS_REVIEW and res.review == ["amount"]
    # Сценариям источник неизвестен: пользователь оставляет столбец пустым — загрузка идёт.
    res = reconcile(SALES, snap_, value_stats=STATS, declined={"amount"})
    assert res.status == ReconcileStatus.OK and res.declined == ["amount"] and not res.proposed
    assert any("по вашему выбору" in w for w in res.warnings)
    # Нужный сценарию столбец пустым оставить нельзя.
    res = reconcile(SALES, snap_, required={"amount"}, value_stats=STATS, declined={"amount"})
    assert res.status == ReconcileStatus.BLOCKED and res.missing_required == ["amount"]
