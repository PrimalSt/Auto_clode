"""Формат подстановки одного значения: для меток слайдов-образцов и ячеек таблиц (F-420).

Число знаков после запятой, масштаб (тыс., млн), знак «+» у положительных, проценты или
процентные пункты и учёт единицы, которая уже написана в шаблоне сразу после метки: если
после ``{{Доля}}`` в шаблоне стоит «%», значение подставляется без своего «%».
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autogenerator.contracts import short_form

from .formats import NBSP, SCALES, Scale, fmt_number

SCALE_ALIASES = {
    "thousand": "thousand",
    "thousands": "thousand",
    "k": "thousand",
    "тыс": "thousand",
    "тыс.": "thousand",
    "million": "million",
    "millions": "million",
    "m": "million",
    "млн": "million",
    "млн.": "million",
    "billion": "billion",
    "billions": "billion",
    "млрд": "billion",
    "млрд.": "billion",
}
SCALE_TEXT = short_form({"enum": sorted(SCALE_ALIASES)})
"""Масштаб в редакторе сценария: и полные имена, и сокращения («млн», «тыс.»)."""
_HAS_PERCENT = re.compile(r"^\s*%")
_HAS_POINTS = re.compile(r"^\s*п\.?\s*п", re.IGNORECASE)
# Символы, недопустимые в XML 1.0, и управляющие: значение вставляется одной строкой.
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿\ud800-\udfff]")


class ValueFormat(BaseModel):
    """Как показать число: ``decimals``, ``scale``, ``sign``, ``percent`` или ``points`` и ``unit``."""

    model_config = ConfigDict(extra="forbid")

    decimals: int | None = Field(None, ge=0, le=6, description="Знаков после запятой; пусто — 0, у процентов 1")
    scale: Scale | None = Field(
        None, description="Масштаб: thousand (тыс.), million (млн), billion (млрд)", json_schema_extra=SCALE_TEXT
    )
    sign: bool = Field(False, description="«+» у положительных")
    percent: bool = Field(False, description="Значение — доля: 0,125 → 12,5%")
    points: bool = Field(False, description="Значение — разница долей: 0,012 → 1,2 п.п.")
    unit: Literal["auto", "add", "none"] = Field(
        "auto",
        description="Единица (%, п.п., млн): auto — «%» и «п.п.», если их нет в шаблоне после метки; "
        "add — всегда, включая слово масштаба; none — никогда",
    )

    @field_validator("scale", mode="before")
    @classmethod
    def _scale_alias(cls, v: Any) -> Any:
        if isinstance(v, str):
            return SCALE_ALIASES.get(v.strip().lower(), v)
        return v

    def format(self, value: Any, after: str = "") -> str | None:
        """Значение текстом; ``None`` — значения нет. ``after`` — текст шаблона сразу после метки."""
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "да" if value else "нет"
        v = float(value)
        if self.percent or self.points:
            decimals = 1 if self.decimals is None else self.decimals
            text = fmt_number(v * 100, decimals=decimals, sign=self.sign)
            assert text is not None
            if self.points:
                unit, present = f"{NBSP}п.п.", _HAS_POINTS.match(after)
            else:
                unit, present = "%", _HAS_PERCENT.match(after)
            if self.unit == "add" or (self.unit == "auto" and not present):
                text += unit
            return text
        text = fmt_number(v, decimals=self.decimals or 0, scale=self.scale, sign=self.sign)
        assert text is not None
        if self.unit == "add" and self.scale:
            text += f"{NBSP}{SCALES[self.scale][1]}"
        return text


def one_line(text: str) -> str:
    """Значение для вставки в прогон: одна строка, без символов, недопустимых в XML, и без
    ``{{``/``}}`` (иначе проверка после сборки приняла бы значение за незаменённую метку)."""
    text = " ".join(text.replace("\r", "\n").replace("\t", " ").split("\n"))
    text = _BAD_XML.sub("", text)
    while "{{" in text or "}}" in text:
        text = text.replace("{{", "{").replace("}}", "}")
    return text
