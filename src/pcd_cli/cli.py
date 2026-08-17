from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

import click

from pcd_cli.catalog import ProjectCatalog
from pcd_cli.config import InvalidConfigError
from pcd_cli.filesystem import canonical_path, format_path
from pcd_cli.models import ExitCode, Project, ProjectSource
from pcd_cli.navigation import navigate_to_project, project_completions, select_project
from pcd_cli.shell_integration import (
    detect_shell,
    render_shell_integration,
    Shell,
    shell_integration_active,
    ShellIntegration,
    ShellIntegrationError,
    ShellIntegrationState,
)

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

    if ctx.invoked_subcommand is None:
        navigate_to_project(catalog, project_name, exact_only=project_name is not None)

    navigate_to_project(catalog, project_name, exact_only=True)


@cli.command(hidden=True)
@click.argument("query")
@click.pass_obj
def project(catalog: ProjectCatalog, query: str) -> None:
    """Resolve a project name routed by the root command."""
    navigate_to_project(catalog, query)


@cli.command()
@click.pass_obj
def init(catalog: ProjectCatalog) -> None:
    """Register the current directory as a scan root."""
    current = Path.cwd()
    parent = catalog.find_parent_root(current)
    added = catalog.add_scan_root(current)

    if not added:
        click.echo(f"Already a root: {format_path(current)}")
        return
    if parent is not None:
        click.echo(f"Note: root is inside {format_path(parent)}", err=True)
    click.echo(f"Added root: {format_path(current)}")


@cli.command()
@click.pass_obj
def uninit(catalog: ProjectCatalog) -> None:
    """Remove the current directory from scan roots."""
    current = Path.cwd()
    if catalog.remove_scan_root(current):
        click.echo(f"Removed root: {format_path(current)}")
        return

    click.echo("Current directory is not a pcd root.", err=True)
    raise click.exceptions.Exit(ExitCode.ERROR)


@cli.command()
@click.argument("path", type=click.Path(path_type=Path, file_okay=False), required=False)
@click.option("--name", help="Custom name for a manual project.")
@click.pass_obj
def add(catalog: ProjectCatalog, path: Path | None, name: str | None) -> None:
    """Register a directory as a manual project."""
    if path is None:
        location = Path.cwd()
    else:
        try:
            location = path.expanduser()
        except RuntimeError as exc:
            raise click.BadParameter("Unknown home directory", param_hint="path") from exc

    if not location.is_absolute():
        location = Path.cwd() / location
    if not location.is_dir():
        raise click.BadParameter(f"Directory does not exist: {location}", param_hint="path")

    project_name = location.name if name is None else name.strip()
    if not project_name:
        raise click.BadParameter("Project name cannot be empty", param_hint="--name")

    item = Project(
        name=project_name,
        path=canonical_path(location),
        display_path=location,
        source=ProjectSource.MANUAL,
    )
    if catalog.add_project(item):
        click.echo(f"Added project: {item.name} -> {format_path(item.display_path)}")
        return
    click.echo(f"Project already exists: {item.name} -> {format_path(item.display_path)}")


@cli.command()
@click.argument(
    "query",
    shell_complete=lambda _ctx, _param, value: project_completions(value),
)
@click.pass_obj
def remove(catalog: ProjectCatalog, query: str) -> None:
    """Remove a manual project registration."""
    matches = catalog.search(query, exact_only=True)
    if not matches:
        click.echo(f"Project not found: {query}", err=True)
        raise click.exceptions.Exit(ExitCode.NOT_FOUND)

    selected = matches[0] if len(matches) == 1 else select_project(catalog, matches, query)
    if selected is None:
        return
    if selected.source is ProjectSource.DISCOVERED:
        click.echo(
            f"{selected.name} is discovered automatically and cannot be removed with pcd remove.",
            err=True,
        )
        raise click.exceptions.Exit(ExitCode.ERROR)
    if catalog.remove_project(selected.path):
        click.echo(f"Removed project: {selected.name}")
        return

    click.echo("Project is no longer registered.", err=True)
    raise click.exceptions.Exit(ExitCode.ERROR)


