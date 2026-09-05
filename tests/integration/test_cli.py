from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from typing import TYPE_CHECKING

import click
import pytest
from click.shell_completion import CompletionItem

import pcd_cli.cli as cli_module
import pcd_cli.cli.app as app_module
from pcd_cli.catalog import ProjectCatalog
from pcd_cli.cli import cli, ProjectCommandGroup
from pcd_cli.config import InvalidConfigError
from pcd_cli.models import Project, ProjectSource
from pcd_cli.shell_integration import ShellIntegrationError

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner


def test_project_option_cannot_mix_with_command(runner: CliRunner) -> None:
    assert runner.invoke(cli, ["--project", "x", "list"]).exit_code == 2


def test_no_arguments_show_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, [])
    help_result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert result.output == help_result.output


def test_group_routes_unknown_name_to_project() -> None:
    group = ProjectCommandGroup()
    group.add_command(click.Command("init"))
    group.add_command(cli_module.project)
    ctx = click.Context(group)

    name, command, rest = group.resolve_command(ctx, ["repo"])

    assert name == "project"
    assert command is cli_module.project
    assert rest == ["repo"]


def test_group_completion_uses_cache(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    ProjectCatalog.create().cache.save((Project("repo", repo, repo, ProjectSource.MANUAL),))
    group = ProjectCommandGroup()
    ctx = click.Context(group)

    items = group.shell_complete(ctx, "re")

    assert any(item.value == "repo" for item in items)


def test_version_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr("pcd_cli.cli.app.metadata.version", missing)
    assert cli_module.package_version() == "0+unknown"


def test_main_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    def config_error(*_args: object, **_kwargs: object) -> None:
        raise InvalidConfigError("bad")

    monkeypatch.setattr(cli_module.cli, "main", config_error)
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 1

    def fs_error(*_args: object, **_kwargs: object) -> None:
        raise OSError("bad")

    monkeypatch.setattr(cli_module.cli, "main", fs_error)
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 1

    def shell_error(*_args: object, **_kwargs: object) -> None:
        raise ShellIntegrationError("bad")

    monkeypatch.setattr(cli_module.cli, "main", shell_error)
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 1


def test_group_keeps_known_command() -> None:
    group = ProjectCommandGroup()
    group.add_command(click.Command("init"))
    ctx = click.Context(group)

    name, command, rest = group.resolve_command(ctx, ["init"])

    assert name == "init"
    assert command is not None
    assert rest == []


def test_group_delegates_option_resolution() -> None:
    group = ProjectCommandGroup()
    ctx = click.Context(group)

    with pytest.raises(click.UsageError, match="No such option"):
        group.resolve_command(ctx, ["--unknown"])


def test_group_delegates_unknown_name_without_project_command() -> None:
    group = ProjectCommandGroup()
    ctx = click.Context(group)

    with pytest.raises(click.UsageError, match="No such command"):
        group.resolve_command(ctx, ["repo"])


def test_group_completion_deduplicates_command_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = ProjectCommandGroup()
    group.add_command(click.Command("init"))
    ctx = click.Context(group)
    monkeypatch.setattr(
        app_module,
        "project_completions",
        lambda _value: [CompletionItem("init")],
    )

    completions = group.shell_complete(ctx, "i")

    assert [item.value for item in completions] == ["init"]
