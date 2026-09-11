from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import pcd_cli.cli.projects as projects_module
from pcd_cli.catalog import ProjectCatalog
from pcd_cli.cli import cli
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
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


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize(
    ("filters", "expected_indices"),
    [
        ([], [0, 1, 2, 3]),
        (["--manual"], [2, 3]),
        (["--discovered"], [0, 1]),
        (["--missing"], [1, 3]),
        (["--manual", "--discovered"], [0, 1, 2, 3]),
        (["--manual", "--missing"], [3]),
        (["--discovered", "--missing"], [1]),
        (["--manual", "--discovered", "--missing"], [1, 3]),
    ],
)
def test_list_filters_by_source_and_missing_status(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filters: list[str],
    expected_indices: list[int],
    as_json: bool,
) -> None:
    root = tmp_path / "projects"
    discovered = root / "discovered"
    manual = tmp_path / "manual"
    missing = tmp_path / "missing"
    gone = root / "gone"
    (discovered / ".git").mkdir(parents=True)
    (gone / ".git").mkdir(parents=True)
    manual.mkdir()
    missing.mkdir()
    monkeypatch.chdir(root)
    assert runner.invoke(cli, ["init"]).exit_code == 0
    assert runner.invoke(cli, ["add", str(manual)]).exit_code == 0
    assert runner.invoke(cli, ["add", str(missing)]).exit_code == 0
    missing.rmdir()
    (gone / ".git").rmdir()
    gone.rmdir()

    rows = [
        ("discovered", str(discovered), "discovered", "available"),
        ("gone", str(gone), "discovered", "missing"),
        ("manual", str(manual), "manual", "available"),
        ("missing", str(missing), "manual", "missing"),
    ]
    expected = [rows[i] for i in expected_indices]
    result = runner.invoke(cli, ["list", *filters, *(["--json"] if as_json else [])])

    assert result.exit_code == 0
    assert result.stderr == ""
    if as_json:
        assert json.loads(result.stdout) == [
            dict(zip(("name", "path", "source", "status"), row, strict=True)) for row in expected
        ]
    else:
        assert [line.split() for line in result.stdout.splitlines()] == [
            ["NAME", "PATH", "SOURCE", "STATUS"],
            *[
                [name, path, "scanned" if source == "discovered" else source, status]
                for name, path, source, status in expected
            ],
        ]


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("filters", [["--discovered"], ["--missing"], ["--manual", "--missing"]])
def test_list_filters_can_return_no_projects(
    runner: CliRunner,
    tmp_path: Path,
    filters: list[str],
    as_json: bool,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert runner.invoke(cli, ["add", str(repo)]).exit_code == 0

    result = runner.invoke(cli, ["list", *filters, *(["--json"] if as_json else [])])

    assert result.exit_code == 0
    assert result.stdout == ("[]\n" if as_json else "No projects found.\n")
    assert result.stderr == ""


def test_list_preserves_symlink_display_path(runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = Path.home() / "project link"
    link.symlink_to(target, target_is_directory=True)
    assert runner.invoke(cli, ["add", str(link), "--name", "docs"]).exit_code == 0

    listed = runner.invoke(cli, ["list"])
    listed_json = runner.invoke(cli, ["list", "--json"])

    assert listed.exit_code == 0
    assert listed.stderr == ""
    assert listed.stdout == (
        "NAME  PATH            SOURCE  STATUS\ndocs  ~/project link  manual  available\n"
    )
    assert listed_json.exit_code == 0
    assert listed_json.stderr == ""
    assert json.loads(listed_json.stdout) == [
        {"name": "docs", "path": str(link), "source": "manual", "status": "available"}
    ]


def test_list_help_describes_filters_and_json(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["list", "--help"])

    assert result.exit_code == 0
    for option in ("--manual", "--discovered", "--missing", "--json"):
        assert option in result.output


def test_list_json_is_machine_readable(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "docs"
    project.mkdir()
    assert runner.invoke(cli, ["add", str(project)]).exit_code == 0
    project.rmdir()
    status_checks = 0
    is_dir = Path.is_dir

    def count_status_checks(path: Path) -> bool:
        nonlocal status_checks
        if path == project:
            status_checks += 1
        return is_dir(path)

    monkeypatch.setattr(Path, "is_dir", count_status_checks)

    result = runner.invoke(cli, ["list", "--manual", "--missing", "--json"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == [
        {
            "name": "docs",
            "path": str(project),
            "source": "manual",
            "status": "missing",
        }
    ]
    assert status_checks == 1


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


def test_remove_missing_project(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["remove", "missing"])

    assert result.exit_code == 3
    assert "Project not found: missing" in result.output


def test_remove_cancelled_selection(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("first", "second"):
        path = tmp_path / name
        path.mkdir()
        assert runner.invoke(cli, ["add", str(path), "--name", "same"]).exit_code == 0

    monkeypatch.setattr(projects_module, "select_project", lambda *_args: None)

    result = runner.invoke(cli, ["remove", "same"])

    assert result.exit_code == 0
    assert len(ProjectCatalog.create().config.load().manual_projects) == 2


def test_remove_handles_disappeared_registration(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "repo"
    path.mkdir()
    assert runner.invoke(cli, ["add", str(path)]).exit_code == 0
    monkeypatch.setattr(ProjectCatalog, "remove_project", lambda _catalog, _path: False)

    result = runner.invoke(cli, ["remove", "repo"])

    assert result.exit_code == 1
    assert "Project is no longer registered" in result.output


def test_roots_and_refresh_commands(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    (root / "repo" / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    assert runner.invoke(cli, ["init"]).exit_code == 0

    roots = runner.invoke(cli, ["roots"])
    refreshed = runner.invoke(cli, ["refresh"])

    assert "PATH" in roots.output
    assert "PROJECTS" in roots.output
    assert "STATUS" in roots.output
    assert str(root) in roots.output
    assert roots.output.splitlines()[1].split()[-2:] == ["1", "available"]
    assert "Found 1 projects." in refreshed.output


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


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("cached", [False, True])
def test_list_empty(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    as_json: bool,
    cached: bool,
) -> None:
    if cached:
        ProjectCatalog.create().cache.save(())

        def fail_refresh(_catalog: ProjectCatalog) -> list[Project]:
            raise AssertionError("valid empty cache must not refresh")

        monkeypatch.setattr(ProjectCatalog, "refresh", fail_refresh)

    result = runner.invoke(cli, ["list", *(["--json"] if as_json else [])])

    assert result.exit_code == 0
    assert result.stdout == ("[]\n" if as_json else "No projects found.\n")
    assert result.stderr == ""


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


def test_config_edit_falls_back_to_environment_for_invalid_config(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ProjectCatalog.create().config
    config.path.parent.mkdir(parents=True)
    config.path.write_text("roots = [", encoding="utf-8")
    monkeypatch.setenv("EDITOR", "nano")
    calls: list[list[str]] = []

    def run_editor(args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("pcd_cli.cli.config.subprocess.run", run_editor)

    result = runner.invoke(cli, ["config", "edit"])

    assert result.exit_code == 0
    assert calls == [["nano", str(config.path)]]


def test_config_edit_rejects_invalid_editor_command(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDITOR", "'")

    result = runner.invoke(cli, ["config", "edit"])

    assert result.exit_code == 2
    assert "Invalid editor command" in result.output


def test_config_edit_rejects_empty_editor_command(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDITOR", "   ")

    result = runner.invoke(cli, ["config", "edit"])

    assert result.exit_code == 2
    assert "$VISUAL or $EDITOR" in result.output


def test_config_edit_reports_editor_failure(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDITOR", "nano")

    def run_editor(args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 7)

    monkeypatch.setattr("pcd_cli.cli.config.subprocess.run", run_editor)

    result = runner.invoke(cli, ["config", "edit"])

    assert result.exit_code == 1
    assert "Editor exited with status 7" in result.output


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
