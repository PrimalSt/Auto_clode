"""Картинка слайда: время на PowerPoint и LibreOffice и завершение зависшего PowerPoint."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from autogenerator.worker import theme_jobs


class Calls:
    def __init__(self, tasklist: str = "", killed: int = 0):
        self.commands: list[list[str]] = []
        self.tasklist = tasklist
        self.killed = killed

    def __call__(self, cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(cmd)
        out = self.tasklist if cmd[0] == "tasklist" else ""
        return subprocess.CompletedProcess(cmd, self.killed if cmd[0] == "taskkill" else 0, out, "")


def test_hung_powerpoint_is_ended_only_if_the_script_started_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pid_file = tmp_path / "powerpoint.pid"
    calls = Calls(tasklist='"POWERPNT.EXE","4242","Console","1","250 000 K"\r\n')
    monkeypatch.setattr(theme_jobs.subprocess, "run", calls)
    # PowerPoint был открыт до картинки: номера нет, ничего не завершается
    assert theme_jobs._end_powerpoint(pid_file) is False and calls.commands == []
    pid_file.write_text("4242\r\n", encoding="ascii")
    assert theme_jobs._end_powerpoint(pid_file) is True
    assert calls.commands[-1] == ["taskkill", "/PID", "4242", "/F"]


def test_pid_of_another_program_is_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pid_file = tmp_path / "powerpoint.pid"
    pid_file.write_text("4242", encoding="ascii")
    calls = Calls(tasklist='"notepad.exe","4242","Console","1","9 000 K"\r\n')
    monkeypatch.setattr(theme_jobs.subprocess, "run", calls)
    assert theme_jobs._end_powerpoint(pid_file) is False
    assert [c[0] for c in calls.commands] == ["tasklist"]


def test_libreoffice_gets_what_is_left_of_the_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    given: dict[str, float] = {}
    clock = iter([0.0, theme_jobs.IMAGE_BUDGET - 30.0])
    monkeypatch.setattr(theme_jobs.time, "monotonic", lambda: next(clock))

    def powerpoint(pptx: Path, png: Path, timeout: float = theme_jobs.IMAGE_TIMEOUT) -> bool:
        given["powerpoint"] = timeout
        return False

    def libreoffice(pptx: Path, png: Path, timeout: float = theme_jobs.LIBREOFFICE_TIMEOUT) -> bool:
        given["libreoffice"] = timeout
        png.write_bytes(b"png")
        return True

    monkeypatch.setattr(theme_jobs, "_powerpoint", powerpoint)
    monkeypatch.setattr(theme_jobs, "_libreoffice", libreoffice)
    note = theme_jobs.slide_image(tmp_path / "s.pptx", tmp_path / "s.png")
    assert note is not None and note.startswith("приблизительно")
    assert given == {"powerpoint": theme_jobs.IMAGE_TIMEOUT, "libreoffice": 30.0}
    assert theme_jobs.IMAGE_BUDGET < 180, "картинка должна укладываться в таймаут исполнителя превью"
