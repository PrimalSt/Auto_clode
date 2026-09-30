"""Манифест шаблона оформления: что есть в .pptx и как это использовать
(ARCHITECTURE.md, раздел 6.5). Манифест пишет ``theme``, читает ``render``."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

EMU_PER_INCH = 914400


class Geometry(BaseModel):
    """Положение и размер в EMU (1 дюйм = 914 400 EMU)."""

    x: int
    y: int
    cx: int
    cy: int

    def inches(self) -> str:
        f = EMU_PER_INCH
        return f"{self.x / f:.2f}, {self.y / f:.2f}; {self.cx / f:.2f} × {self.cy / f:.2f} дюйма"


class PlaceholderInfo(BaseModel):
    idx: int
    type: str = Field(description="Тип плейсхолдера: title, ctrTitle, subTitle, body, obj, …")
    name: str
    geometry: Geometry | None = Field(None, description="None — у плейсхолдера нет геометрии")


class LayoutInfo(BaseModel):
    """Макет шаблона. Ключ — ``sldLayoutId`` из мастера: имена макетов повторяются."""

    key: str
    name: str
    master: int = Field(description="Номер мастера, с нуля")
    preserve: bool = False
    placeholders: list[PlaceholderInfo] = Field(default_factory=list)


class LayoutRole(StrEnum):
    """Роли макетов, на которые ссылается сценарий (``layout: title_and_content``)."""

    TITLE = "title"
    SECTION = "section"
    TITLE_AND_CONTENT = "title_and_content"
    TITLE_AND_TWO_CONTENT = "title_and_two_content"
    TITLE_ONLY = "title_only"
    BLANK = "blank"


class SlotInfo(BaseModel):
    """Область макета, куда ставится блок: плейсхолдер или прямоугольник на слайде."""

    name: str
    placeholder_idx: int | None = None
    geometry: Geometry


class RoleBinding(BaseModel):
    role: LayoutRole
    layout_key: str
    layout_name: str
    slots: list[SlotInfo]
    guessed: bool = Field(True, description="Роль предложена приложением и ещё не подтверждена")

    def slot(self, name: str) -> SlotInfo | None:
        for s in self.slots:
            if s.name == name:
                return s
        return None


class TemplateSlideInfo(BaseModel):
    """Слайд шаблона — будущий слайд-образец (заполнение появится на этапе M3)."""

    slide_id: int = Field(description="sldId: номер слайда меняется при перестановке, id — нет")
    number: int
    layout_key: str
    title: str | None = None
    markers: list[str] = Field(default_factory=list, description="Имена меток {{…}} в тексте")
    charts: int = 0
    tables: int = 0


class ThemeManifest(BaseModel):
    source_path: str = Field(description="Исходный файл шаблона")
    pptx_path: str = Field(description="Рабочая копия: .pptx, который открывает render")
    sha256: str
    slide_width: int
    slide_height: int
    layouts: list[LayoutInfo]
    roles: list[RoleBinding]
    slides: list[TemplateSlideInfo] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, description="Замечания импорта")

    def role(self, role: str) -> RoleBinding | None:
        for r in self.roles:
            if r.role == role:
                return r
        return None
