"""Сборка презентации: слайды из макетов в рабочей копии шаблона, вызов блоков из реестра,
проверка результата, сохранение (ARCHITECTURE.md, раздел 6.5)."""

from .build import build_presentation, sanitize_filename, save_presentation, validate_slides

__all__ = ["build_presentation", "sanitize_filename", "save_presentation", "validate_slides"]
