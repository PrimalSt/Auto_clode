"""Сверка структуры выгрузки с источником и черновик источника по первой выгрузке
(ARCHITECTURE.md, раздел 6.2)."""

from .draft import draft_source, suggest_id
from .reconcile import normalize_name, reconcile

__all__ = ["draft_source", "normalize_name", "reconcile", "suggest_id"]
