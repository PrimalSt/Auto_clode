"""Сверка структуры выгрузки с источником (ARCHITECTURE.md, раздел 6.2).

Столбец файла подходит столбцу источника, если совпадает нормализованное название или
любое из ``aliases``. Если точного совпадения нет, сравниваются названия без месяцев:
«Были ли покупки в январе» в источнике и «… в апреле» в новой выгрузке — один столбец.

Для столбцов, которые так и не нашлись, ищутся кандидаты среди оставшихся столбцов файла:
оценка ``0,5 × похожесть названия + 0,2 × совместимость типа + 0,3 × похожесть значений``
(названия сравнивает rapidfuzz, значения — по профилю выборки файла и профилю прежних
загрузок источника). Лучший кандидат с оценкой не ниже ``PROPOSE`` предлагается: его
подтверждает пользователь (экран сопоставления, в CLI — вопрос или ``--accept-mapping``), и
подтверждённое название добавляется в ``aliases`` источника.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence

from rapidfuzz import fuzz

from autogenerator.contracts import (
    ColumnProfile,
    ColumnSnapshot,
    ColumnSpec,
    DType,
    MappingCandidate,
    ReconcileResult,
    ReconcileStatus,
    SchemaSnapshot,
    SourceSpec,
)
from autogenerator.contracts.periods import month_of_word

MONTH_TOKEN = "{месяц}"

WEIGHTS = {"name": 0.5, "type": 0.2, "value": 0.3}
CANDIDATE = 0.4
"""Ниже этой оценки столбец файла кандидатом не показывается."""
PROPOSE = 0.6
"""С этой оценки лучший кандидат предлагается как сопоставление."""
SHOW = 3
"""Сколько кандидатов показывать на столбец."""
AMBIGUOUS = 0.05
"""Если два лучших кандидата ближе друг к другу, сопоставление не предлагается: выбирает пользователь."""

NUMERIC = {DType.INT, DType.FLOAT}
TEMPORAL = {DType.DATE, DType.DATETIME}


def normalize_name(name: str) -> str:
    """Нижний регистр, ``ё → е``, без пунктуации, схлопнутые пробелы."""
    s = unicodedata.normalize("NFKC", name).lower().replace("ё", "е")
    s = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in s)
    return re.sub(r"\s+", " ", s).strip()


def month_pattern(name: str) -> str:
    """Нормализованное название, где названия месяцев заменены на ``{месяц}``:
    «Были ли покупки в ноябре-январе» → «были ли покупки в {месяц} {месяц}»."""
    return " ".join(MONTH_TOKEN if month_of_word(w) else w for w in normalize_name(name).split())


# --- оценка кандидатов -----------------------------------------------------------


def name_score(file_name: str, spec: ColumnSpec) -> float:
    """Похожесть названия столбца файла на название столбца источника, любое из его
    ``aliases`` или ``id``: среднее «по набору слов» и «по порядку слов», от 0 до 1.
    Первое не наказывает за лишнее слово («Сумма» → «Сумма руб»), второе — наказывает,
    поэтому «Дата» к «Дата отгрузки» ближе, чем к «Регион», но не так близко, как к «Дата»."""
    a = month_pattern(file_name)
    best = 0.0
    for n in [spec.name, *spec.aliases, spec.id.replace("_", " ")]:
        b = month_pattern(n)
        if not a or not b:
            continue
        r = (fuzz.token_set_ratio(a, b) + fuzz.token_sort_ratio(a, b)) / 200
        best = max(best, r)
    return round(best, 3)


def type_score(file_type: DType | None, spec_type: DType) -> float | None:
    """1 — тот же тип, 0,7 — приводится без потерь (целое и дробное, дата и дата со временем,
    число или дата в текстовый столбец), 0 — несовместим. ``None`` — тип файла неизвестен."""
    if file_type is None:
        return None
    if file_type == spec_type:
        return 1.0
    if {file_type, spec_type} <= NUMERIC or {file_type, spec_type} <= TEMPORAL:
        return 0.7
    if spec_type == DType.STRING:
        return 0.7
    if spec_type == DType.BOOL and file_type == DType.STRING:
        return 0.3
    return 0.0


def _num(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def value_score(file: ColumnProfile | None, hist: ColumnProfile | None, dtype: DType) -> float | None:
    """Похожесть значений столбца файла (профиль выборки) на значения столбца в прежних
    загрузках (профиль последней загрузки). ``None`` — сравнить не с чем."""
    if file is None or hist is None or not file.rows or not hist.rows:
        return None
    main: float | None = None
    if dtype in NUMERIC:
        a = (_num(file.min), _num(file.max))
        b = (_num(hist.min), _num(hist.max))
        if None not in a and None not in b:
            lo, hi = max(a[0], b[0]), min(a[1], b[1])  # type: ignore[type-var]
            span = max(a[1], b[1]) - min(a[0], b[0])  # type: ignore[type-var,operator]
            iou = 1.0 if span == 0 else max(hi - lo, 0) / span  # type: ignore[operator]
            ma = math.log10(max(abs(a[0]), abs(a[1])) + 1)  # type: ignore[arg-type]
            mb = math.log10(max(abs(b[0]), abs(b[1])) + 1)  # type: ignore[arg-type]
            main = 0.5 * iou + 0.5 * max(0.0, 1 - abs(ma - mb) / 3)
    elif dtype in TEMPORAL:
        main = 0.5  # даты у разных периодов разные: тут похожесть значений ничего не говорит
    else:
        if file.top_skipped and hist.top_skipped:
            main = 0.6  # почти все значения разные у обоих: номера, ИНН, названия
        else:
            ta = {v.value for v in file.top}
            tb = {v.value for v in hist.top}
            if ta and tb:
                main = len(ta & tb) / min(len(ta), len(tb))
    if main is None:
        return None
    nulls = 1 - abs(file.nulls / file.rows - hist.nulls / hist.rows)
    return round(0.8 * main + 0.2 * nulls, 3)


def score(
    col: ColumnSnapshot,
    spec: ColumnSpec,
    typed: bool,
    stats: Mapping[str, ColumnProfile] | None,
) -> MappingCandidate:
    """Оценка столбца файла как кандидата на столбец источника. ``typed`` — типы и профиль
    снимка выведены по выборке (у снимка Excel по шапке их нет)."""
    parts: dict[str, float | None] = {
        "name": name_score(col.source_name, spec),
        "type": type_score(col.dtype if typed else None, spec.dtype),
        "value": value_score(col.profile if typed else None, (stats or {}).get(spec.id), spec.dtype),
    }
    known = {k: v for k, v in parts.items() if v is not None}
    total = sum(WEIGHTS[k] * v for k, v in known.items()) / sum(WEIGHTS[k] for k in known)
    if parts["type"] == 0.0:
        total = min(total, PROPOSE - 0.01)  # несовместимый тип не предлагается сам
    return MappingCandidate(
        file_name=col.source_name,
        score=round(total, 3),
        name_score=parts["name"] or 0.0,
        type_score=parts["type"],
        value_score=parts["value"],
        dtype=col.dtype if typed else None,
    )


# --- сверка ------------------------------------------------------------------------


def reconcile(
    source: SourceSpec,
    snapshot: SchemaSnapshot,
    required: Collection[str] | None = None,
    value_stats: Mapping[str, ColumnProfile] | None = None,
    dependents: Mapping[str, Sequence[str]] | None = None,
    declined: Collection[str] | None = None,
) -> ReconcileResult:
    """Сопоставить столбцы файла со столбцами источника.

    ``required`` — id столбцов, которые используют сценарии (их даёт ``engine.column_usage``);
    ``None`` — неизвестно (ни один сценарий из папки данных не берёт этот источник), тогда
    важен каждый столбец. Столбец периода обязателен всегда (если период не задаётся при
    загрузке). ``value_stats`` — профиль столбцов прежних загрузок, ``dependents`` — кто
    использует столбец (для объяснения, почему загрузка остановлена). ``declined`` — столбцы,
    которые пользователь решил оставить пустыми: их не предлагают снова; нужный сценарию
    столбец от этого не перестаёт быть нужным, и загрузка остаётся остановленной.

    Итог: ``ok``; ``needs_review`` — для пропавшего столбца, который нужен сценариям (или при
    неизвестном использовании — для любого), нашёлся вероятный кандидат: пара предлагается в
    ``proposed`` и ждёт подтверждения (если два кандидата почти равны, пары нет — выбирает
    пользователь из ``candidates``); ``blocked`` — нужный сценарию столбец не найден и
    кандидата для него нет.
    """
    spec_columns = source.file_columns
    in_file = {c.id for c in spec_columns}
    known_usage = required is not None
    needed = (set(required or ()) | {source.period_column}) & in_file
    file_by_norm: dict[str, list[str]] = {}
    for col in snapshot.columns:
        file_by_norm.setdefault(normalize_name(col.source_name), []).append(col.source_name)

    mapping: dict[str, str] = {}
    pending: list[ColumnSpec] = []
    messages: list[str] = []
    warnings: list[str] = []
    for spec in spec_columns:
        found: list[str] = []
        for n in [spec.name, *spec.aliases]:
            for fname in file_by_norm.get(normalize_name(n), []):
                if fname not in found and fname not in mapping:
                    found.append(fname)
        if not found:
            pending.append(spec)
            continue
        if len(found) > 1:
            warnings.append(f"Столбцу «{spec.id}» подходят несколько столбцов файла: {', '.join(found)}; взят первый")
        mapping[found[0]] = spec.id

    # Второй проход — по названиям без месяцев, только среди ещё не сопоставленных столбцов.
    by_pattern: dict[str, str] = {}
    file_by_pattern: dict[str, list[str]] = {}
    for col in snapshot.columns:
        if col.source_name not in mapping:
            file_by_pattern.setdefault(month_pattern(col.source_name), []).append(col.source_name)
    missing: list[ColumnSpec] = []
    for spec in pending:
        found = []
        for n in [spec.name, *spec.aliases]:
            pat = month_pattern(n)
            if MONTH_TOKEN not in pat:
                continue
            for fname in file_by_pattern.get(pat, []):
                if fname not in found and fname not in mapping:
                    found.append(fname)
        if len(found) == 1:
            mapping[found[0]] = spec.id
            by_pattern[found[0]] = spec.name
            continue
        if len(found) > 1:
            warnings.append(
                f"Столбцу «{spec.name}» (id {spec.id}) с точностью до месяца подходят несколько столбцов файла: "
                f"{', '.join(found)}; ни один не взят — добавьте нужное название в aliases"
            )
        missing.append(spec)

    # Третий проход — кандидаты по похожести среди оставшихся столбцов файла.
    typed = snapshot.sample_rows > 0
    free = [c for c in snapshot.columns if c.source_name not in mapping]
    candidates: dict[str, list[MappingCandidate]] = {}
    pairs: list[tuple[float, str, str]] = []
    likely: set[str] = set()  # у столбца есть кандидат с оценкой не ниже PROPOSE
    left_empty = set(declined or ())
    for spec in missing:
        if spec.id in left_empty:
            continue
        scored = sorted((score(c, spec, typed, value_stats) for c in free), key=lambda m: (-m.score, m.file_name))
        shown = [m for m in scored if m.score >= CANDIDATE][:SHOW]
        if shown:
            candidates[spec.id] = shown
        good = [m for m in scored if m.score >= PROPOSE]
        if good:
            likely.add(spec.id)
        if len(good) > 1 and good[0].score - good[1].score < AMBIGUOUS:
            continue  # два почти одинаковых кандидата: выбирает пользователь
        pairs += [(m.score, spec.id, m.file_name) for m in good]
    best: dict[str, str] = {}  # id → название в файле: каждому столбцу не больше одного
    for _, cid, fname in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
        if cid not in best and fname not in best.values():
            best[cid] = fname

    proposed: dict[str, str] = {}
    review: set[str] = set()  # пропавшие столбцы, по которым нужен выбор пользователя
    missing_required: list[str] = []
    missing_optional: list[str] = []
    for spec in missing:
        important = (spec.id in needed or not known_usage) and spec.id not in left_empty
        picked = best.get(spec.id)
        if important and spec.id in likely:
            review.add(spec.id)
        if picked is not None and important:
            proposed[picked] = spec.id
            continue
        if spec.id in needed:
            missing_required.append(spec.id)
        else:
            missing_optional.append(spec.id)
            if picked is not None:
                cand = next(m for m in candidates[spec.id] if m.file_name == picked)
                warnings.append(
                    f"Нет столбца «{spec.name}» (id {spec.id}); похоже, это «{picked}» (сходство {_pct(cand.score)}). "
                    "Сценариям он не нужен, в этой загрузке он пустой"
                )

    if by_pattern:
        messages.append(
            "Столбцы с другим месяцем в названии: " + "; ".join(f"«{f}» → «{s}»" for f, s in by_pattern.items())
        )
    if proposed:
        messages.append(
            "Похоже, переименованы: "
            + "; ".join(f"«{f}» → «{source.column(c).name}» (id {c})" for f, c in proposed.items())
        )
    unmapped = [
        c.source_name for c in snapshot.columns if c.source_name not in mapping and c.source_name not in proposed
    ]
    # кто зависит от столбцов, которых нет: и тех, что остановили загрузку, и тех, по которым нужен выбор
    asked = [c.id for c in missing if c.id in review or c.id in missing_required]
    deps = {c: list((dependents or {}).get(c, [])) for c in asked if (dependents or {}).get(c)}
    for cid in missing_required:
        spec = source.column(cid)
        what = "столбца периода" if cid == source.period_column else "столбца"
        why = "" if cid == source.period_column else ", а он нужен сценарию"
        users = f" ({'; '.join(deps[cid])})" if cid in deps else ""
        warnings.append(
            f"Нет {what} «{spec.name}» (id {cid}){why}{users}. Искали названия: {', '.join([spec.name, *spec.aliases])}"
        )
    for cid in missing_optional:
        if any(cid in w and "похоже" in w for w in warnings):
            continue
        how = " по вашему выбору" if cid in left_empty else ""
        warnings.append(f"Нет столбца «{source.column(cid).name}» (id {cid}); в этой загрузке он пустой{how}")
    if unmapped:
        messages.append(f"Столбцы файла, которых нет в источнике (не используются): {', '.join(unmapped)}")

    if any(c not in review for c in missing_required):
        status = ReconcileStatus.BLOCKED
    elif review:
        status = ReconcileStatus.NEEDS_REVIEW
    else:
        status = ReconcileStatus.OK
    return ReconcileResult(
        status=status,
        mapping=mapping,
        proposed=proposed,
        candidates=candidates,
        dependents=deps,
        review=[c.id for c in missing if c.id in review],
        declined=sorted(left_empty & {c.id for c in missing}),
        missing_required=missing_required,
        missing_optional=missing_optional,
        unmapped_file_columns=unmapped,
        by_pattern=by_pattern,
        messages=messages,
        warnings=warnings,
    )


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def with_aliases(source: SourceSpec, pairs: Mapping[str, str]) -> SourceSpec:
    """Источник, где подтверждённые названия файла (``название → id``) добавлены в ``aliases``
    своих столбцов (F-605): в следующий раз такое переименование сопоставится само."""
    cols = []
    for c in source.columns:
        names = [f for f, cid in pairs.items() if cid == c.id]
        known = {normalize_name(n) for n in [c.name, *c.aliases]}
        new = [n for n in names if normalize_name(n) not in known]
        cols.append(c.model_copy(update={"aliases": [*c.aliases, *new]}) if new else c)
    return source.model_copy(update={"columns": cols})
