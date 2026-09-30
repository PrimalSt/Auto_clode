"""Сверка структуры выгрузки с источником и сопоставление столбцов (ARCHITECTURE.md, раздел 6.2)."""

from .reconcile import normalize_name, reconcile

__all__ = ["normalize_name", "reconcile"]
