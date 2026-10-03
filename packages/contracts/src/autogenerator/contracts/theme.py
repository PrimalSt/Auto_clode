"""Манифест шаблона оформления: что есть в .pptx и как это использовать
(ARCHITECTURE.md, раздел 6.5). Манифест пишет ``theme``, читает ``render``.

Слайды шаблона — слайды-образцы: для каждого записаны метки ``{{…}}`` (где стоят и как
оформлены), графики (группы серий, оси, форматы подписей) и таблицы. Слайды и фигуры
адресуются по id, а не по номеру и имени: номер слайда меняется при перестановке, а имена
фигур в шаблонах повторяются.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from .results import IssueLevel

EMU_PER_INCH = 914400
MARKER_PATTERN = r"\{\{([^{}\n]{1,64})\}\}"
"""Метка слайда-образца: ``{{имя}}``, имя — до 64 любых символов, кроме фигурных скобок и
переноса строки; пробелы по краям имени не учитываются (F-418)."""


class Geometry(BaseModel):
    """Положение и размер в EMU (1 дюйм = 914 400 EMU)."""

    x: int
    y: int
    cx: int
    cy: int

    def inches(self) -> str:
        f = EMU_PER_INCH
        return f"{self.x / f:.2f}, {self.y / f:.2f}; {self.cx / f:.2f} × {self.cy / f:.2f} дюйма"

    @property
    def right(self) -> int:
        return self.x + self.cx

    @property
    def bottom(self) -> int:
        return self.y + self.cy

    def intersection(self, other: Geometry) -> int:
        """Площадь пересечения с другой областью, EMU²."""
        w = min(self.right, other.right) - max(self.x, other.x)
        h = min(self.bottom, other.bottom) - max(self.y, other.y)
        return max(w, 0) * max(h, 0)


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
    fingerprint: str = Field(
        "",
        description="Отпечаток структуры (типы, индексы и геометрия плейсхолдеров): по нему макет "
        "находится после повторного импорта, если его id сменился",
    )
    slides: int = Field(0, description="Сколько слайдов шаблона стоит на этом макете")
    text_shapes: bool = Field(False, description="На макете есть свои надписи с текстом (не плейсхолдеры)")


class LayoutRole(StrEnum):
    """Роли макетов, на которые ссылается сценарий (``layout: title_and_content``)."""

    TITLE = "title"
    SECTION = "section"
    TITLE_AND_CONTENT = "title_and_content"
    TITLE_AND_TWO_CONTENT = "title_and_two_content"
    TITLE_TEXT_AND_CONTENT = "title_text_and_content"
    TITLE_ONLY = "title_only"
    BLANK = "blank"
    FINAL = "final"


class SlotInfo(BaseModel):
    """Область макета, куда ставится блок: плейсхолдер или прямоугольник на слайде."""

    name: str
    placeholder_idx: int | None = None
    geometry: Geometry


class DesignElement(BaseModel):
    """Элемент оформления роли: логотип, волна и т. п. — фигура со слайда этой роли в шаблоне,
    которой нет на макете роли. Переносится на новые слайды этой роли (раздел 6.5). Если такая
    же фигура есть на макете с ``preserve="1"``, она берётся оттуда: тогда элемент не пропадёт,
    если слайд удалят из шаблона. Положение — как на слайде роли."""

    name: str
    kind: Literal["picture", "group", "shape"]
    shape_id: int = Field(description="id фигуры в источнике")
    from_slide: int | None = Field(None, description="sldId слайда шаблона, откуда фигура")
    from_layout: str | None = Field(None, description="Ключ макета с preserve, где есть такая же фигура")
    geometry: Geometry


class RoleBinding(BaseModel):
    role: LayoutRole
    layout_key: str
    layout_name: str
    slots: list[SlotInfo]
    guessed: bool = Field(True, description="Роль предложена приложением и ещё не подтверждена")
    drop_placeholders: list[int] = Field(
        default_factory=list,
        description="Плейсхолдеры макета, которые удаляются с нового слайда: роль построена из ближайшего "
        "макета (например, «пустой» — из макета только с заголовком)",
    )
    derived: str | None = Field(None, description="Как роль построена, если подходящего макета в шаблоне нет")
    decorations: list[DesignElement] = Field(
        default_factory=list, description="Элементы оформления: переносятся на новые слайды этой роли"
    )

    def slot(self, name: str) -> SlotInfo | None:
        for s in self.slots:
            if s.name == name:
                return s
        return None


class TextStyle(BaseModel):
    """Оформление прогона, в котором начинается метка: с ним вставляется значение."""

    font: str | None = None
    size: float | None = Field(None, description="Размер шрифта, пт; None — наследуется")
    bold: bool | None = None
    lang: str | None = None


class MarkerInfo(BaseModel):
    """Одно вхождение метки ``{{имя}}`` на слайде шаблона."""

    name: str
    shape_id: int
    shape_name: str
    occurrence: int = Field(description="Номер вхождения этого имени в фигуре, с единицы")
    paragraph: int = Field(description="Номер абзаца в фигуре (в ячейке таблицы), с нуля")
    cell: tuple[int, int] | None = Field(None, description="Строка и столбец ячейки таблицы")
    text_after: str = Field("", description="Текст абзаца сразу после метки: «%», « млн.» и т. п.")
    runs: int = Field(1, description="Сколько прогонов занимает метка (PowerPoint режет текст по языку)")
    style: TextStyle = Field(default_factory=TextStyle)
    geometry: Geometry | None = None
    wrap: bool = True
    align: str | None = None
    autofit: str | None = Field(None, description="shape — фигура по тексту, text — текст по фигуре, None — нет")
    replaceable: bool = True
    reason: str | None = Field(None, description="Почему метку нельзя заменить")

    @property
    def key(self) -> str:
        """Адрес вхождения в привязке: ``имя@фигура#номер``."""
        return f"{self.name}@{self.shape_id}#{self.occurrence}"