@cli.command("list")
@click.pass_obj
def list_projects(catalog: ProjectCatalog) -> None:
    """List all known projects."""
    projects = catalog.projects()
    if not projects:
        click.echo("No projects found.")
        return

    name_width = max(len(project.name) for project in projects)
    for item in projects:
        status = "ok" if item.path.is_dir() else "missing"
        click.echo(
            f"{item.name:<{name_width}}  {format_path(item.display_path)}  {item.source.value:<10}  {status}"
        )


@cli.command()
@click.pass_obj
def roots(catalog: ProjectCatalog) -> None:
    """List registered scan roots."""
    for root in catalog.config.load().roots:
        click.echo(format_path(root))


@cli.command()
@click.pass_obj
def refresh(catalog: ProjectCatalog) -> None:
    """Rescan roots and rebuild the project cache."""
    click.echo(f"Found {len(catalog.refresh())} projects.")


@cli.command("config-path")
@click.pass_obj
def config_path(catalog: ProjectCatalog) -> None:
    """Print the path to the user-editable configuration file."""
    click.echo(catalog.config.path)


@cli.group("shell")
def shell_commands() -> None:
    """Install and manage shell integration."""


@shell_commands.command("install")
@click.argument(
    "shell",
    required=False,
    type=click.Choice([item.value for item in Shell], case_sensitive=False),
)
def install_shell(shell: str | None) -> None:
    """Install persistent shell integration into the shell startup file."""
    integration = _shell_integration(shell)
    if integration.install():
        click.echo(f"Installed {integration.shell.value} integration in {integration.config_path}")
        click.echo(f"Restart the shell or run: exec {integration.shell.value}")
        return

    state = integration.state()
    if state is ShellIntegrationState.MANAGED:
        click.echo(f"Shell integration is already installed in {integration.config_path}")
        return
    click.echo(
        f"Shell integration is already configured manually in {integration.config_path}; left unchanged."
    )


@shell_commands.command("status")
@click.argument(
    "shell",
    required=False,
    type=click.Choice([item.value for item in Shell], case_sensitive=False),
)
def shell_status(shell: str | None) -> None:
    """Show whether shell integration is configured and active."""
    integration = _shell_integration(shell)
    click.echo(f"Shell: {integration.shell.value}")
    click.echo(f"Config: {integration.config_path}")
    click.echo(f"Configured: {integration.state().value}")
    active = shell_integration_active()
    click.echo(f"Active in current shell: {'yes' if active else 'no'}")


@shell_commands.command("uninstall")
@click.argument(
    "shell",
    required=False,
    type=click.Choice([item.value for item in Shell], case_sensitive=False),
)
def uninstall_shell(shell: str | None) -> None:
    """Remove integration installed by `pcd shell install`."""
    integration = _shell_integration(shell)
    if integration.uninstall():
        click.echo(f"Removed shell integration from {integration.config_path}")
        return

    state = integration.state()
    if state is ShellIntegrationState.MANUAL:
        click.echo(f"Integration in {integration.config_path} is managed manually; left unchanged.")
        return
    click.echo(f"Shell integration is not installed in {integration.config_path}")


@shell_commands.command("print")
@click.argument(
    "shell",
    required=False,
    type=click.Choice([item.value for item in Shell], case_sensitive=False),
)
def print_shell_integration(shell: str | None) -> None:
    """Print shell integration for manual dotfile management."""
    selected = _selected_shell(shell)
    click.echo(render_shell_integration(selected), nl=False)


@cli.command("shell-init", hidden=True)
@click.argument("shell", type=click.Choice([item.value for item in Shell], case_sensitive=False))
def shell_init(shell: str) -> None:
    """Backward-compatible alias for `pcd shell print`."""
    click.echo(render_shell_integration(Shell(shell.casefold())), nl=False)


def _shell_integration(shell: str | None) -> ShellIntegration:
    return ShellIntegration.for_shell(_selected_shell(shell))


def _selected_shell(shell: str | None) -> Shell:
    if shell is not None:
        return Shell(shell.casefold())
    try:
        return detect_shell()
    except ShellIntegrationError as exc:
        raise click.UsageError(str(exc)) from exc


def main() -> None:
    """Console-script boundary for application-specific failures."""
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
