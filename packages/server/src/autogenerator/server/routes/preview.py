"""Превью черновика: проверка без данных, строки узла, пробная сборка слайда.

Эндпоинты принимают несохранённый черновик, чтобы результат правок был виден до сохранения.
Превью узла и слайда — задания очереди ``preview`` и исполнителя ``preview``: они не ждут, пока
собирается отчёт, а устаревшее превью окно отменяет (``POST /api/jobs/{id}/cancel``).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, status
from fastapi.responses import FileResponse

from autogenerator.contracts import AgenError, Issue, IssueLevel, JobContext, JobInfo, PreviewResult
from autogenerator.home import Home

from ..deps import HomeDep, StateDep, Wait, draft, file_reply, job_reply, period
from ..models import DraftIn, NodePreviewIn, SlidePreviewIn, SlidePreviewOut

router = APIRouter(tags=["превью"])


@router.post("/api/preview/validate")
def validate_draft(body: DraftIn, home: HomeDep) -> list[Issue]:
    """Проверить черновик без данных: ссылки, типы, плагины, слайды и метки шаблона."""
    try:
        spec = draft(home, body.text, body.spec)
    except AgenError as e:
        return [Issue(level=IssueLevel.ERROR, node="scenario", message=str(e), code=str(e.code))]
    return home.validate_draft(spec)


@router.post("/api/preview/node", status_code=status.HTTP_202_ACCEPTED)
def preview_node(body: NodePreviewIn, state: StateDep, wait: Wait = None) -> JobInfo:
    """Первые строки и числа строк узла черновика (итог — ``PreviewResult``)."""

    def job(home: Home, _: JobContext) -> PreviewResult:
        spec = draft(home, body.text, body.spec)
        return home.preview_node(spec, body.target, period=period(body.period), rows=body.rows, sample=body.sample)

    return job_reply(state, state.submit("preview", job, lane="preview", title=f"Превью {body.target}"), wait)


@router.post("/api/preview/slide", status_code=status.HTTP_202_ACCEPTED)
def preview_slide(body: SlidePreviewIn, state: StateDep, wait: Wait = None) -> JobInfo:
    """Пробная сборка одного слайда и его картинка (итог — ``SlidePreviewOut``: пути файлов API)."""

    def job(home: Home, _: JobContext) -> SlidePreviewOut:
        spec = draft(home, body.text, body.spec)
        p = home.preview_slide(spec, body.slide, period=period(body.period), image=body.image)
        files = {"pptx": f"/api/preview/files/{p.id}/slide.pptx"}
        if p.result.image_path:
            files["png"] = f"/api/preview/files/{p.id}/slide.png"
        return SlidePreviewOut(id=p.id, result=p.result, files=files)

    return job_reply(state, state.submit("preview_slide", job, lane="preview", title=f"Слайд {body.slide}"), wait)


@router.get("/api/preview/files/{preview_id}/{name}", response_class=FileResponse)
def preview_file(preview_id: str, name: Literal["slide.pptx", "slide.png"], home: HomeDep) -> FileResponse:
    media = "image/png" if name.endswith(".png") else None
    return file_reply(home.preview_file(preview_id, name), media)
