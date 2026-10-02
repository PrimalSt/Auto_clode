"""Роли макетов в корпоративном стиле: заголовок обложки — плейсхолдер «текст», текстовые
области мелкие, у части макетов рамки для картинок."""

from autogenerator.contracts import ChartGroupInfo, ChartInfo, Geometry, LayoutInfo, LayoutRole, MarkerInfo
from autogenerator.contracts import PlaceholderInfo as Ph
from autogenerator.contracts.theme import EMU_PER_INCH
from autogenerator.theme.layouts import guess_role, guess_roles
from autogenerator.theme.slides import labels_per_category

IN = EMU_PER_INCH
W, H = 15 * IN, 8 * IN


def g(x: float, y: float, cx: float, cy: float) -> Geometry:
    return Geometry(x=int(x * IN), y=int(y * IN), cx=int(cx * IN), cy=int(cy * IN))


TITLE = Ph(idx=0, type="title", name="Заголовок", geometry=g(0.7, 0.5, 13, 1))


def lay(key: str, name: str, *phs: Ph, preserve: bool = True, slides: int = 0, text: bool = False) -> LayoutInfo:
    return LayoutInfo(
        key=key, name=name, master=0, preserve=preserve, placeholders=list(phs), slides=slides, text_shapes=text
    )


COVER = lay(
    "1",
    "4_Обложка_3",
    Ph(idx=13, type="body", name="Дата", geometry=g(1, 6, 4, 0.5)),
    Ph(idx=14, type="body", name="Название", geometry=g(1, 2, 9, 2)),
    slides=1,
)
COVER_PHOTO = lay("2", "2_Обложка_2", Ph(idx=11, type="pic", name="Фото", geometry=g(0, 0, 15, 8)), *COVER.placeholders)
SECTION = lay("3", "Раздел", Ph(idx=14, type="body", name="Текст", geometry=g(1, 3, 8, 1.5)))
THESIS = lay("4", "Тезис акцент", TITLE, Ph(idx=27, type="body", name="Текст", geometry=g(0.7, 2, 13, 3.4)))
TWO = lay(
    "5",
    "Тезисы_2",
    TITLE,
    Ph(idx=19, type="body", name="Левый", geometry=g(0.7, 2, 6, 2.5)),
    Ph(idx=25, type="body", name="Правый", geometry=g(7.5, 2, 6, 2.5)),
)
PHOTO = lay(
    "6",
    "Текст с фото",
    TITLE,
    Ph(idx=56, type="body", name="Текст", geometry=g(0.7, 2, 6, 1.5)),
    Ph(idx=57, type="pic", name="Фото", geometry=g(7.5, 0, 7.5, 8)),
)
TABLE = lay("7", "Таблица_1", TITLE)
MARKERS = lay("8", "Маркеры", text=True)
FINAL = lay("9", "3_Последний", Ph(idx=20, type="body", name="Контакты", geometry=g(1, 3, 6, 1)))
NO_GEOMETRY = lay(
    "10", "Заголовок и объект", Ph(idx=0, type="title", name="Заголовок"), Ph(idx=1, type="obj", name="Объект")
)
ALL = [COVER, COVER_PHOTO, SECTION, THESIS, TWO, PHOTO, TABLE, MARKERS, FINAL, NO_GEOMETRY]


def test_guess_role_by_name_and_structure():
    area = W * H
    assert guess_role(COVER, area) == LayoutRole.TITLE
    assert guess_role(SECTION, area) == LayoutRole.SECTION
    assert guess_role(THESIS, area) == LayoutRole.TITLE_AND_CONTENT
    assert guess_role(TWO, area) == LayoutRole.TITLE_AND_TWO_CONTENT
    assert guess_role(PHOTO, area) is None  # рамка для картинки
    assert guess_role(TABLE, area) == LayoutRole.TITLE_ONLY
    assert guess_role(MARKERS, area) is None  # своя надпись на макете — не пустой
    assert guess_role(lay("11", "Пустой"), area) == LayoutRole.BLANK
    assert guess_role(FINAL, area) == LayoutRole.FINAL
    assert guess_role(NO_GEOMETRY, area) is None


def test_roles_and_derived_roles():
    roles, notes = guess_roles(ALL, W, H)
    by = {r.role: r for r in roles}
    assert set(by) == set(LayoutRole)
    # Обложка без рамки для фото; заголовок — самый большой текстовый плейсхолдер.
    assert by[LayoutRole.TITLE].layout_name == "4_Обложка_3"
    assert by[LayoutRole.TITLE].slot("title").placeholder_idx == 14
    assert by[LayoutRole.TITLE].slot("subtitle").placeholder_idx == 13
    assert by[LayoutRole.SECTION].slot("title").placeholder_idx == 14
    blank = by[LayoutRole.BLANK]
    assert blank.layout_name == "Таблица_1" and blank.drop_placeholders == [0] and blank.derived
    ttc = by[LayoutRole.TITLE_TEXT_AND_CONTENT]
    assert ttc.layout_name == "Тезисы_2" and ttc.derived
    assert [s.name for s in ttc.slots] == ["title", "text", "body"]
    assert ttc.slot("text").placeholder_idx == 19 and ttc.slot("body").placeholder_idx == 25
    assert len([n for n in notes if "построена" in n]) == 2


def test_preserved_layout_is_preferred():
    copy = lay("20", "Тезис акцент", *THESIS.placeholders, preserve=False, slides=14)
    roles, _ = guess_roles([copy, THESIS], W, H)
    assert roles[0].layout_key == THESIS.key


def _marker(name: str, box: Geometry) -> MarkerInfo:
    return MarkerInfo(name=name, shape_id=hash(name) % 1000, shape_name=name, occurrence=1, paragraph=0, geometry=box)


def _chart(direction: str, n: int = 4) -> ChartInfo:
    return ChartInfo(
        shape_id=1,
        shape_name="Диаграмма",
        geometry=g(0.7, 2, 13, 6),
        groups=[ChartGroupInfo(number=1, kind="bar", direction=direction, grouping="percentStacked")],
        categories=n,
    )


def test_labels_per_category():
    bars = _chart("bar")
    # Надписи справа от полос, по одной на категорию, плюс разбросанные внутри полос.
    right = [_marker(f"r{i}", g(13.9, 2.6 + 1.36 * i, 1.0, 0.7)) for i in range(4)]
    inside = [_marker(f"in{i}", g(2.7 + (i % 2) * 5, 3.2 + 1.36 * i, 0.8 + 0.3 * i, 0.7)) for i in range(4)]
    assert labels_per_category(bars, right + inside)
    assert not labels_per_category(bars, right[:3] + inside[:1])
    # Карточки показателей над столбцами не считаются надписями категорий.
    cols = _chart("col", 3)
    cards = [_marker(f"k{i}", g(0.6 + 4 * i, 0.9, 3.6, 1.0)) for i in range(3)]
    assert not labels_per_category(cols, cards)
    under = [_marker(f"u{i}", g(1.5 + 4.3 * i, 8.1, 1.0, 0.4)) for i in range(3)]
    assert labels_per_category(cols, under)
