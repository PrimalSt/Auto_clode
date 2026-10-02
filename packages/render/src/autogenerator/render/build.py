"""Сборка презентации в рабочей копии шаблона (ARCHITECTURE.md, раздел 6.5).

Отчёт никогда не собирается из пустого ``Presentation()``: всегда открывается копия шаблона,
поэтому сохраняются мастера, макеты, тема и шрифты. Этап M0 собирает слайды из макетов
(блоки ставятся в области макета); слайды-образцы шаблона с метками, графиками и таблицами —
на этапе M3, а пока все слайды шаблона из отчёта удаляются.

Ошибка блока не валит отчёт: на слайд ставится пометка «Ошибка: …», запись идёт в журнал,
остальные блоки и слайды собираются.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Pt
from pydantic import ValidationError

from autogenerator.contracts import (
    AgenError,
    BlockData,
    BlockTarget,
    EngineResult,
    ErrorCode,
    Issue,
    IssueLevel,
    NodeKind,
    NodeState,
    NodeStatus,
    Period,
    RenderResult,
    ScenarioSpec,
    ThemeManifest,
)
from autogenerator.contracts.yaml_io import validation_message
from autogenerator.plugin_host import PluginRegistry

P14_SECTIONS = "{http://schemas.microsoft.com/office/powerpoint/2010/main}sectionLst"
P_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
DEFAULT_SLOT = "body"
ERROR_COLOR = RGBColor(0xC0, 0x00, 0x00)


# --- проверка без данных ---------------------------------------------------------------


def validate_slides(
    scenario: ScenarioSpec, registry: PluginRegistry, theme: ThemeManifest | None = None
) -> list[Issue]:
    """Проверить слайды: макеты, блоки и их параметры, области, ссылки на наборы и показатели."""
    issues: list[Issue] = []
    datasets = {d.id for d in scenario.datasets}
    metrics = {m.id for m in scenario.metrics} | {c for m in scenario.metrics for c in m.compare_ids()}

    def err(node: str, msg: str) -> None:
        issues.append(Issue(level=IssueLevel.ERROR, node=node, message=msg))

    for n, slide in enumerate((s for s in scenario.slides if s.enabled), start=1):
        snode = f"slide:{n}"
        if slide.example is not None:
            err(snode, "слайды-образцы шаблона появятся на этапе M3; пока используйте layout")
            continue
        binding = theme.role(slide.layout) if theme and slide.layout else None
        if theme is not None and binding is None:
            known = ", ".join(r.role.value for r in theme.roles)
            err(snode, f"в шаблоне нет макета для роли «{slide.layout}» (есть: {known})")
        for j, block in enumerate(slide.blocks, start=1):
            bnode = f"{snode}/block:{block.id or j}"
            try:
                plugin = registry.block(block.type)
                params = plugin.parse_params(block.params, block.type_version)
                needs = plugin.data_needs(params)
            except AgenError as e:
                err(bnode, e.message)
                continue
            except ValidationError as e:
                err(bnode, validation_message(e, f"параметры блока «{block.type}»"))
                continue
            except Exception as e:
                err(bnode, str(e).splitlines()[0] if str(e) else type(e).__name__)
                continue
            for d in sorted(needs.datasets - datasets):
                err(bnode, f"нет набора «{d}»")
            for m in sorted(needs.metrics - metrics):
                err(bnode, f"нет показателя «{m}»")
            slot = block.slot or DEFAULT_SLOT
            if binding is not None and binding.slot(slot) is None:
                known = ", ".join(s.name for s in binding.slots)
                err(bnode, f"у макета «{binding.layout_name}» нет области «{slot}» (есть: {known})")
    return issues


# --- сборка -----------------------------------------------------------------------------


@dataclass
class _Ctx:
    period: Period
    scenario_name: str
    node: str
    issues: list[Issue] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.issues.append(Issue(level=IssueLevel.WARNING, node=self.node, message=message))


def _layouts_by_key(prs: Any) -> dict[str, Any]:
    out = {}
    for master in prs.slide_masters:
        lst = master._element.find(f"{P_NS}sldLayoutIdLst")
        for entry, layout in zip(list(lst) if lst is not None else [], master.slide_layouts, strict=False):
            out[str(entry.get("id"))] = layout
    return out


def _drop_template_slides(prs: Any) -> int:
    """Удалить слайды шаблона вместе со связями: иначе в отчёте останутся невидимые слайды
    со старыми данными. Список разделов удаляется: он ссылается на удалённые слайды."""
    lst = prs.slides._sldIdLst
    n = 0
    for sld in list(lst):
        prs.part.drop_rel(sld.rId)
        lst.remove(sld)
        n += 1
    for sections in prs.part._element.iter(P14_SECTIONS):
        ext = sections.getparent()
        ext.getparent().remove(ext)
    return n


def _error_marker(slide: Any, geometry: Any, message: str) -> None:
    box = slide.shapes.add_textbox(
        Emu(geometry.x), Emu(geometry.y), Emu(geometry.cx), Emu(min(geometry.cy, int(Pt(60))))
    )
    tf = box.text_frame
    tf.word_wrap = True
    tf.text = f"Ошибка: {message}"
    for r in tf.paragraphs[0].runs:
        r.font.color.rgb = ERROR_COLOR
        r.font.size = Pt(14)


def _remove_empty_placeholders(slide: Any) -> None:
    for ph in list(slide.placeholders):
        if getattr(ph, "has_text_frame", False) and ph.text_frame.text.strip():
            continue
        el = ph._element
        el.getparent().remove(el)


def _slide_text(slide: Any) -> list[str]:
    out = []
    for sh in slide.shapes:
        if getattr(sh, "has_text_frame", False) and sh.has_text_frame:
            out.append(sh.text_frame.text)
        if getattr(sh, "has_table", False) and sh.has_table:
            out.extend(c.text for row in sh.table.rows for c in row.cells)
    return out


def build_presentation(
    scenario: ScenarioSpec,
    theme: ThemeManifest,
    engine: EngineResult,
    registry: PluginRegistry,
    output: str | Path,
) -> RenderResult:
    """Собрать .pptx по слайдам сценария, манифесту шаблона и итогам движка."""
    result = RenderResult()
    issues = validate_slides(scenario, registry, theme)
    if any(i.level == IssueLevel.ERROR for i in issues):
        result.issues = issues
        return result

    prs = Presentation(theme.pptx_path)
    _drop_template_slides(prs)
    layouts = _layouts_by_key(prs)
    failed = {n.id: n for n in engine.nodes if n.state != NodeState.OK}
    data = BlockData(datasets=engine.datasets, metrics=engine.metrics)

    for n, spec in enumerate((s for s in scenario.slides if s.enabled), start=1):
        snode = f"slide:{n}"
        binding = theme.role(spec.layout or "")
        assert binding is not None  # проверено в validate_slides
        layout = layouts.get(binding.layout_key)
        if layout is None:
            raise AgenError(ErrorCode.LAYOUT_MISSING, f"В рабочей копии шаблона нет макета {binding.layout_key}")
        slide = prs.slides.add_slide(layout)
        phs = {ph.placeholder_format.idx: ph for ph in slide.placeholders}
        slide_errors: list[str] = []
        for j, block in enumerate(spec.blocks, start=1):
            bnode = f"{snode}/block:{block.id or j}"
            slot = binding.slot(block.slot or DEFAULT_SLOT)
            assert slot is not None
            plugin = registry.block(block.type)
            params = plugin.parse_params(block.params, block.type_version)
            needs = plugin.data_needs(params)
            ctx = _Ctx(period=engine.period, scenario_name=scenario.name, node=bnode)
            deps = [f"dataset:{d}" for d in sorted(needs.datasets)] + [f"metric:{m}" for m in sorted(needs.metrics)]
            broken = [d for d in deps if d in failed]
            target = BlockTarget(
                slide=slide,
                geometry=slot.geometry,
                placeholder=phs.get(slot.placeholder_idx) if slot.placeholder_idx is not None else None,
                slot=slot.name,
            )
            try:
                if broken:
                    root = failed[broken[0]].blocked_by or broken[0]
                    raise ValueError(f"зависит от «{root}», в котором ошибка")
                # Итоги движка могли прийти не целиком (например, render запущен отдельно
                # на сохранённых файлах): блоку без своих данных — понятная ошибка, а не KeyError.
                absent = [f"набора «{d}»" for d in sorted(needs.datasets) if d not in data.datasets]
                absent += [f"показателя «{m}»" for m in sorted(needs.metrics) if m not in data.metrics]
                if absent:
                    raise ValueError(f"в итогах расчёта нет {', '.join(absent)}")
                plugin.render(target, params, data, ctx)
            except Exception as e:
                msg = str(e).splitlines()[0] if str(e) else type(e).__name__
                slide_errors.append(f"блок {block.id or j} ({block.type}): {msg}")
                ctx.issues.append(Issue(level=IssueLevel.ERROR, node=bnode, message=msg))
                _error_marker(slide, slot.geometry, msg)
            issues.extend(ctx.issues)
        _remove_empty_placeholders(slide)
        result.nodes.append(
            NodeStatus(
                id=snode,
                kind=NodeKind.SLIDE,
                state=NodeState.ERROR if slide_errors else NodeState.OK,
                message="; ".join(slide_errors) or None,
            )
        )

    # Проверка после сборки: в тексте не должно остаться «{{». Такой отчёт не сохраняется.
    leftovers = [
        Issue(
            level=IssueLevel.ERROR,
            node=f"slide:{n}",
            message="на слайде осталась незаменённая метка «{{»",
        )
        for n, slide in enumerate(prs.slides, start=1)
        if any("{{" in t for t in _slide_text(slide))
    ]
    result.issues = issues + leftovers
    result.slides = len(prs.slides)
    if leftovers:
        return result

    props = prs.core_properties
    props.title = f"{scenario.name}: {engine.period.key}"
    props.author = "Autogenerator"
    props.last_modified_by = "Autogenerator"
    props.modified = datetime.now(UTC).replace(tzinfo=None)
    result.output_path = str(save_presentation(prs, Path(output), result.issues))
    return result


# --- сохранение ---------------------------------------------------------------------------

_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str) -> str:
    """Имя файла, допустимое в Windows: без ``<>:"/\\|?*``, завершающих точек и пробелов и
    зарезервированных имён."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .")
    if not s:
        s = "Отчёт"
    if s.split(".")[0].upper() in _RESERVED:
        s = "_" + s
    return s


def _busy(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with path.open("ab"):
            return False
    except PermissionError:
        return True


def save_presentation(prs: Any, path: Path, issues: list[Issue]) -> Path:
    """Сохранить атомарно. Если файл открыт в PowerPoint, сохранить рядом как «… (2).pptx»."""
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path
    n = 2
    while _busy(target):
        target = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
        if n > 99:
            raise AgenError(ErrorCode.OUTPUT_BUSY, f"Файл {path.name} и его копии заняты")
    if target != path:
        issues.append(
            Issue(
                level=IssueLevel.WARNING,
                message=f"{path.name} открыт в другой программе; сохранено как {target.name}",
            )
        )
    # Временный файл рядом и переименование: полузаписанного отчёта не бывает. Файл создаёт
    # сам python-pptx, поэтому права — обычные, как у любого нового файла пользователя.
    tmp = target.with_name(f".{target.stem}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        prs.save(str(tmp))
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target
