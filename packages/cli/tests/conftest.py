from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    """Папка данных — своя у каждого теста: настоящая папка приложения не трогается."""
    home = tmp_path / "agen-home"
    monkeypatch.setenv("AGEN_HOME", str(home))
    return home
