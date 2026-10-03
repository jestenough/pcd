from __future__ import annotations

import os
from pathlib import Path

import pytest

from pcd_cli.filesystem import ApplicationPaths, atomic_write, format_path


def test_atomic_replaces_file(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"

    with atomic_write(path) as stream:
        stream.write("first")
    with atomic_write(path) as stream:
        stream.write("second")

    assert path.read_text(encoding="utf-8") == "second"
    assert not list(tmp_path.glob(".file.txt.*"))


def test_atomic_cleans_temp_file_on_error(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"

    with pytest.raises(RuntimeError), atomic_write(path) as stream:
        stream.write("partial")
        raise RuntimeError("stop")

    assert not path.exists()
    assert not list(tmp_path.glob(".file.txt.*"))


def test_short_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))

    assert format_path(home) == "~"
    assert format_path(home / "x") == "~/x"
    assert format_path(tmp_path / "outside") == str(tmp_path / "outside")


def test_application_paths_follow_platformdirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    paths = ApplicationPaths.resolve()

    if os.name == "nt":
        base = Path(os.environ["WIN_PD_OVERRIDE_LOCAL_APPDATA"]) / "pcd-cli"
        assert paths.config == base / "config.toml"
        assert paths.cache == base / "Cache/projects.jsonl"
        assert paths.history == base / "history.json"
    else:
        assert paths.config == tmp_path / "config/pcd-cli/config.toml"
        assert paths.cache == tmp_path / "cache/pcd-cli/projects.jsonl"
        assert paths.history == tmp_path / "state/pcd-cli/history.json"
