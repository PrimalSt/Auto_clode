"""Сборка Python приложения для установщика (ARCHITECTURE.md, раздел 4.1; PRD F-802).

Результат — папка ``build/bundle``, которую оболочка Tauri кладёт в установщик
(``shell/tauri.bundle.conf.json``)::

    python/   Python 3.12 (python-build-standalone через uv) со всеми модулями и библиотеками
              по uv.lock, байт-код уже скомпилирован; ipykernel — для ядра Jupyter «Autogenerator»
    bin/      agen.cmd: команда ``agen`` (установщик добавляет папку в PATH пользователя)
    sdk/      wheels/ — пакеты модулей, constraints.txt — версии библиотек приложения:
              ``pip install --find-links sdk/wheels autogenerator-api -c sdk/constraints.txt``

Собирать на той системе, для которой установщик (CI: windows-latest): библиотеки ставятся
под неё. Перед сборкой нужен собранный интерфейс (``npm run build`` в ``frontend``): он
входит в пакет сервера.

Запуск из корня репозитория::

    uv run python tools/build_bundle.py [--out build/bundle]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = "3.12"
UI_INDEX = ROOT / "packages" / "server" / "src" / "autogenerator" / "server" / "ui" / "index.html"
WINDOWS = sys.platform == "win32"


def run(*args: str | Path, capture: bool = False) -> str:
    cmd = [str(a) for a in args]
    print("$", " ".join(cmd), flush=True)
    out = subprocess.run(cmd, cwd=ROOT, check=True, text=True, capture_output=capture)
    return out.stdout.strip() if capture else ""


def standalone_python() -> Path:
    """Корень установки python-build-standalone (uv ставит его в свою папку). ``--system`` —
    не окружение ``.venv``, в котором идёт сам скрипт (``uv run``)."""
    run("uv", "python", "install", PYTHON)
    exe = Path(run("uv", "python", "find", "--managed-python", "--system", PYTHON, capture=True))
    root = exe.parent if WINDOWS else exe.parent.parent
    if (root / "pyvenv.cfg").exists():
        sys.exit(f"Ожидался Python из uv, а найдено окружение: {root}")
    return root


def python_exe(root: Path) -> Path:
    return root / "python.exe" if WINDOWS else root / "bin" / "python3"


def folder_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def build(out: Path) -> None:
    if not UI_INDEX.is_file():
        sys.exit("Нет собранного интерфейса: сначала `npm ci && npm run build` в папке frontend")
    if out.exists():
        shutil.rmtree(out)
    python, sdk, wheels, bin_dir = out / "python", out / "sdk", out / "sdk" / "wheels", out / "bin"

    print("== Python приложения", flush=True)
    shutil.copytree(standalone_python(), python, symlinks=True)
    py = python_exe(python)

    print("== Библиотеки по uv.lock", flush=True)
    sdk.mkdir(parents=True)
    constraints = sdk / "constraints.txt"
    run(
        "uv", "export", "--frozen", "--no-dev", "--group", "bundle", "--no-emit-workspace", "--no-hashes",
        "--no-header", "--format", "requirements.txt", "--output-file", constraints,
    )  # fmt: skip
    install: list[str | Path] = ["uv", "pip", "install", "--python", py, "--break-system-packages"]
    install += ["--compile-bytecode", "--no-deps"]
    run(*install, "-r", constraints)

    print("== Модули приложения", flush=True)
    run("uv", "build", "--all-packages", "--wheel", "--out-dir", wheels)
    for junk in wheels.glob(".gitignore"):
        junk.unlink()
    run(*install, *sorted(wheels.glob("*.whl")))
    if WINDOWS:
        # Scripts\*.exe знают абсолютный путь к Python на машине сборки: после установки они не
        # работают. Приложение, agen.cmd и ядро Jupyter зовут `python.exe -m …`.
        shutil.rmtree(python / "Scripts", ignore_errors=True)

    print("== Команда agen", flush=True)
    bin_dir.mkdir()
    if WINDOWS:
        cmd = '@"%~dp0..\\python\\python.exe" -m autogenerator.cli %*\r\n'
        (bin_dir / "agen.cmd").write_text(cmd, encoding="ascii")
    else:
        sh = bin_dir / "agen"
        sh.write_text('#!/bin/sh\nexec "$(dirname "$0")/../python/bin/python3" -m autogenerator.cli "$@"\n')
        sh.chmod(0o755)

    print("== Проверка", flush=True)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env["PYTHONNOUSERSITE"] = "1"
    check = "import autogenerator.server, autogenerator.worker, autogenerator.api, ipykernel; print('ok')"
    subprocess.run([str(py), "-c", check], check=True, env=env, cwd=out)
    subprocess.run([str(py), "-m", "autogenerator.cli", "--help"], check=True, env=env, cwd=out, capture_output=True)
    with tempfile.TemporaryDirectory() as tmp:
        schema = Path(tmp) / "openapi.json"
        subprocess.run([str(py), "-m", "autogenerator.server", "--openapi", str(schema)], check=True, env=env, cwd=out)
    ui = list(python.rglob("autogenerator/server/ui/index.html"))
    if not ui:
        sys.exit("В пакете сервера нет интерфейса (ui/index.html)")

    sizes = ", ".join(f"{p.name} {folder_size(p) / 1e6:.0f} МБ" for p in (python, sdk, bin_dir))
    print(f"Готово: {out} ({sizes})")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, default=ROOT / "build" / "bundle", help="Куда собрать (build/bundle)")
    build(p.parse_args().out.resolve())


if __name__ == "__main__":
    main()
