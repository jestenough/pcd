from __future__ import annotations

from importlib import metadata
from typing import TYPE_CHECKING

import click

from pcd_cli.catalog import ProjectCatalog
from pcd_cli.cli.config import config_commands
from pcd_cli.cli.projects import project_commands
from pcd_cli.cli.roots import root_commands
from pcd_cli.cli.shell import shell_commands, shell_init
from pcd_cli.config import InvalidConfigError
from pcd_cli.models import ExitCode
from pcd_cli.navigation import navigate_to_project, project_completions
from pcd_cli.shell_integration import ShellIntegrationError

if TYPE_CHECKING:
    from click.shell_completion import CompletionItem


class ProjectCommandGroup(click.Group):
    """Route an unknown first argument to project lookup."""

    def resolve_command(
        self,
        ctx: click.Context,
        args: list[str],
    ) -> tuple[str | None, click.Command | None, list[str]]:
        candidate = args[0] if args else None
        if candidate is None or candidate.startswith("-"):
            return super().resolve_command(ctx, args)
        if self.get_command(ctx, candidate) is not None:
            return super().resolve_command(ctx, args)

        project_command = self.get_command(ctx, "project")
        if project_command is None:
            return super().resolve_command(ctx, args)

        return "project", project_command, args

    def shell_complete(self, ctx: click.Context, incomplete: str) -> list[CompletionItem]:
        completions = super().shell_complete(ctx, incomplete)
        known_values = {item.value for item in completions}

        for completion in project_completions(incomplete):
            if completion.value not in known_values:
                completions.append(completion)

        return completions


def package_version() -> str:
    try:
        return metadata.version("pcd-cli")
    except metadata.PackageNotFoundError:
        return "0+unknown"


@click.group(cls=ProjectCommandGroup, invoke_without_command=True)
@click.option(
    "--project",
    "project_name",
    shell_complete=lambda _ctx, _param, value: project_completions(value),
    help="Resolve a project whose name is reserved by a command.",
)
@click.version_option(version=package_version())
@click.pass_context
def cli(ctx: click.Context, project_name: str | None) -> None:
    """Jump to local projects by name."""
    catalog = ProjectCatalog.create()
    ctx.obj = catalog

    if project_name is not None and ctx.invoked_subcommand is not None:
        raise click.UsageError("--project cannot be combined with a command")

    if ctx.invoked_subcommand is not None:
        return

    if project_name is None:
        click.echo(ctx.get_help())
        return

    navigate_to_project(catalog, project_name, exact_only=True)


for command in (
    *project_commands,
    *root_commands,
    config_commands,
    shell_commands,
    shell_init,
):
    cli.add_command(command)


def main() -> None:
    try:
        cli.main()
    except InvalidConfigError as exc:
        click.echo(f"Config error: {exc}", err=True)
        raise SystemExit(ExitCode.ERROR) from None
    except ShellIntegrationError as exc:
        click.echo(f"Shell integration error: {exc}", err=True)
        raise SystemExit(ExitCode.ERROR) from None
    except OSError as exc:
        click.echo(f"Filesystem error: {exc}", err=True)
        raise SystemExit(ExitCode.ERROR) from None
