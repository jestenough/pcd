from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from pcd_cli.catalog import ProjectCatalog

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture
def projects() -> ProjectCatalog:
    return ProjectCatalog.create()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()
