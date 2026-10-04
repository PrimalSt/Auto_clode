"""Черновик сценария до сохранения: проверка, превью узла и пробная сборка слайда на истории
из папки данных (F-505, F-506). Так окно показывает результат правки сразу, а сохранение
(новая версия) — отдельным действием."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    HistoryManifest,
    Issue,
    IssueLevel,
    Period,
    PreviewRequest,
    PreviewResult,
    RunRequest,
    RunResult,
    ScenarioSpec,
    SourceSpec,
    ThemeVersionRecord,
)

from .base import HomeBase

PREVIEWS_KEPT = 20
"""Сколько последних пробных сборок слайдов хранить в ``tmp/preview``."""


@dataclass
class SlidePreview:
    """Пробная сборка слайда: итог запуска и папка с ``slide.pptx`` и ``slide.png``."""

    id: str
    folder: Path
    result: RunResult


class DraftsMixin(HomeBase):
    def _draft_inputs(self, spec: ScenarioSpec) -> tuple[list[SourceSpec], dict[str, HistoryManifest]]:
        """Источники и истории входов черновика из папки данных. Входы, чьих источников нет,
        остаются без истории: о них скажет проверка сценария."""
        sources: dict[str, SourceSpec] = {}
        histories: dict[str, HistoryManifest] = {}
        for inp in spec.inputs:
            if not self.store.has_source(inp.source):
                continue
            sources.setdefault(inp.source, self.store.get_source(inp.source).spec)
            histories[inp.id] = self.store.history_manifest(inp.source)
        return list(sources.values()), histories

    def _draft_theme(self, spec: ScenarioSpec) -> ThemeVersionRecord:
        if spec.theme is None:
            raise AgenError(ErrorCode.SPEC_INVALID, "У сценария не выбран шаблон оформления")
        if not self.store.has_theme(spec.theme):
            raise AgenError(
                ErrorCode.NOT_FOUND,
                f"Шаблона оформления «{spec.theme}» нет в папке данных",
                hint="Загрузите шаблон в разделе «Оформление» и выберите его в сценарии.",
            )
        return self.store.get_theme(spec.theme).current

    def validate_draft(self, spec: ScenarioSpec) -> list[Issue]:
        """Проверить черновик без данных: источники из папки данных, текущая версия шаблона."""
        try:
            tv = self._draft_theme(spec)
        except AgenError as e:
            return [Issue(level=IssueLevel.ERROR, node="scenario", message=str(e), code=str(e.code))]
        sources, _ = self._draft_inputs(spec)
        return self.worker.validate(
            RunRequest(scenario=spec, sources=sources, inputs={}, theme=tv.pptx_uri, theme_roles=tv.roles)
        )

    def preview_node(
        self,
        spec: ScenarioSpec,
        target: str,
        *,
        period: str | Period | None = None,
        rows: int = 20,
        sample: int | None = None,
    ) -> PreviewResult:
        """Первые строки и числа строк узла черновика: входа после шага (``sales/dedupe``),
        набора (``dataset:…``) или показателя (``metric:…``). На больших данных — по выборке."""
        sources, histories = self._draft_inputs(spec)
        return self.worker.preview(
            PreviewRequest(
                scenario=spec,
                sources=sources,
                histories=histories,
                target=target,
                period=Period.parse(period) if isinstance(period, str) else period,
                rows=rows,
                sample=sample,
                cache_dir=str(self.folder.root / "cache" / "engine"),
                temp_dir=str(self.folder.tmp / "engine"),
            )
        )

    def preview_slide(
        self, spec: ScenarioSpec, number: int, *, period: str | Period | None = None, image: bool = True
    ) -> SlidePreview:
        """Пробная сборка одного слайда черновика (номер среди включённых, с единицы) и его
        картинка (PowerPoint или LibreOffice, если есть). Непривязанные и пустые метки не
        останавливают сборку, а подсвечиваются."""
        tv = self._draft_theme(spec)
        sources, histories = self._draft_inputs(spec)
        root = self.folder.tmp / "preview"
        pid = uuid.uuid4().hex[:12]
        folder = root / pid
        folder.mkdir(parents=True)
        self._prune_previews(root)
        result = self.worker.run(
            RunRequest(
                scenario=spec,
                sources=sources,
                histories=histories,
                theme=tv.pptx_uri,
                theme_roles=tv.roles,
                period=Period.parse(period) if isinstance(period, str) else period,
                output=str(folder / "slide.pptx"),
                cache_dir=str(self.folder.root / "cache" / "engine"),
                temp_dir=str(self.folder.tmp / "engine"),
                slide=number,
                preview=True,
                image=str(folder / "slide.png") if image else None,
            )
        )
        return SlidePreview(pid, folder, result)

    def preview_file(self, preview_id: str, name: str) -> Path:
        """Файл пробной сборки: ``slide.pptx`` или ``slide.png``."""
        if name not in ("slide.pptx", "slide.png") or not preview_id.isalnum():
            raise AgenError(ErrorCode.NOT_FOUND, f"Нет файла превью {preview_id}/{name}")
        path = self.folder.tmp / "preview" / preview_id / name
        if not path.is_file():
            raise AgenError(ErrorCode.NOT_FOUND, f"Нет файла превью {preview_id}/{name}")
        return path

    @staticmethod
    def _prune_previews(root: Path) -> None:
        dirs = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)
        for d in dirs[PREVIEWS_KEPT:]:
            shutil.rmtree(d, ignore_errors=True)
