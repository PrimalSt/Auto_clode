"""Задания оформления: импорт и проверка шаблона (``import_theme``), заготовка слайдов
сценария и картинка слайда (``slide_image``, ARCHITECTURE.md, разделы 6.5 и 6.6)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from autogenerator.contracts import AgenError, ErrorCode, ThemeManifest

IMAGE_TIMEOUT = 120  # с


def import_theme(path: str | Path, out_dir: str | Path | None = None) -> ThemeManifest:
    """Импорт и проверка шаблона. Без ``out_dir`` рабочая копия — во временной папке и после
    импорта удаляется: остаётся только манифест (для отчёта и заготовки)."""
    from autogenerator.theme import import_template

    if out_dir is not None:
        return import_template(path, out_dir)
    with tempfile.TemporaryDirectory(prefix="agen-theme-") as tmp:
        return import_template(path, tmp)


def describe_theme(m: ThemeManifest, *, layouts: bool = False, verbose: bool = False, name: str | None = None) -> str:
    from autogenerator.theme import describe

    return describe(m, layouts=layouts, verbose=verbose, name=name)


def scaffold_theme(m: ThemeManifest, name: str | None = None) -> str:
    from autogenerator.theme import scaffold

    return scaffold(m, name)


# --- картинка слайда --------------------------------------------------------------------

_POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
$running = @(Get-Process POWERPNT -ErrorAction SilentlyContinue).Count -gt 0
$app = New-Object -ComObject PowerPoint.Application
try {
    # Только чтение, без окна: если PowerPoint уже открыт, картинка рисуется в нём.
    $pres = $app.Presentations.Open($env:AGEN_PPTX, -1, 0, 0)
    try {
        $w = [int]($pres.PageSetup.SlideWidth * 2); $h = [int]($pres.PageSetup.SlideHeight * 2)
        $pres.Slides.Item(1).Export($env:AGEN_PNG, 'PNG', $w, $h)
    } finally { $pres.Close() }
} finally {
    if (-not $running -and $app.Presentations.Count -eq 0) { $app.Quit() }
}
"""


def _powerpoint(pptx: Path, png: Path) -> bool:
    if sys.platform != "win32" or shutil.which("powershell") is None:
        return False
    env = {**os.environ, "AGEN_PPTX": str(pptx.resolve()), "AGEN_PNG": str(png.resolve())}
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL],
            env=env,
            capture_output=True,
            timeout=IMAGE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0 and png.exists()


def _soffice() -> str | None:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
        if base and (p := Path(base) / "LibreOffice" / "program" / "soffice.exe").exists():
            return str(p)
    return None


def _libreoffice(pptx: Path, png: Path) -> bool:
    exe = _soffice()
    if exe is None:
        return False
    with tempfile.TemporaryDirectory(prefix="agen-lo-") as tmp:
        # Отдельный профиль: при открытом у пользователя LibreOffice преобразование иначе
        # не выполняется. В PNG LibreOffice выводит первый слайд — превью собирает один слайд.
        profile = Path(tmp, "profile").resolve().as_uri()
        try:
            subprocess.run(
                [
                    exe,
                    f"-env:UserInstallation={profile}",
                    "--headless",
                    "--convert-to",
                    "png",
                    "--outdir",
                    tmp,
                    str(pptx),
                ],
                capture_output=True,
                timeout=IMAGE_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        out = Path(tmp, pptx.stem + ".png")
        if not out.exists():
            return False
        png.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(out), png)
    return True


def slide_image(pptx: str | Path, png: str | Path) -> str | None:
    """Картинка первого слайда ``pptx`` в ``png``. Рисует PowerPoint (Windows, с настоящими
    шрифтами шаблона), иначе LibreOffice. Возвращает пометку для картинки LibreOffice
    («приблизительно»: встроенные шрифты он не использует) или ``None`` для PowerPoint."""
    src, dst = Path(pptx), Path(png)
    dst.unlink(missing_ok=True)
    if _powerpoint(src, dst):
        return None
    if _libreoffice(src, dst):
        return "приблизительно: картинку нарисовал LibreOffice, шрифты шаблона могут отличаться"
    raise AgenError(
        ErrorCode.RENDER_FAILED,
        "Картинку слайда нарисовать нечем: нет ни PowerPoint, ни LibreOffice",
        hint="Откройте собранный .pptx сам или установите LibreOffice.",
    )
