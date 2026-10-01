"""Сверка структуры выгрузки с источником (ARCHITECTURE.md, раздел 6.2).

Столбец файла подходит столбцу источника, если совпадает нормализованное название или
любое из ``aliases``. Если точного совпадения нет, сравниваются названия без месяцев:
«Были ли покупки в январе» в источнике и «… в апреле» в новой выгрузке — один столбец.
Кандидаты по похожести названий, типов и значений (rapidfuzz) и экран сопоставления —
на этапе M4.
"""

from __future__ import annotations

import re
import unicodedata

from autogenerator.contracts import ReconcileResult, ReconcileStatus, SchemaSnapshot, SourceSpec
from autogenerator.contracts.periods import month_of_word

MONTH_TOKEN = "{месяц}"


def normalize_name(name: str) -> str:
    """Нижний регистр, ``ё → е``, без пунктуации, схлопнутые пробелы."""
    s = unicodedata.normalize("NFKC", name).lower().replace("ё", "е")
    s = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in s)
    return re.sub(r"\s+", " ", s).strip()


def month_pattern(name: str) -> str:
    """Нормализованное название, где названия месяцев заменены на ``{месяц}``:
    «Были ли покупки в ноябре-январе» → «были ли покупки в {месяц} {месяц}»."""
    return " ".join(MONTH_TOKEN if month_of_word(w) else w for w in normalize_name(name).split())


def reconcile(
    source: SourceSpec,
    snapshot: SchemaSnapshot,
    required: set[str] | None = None,
) -> ReconcileResult:
    """Сопоставить столбцы файла со столбцами источника.

    ``required`` — id столбцов, которые используют сценарии (их даёт ``engine.column_usage``).
    Столбец периода обязателен всегда (если период не задаётся при загрузке). Без пары
    остались обязательные столбцы — ``blocked``, необязательные — предупреждение, в этой
    загрузке они будут пустыми.
    """
    spec_columns = source.file_columns
    in_file = {c.id for c in spec_columns}
    required = (set(required or ()) | {source.period_column}) & in_file
    file_by_norm: dict[str, list[str]] = {}
    for col in snapshot.columns:
        file_by_norm.setdefault(normalize_name(col.source_name), []).append(col.source_name)

    mapping: dict[str, str] = {}
    pending = []
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
    missing_required: list[str] = []
    missing_optional: list[str] = []
    for spec in pending:
        names = [spec.name, *spec.aliases]
        found = []
        for n in names:
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
        (missing_required if spec.id in required else missing_optional).append(spec.id)

    if by_pattern:
        messages.append(
            "Столбцы с другим месяцем в названии: " + "; ".join(f"«{f}» → «{s}»" for f, s in by_pattern.items())
        )
    unmapped = [c.source_name for c in snapshot.columns if c.source_name not in mapping]
    for cid in missing_required:
        spec = source.column(cid)
        what = "столбца периода" if cid == source.period_column else "столбца"
        why = "" if cid == source.period_column else ", а он нужен сценарию"
        warnings.append(
            f"Нет {what} «{spec.name}» (id {cid}){why}. Искали названия: {', '.join([spec.name, *spec.aliases])}"
        )
    for cid in missing_optional:
        warnings.append(f"Нет столбца «{source.column(cid).name}» (id {cid}); в этой загрузке он пустой")
    if unmapped:
        messages.append(f"Столбцы файла, которых нет в источнике (не используются): {', '.join(unmapped)}")

    status = ReconcileStatus.BLOCKED if missing_required else ReconcileStatus.OK
    return ReconcileResult(
        status=status,
        mapping=mapping,
        missing_required=missing_required,
        missing_optional=missing_optional,
        unmapped_file_columns=unmapped,
        by_pattern=by_pattern,
        messages=messages,
        warnings=warnings,
    )
