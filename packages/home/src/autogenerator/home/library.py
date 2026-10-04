"""Сценарии и шаблоны оформления в папке данных (F-501…F-504, F-410).

Каждое сохранение сценария — новая неизменяемая версия; версия помнит текст YAML (с
комментариями) и версию шаблона, на которой сохранена. Шаблон хранится файлом в папке
данных (``local/themes/{id}/{номер}.pptx``); каждый импорт нового файла и каждое
подтверждение ролей макетов — новая версия шаблона. Сценарии на этом шаблоне при этом
получают новую версию с ним, а то, что на новом шаблоне не сходится (пропавшие слайды-образцы,
метки, макеты), показывается списком.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    Issue,
    IssueLevel,
    RunRequest,
    ScenarioRecord,
    ScenarioSpec,
    ScenarioVersionRecord,
    SourceSpec,
    ThemeManifest,
    ThemeRecord,
    ThemeVersionRecord,
)
from autogenerator.contracts.yaml_io import dump_yaml, loads_yaml

from .base import HomeBase, file_sha256

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
THEME_SUFFIXES = (".pptx", ".potx")


@dataclass
class SavedScenario:
    """Сохранённый сценарий и итог его проверки: сохраняется и сценарий с ошибками (черновик),
    но запустить его не получится, пока ошибки не исправлены."""

    record: ScenarioRecord
    issues: list[Issue] = field(default_factory=list)
    theme: ThemeImport | None = None
    """Шаблон, который пришлось загрузить в папку данных (поле theme было путём к файлу)."""

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == IssueLevel.ERROR]


@dataclass
class ThemeImport:
    """Итог импорта шаблона или подтверждения ролей."""

    record: ThemeRecord
    skipped: bool = False
    """Новой версии нет: тот же файл уже загружен (``matched``) или роли не изменились."""
    matched: int | None = None
    """Версия шаблона с тем же файлом, если файл уже был загружен."""
    scenarios: list[str] = field(default_factory=list)
    """Сценарии, которые получили новую версию с этой версией шаблона."""
    lost: dict[str, list[str]] = field(default_factory=dict)
    """Сценарий → что на новой версии шаблона не сходится (ошибки проверки, которых не было на
    прежней). Такой сценарий остаётся на прежней версии шаблона, пока его не исправят."""

    @property
    def manifest(self) -> ThemeManifest:
        return self.record.current.manifest


def check_id(value: str, what: str) -> str:
    if not ID_RE.match(value):
        raise AgenError(
            ErrorCode.SPEC_INVALID,
            f"id {what} «{value}» не подходит: нужны строчные латинские буквы, цифры, «_» или «-» (до 64 знаков)",
        )
    return value


def _set_line(text: str, key: str, value: str, after: str | None = None) -> str:
    """Заменить верхнеуровневую строку ``key: …`` в тексте YAML (или вставить её после
    ``after:``), не трогая комментарии и остальной текст."""
    pat = re.compile(rf"^{key}:.*$", re.MULTILINE)
    line = f"{key}: {value}"
    if pat.search(text):
        return pat.sub(lambda _: line, text, count=1)
    if after is not None:
        m = re.search(rf"^{after}:.*$", text, re.MULTILINE)
        if m:
            return text[: m.end()] + "\n" + line + text[m.end() :]
    return line + "\n" + text


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class LibraryMixin(HomeBase):
    def _make_id(self, name: str, fallback: str) -> str:
        ident = self.worker.suggest_id(name) if name else ""
        ident = ident.removeprefix("c_")
        return ident if ident and ident != "column" and ID_RE.match(ident) else fallback

    # --- сценарии ----------------------------------------------------------------

    def scenarios(self) -> list[ScenarioRecord]:
        return self.store.list_scenarios()

    def scenario(self, scenario_id: str) -> ScenarioRecord:
        return self.store.get_scenario(scenario_id)

    def has_scenario(self, scenario_id: str) -> bool:
        return self.store.has_scenario(scenario_id)

    def scenario_versions(self, scenario_id: str) -> list[ScenarioVersionRecord]:
        return self.store.scenario_versions(scenario_id)

    def scenario_version(self, scenario_id: str, version: int | None = None) -> ScenarioVersionRecord:
        if version is None:
            return self.store.get_scenario(scenario_id).current
        return self.store.get_scenario_version(scenario_id, version)

    def save_scenario(
        self,
        scenario: str | Path | ScenarioSpec,
        scenario_id: str | None = None,
        *,
        theme: str | Path | None = None,
        comment: str = "",
        theme_files: bool = True,
    ) -> SavedScenario:
        """Сохранить сценарий из YAML (или готовой спецификации) в папку данных: новый — с
        версией 1, существующий — новой версией (F-503). id по умолчанию — по названию
        сценария. Оформление — ``theme`` или поле ``theme`` сценария: id шаблона из папки
        данных или путь к .pptx (относительно файла сценария); файл загружается в папку
        данных, и в сценарии остаётся id шаблона. ``theme_files=False`` — только id шаблона
        (так сохраняет окно: шаблон загружается в разделе «Оформление»)."""
        if isinstance(scenario, ScenarioSpec):
            self._need_write()
            return self._save(scenario, None, scenario_id, Path.cwd(), theme, comment, theme_files)
        p = Path(scenario)
        if not p.is_file():
            raise AgenError(ErrorCode.FILE_NOT_FOUND, f"Файл не найден: {p}")
        return self.save_scenario_text(
            p.read_text(encoding="utf-8"),
            scenario_id,
            theme=theme,
            comment=comment,
            base=p.resolve().parent,
            where=p.name,
            theme_files=theme_files,
        )

    def save_scenario_text(
        self,
        text: str,
        scenario_id: str | None = None,
        *,
        theme: str | Path | None = None,
        comment: str = "",
        base: str | Path | None = None,
        where: str = "сценарий",
        theme_files: bool = True,
    ) -> SavedScenario:
        """То же из текста YAML (редактор кода в окне): текст сохраняется как есть, с
        комментариями. ``base`` — папка, от которой считается путь к шаблону-файлу."""
        self._need_write()
        spec = self.worker.load_scenario(loads_yaml(text, where), where)
        return self._save(spec, text, scenario_id, Path(base) if base else Path.cwd(), theme, comment, theme_files)

    def _save(
        self,
        spec: ScenarioSpec,
        text: str | None,
        scenario_id: str | None,
        base: Path,
        theme: str | Path | None,
        comment: str,
        theme_files: bool,
    ) -> SavedScenario:
        sid = check_id(scenario_id, "сценария") if scenario_id else self._make_id(spec.name, "scenario")
        ref = str(theme) if theme is not None else spec.theme
        imported: ThemeImport | None = None
        pinned: tuple[str, int] | None = None
        if ref is not None:
            imported, pinned = self._scenario_theme(ref, base, sid, theme_files)
            if spec.theme != pinned[0]:
                spec = spec.model_copy(update={"theme": pinned[0]})
                if text is not None:
                    text = _set_line(text, "theme", pinned[0], after="name")
        rec = self.store.save_scenario(sid, spec, text, theme=pinned, comment=comment)
        return SavedScenario(rec, self.validate_scenario(sid), imported if imported and not imported.skipped else None)

    def _scenario_theme(
        self, ref: str, base: Path, scenario_id: str, files: bool = True
    ) -> tuple[ThemeImport | None, tuple[str, int]]:
        if self.store.has_theme(ref):
            t = self.store.get_theme(ref)
            return None, (t.id, t.version)
        if not files:
            raise AgenError(
                ErrorCode.NOT_FOUND,
                f"Шаблона оформления «{ref}» нет в папке данных",
                hint="Загрузите шаблон в разделе «Оформление» и выберите его в сценарии.",
            )
        p = Path(ref)
        p = p if p.is_absolute() else base / p
        if p.is_file():
            imp = self._import_for_scenario(p, scenario_id)
            return imp, (imp.record.id, imp.matched or imp.record.version)
        raise AgenError(
            ErrorCode.FILE_NOT_FOUND,
            f"Шаблон оформления «{ref}» не найден: в папке данных такого шаблона нет, файла {p} тоже",
            hint="Загрузите шаблон (agen theme import файл.pptx) и укажите его id, или путь к файлу "
            "относительно сценария.",
        )

    def validate_scenario(self, scenario_id: str, version: int | None = None) -> list[Issue]:
        """Проверить сохранённый сценарий без данных: источники из папки данных, плагины,
        ссылки, слайды и метки на его версии шаблона."""
        v = self.scenario_version(scenario_id, version)
        if v.theme_id is None or v.theme_version is None:
            return [
                Issue(
                    level=IssueLevel.ERROR,
                    node=f"scenario:{scenario_id}",
                    message="У сценария нет шаблона оформления: укажите theme (id шаблона или путь к .pptx)",
                )
            ]
        return self._check(v.spec, self.store.get_theme_version(v.theme_id, v.theme_version), scenario_id)

    def _check(self, spec: ScenarioSpec, tv: ThemeVersionRecord, scenario_id: str) -> list[Issue]:
        """Проверка сценария на заданной версии шаблона."""
        srcs = self._scenario_sources(spec)
        try:
            return self.worker.validate(
                RunRequest(scenario=spec, sources=srcs, inputs={}, theme=tv.pptx_uri, theme_roles=tv.roles)
            )
        except AgenError as e:
            return [Issue(level=IssueLevel.ERROR, node=f"scenario:{scenario_id}", message=str(e), code=str(e.code))]

    def _scenario_sources(self, spec: ScenarioSpec) -> list[SourceSpec]:
        ids = dict.fromkeys(i.source for i in spec.inputs)
        return [self.store.get_source(s).spec for s in ids if self.store.has_source(s)]

    def scenario_text(self, scenario_id: str, version: int | None = None) -> str:
        """Сценарий текстом YAML: как его сохранили (с комментариями) или по спецификации."""
        v = self.scenario_version(scenario_id, version)
        return v.text if v.text is not None else dump_yaml(v.spec.model_dump(mode="json", exclude_defaults=True))

    def copy_scenario(self, scenario_id: str, new_id: str, name: str | None = None) -> ScenarioRecord:
        """Копия сценария (F-502) под новым id, по умолчанию с названием «… (копия)»."""
        self._need_write()
        check_id(new_id, "сценария")
        if self.store.has_scenario(new_id):
            raise AgenError(ErrorCode.ALREADY_EXISTS, f"Сценарий «{new_id}» уже есть")
        v = self.store.get_scenario(scenario_id).current
        title = name or f"{v.spec.name} (копия)"
        spec = v.spec.model_copy(update={"name": title})
        text = _set_line(v.text, "name", json.dumps(title, ensure_ascii=False)) if v.text is not None else None
        theme = (v.theme_id, v.theme_version) if v.theme_id and v.theme_version else None
        return self.store.save_scenario(new_id, spec, text, theme=theme, comment=f"копия {scenario_id} v{v.number}")

    def delete_scenario(self, scenario_id: str) -> None:
        """Удалить сценарий со всеми версиями и запусками (и их отчётами в папке данных)."""
        self._need_write()
        runs = self.store.list_runs(scenario_id)
        self.store.delete_scenario(scenario_id)
        for r in runs:
            self.blobs.delete(self.folder.output_dir(r.id).as_posix())

    # --- шаблоны оформления --------------------------------------------------------

    def themes(self) -> list[ThemeRecord]:
        return self.store.list_themes()

    def theme(self, theme_id: str) -> ThemeRecord:
        return self.store.get_theme(theme_id)

    def has_theme(self, theme_id: str) -> bool:
        return self.store.has_theme(theme_id)

    def theme_versions(self, theme_id: str) -> list[ThemeVersionRecord]:
        return self.store.theme_versions(theme_id)

    def theme_version(self, theme_id: str, version: int | None = None) -> ThemeVersionRecord:
        if version is None:
            return self.store.get_theme(theme_id).current
        return self.store.get_theme_version(theme_id, version)

    def import_theme(
        self, path: str | Path, theme_id: str | None = None, *, name: str | None = None, comment: str = ""
    ) -> ThemeImport:
        """Загрузить шаблон .pptx или .potx в папку данных: проверка, роли макетов, слайды-образцы
        (F-410). Без ``theme_id`` файл, который уже загружен (в любой шаблон, любой версией),
        второй раз не загружается, а id нового шаблона берётся по имени файла; если шаблон с таким
        id уже есть, нужно явно указать ``theme_id``: его новая версия или другой шаблон.
        С ``theme_id`` существующего шаблона — его новая версия (если файл не тот же, что у текущей):
        подтверждённые роли макетов переносятся (если макет остался), сценарии на этом шаблоне
        получают новую версию с ним."""
        self._need_write()
        p = self._theme_file(path)
        sha = file_sha256(p)
        if theme_id is None:
            same = self._same_file(sha)
            if same is not None:
                return same
            tid = self._make_id(p.stem, "theme")
            if self.store.has_theme(tid):
                raise AgenError(
                    ErrorCode.ALREADY_EXISTS,
                    f"Шаблон «{tid}» уже есть, а файл {p.name} другой",
                    hint=f"Новая версия этого шаблона: agen theme import {p.name} --id {tid}; "
                    "отдельный шаблон: --id другой_id.",
                )
        else:
            tid = check_id(theme_id, "шаблона")
        return self._add_theme_version(p, tid, sha, name=name, comment=comment)

    def _theme_file(self, path: str | Path) -> Path:
        p = Path(path)
        if not p.is_file():
            raise AgenError(ErrorCode.FILE_NOT_FOUND, f"Файл не найден: {p}")
        if p.suffix.lower() not in THEME_SUFFIXES:
            raise AgenError(ErrorCode.SPEC_INVALID, f"Шаблон оформления — файл .pptx или .potx, а не {p.name}")
        return p

    def _same_file(self, sha: str) -> ThemeImport | None:
        """Шаблон, в котором этот файл уже загружен: сначала тот, где это текущая версия."""
        found = self.store.find_theme_versions(sha)
        if not found:
            return None
        themes = {v.theme_id: self.store.get_theme(v.theme_id) for v in found}
        current = [v for v in found if themes[v.theme_id].version == v.number]
        v = (current or found)[-1]
        return ThemeImport(themes[v.theme_id], skipped=True, matched=v.number)

    def _import_for_scenario(self, path: Path, scenario_id: str) -> ThemeImport:
        """Шаблон-файл из поля theme сценария. Уже загруженный файл берётся как есть. Если сценарий
        уже стоит на шаблоне из файла с тем же именем, файл поменяли — это новая версия его
        шаблона. Иначе это новый шаблон с id по имени файла; чужой шаблон с таким id не трогается,
        id становится «id-2»."""
        p = self._theme_file(path)
        sha = file_sha256(p)
        same = self._same_file(sha)
        if same is not None:
            return same
        own = self.store.get_scenario(scenario_id).current.theme_id if self.store.has_scenario(scenario_id) else None
        if own is not None and self.store.has_theme(own) and self.store.get_theme(own).current.original_name == p.name:
            tid = own
        else:
            tid = self._make_id(p.stem, "theme")
            if self.store.has_theme(tid):
                tid = self._free_theme_id(tid)
        return self._add_theme_version(p, tid, sha, name=None, comment="")

    def _free_theme_id(self, base: str) -> str:
        n = 2
        while self.store.has_theme(f"{base[:60]}-{n}"):
            n += 1
        return f"{base[:60]}-{n}"

    def _add_theme_version(self, p: Path, tid: str, sha: str, *, name: str | None, comment: str) -> ThemeImport:
        prev = self.store.get_theme(tid) if self.store.has_theme(tid) else None
        if prev is not None and prev.current.sha256 == sha:
            return ThemeImport(prev, skipped=True, matched=prev.version)
        number = self.store.next_theme_version(tid)
        dst = self.folder.theme_file(tid, number, p.suffix.lower())
        manifest, roles = self._import(p, prev.current.roles if prev else {}, strict=False)
        self.blobs.put_file(str(p), dst.as_posix())
        record = ThemeVersionRecord(
            theme_id=tid,
            number=number,
            pptx_uri=dst.as_posix(),
            sha256=sha,
            original_name=p.name,
            manifest=manifest.model_copy(update={"source_path": dst.as_posix(), "pptx_path": dst.as_posix()}),
            roles=roles,
            comment=comment or ("загружен" if prev is None else f"новый файл {p.name}"),
            imported_at=_now(),
        )
        try:
            rec = self.store.add_theme_version(record, name=name or (None if prev else p.stem))
        except BaseException:
            self.blobs.delete(dst.as_posix())
            raise
        return self._bump(rec, f"шаблон {tid}: версия {number}")

    def set_theme_roles(self, theme_id: str, roles: Mapping[str, str | None], comment: str = "") -> ThemeImport:
        """Подтвердить роли макетов (роль → ключ макета; пусто — снять подтверждение, роль снова
        подбирает приложение). Новая версия шаблона с тем же файлом; сценарии на нём получают
        новую версию."""
        self._need_write()
        t = self.store.get_theme(theme_id)
        merged = {k: v for k, v in {**t.current.roles, **roles}.items() if v}
        manifest, kept = self._import(Path(t.current.pptx_uri), merged, strict=True)
        if kept == t.current.roles:
            return ThemeImport(t, skipped=True)  # роли не изменились
        number = self.store.next_theme_version(theme_id)
        record = t.current.model_copy(
            update={
                "number": number,
                "manifest": manifest.model_copy(
                    update={"source_path": t.current.pptx_uri, "pptx_path": t.current.pptx_uri}
                ),
                "roles": kept,
                "comment": comment or "подтверждены роли макетов: " + ", ".join(f"{k} → {v}" for k, v in kept.items()),
                "imported_at": _now(),
            }
        )
        rec = self.store.add_theme_version(record)
        return self._bump(rec, f"шаблон {theme_id}: роли макетов, версия {number}")

    def _import(self, path: Path, roles: Mapping[str, str], strict: bool) -> tuple[ThemeManifest, dict[str, str]]:
        with tempfile.TemporaryDirectory(prefix="theme-", dir=self.folder.tmp) as tmp:
            manifest = self.worker.import_theme(path, tmp, roles=dict(roles), strict_roles=strict)
        kept = {str(b.role): b.layout_key for b in manifest.roles if not b.guessed and str(b.role) in roles}
        return manifest, kept

    def _bump(self, rec: ThemeRecord, comment: str) -> ThemeImport:
        """Перевести сценарии на новую версию шаблона. Сценарий, у которого на новой версии
        появились ошибки (пропал слайд-образец, метка, макет), остаётся на прежней версии:
        отчёты по нему собираются как раньше, а список ошибок говорит, что поправить."""
        out = ThemeImport(rec)
        new = rec.current
        for sc in self.store.scenarios_using_theme(rec.id):
            if sc.current.theme_version == rec.version:
                continue
            before = set()
            if sc.current.theme_version is not None:
                old = self.store.get_theme_version(rec.id, sc.current.theme_version)
                before = {str(i) for i in self._check(sc.spec, old, sc.id) if i.level == IssueLevel.ERROR}
            lost = [str(i) for i in self._check(sc.spec, new, sc.id) if i.level == IssueLevel.ERROR]
            lost = [e for e in lost if e not in before]
            if lost:
                out.lost[sc.id] = lost
                continue
            self.store.save_scenario(sc.id, sc.spec, sc.current.text, theme=(rec.id, rec.version), comment=comment)
            out.scenarios.append(sc.id)
        return out

    def theme_path(self, theme_id: str, version: int | None = None) -> Path:
        """Файл шаблона в папке данных."""
        return Path(self.theme_version(theme_id, version).pptx_uri)

    def export_theme(self, theme_id: str, out: str | Path, version: int | None = None) -> Path:
        """Копия файла шаблона (например, чтобы доработать его в PowerPoint и загрузить снова)."""
        v = self.theme_version(theme_id, version)
        dst = Path(out)
        if dst.is_dir() or dst.suffix.lower() not in THEME_SUFFIXES:
            dst = dst / f"{theme_id}_v{v.number}{Path(v.pptx_uri).suffix}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(v.pptx_uri, dst)
        return dst

    def delete_theme(self, theme_id: str) -> None:
        """Удалить шаблон со всеми версиями. Шаблон, на котором стоят сценарии, не удаляется."""
        self._need_write()
        users = [s.id for s in self.store.scenarios_using_theme(theme_id)]
        if users:
            raise AgenError(
                ErrorCode.IN_USE,
                f"Шаблон «{theme_id}» используют сценарии: {', '.join(users)}",
                hint="Сначала переведите их на другой шаблон или удалите.",
            )
        self.store.delete_theme(theme_id)
        self.blobs.delete(self.folder.theme_dir(theme_id).as_posix())
