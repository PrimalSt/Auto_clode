"""Макеты всех мастеров и роли макетов (F-402, ARCHITECTURE.md, раздел 6.5).

Роль — то, на что ссылается сценарий (``layout: title_and_content``). Приложение предлагает
макет под каждую роль по составу плейсхолдеров (``agen theme check --layouts`` показывает,
какой макет взят под какую роль), пользователь подтверждает или меняет их (``agen theme
roles``, окно «Оформление»): подтверждённые роли хранятся в версии шаблона. Недостающую роль
приложение строит из ближайшего макета: «пустой» — из макета только с заголовком (заголовок
удаляется), «заголовок, текст и блок» — из макета с двумя областями, «финальный» — из
титульного.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    Geometry,
    LayoutInfo,
    LayoutRole,
    PlaceholderInfo,
    RoleBinding,
    SlotInfo,
)
from autogenerator.contracts.ooxml import P
from autogenerator.contracts.theme import EMU_PER_INCH

# Плейсхолдеры, которые не считаются областями для блоков.
SERVICE_TYPES = {"dt", "ftr", "sldNum", "hdr"}
CONTENT_TYPES = {"obj", "body", "chart", "tbl", "dgm"}
PICTURE_TYPES = {"pic", "media", "clipArt"}
TITLE_TYPES = {"title", "ctrTitle"}
# Область меньше этой доли слайда — подпись, а не место для графика или текста.
MIN_CONTENT_SHARE = 0.10
MARGIN = EMU_PER_INCH // 2
# В корпоративных шаблонах заголовок обложки, раздела и финального слайда — плейсхолдер типа
# «текст», поэтому эти роли узнаются и по имени макета.
TITLE_NAMES = re.compile(r"обложк|титул|title slide|cover", re.IGNORECASE)
SECTION_NAMES = re.compile(r"раздел|section", re.IGNORECASE)
FINAL_NAMES = re.compile(r"последн|финал|заверш|спасибо|\bend\b|final|closing|thank", re.IGNORECASE)


def _geometry(shape: Any) -> Geometry | None:
    try:
        x, y, cx, cy = shape.left, shape.top, shape.width, shape.height
    except Exception:
        return None
    if None in (x, y, cx, cy):
        return None
    return Geometry(x=int(x), y=int(y), cx=int(cx), cy=int(cy))


def _ph_type(ph: Any) -> str:
    el = ph._element.ph
    if el is None:
        return "obj"
    return el.get("type") or "obj"


def _placeholders(layout: Any) -> list[PlaceholderInfo]:
    return [
        PlaceholderInfo(idx=ph.placeholder_format.idx, type=_ph_type(ph), name=ph.name, geometry=_geometry(ph))
        for ph in layout.placeholders
    ]


def fingerprint(placeholders: list[PlaceholderInfo]) -> str:
    """Отпечаток структуры макета: типы, индексы и геометрия плейсхолдеров (с точностью до
    0,1 дюйма). По нему макет находится после повторного импорта шаблона."""
    step = EMU_PER_INCH // 10
    parts = []
    for p in sorted(placeholders, key=lambda p: (p.type, p.idx)):
        g = p.geometry
        geo = f"{g.x // step},{g.y // step},{g.cx // step},{g.cy // step}" if g else "-"
        parts.append(f"{p.type}#{p.idx}@{geo}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _has_text_shapes(layout: Any) -> bool:
    """На макете есть свои надписи с текстом (не плейсхолдеры): такой макет не «пустой»."""
    for sh in layout.shapes:
        if sh.is_placeholder or not getattr(sh, "has_text_frame", False):
            continue
        if sh.text_frame.text.strip():
            return True
    return False


def read_layouts(prs: Any) -> list[tuple[LayoutInfo, Any]]:
    """Макеты всех мастеров: ``prs.slide_layouts`` в python-pptx видит только первый мастер."""
    used: dict[Any, int] = {}
    for slide in prs.slides:
        used[slide.slide_layout.part] = used.get(slide.slide_layout.part, 0) + 1
    out = []
    for mi, master in enumerate(prs.slide_masters):
        lst = master._element.find(f"{P}sldLayoutIdLst")
        entries = list(lst) if lst is not None else []
        for entry, layout in zip(entries, master.slide_layouts, strict=False):
            phs = _placeholders(layout)
            info = LayoutInfo(
                key=str(entry.get("id")),
                name=layout.name,
                master=mi,
                preserve=layout._element.get("preserve") == "1",
                placeholders=phs,
                fingerprint=fingerprint(phs),
                slides=used.get(layout.part, 0),
                text_shapes=_has_text_shapes(layout),
            )
            out.append((info, layout))
    return out


def _area(g: Geometry | None) -> int:
    return g.cx * g.cy if g else 0


def has_geometry(info: LayoutInfo) -> bool:
    return all(p.geometry is not None for p in info.placeholders if p.type not in SERVICE_TYPES)


@dataclass
class _Parts:
    """Плейсхолдеры макета по назначению."""

    titles: list[PlaceholderInfo]
    subtitles: list[PlaceholderInfo]
    content: list[PlaceholderInfo]
    small: list[PlaceholderInfo]
    pictures: list[PlaceholderInfo]

    @property
    def text(self) -> list[PlaceholderInfo]:
        """Текстовые плейсхолдеры по убыванию площади: заголовок обложки — самый большой."""
        return sorted(self.small + self.content, key=lambda p: -_area(p.geometry))


def _parts(info: LayoutInfo, slide_area: int) -> _Parts:
    phs = [p for p in info.placeholders if p.type not in SERVICE_TYPES]
    titles = [p for p in phs if p.type in TITLE_TYPES]
    content = [p for p in phs if p.type in CONTENT_TYPES and _area(p.geometry) >= MIN_CONTENT_SHARE * slide_area]
    return _Parts(
        titles=titles,
        subtitles=[p for p in phs if p.type == "subTitle"],
        content=content,
        small=[p for p in phs if p.type in ("body", "obj") and p not in content],
        pictures=[p for p in phs if p.type in PICTURE_TYPES],
    )


def guess_role(info: LayoutInfo, slide_area: int) -> LayoutRole | None:
    """Роль, под которую подходит макет. Обложка, раздел и финальный слайд узнаются и по имени;
    макеты с плейсхолдерами для картинок под остальные роли не предлагаются: блок в них не
    встанет, а пустая рамка «Вставьте рисунок» испортит слайд."""
    if not has_geometry(info):
        return None  # макет без геометрии не предлагается для новых слайдов
    x = _parts(info, slide_area)
    if FINAL_NAMES.search(info.name):
        return LayoutRole.FINAL
    if TITLE_NAMES.search(info.name) or any(p.type == "ctrTitle" for p in x.titles):
        return LayoutRole.TITLE
    if SECTION_NAMES.search(info.name):
        return LayoutRole.SECTION
    if x.pictures:
        return None
    if not (x.titles or x.subtitles or x.content or x.small):
        return None if info.text_shapes else LayoutRole.BLANK
    if x.titles and x.subtitles and not x.content:
        return LayoutRole.TITLE
    if len(x.titles) != 1 or x.subtitles:
        return None
    n, k = len(x.content), len(x.small)
    if n == 1 and k == 0:
        return LayoutRole.TITLE_AND_CONTENT
    if n == 1 and k == 1:
        return LayoutRole.TITLE_TEXT_AND_CONTENT
    if n == 2 and k == 0:
        # Объект и текст рядом («Объект с подписью») — текст и блок; два объекта или два текста — два блока.
        kinds = {p.type for p in x.content}
        return LayoutRole.TITLE_TEXT_AND_CONTENT if kinds == {"obj", "body"} else LayoutRole.TITLE_AND_TWO_CONTENT
    if n == 0 and k == 1:
        return LayoutRole.SECTION
    if n == 0 and k == 0:
        return LayoutRole.TITLE_ONLY
    return None


def _full(width: int, height: int) -> Geometry:
    return Geometry(x=MARGIN, y=MARGIN, cx=width - 2 * MARGIN, cy=height - 2 * MARGIN)


def _slot(name: str, p: PlaceholderInfo) -> SlotInfo:
    assert p.geometry is not None
    return SlotInfo(name=name, placeholder_idx=p.idx, geometry=p.geometry)


def _slots(role: LayoutRole, info: LayoutInfo, width: int, height: int) -> list[SlotInfo]:
    x = _parts(info, width * height)
    slots: list[SlotInfo] = []
    title = x.titles[0] if x.titles else None
    text = x.text
    if title is None and role in (LayoutRole.TITLE, LayoutRole.SECTION, LayoutRole.FINAL) and text:
        title = text.pop(0)  # заголовок — текстовый плейсхолдер, самый большой на макете
    if title is not None:
        slots.append(_slot("title", title))
    if role == LayoutRole.TITLE:
        subs = x.subtitles or [p for p in text if p is not title]
        if subs:
            slots.append(_slot("subtitle", subs[0]))
    elif role in (LayoutRole.SECTION, LayoutRole.TITLE_AND_CONTENT):
        rest = [p for p in text if p is not title]
        if rest:
            slots.append(_slot("body", rest[0]))
    elif role == LayoutRole.TITLE_TEXT_AND_CONTENT:
        texts = x.small or [p for p in x.content if p.type == "body"]
        body = next(p for p in x.content if p not in texts)
        slots += [_slot("text", texts[0]), _slot("body", body)]
    elif role == LayoutRole.TITLE_AND_TWO_CONTENT:
        pair = sorted(x.content[:2], key=lambda p: p.geometry.x if p.geometry else 0)
        slots += [_slot(name, p) for name, p in zip(("left", "right"), pair, strict=True)]
    elif role == LayoutRole.TITLE_ONLY and title is not None and title.geometry is not None:
        slots.append(SlotInfo(name="body", geometry=_below(title.geometry, height)))
    elif role in (LayoutRole.BLANK, LayoutRole.FINAL) and title is None:
        slots.append(SlotInfo(name="body", geometry=_full(width, height)))
    return slots


def _below(g: Geometry, height: int) -> Geometry:
    """Область под заголовком — для блоков на макете без плейсхолдеров содержимого."""
    top = g.y + g.cy + MARGIN // 2
    return Geometry(x=g.x, y=top, cx=g.cx, cy=max(height - top - MARGIN, MARGIN))


def _rank(info: LayoutInfo, slide_area: int) -> tuple[int, int, int, float, int]:
    """Из нескольких подходящих макетов берётся макет без плейсхолдеров для картинок, затем
    сохраняемый (``preserve``: PowerPoint может удалить макет без него, когда на нём не
    останется слайдов), затем тот, на котором больше слайдов шаблона, с большей областью
    содержимого, из первого мастера."""
    x = _parts(info, slide_area)
    content = sum(_area(p.geometry) for p in x.content) / slide_area
    return (1 if x.pictures else 0, 0 if info.preserve else 1, -info.slides, -round(content, 2), info.master)


ROLE_NAMES = {
    LayoutRole.TITLE: "титульный",
    LayoutRole.SECTION: "раздел",
    LayoutRole.TITLE_AND_CONTENT: "заголовок и блок",
    LayoutRole.TITLE_AND_TWO_CONTENT: "заголовок и два блока",
    LayoutRole.TITLE_TEXT_AND_CONTENT: "заголовок, текст и блок",
    LayoutRole.TITLE_ONLY: "только заголовок",
    LayoutRole.BLANK: "пустой",
    LayoutRole.FINAL: "финальный",
}


def bind_role(role: LayoutRole, info: LayoutInfo, width: int, height: int) -> RoleBinding:
    """Роль на макете, который выбрал пользователь (подтверждение ролей, F-402). Макет должен
    подходить роли: у «заголовка и двух блоков» — две области содержимого и т. д.; «пустой»
    можно поставить на любой макет с геометрией — его заголовок тогда удаляется."""
    what = f"Макет «{info.name}» не подходит роли «{ROLE_NAMES[role]}»"
    if not has_geometry(info):
        raise AgenError(ErrorCode.LAYOUT_MISSING, f"{what}: у его плейсхолдеров нет размеров и положения")
    x = _parts(info, width * height)
    regions = x.titles + x.subtitles + x.content + x.small
    need = {
        LayoutRole.TITLE_AND_CONTENT: (len(x.content) >= 1 and bool(x.titles), "нужны заголовок и область содержимого"),
        LayoutRole.TITLE_AND_TWO_CONTENT: (len(x.content) >= 2, "нужны две области содержимого"),
        LayoutRole.TITLE_TEXT_AND_CONTENT: (
            len(x.content) >= 1 and len(x.content) + len(x.small) >= 2,
            "нужны область текста и область содержимого",
        ),
        LayoutRole.TITLE_ONLY: (bool(x.titles), "нужен плейсхолдер заголовка"),
        LayoutRole.TITLE: (bool(regions), "нужен плейсхолдер для названия"),
        LayoutRole.SECTION: (bool(regions), "нужен плейсхолдер для названия раздела"),
        LayoutRole.FINAL: (True, ""),
        LayoutRole.BLANK: (True, ""),
    }
    ok, why = need[role]
    if not ok:
        raise AgenError(ErrorCode.LAYOUT_MISSING, f"{what}: {why}")
    binding = RoleBinding(role=role, layout_key=info.key, layout_name=info.name, slots=[], guessed=False)
    if role == LayoutRole.BLANK:
        binding.drop_placeholders = [p.idx for p in regions]
        binding.slots = [SlotInfo(name="body", geometry=_full(width, height))]
        return binding
    try:
        binding.slots = _slots(role, info, width, height)
    except (StopIteration, ValueError, IndexError):
        raise AgenError(ErrorCode.LAYOUT_MISSING, what) from None
    return binding


def guess_roles(
    layouts: list[LayoutInfo],
    width: int,
    height: int,
    overrides: dict[str, str] | None = None,
    strict: bool = False,
) -> tuple[list[RoleBinding], list[str]]:
    """Роли макетов: подтверждённые пользователем (``overrides``: роль → ключ макета),
    предложенные по составу плейсхолдеров и построенные для недостающих. Построенные роли
    следуют за подтверждёнными: финальная без своего макета берётся с подтверждённого
    титульного. ``strict`` — неподходящий или пропавший макет подтверждения — ошибка, иначе
    роль предлагается заново с замечанием (так при повторном импорте шаблона)."""
    area = width * height
    candidates: dict[LayoutRole, list[LayoutInfo]] = {}
    for info in layouts:
        role = guess_role(info, area)
        if role is not None:
            candidates.setdefault(role, []).append(info)
    roles: dict[LayoutRole, RoleBinding] = {}
    for role, infos in candidates.items():
        best = min(infos, key=lambda i: _rank(i, area))
        roles[role] = RoleBinding(
            role=role, layout_key=best.key, layout_name=best.name, slots=_slots(role, best, width, height)
        )
    by_key = {i.key: i for i in layouts}
    notes: list[str] = []
    for name, key in (overrides or {}).items():
        try:
            role = LayoutRole(name)
        except ValueError:
            known = ", ".join(r.value for r in LayoutRole)
            raise AgenError(ErrorCode.SPEC_INVALID, f"Нет роли макета «{name}» (есть: {known})") from None
        layout = by_key.get(key)
        try:
            if layout is None:
                raise AgenError(ErrorCode.LAYOUT_MISSING, f"Макета с id {key} в шаблоне нет")
            roles[role] = bind_role(role, layout, width, height)
        except AgenError as e:
            if strict:
                raise
            notes.append(f"Подтверждение роли «{ROLE_NAMES[role]}» не перенесено ({e.message}): роль предложена заново")

    def derive(role: LayoutRole, base: LayoutRole, how: str) -> None:
        src = roles.get(base)
        if role in roles or src is None:
            return
        info = by_key[src.layout_key]
        binding = RoleBinding(role=role, layout_key=info.key, layout_name=info.name, slots=list(src.slots), derived=how)
        if role == LayoutRole.BLANK:
            title = src.slot("title")
            binding.drop_placeholders = [title.placeholder_idx] if title and title.placeholder_idx is not None else []
            binding.slots = [SlotInfo(name="body", geometry=_full(width, height))]
        elif role == LayoutRole.TITLE_TEXT_AND_CONTENT:
            left, right = src.slot("left"), src.slot("right")
            binding.slots = [s for s in src.slots if s.name == "title"]
            if left is not None and right is not None:
                binding.slots += [left.model_copy(update={"name": "text"}), right.model_copy(update={"name": "body"})]
        roles[role] = binding
        notes.append(f"Роль «{role.value}» построена из макета «{info.name}»: {how}")

    derive(LayoutRole.BLANK, LayoutRole.TITLE_ONLY, "заголовок удаляется")
    derive(LayoutRole.TITLE_TEXT_AND_CONTENT, LayoutRole.TITLE_AND_TWO_CONTENT, "левая область — текст")
    derive(LayoutRole.FINAL, LayoutRole.TITLE, "финальный слайд на титульном макете")
    missing = [r.value for r in LayoutRole if r not in roles]
    if missing:
        notes.append(f"Не нашлись макеты для ролей: {', '.join(missing)}")
    return [roles[r] for r in LayoutRole if r in roles], notes
