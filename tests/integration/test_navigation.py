from __future__ import annotations

import errno
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import pcd_cli.navigation as navigation_module
from pcd_cli.catalog import ProjectCatalog
from pcd_cli.cli import cli
from pcd_cli.filesystem import canonical_path
from pcd_cli.history import UsageHistory
from pcd_cli.integrations.shell import inactive_shell_message
from pcd_cli.models import Project, ProjectSource
from pcd_cli.navigation import select_project
from pcd_cli.picker import ProjectPicker

if TYPE_CHECKING:
    from click.testing import CliRunner


def test_project_argument_dispatch(runner: CliRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert runner.invoke(cli, ["add", str(repo)]).exit_code == 0

    result = runner.invoke(cli, ["repo"])

    assert result.exit_code == 0
    assert result.output.splitlines()[0] == str(canonical_path(repo))


def test_cache_miss_refreshes_new_repo(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.chdir(root)
    assert runner.invoke(cli, ["init"]).exit_code == 0

    repo = root / "new"
    (repo / ".git").mkdir(parents=True)

    result = runner.invoke(cli, ["new"])

    assert result.exit_code == 0
    assert result.output.splitlines()[0] == str(canonical_path(repo))


def test_not_found_is_exit_three(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["missing"])

    assert result.exit_code == 3
    assert "Project not found: missing" in result.output


def test_reserved_name_uses_project_option(runner: CliRunner, tmp_path: Path) -> None:
    path = tmp_path / "init"
    path.mkdir()
    assert runner.invoke(cli, ["add", str(path)]).exit_code == 0

    result = runner.invoke(cli, ["--project", "init"])

    assert result.exit_code == 0
    assert result.output.splitlines()[0] == str(canonical_path(path))


def test_duplicate_name_uses_choice(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    assert runner.invoke(cli, ["add", str(first), "--name", "same"]).exit_code == 0
    assert runner.invoke(cli, ["add", str(second), "--name", "same"]).exit_code == 0

    monkeypatch.setattr(
        navigation_module,
        "select_project",
        lambda _app, projects, _query="": projects[1],
    )
    result = runner.invoke(cli, ["same"])

    assert result.exit_code == 0
    assert result.output.splitlines()[0] == str(canonical_path(second))


def test_select_project_opens_picker_for_multiple_matches(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matches = (
        Project("first", tmp_path / "first", tmp_path / "first", ProjectSource.MANUAL),
        Project("second", tmp_path / "second", tmp_path / "second", ProjectSource.MANUAL),
    )
    monkeypatch.setattr(ProjectPicker, "run", lambda _picker: matches[1])

    assert select_project(projects, matches, "") is matches[1]


def test_cancel_multiple_does_not_refresh(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("app-one", "app-two"):
        path = tmp_path / name
        path.mkdir()
        assert runner.invoke(cli, ["add", str(path)]).exit_code == 0

    monkeypatch.setattr(navigation_module, "select_project", lambda *_args: None)

    def fail(_self: ProjectCatalog) -> list[Project]:
        raise AssertionError("cancel must not refresh")

    monkeypatch.setattr(ProjectCatalog, "refresh", fail)
    result = runner.invoke(cli, ["app"])

    assert result.exit_code == 0


def test_missing_manual_path(runner: CliRunner, tmp_path: Path) -> None:
    path = tmp_path / "notes"
    path.mkdir()
    assert runner.invoke(cli, ["add", str(path)]).exit_code == 0
    path.rmdir()

    result = runner.invoke(cli, ["notes"])

    assert result.exit_code == 1
    assert "no longer exists" in result.output


def test_shell_protocol(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert runner.invoke(cli, ["add", str(repo)]).exit_code == 0
    monkeypatch.setenv("PCD_SHELL", "1")

    result = runner.invoke(cli, ["repo"])

    assert result.exit_code == 10
    assert result.output == f"{repo}\n"


@pytest.mark.parametrize("shell_active", [False, True])
@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EROFS, errno.ENOSPC])
def test_navigation_survives_history_write_errors(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shell_active: bool,
    error_number: int,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert runner.invoke(cli, ["add", str(repo)]).exit_code == 0
    monkeypatch.setenv("PCD_SHELL", "1" if shell_active else "0")
    error = OSError(error_number, "History write failed")

    def fail_record(_self: UsageHistory, path: Path) -> None:
        assert path == canonical_path(repo)
        raise error

    monkeypatch.setattr(UsageHistory, "record", fail_record)

    result = runner.invoke(cli, ["repo"])

    assert result.exit_code == (10 if shell_active else 0)
    assert result.stdout == f"{canonical_path(repo)}\n"
    expected_stderr = f"Warning: could not write usage history: {error}\n"
    if not shell_active:
        expected_stderr += f"{inactive_shell_message()}\n"
    assert result.stderr == expected_stderr


def test_navigation_survives_unusable_history_directory(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert runner.invoke(cli, ["add", str(repo)]).exit_code == 0
    history_parent = ProjectCatalog.create().history.path.parent
    history_parent.parent.mkdir(parents=True, exist_ok=True)
    history_parent.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("PCD_SHELL", "1")

    result = runner.invoke(cli, ["repo"])

    assert result.exit_code == 10
    assert result.stdout == f"{canonical_path(repo)}\n"
    assert result.stderr.startswith("Warning: could not write usage history: ")
    assert str(history_parent) in result.stderr
    assert history_parent.read_text(encoding="utf-8") == "not a directory"


def test_navigation_does_not_hide_unexpected_history_errors(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert runner.invoke(cli, ["add", str(repo)]).exit_code == 0
    error = RuntimeError("Unexpected history error")

    def fail_record(_self: UsageHistory, _path: Path) -> None:
        raise error

    monkeypatch.setattr(UsageHistory, "record", fail_record)

    result = runner.invoke(cli, ["repo"])

    assert result.exit_code == 1
    assert result.exception is error
    assert result.stdout == ""
    assert result.stderr == ""


def test_navigation_recommends_shell_install_when_integration_is_absent(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    repo = tmp_path / "repo"
    repo.mkdir()
    assert runner.invoke(cli, ["add", str(repo)]).exit_code == 0

    result = runner.invoke(cli, ["repo"])

    assert result.exit_code == 0
    assert "Shell integration is not installed" in result.output
    assert "pcd shell install" in result.output


def test_navigation_recommends_reload_when_integration_is_configured(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    (Path.home() / ".zshrc").write_text(
        'eval "$(pcd shell init zsh)"\n',
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    assert runner.invoke(cli, ["add", str(repo)]).exit_code == 0

    result = runner.invoke(cli, ["repo"])

    assert result.exit_code == 0
    assert "configured" in result.output
    assert f"source {Path.home() / '.zshrc'}" in result.output
    assert "pcd shell install" not in result.output


def test_navigation_recommends_reload_after_managed_install(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    assert runner.invoke(cli, ["shell", "install"]).exit_code == 0
    repo = tmp_path / "repo"
    repo.mkdir()
    assert runner.invoke(cli, ["add", str(repo)]).exit_code == 0

    result = runner.invoke(cli, ["repo"])

    assert result.exit_code == 0
    assert "configured" in result.output
    assert f"source {Path.home() / '.zshrc'}" in result.output
    assert "pcd shell install" not in result.output


def test_completion_deduplicates_names(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    ProjectCatalog.create().cache.save(
        (
            Project("same", first, first, ProjectSource.MANUAL),
            Project("same", second, second, ProjectSource.MANUAL),
        )
    )

    assert [item.value for item in navigation_module.project_completions("sa")] == ["same"]
