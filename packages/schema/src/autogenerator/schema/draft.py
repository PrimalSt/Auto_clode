"""Черновик источника по первой выгрузке (F-151): столбцы с латинскими id, типы, столбец
периода. Пользователь правит черновик и сохраняет; следующие выгрузки сверяются с ним."""

from __future__ import annotations

import re

from autogenerator.contracts import (
    AgenError,
    ColumnSnapshot,
    ColumnSpec,
    DType,
    ErrorCode,
    OverlapPolicy,
    PeriodUnit,
    ReadOptions,
    SchemaSnapshot,
    SourceSpec,
)
from autogenerator.contracts.sources import ID_PATTERN

from .reconcile import normalize_name

# Частые слова в названиях столбцов выгрузок → id. Слово подходит и в других падежах
# («заказа», «оплаты»): сравнивается основа без последней буквы.
WORDS = {
    "дата": "date",
    "период": "period",
    "месяц": "month",
    "неделя": "week",
    "год": "year",
    "день": "day",
    "номер": "no",
    "код": "code",
    "заказ": "order",
    "строка": "line",
    "документ": "doc",
    "договор": "contract",
    "сумма": "amount",
    "выручка": "revenue",
    "регион": "region",
    "город": "city",
    "филиал": "branch",
    "подразделение": "department",
    "менеджер": "manager",
    "сотрудник": "employee",
    "клиент": "client",
    "покупатель": "customer",
    "контрагент": "counterparty",
    "поставщик": "supplier",
    "инн": "inn",
    "канал": "channel",
    "категория": "category",
    "подкатегория": "subcategory",
    "группа": "group",
    "товар": "product",
    "продукт": "product",
    "наименование": "name",
    "название": "name",
    "артикул": "sku",
    "количество": "qty",
    "цена": "price",
    "скидка": "discount",
    "ндс": "vat",
    "без": "excl",
    "себестоимость": "cost",
    "валовая": "gross",
    "прибыль": "profit",
    "маржа": "margin",
    "статус": "status",
    "тип": "type",
    "вид": "kind",
    "оплата": "payment",
    "отгрузка": "shipment",
    "доставка": "delivery",
    "способ": "method",
    "склад": "warehouse",
    "возврат": "return",
    "комментарий": "comment",
    "план": "plan",
    "факт": "fact",
    "продажи": "sales",
    "проект": "project",
    "%": "pct",
}

_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i",
        "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
        "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)  # fmt: skip

MAX_ID = 40
PERIOD_HINTS = ("дата", "период", "date", "месяц")


def _word_id(word: str) -> tuple[str, bool]:
    """id слова и признак «слово не в начальной форме» (обычно родительный падеж)."""
    if word in WORDS:
        return WORDS[word], False
    for key, value in WORDS.items():
        if len(key) >= 4 and word.startswith(key[:-1]) and len(word) - len(key) <= 2:
            return value, True
    return word.translate(_TRANSLIT), False


def suggest_id(name: str, taken: set[str] | None = None) -> str:
    """Латинский id по названию столбца: «Дата заказа» → ``order_date``,
    «Скидка %» → ``discount_pct``, незнакомые слова — транслитом. Повторы — с номером."""
    words = [w for w in normalize_name(name).split() if w] + (["%"] if "%" in name else [])
    found = [_word_id(w) for w in words]
    parts = [w for w, _ in found]
    if len(found) == 2 and found[1][1]:
        # «Дата заказа» — «дата чего»: по-английски определяемое слово идёт вторым.
        parts.reverse()
    ident = re.sub(r"[^a-z0-9_]+", "_", "_".join(parts).lower()).strip("_")
    ident = re.sub(r"_+", "_", ident)[:MAX_ID].rstrip("_") or "column"
    if not ID_PATTERN.match(ident):
        ident = f"c_{ident}"
    taken = taken if taken is not None else set()
    out, n = ident, 2
    while out in taken:
        out, n = f"{ident}_{n}", n + 1
    taken.add(out)
    return out


def _period_candidate(columns: list[ColumnSnapshot]) -> ColumnSnapshot | None:
    dated = [c for c in columns if c.dtype in (DType.DATE, DType.DATETIME)]
    if not dated:
        return None
    hinted = [c for c in dated if any(h in normalize_name(c.source_name) for h in PERIOD_HINTS)]
    pool = hinted or dated
    return max(pool, key=lambda c: c.non_null)


def draft_source(
    snapshot: SchemaSnapshot,
    source_id: str,
    name: str,
    period_column: str | None = None,
    period_type: PeriodUnit = PeriodUnit.MONTH,
    explicit: ReadOptions | None = None,
) -> SourceSpec:
    """Черновик источника по снимку структуры.

    ``period_column`` — название столбца периода в файле; пусто — первый столбец с датами,
    в названии которого есть «дата» или «период». ``explicit`` — параметры чтения, заданные
    пользователем: они сохраняются как есть, а из найденных сохраняются кодировка и
    разделитель (строка шапки и листы ищутся в каждом файле заново).
    """
    taken: set[str] = set()
    columns: list[ColumnSpec] = []
    by_name: dict[str, ColumnSpec] = {}
    for c in snapshot.columns:
        spec = ColumnSpec(
            id=suggest_id(c.source_name, taken),
            name=c.source_name,
            dtype=c.dtype,
            format=c.format if c.dtype in (DType.DATE, DType.DATETIME) else None,
        )
        columns.append(spec)
        by_name[c.source_name] = spec
    if period_column is not None:
        if period_column not in by_name:
            raise AgenError(
                ErrorCode.SPEC_INVALID,
                f"В файле нет столбца «{period_column}». Столбцы: {', '.join(by_name)}",
            )
        pspec = by_name[period_column]
        if pspec.dtype not in (DType.DATE, DType.DATETIME):
            pspec.dtype = DType.DATE
    else:
        found = _period_candidate(snapshot.columns)
        if found is None:
            raise AgenError(
                ErrorCode.SPEC_INVALID,
                "В файле не нашлось столбца с датами для периода",
                hint="Укажите столбец периода явно (--period-column «Название»); в нём должны быть даты.",
            )
        pspec = by_name[found.source_name]
    o = snapshot.options
    given = explicit.model_dump(exclude_unset=True, exclude_none=True) if explicit else {}
    options = ReadOptions(**{"encoding": o.encoding, "delimiter": o.delimiter, "quote": o.quote, **given})
    return SourceSpec(
        id=source_id,
        name=name,
        format=snapshot.format,
        options=options,
        period_column=pspec.id,
        period_type=period_type,
        overlap_policy=OverlapPolicy.REPLACE_PERIOD,
        columns=columns,
    )
