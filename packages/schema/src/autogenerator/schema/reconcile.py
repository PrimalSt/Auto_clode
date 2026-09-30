"""Сверка структуры выгрузки с источником (ARCHITECTURE.md, раздел 6.2).

Этап M0: точное совпадение по нормализованному названию или любому из ``aliases``.
Кандидаты по похожести названий, типов и значений (rapidfuzz) и экран сопоставления —
на этапе M4.
"""

from __future__ import annotations

import re
import unicodedata

from autogenerator.contracts import ReconcileResult, ReconcileStatus, SchemaSnapshot, SourceSpec


def normalize_name(name: str) -> str:
    """Нижний регистр, ``ё → е``, без пунктуации, схлопнутые пробелы."""
    s = unicodedata.normalize("NFKC", name).lower().replace("ё", "е")
    s = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in s)
    return re.sub(r"\s+", " ", s).strip()


def reconcile(
    source: SourceSpec,
    snapshot: SchemaSnapshot,
    required: set[str] | None = None,
) -> ReconcileResult:
    """Сопоставить столбцы файла со столбцами источника.

    ``required`` — id столбцов, которые используют сценарии (их даёт ``engine.column_usage``).
    Столбец периода обязателен всегда. Без пары остались обязательные столбцы — ``blocked``,
    необязательные — предупреждение, в этой загрузке они будут пустыми.
    """
    required = set(required or ()) | {source.period_column}
    file_by_norm: dict[str, list[str]] = {}
    for col in snapshot.columns:
        file_by_norm.setdefault(normalize_name(col.source_name), []).append(col.source_name)

    mapping: dict[str, str] = {}
    missing_required: list[str] = []
    missing_optional: list[str] = []
    messages: list[str] = []
    for spec in source.columns:
        names = [spec.name, *spec.aliases]
        found: list[str] = []
        for n in names:
            for fname in file_by_norm.get(normalize_name(n), []):
                if fname not in found and fname not in mapping:
                    found.append(fname)
        if not found:
            (missing_required if spec.id in required else missing_optional).append(spec.id)
            continue
        if len(found) > 1:
            messages.append(f"Столбцу «{spec.id}» подходят несколько столбцов файла: {', '.join(found)}; взят первый")
        mapping[found[0]] = spec.id

    unmapped = [c.source_name for c in snapshot.columns if c.source_name not in mapping]
    for cid in missing_required:
        spec = source.column(cid)
        messages.append(
            f"Нет столбца «{spec.name}» (id {cid}), а он нужен сценарию. "
            f"Искали названия: {', '.join([spec.name, *spec.aliases])}"
        )
    for cid in missing_optional:
        messages.append(f"Нет столбца «{source.column(cid).name}» (id {cid}); в этой загрузке он пустой")
    if unmapped:
        messages.append(f"Столбцы файла, которых нет в источнике (не используются): {', '.join(unmapped)}")

    status = ReconcileStatus.BLOCKED if missing_required else ReconcileStatus.OK
    return ReconcileResult(
        status=status,
        mapping=mapping,
        missing_required=missing_required,
        missing_optional=missing_optional,
        unmapped_file_columns=unmapped,
        messages=messages,
    )
