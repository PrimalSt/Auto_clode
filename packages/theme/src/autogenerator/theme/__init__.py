"""Импорт .pptx-шаблона: размер слайда, макеты и их роли, слайды-образцы, шрифты, проверка
шаблона, манифест (ARCHITECTURE.md, раздел 6.5)."""

from .importer import import_template, make_working_copy
from .report import describe

__all__ = ["describe", "import_template", "make_working_copy"]
