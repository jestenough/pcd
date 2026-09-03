from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from pcd_cli.catalog import ProjectCatalog
from pcd_cli.cli import cli
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from click.testing import CliRunner


def test_init_discovers_repo(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "projects"
    (root / "repo/.git").mkdir(parents=True)
    monkeypatch.chdir(root)

    result = runner.invoke(cli, ["init"])

    assert result.exit_code == 0
    assert "Added root" in result.output
    assert [item.name for item in (ProjectCatalog.create().cache.load() or [])] == ["repo"]


def test_manual_add_list_remove(runner: CliRunner, tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()

    added = runner.invoke(cli, ["add", str(notes), "--name", "docs"])
    listed = runner.invoke(cli, ["list"])
    removed = runner.invoke(cli, ["remove", "docs"])

    assert added.exit_code == 0
    assert "docs" in listed.output
    assert "manual" in listed.output
    assert removed.exit_code == 0
    assert ProjectCatalog.create().config.load().manual_projects == ()


def test_uninit(runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.chdir(root)
    assert runner.invoke(cli, ["init"]).exit_code == 0

    result = runner.invoke(cli, ["uninit"])

    assert result.exit_code == 0
    assert ProjectCatalog.create().config.load().roots == ()


def test_uninit_non_root(runner: CliRunner) -> None:
    assert runner.invoke(cli, ["uninit"]).exit_code == 1


def test_remove_discovered_is_rejected(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    (root / "repo/.git").mkdir(parents=True)
    monkeypatch.chdir(root)
    assert runner.invoke(cli, ["init"]).exit_code == 0

    result = runner.invoke(cli, ["remove", "repo"])

    assert result.exit_code == 1
    assert "discovered automatically" in result.output


def test_roots_and_refresh_commands(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.chdir(root)
    assert runner.invoke(cli, ["init"]).exit_code == 0

    roots = runner.invoke(cli, ["roots"])
    refreshed = runner.invoke(cli, ["refresh"])

    assert str(root) in roots.output
    assert "Found 0 projects." in refreshed.output


def test_shell_init(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["shell-init", "bash"])

    assert result.exit_code == 0
    assert "bash_source" in result.output


def test_invalid_add_arguments(runner: CliRunner, tmp_path: Path) -> None:
    missing = runner.invoke(cli, ["add", str(tmp_path / "missing")])

    path = tmp_path / "repo"
    path.mkdir()
    empty = runner.invoke(cli, ["add", str(path), "--name", "   "])

    assert missing.exit_code == 2
    assert empty.exit_code == 2


def test_duplicate_path_is_not_added_twice(runner: CliRunner, tmp_path: Path) -> None:
    path = tmp_path / "repo"
    path.mkdir()
    assert runner.invoke(cli, ["add", str(path), "--name", "one"]).exit_code == 0

    result = runner.invoke(cli, ["add", str(path), "--name", "two"])

    assert result.exit_code == 0
    assert "already exists" in result.output


def test_list_empty(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["list"])

    assert result.exit_code == 0
    assert "No projects found" in result.output


def test_stale_discovered_project_is_rejected(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    path = tmp_path / "gone"
    ProjectCatalog.create().cache.save((Project("gone", path, path, ProjectSource.DISCOVERED),))

    result = runner.invoke(cli, ["gone"])

    assert result.exit_code == 1
    assert "no longer exists" in result.output


def test_init_duplicate_and_nested(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)

    monkeypatch.chdir(outer)
    assert runner.invoke(cli, ["init"]).exit_code == 0
    assert "Already a root" in runner.invoke(cli, ["init"]).output

    monkeypatch.chdir(inner)
    result = runner.invoke(cli, ["init"])

    assert result.exit_code == 0
    assert "root is inside" in result.output


def test_add_uses_current_directory(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "cwd"
    root.mkdir()
    monkeypatch.chdir(root)

    result = runner.invoke(cli, ["add"])

    assert result.exit_code == 0
    assert "cwd" in result.output


def test_relative_add_is_stored_as_absolute(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "work"
    repo = root / "repo"
    repo.mkdir(parents=True)
    monkeypatch.chdir(root)

    assert runner.invoke(cli, ["add", "repo"]).exit_code == 0

    project = ProjectCatalog.create().config.load().manual_projects[0]
    assert project.display_path == repo


def test_add_rejects_unknown_home_directory(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["add", "~pcd-user-that-does-not-exist/repo"])

    assert result.exit_code == 2
    assert "Unknown home directory" in result.output


def test_config_path(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["config", "path"])

    assert result.exit_code == 0
    assert result.output.strip().endswith("pcd-cli/config.toml")


def test_config_show_prints_effective_values(runner: CliRunner) -> None:
    config = ProjectCatalog.create().config
    config.path.parent.mkdir(parents=True)
    config.path.write_text("roots = []\n[scan]\nhidden = false\n", encoding="utf-8")

    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    assert "hidden = false" in result.output
    assert "follow_symlinks = false" in result.output
    assert "node_modules" in result.output
    assert "projects = []" in result.output


def test_config_edit_uses_visual_and_creates_config(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run_editor(args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert check is False
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setenv("VISUAL", "code --wait")
    monkeypatch.setenv("EDITOR", "vim")
    monkeypatch.setattr("pcd_cli.cli.config.subprocess.run", run_editor)

    result = runner.invoke(cli, ["config", "edit"])
    config_path = ProjectCatalog.create().config.path

    assert result.exit_code == 0
    assert calls == [["code", "--wait", str(config_path)]]
    assert config_path.is_file()


def test_config_edit_prefers_configured_editor(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ProjectCatalog.create().config
    config.path.parent.mkdir(parents=True)
    config.path.write_text('editor = "nvim --clean"\n', encoding="utf-8")
    monkeypatch.setenv("VISUAL", "code --wait")
    calls: list[list[str]] = []

    def run_editor(args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert check is False
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("pcd_cli.cli.config.subprocess.run", run_editor)

    result = runner.invoke(cli, ["config", "edit"])

    assert result.exit_code == 0
    assert calls == [["nvim", "--clean", str(config.path)]]


def test_config_edit_requires_configured_editor(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)

    result = runner.invoke(cli, ["config", "edit"])

    assert result.exit_code == 2
    assert "$VISUAL or $EDITOR" in result.output


def test_config_validate_reports_precise_error(runner: CliRunner) -> None:
    config = ProjectCatalog.create().config
    config.path.parent.mkdir(parents=True)
    config.path.write_text("roots = [", encoding="utf-8")

    result = runner.invoke(cli, ["config", "validate"])

    assert result.exit_code == 1
    assert "Invalid TOML" in result.output
    assert "line 1 col 9" in result.output


def test_config_validate_accepts_effective_defaults(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["config", "validate"])

    assert result.exit_code == 0
    assert "Configuration is valid" in result.output
