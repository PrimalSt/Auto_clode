"""Шрифты шаблона: какие используются, какие встроены в файл и установлены ли они.

Встроенные шрифты показывает PowerPoint, но библиотеки для замеров текста их прочитать не
могут: без установленных шрифтов проверки переполнения приблизительные (ARCHITECTURE.md,
раздел 6.5).
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
import sys
from typing import Any

from lxml import etree

from autogenerator.contracts import FontInfo
from autogenerator.contracts.ooxml import A, P

# Ссылки на шрифты темы (+mj-lt — шрифт заголовков, +mn-lt — основной) и служебные имена.
_THEME_REF = re.compile(r"^\+(mj|mn)-")
_STYLE_SUFFIX = re.compile(r"\s+(Bold|Italic|Bold Italic|Regular|Light|Semibold|Black)$", re.IGNORECASE)


def _theme_fonts(prs: Any) -> list[str]:
    out: list[str] = []
    for master in prs.slide_masters:
        for rel in master.part.rels.values():
            if not rel.reltype.endswith("/theme"):
                continue
            root = etree.fromstring(rel.target_part.blob)
            for kind in ("majorFont", "minorFont"):
                latin = root.find(f".//{A}fontScheme/{A}{kind}/{A}latin")
                if latin is not None and latin.get("typeface"):
                    out.append(str(latin.get("typeface")))
    return out


def _run_fonts(root: etree._Element) -> list[str]:
    out = []
    for tag in ("latin", "cs", "ea"):
        for el in root.iter(f"{A}{tag}"):
            face = el.get("typeface")
            if face and not _THEME_REF.match(face):
                out.append(face)
    return out


def _embedded(prs: Any) -> set[str]:
    lst = prs.part._element.find(f"{P}embeddedFontLst")
    if lst is None:
        return set()
    return {str(f.get("typeface")) for f in lst.iter(f"{P}font") if f.get("typeface")}


@functools.cache
def installed_fonts() -> frozenset[str] | None:
    """Семейства установленных шрифтов (в нижнем регистре); None — узнать нельзя."""
    if sys.platform == "win32":
        return _windows_fonts()
    if shutil.which("fc-list"):
        try:
            out = subprocess.run(
                ["fc-list", ":", "family"], capture_output=True, text=True, timeout=30, check=False
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        names = {n.strip().lower() for line in out.splitlines() for n in line.split(",") if n.strip()}
        return frozenset(names)
    return None


def _windows_fonts() -> frozenset[str] | None:
    """Шрифты из реестра Windows: «Arial Bold (TrueType)», «Cambria & Cambria Math (TrueType)»."""
    if sys.platform != "win32":
        return None
    import winreg

    key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    names: set[str] = set()
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, key_path)
        except OSError:
            continue
        i = 0
        while True:
            try:
                value = winreg.EnumValue(key, i)[0]
            except OSError:
                break
            i += 1
            base = re.sub(r"\s*\(.*\)$", "", value)
            for part in base.split(" & "):
                names.add(part.strip().lower())
                names.add(_STYLE_SUFFIX.sub("", part.strip()).lower())
    return frozenset(names) if names else None


def template_fonts(prs: Any) -> list[FontInfo]:
    """Шрифты темы, текста слайдов, макетов и мастеров; встроенные; установлены ли."""
    seen: dict[str, None] = {}
    for name in _theme_fonts(prs):
        seen.setdefault(name, None)
    roots = [prs.part._element]
    for master in prs.slide_masters:
        roots.append(master._element)
        roots += [layout._element for layout in master.slide_layouts]
    roots += [slide._element for slide in prs.slides]
    for root in roots:
        for name in _run_fonts(root):
            seen.setdefault(name, None)
    embedded = _embedded(prs)
    for name in embedded:
        seen.setdefault(name, None)
    have = installed_fonts()
    return [
        FontInfo(
            name=name,
            embedded=name in embedded,
            installed=None if have is None else name.lower() in have,
        )
        for name in seen
    ]