class SeriesInfo(BaseModel):
    """Серия графика шаблона."""

    idx: int
    order: int
    name: str = ""
    color: str | None = Field(None, description="Цвет заливки или линии, RGB hex; None — по теме или авто")
    number_format: str | None = None
    points: int = 0


class ChartGroupInfo(BaseModel):
    """Группа серий графика: столбцы, линия, круговая и т. п. со своими осями."""

    number: int = Field(description="Номер группы в графике, с единицы, в порядке документа")
    kind: str = Field(description="bar, line, pie, doughnut, area, scatter, …")
    direction: str | None = Field(None, description="col — вертикальные столбцы, bar — горизонтальные")
    grouping: str | None = Field(None, description="clustered, stacked, percentStacked, standard")
    secondary: bool = Field(False, description="Группа на второй оси значений")
    series: list[SeriesInfo] = Field(default_factory=list)
    label_format: str | None = Field(None, description="Формат подписей данных, если задан в шаблоне")
    labels: bool = Field(False, description="Подписи данных включены")


class ChartInfo(BaseModel):
    """График на слайде шаблона."""

    shape_id: int
    shape_name: str
    geometry: Geometry | None = None
    chart_type: str = Field("", description="Тип графика по python-pptx: COLUMN_STACKED, PIE, …")
    groups: list[ChartGroupInfo] = Field(default_factory=list)
    categories: int = 0
    point_settings: int = Field(0, description="Настройки отдельных точек и секторов (dPt, dLbl)")
    labels_per_category: bool = Field(
        False,
        description="Рядом с графиком надписи с метками расставлены напротив каждой категории: число и порядок "
        "категорий закрепляются при привязке",
    )
    overlaid: list[int] = Field(default_factory=list, description="id других графиков, наложенных на этот")

    @property
    def series_count(self) -> list[int]:
        return [len(g.series) for g in self.groups]


class TableInfo(BaseModel):
    """Таблица на слайде шаблона."""

    shape_id: int
    shape_name: str
    geometry: Geometry | None = None
    rows: int
    cols: int
    style_id: str | None = None
    first_row: bool = Field(True, description="Первая строка — заголовок")
    header: list[str] = Field(default_factory=list, description="Текст первой строки")


class TemplateSlideInfo(BaseModel):
    """Слайд шаблона — слайд-образец."""

    slide_id: int = Field(description="sldId: номер слайда меняется при перестановке, id — нет")
    number: int
    layout_key: str
    layout_name: str = ""
    title: str | None = None
    markers: list[MarkerInfo] = Field(default_factory=list)
    charts: list[ChartInfo] = Field(default_factory=list)
    tables: list[TableInfo] = Field(default_factory=list)
    agen_tag: str | None = Field(None, description="Служебный тег AGEN_SLIDE выгруженного слайда (F-422)")

    @property
    def marker_names(self) -> list[str]:
        return list(dict.fromkeys(m.name for m in self.markers))

    def chart(self, shape_id: int) -> ChartInfo | None:
        return next((c for c in self.charts if c.shape_id == shape_id), None)

    def table(self, shape_id: int) -> TableInfo | None:
        return next((t for t in self.tables if t.shape_id == shape_id), None)


class LintIssue(BaseModel):
    """Замечание проверки шаблона (F-410)."""

    code: str = Field(description="Вид замечания: marker_split, duplicate_marker, hardcoded_year, …")
    level: IssueLevel = IssueLevel.WARNING
    message: str
    slide: int | None = Field(None, description="Номер слайда шаблона")
    slide_id: int | None = None
    shape_id: int | None = None

    def __str__(self) -> str:
        where = f"слайд {self.slide}: " if self.slide is not None else ""
        return f"{where}{self.message}"


class FontInfo(BaseModel):
    name: str
    embedded: bool = False
    installed: bool | None = Field(None, description="None — проверить нельзя")


class ThemeManifest(BaseModel):
    source_path: str = Field(description="Исходный файл шаблона")
    pptx_path: str = Field(description="Рабочая копия: .pptx, который открывает render")
    sha256: str
    slide_width: int
    slide_height: int
    layouts: list[LayoutInfo]
    roles: list[RoleBinding]
    slides: list[TemplateSlideInfo] = Field(default_factory=list)
    fonts: list[FontInfo] = Field(default_factory=list)
    lint: list[LintIssue] = Field(default_factory=list, description="Отчёт проверки шаблона")
    notes: list[str] = Field(default_factory=list, description="Замечания импорта")

    def role(self, role: str) -> RoleBinding | None:
        for r in self.roles:
            if r.role == role:
                return r
        return None

    def slide(self, slide_id: int) -> TemplateSlideInfo | None:
        return next((s for s in self.slides if s.slide_id == slide_id), None)
