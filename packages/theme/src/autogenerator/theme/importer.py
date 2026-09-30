"""Импорт .pptx-шаблона: рабочая копия, макеты всех мастеров, роли макетов, слайды шаблона
(ARCHITECTURE.md, раздел 6.5).

Этап M0: роли макетов предлагаются по составу плейсхолдеров, без подтверждения пользователем;
слайды шаблона только перечисляются (метки, графики, таблицы). Проверка шаблона (lint),
элементы оформления и заполнение слайдов-образцов — на этапе M3.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    Geometry,
    LayoutInfo,
    LayoutRole,
    PlaceholderInfo,
    RoleBinding,
    SlotInfo,
    TemplateSlideInfo,
    ThemeManifest,
)
from autogenerator.contracts.theme import EMU_PER_INCH

CT_PRESENTATION = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
CT_TEMPLATE = "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml"
CT_SLIDESHOW = "application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml"
MARKER_RE = re.compile(r"\{\{([^{}\n]{1,64})\}\}")

# Плейсхолдеры, которые не считаются областями для блоков.
SERVICE_TYPES = {"dt", "ftr", "sldNum", "hdr"}
CONTENT_TYPES = {"obj", "body", "chart", "tbl", "pic", "media", "clipArt", "dgm"}
# Область меньше этой доли слайда — подпись, а не место для графика или текста. Текстовый
# плейсхолдер (body) считается областью содержимого, только если он заметно больше.
MIN_CONTENT_SHARE = 0.12
MIN_BODY_SHARE = 0.25
MARGIN = EMU_PER_INCH // 2


def _main_content_type(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            ct = z.read("[Content_Types].xml").decode("utf-8")
            names = set(z.namelist())
    except (zipfile.BadZipFile, KeyError) as e:
        raise AgenError(ErrorCode.THEME_INVALID, f"{path.name} — не файл PowerPoint") from e
    m = re.search(r'PartName="/ppt/presentation\.xml"\s+ContentType="([^"]+)"', ct) or re.search(
        r'ContentType="([^"]+)"\s+PartName="/ppt/presentation\.xml"', ct
    )
    if not m:
        raise AgenError(ErrorCode.THEME_INVALID, f"В {path.name} нет основной части презентации")
    if "macroEnabled" in m.group(1) or "ppt/vbaProject.bin" in names:
        raise AgenError(
            ErrorCode.THEME_INVALID,
            f"{path.name} содержит макросы; такие шаблоны не принимаются",
            hint="Сохраните шаблон в PowerPoint как «Презентация PowerPoint (.pptx)» без макросов.",
        )
    return m.group(1)


def make_working_copy(src: Path, dst: Path) -> list[str]:
    """Скопировать шаблон так, чтобы его открыл python-pptx (у .potx меняется тип основной части)."""
    notes: list[str] = []
    ct = _main_content_type(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if ct == CT_PRESENTATION:
        shutil.copyfile(src, dst)
        return notes
    if ct not in (CT_TEMPLATE, CT_SLIDESHOW):
        raise AgenError(ErrorCode.THEME_INVALID, f"Неизвестный тип файла {src.name}: {ct}")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(ct.encode(), CT_PRESENTATION.encode())
            zout.writestr(item, data)
    notes.append("Шаблон .potx/.ppsx открыт как презентация: в рабочей копии заменён тип основной части")
    return notes


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
    out = []
    for ph in layout.placeholders:
        out.append(
            PlaceholderInfo(
                idx=ph.placeholder_format.idx,
                type=_ph_type(ph),
                name=ph.name,
                geometry=_geometry(ph),
            )
        )
    return out


def _layouts(prs: Any) -> list[tuple[LayoutInfo, Any]]:
    """Макеты всех мастеров: ``prs.slide_layouts`` в python-pptx видит только первый мастер."""
    out = []
    for mi, master in enumerate(prs.slide_masters):
        lst = master._element.find("{http://schemas.openxmlformats.org/presentationml/2006/main}sldLayoutIdLst")
        entries = list(lst) if lst is not None else []
        for entry, layout in zip(entries, master.slide_layouts, strict=False):
            info = LayoutInfo(
                key=str(entry.get("id")),
                name=layout.name,
                master=mi,
                preserve=layout._element.get("preserve") == "1",
                placeholders=_placeholders(layout),
            )
            out.append((info, layout))
    return out


def _area(g: Geometry | None) -> int:
    return g.cx * g.cy if g else 0


def _is_content(p: PlaceholderInfo, slide_area: int) -> bool:
    share = MIN_BODY_SHARE if p.type == "body" else MIN_CONTENT_SHARE
    return p.type in CONTENT_TYPES and _area(p.geometry) >= share * slide_area


def _guess_role(info: LayoutInfo, slide_area: int) -> LayoutRole | None:
    phs = [p for p in info.placeholders if p.type not in SERVICE_TYPES]
    if any(p.geometry is None for p in phs):
        return None  # макет без геометрии не предлагается для новых слайдов
    titles = [p for p in phs if p.type in ("title", "ctrTitle")]
    subtitles = [p for p in phs if p.type == "subTitle"]
    content = [p for p in phs if _is_content(p, slide_area)]
    small_body = [p for p in phs if p.type == "body" and p not in content]
    if not phs:
        return LayoutRole.BLANK
    if any(p.type == "ctrTitle" for p in titles) or (titles and subtitles and not content):
        return LayoutRole.TITLE
    if len(titles) != 1:
        return None
    if len(content) == 1 and not small_body:
        return LayoutRole.TITLE_AND_CONTENT
    if len(content) == 2 and not small_body:
        return LayoutRole.TITLE_AND_TWO_CONTENT
    if not content and len(small_body) == 1:
        return LayoutRole.SECTION
    if not content and not small_body and not subtitles:
        return LayoutRole.TITLE_ONLY
    return None


def _slots(role: LayoutRole, info: LayoutInfo, width: int, height: int) -> list[SlotInfo]:
    phs = [p for p in info.placeholders if p.type not in SERVICE_TYPES and p.geometry is not None]
    title = next((p for p in phs if p.type in ("title", "ctrTitle")), None)
    slots: list[SlotInfo] = []
    if title is not None and title.geometry is not None:
        slots.append(SlotInfo(name="title", placeholder_idx=title.idx, geometry=title.geometry))
    for p in phs:
        if p.type == "subTitle" and p.geometry is not None:
            slots.append(SlotInfo(name="subtitle", placeholder_idx=p.idx, geometry=p.geometry))
    content = [p for p in phs if p is not title and _is_content(p, width * height)]
    if role == LayoutRole.SECTION:
        content = [p for p in phs if p.type == "body"]
    if role in (LayoutRole.TITLE_AND_CONTENT, LayoutRole.SECTION) and content:
        big = max(content, key=lambda p: _area(p.geometry))
        assert big.geometry is not None
        slots.append(SlotInfo(name="body", placeholder_idx=big.idx, geometry=big.geometry))
    elif role == LayoutRole.TITLE_AND_TWO_CONTENT:
        pair = sorted(
            sorted(content, key=lambda p: -_area(p.geometry))[:2],
            key=lambda p: p.geometry.x if p.geometry else 0,
        )
        for name, p in zip(("left", "right"), pair, strict=False):
            assert p.geometry is not None
            slots.append(SlotInfo(name=name, placeholder_idx=p.idx, geometry=p.geometry))
    elif role == LayoutRole.TITLE_ONLY and title is not None and title.geometry is not None:
        # Область под заголовком — для блоков на макете без плейсхолдеров содержимого.
        g = title.geometry
        top = g.y + g.cy + MARGIN // 2
        slots.append(
            SlotInfo(
                name="body",
                geometry=Geometry(x=g.x, y=top, cx=g.cx, cy=max(height - top - MARGIN, MARGIN)),
            )
        )
    elif role == LayoutRole.BLANK:
        slots.append(
            SlotInfo(
                name="body",
                geometry=Geometry(x=MARGIN, y=MARGIN, cx=width - 2 * MARGIN, cy=height - 2 * MARGIN),
            )
        )
    return slots


def _texts(tf: Any) -> list[str]:
    return [p.text for p in tf.paragraphs]


def _walk(shapes: Any, markers: list[str], counts: dict[str, int]) -> None:
    """Обойти фигуры слайда, заходя в группы: метки {{…}}, графики и таблицы."""
    for sh in shapes:
        if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
            _walk(sh.shapes, markers, counts)
            continue
        texts: list[str] = []
        if getattr(sh, "has_chart", False) and sh.has_chart:
            counts["charts"] += 1
        if getattr(sh, "has_table", False) and sh.has_table:
            counts["tables"] += 1
            texts += [t for row in sh.table.rows for cell in row.cells for t in _texts(cell.text_frame)]
        if getattr(sh, "has_text_frame", False) and sh.has_text_frame:
            texts += _texts(sh.text_frame)
        for t in texts:
            markers.extend(m.strip() for m in MARKER_RE.findall(t))


def _slides(prs: Any, layout_keys: dict[str, str]) -> list[TemplateSlideInfo]:
    out = []
    ids = [int(s.get("id")) for s in prs.slides._sldIdLst]
    for number, (sid, slide) in enumerate(zip(ids, prs.slides, strict=True), start=1):
        markers: list[str] = []
        counts = {"charts": 0, "tables": 0}
        _walk(slide.shapes, markers, counts)
        title = slide.shapes.title.text_frame.text if slide.shapes.title is not None else None
        out.append(
            TemplateSlideInfo(
                slide_id=sid,
                number=number,
                layout_key=layout_keys.get(slide.slide_layout.part.partname, ""),
                title=title,
                markers=list(dict.fromkeys(markers)),
                charts=counts["charts"],
                tables=counts["tables"],
            )
        )
    return out


def import_template(path: str | Path, out_dir: str | Path) -> ThemeManifest:
    """Импортировать шаблон: рабочая копия в ``out_dir/template.pptx`` и манифест."""
    src = Path(path)
    if not src.exists():
        raise AgenError(ErrorCode.FILE_NOT_FOUND, f"Шаблон не найден: {src}")
    work = Path(out_dir) / "template.pptx"
    notes = make_working_copy(src, work)
    try:
        prs = Presentation(str(work))
    except Exception as e:
        raise AgenError(ErrorCode.THEME_INVALID, f"python-pptx не открыл шаблон {src.name}: {e}") from e
    width, height = int(prs.slide_width or Emu(0)), int(prs.slide_height or Emu(0))
    layouts = _layouts(prs)
    roles: dict[LayoutRole, RoleBinding] = {}
    for info, _ in layouts:
        role = _guess_role(info, width * height)
        if role is None or role in roles:
            continue
        roles[role] = RoleBinding(
            role=role,
            layout_key=info.key,
            layout_name=info.name,
            slots=_slots(role, info, width, height),
        )
    missing = [r.value for r in LayoutRole if r not in roles]
    if missing:
        notes.append(f"Не нашлись макеты для ролей: {', '.join(missing)}")
    layout_keys = {layout.part.partname: info.key for info, layout in layouts}
    return ThemeManifest(
        source_path=str(src),
        pptx_path=str(work),
        sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
        slide_width=width,
        slide_height=height,
        layouts=[info for info, _ in layouts],
        roles=[roles[r] for r in LayoutRole if r in roles],
        slides=_slides(prs, layout_keys),
        notes=notes,
    )
