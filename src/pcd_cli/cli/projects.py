from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from pcd_cli.filesystem import canonical_path, format_path
from pcd_cli.models import ExitCode, Project, ProjectSource
from pcd_cli.navigation import navigate_to_project, project_completions, select_project

if TYPE_CHECKING:
    from pcd_cli.catalog import ProjectCatalog


@click.command(hidden=True)
@click.argument("query")
@click.pass_obj
def project(catalog: ProjectCatalog, query: str) -> None:
    """Resolve a project name routed by the root command."""
    navigate_to_project(catalog, query)


@click.command()
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


@click.command()
@click.pass_obj
def uninit(catalog: ProjectCatalog) -> None:
    """Remove the current directory from scan roots."""
    current = Path.cwd()
    if catalog.remove_scan_root(current):
        click.echo(f"Removed root: {format_path(current)}")
        return

    click.echo("Current directory is not a pcd root.", err=True)
    raise click.exceptions.Exit(ExitCode.ERROR)


@click.command()
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


@click.command()
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


@click.command("list")
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


@click.command()
@click.pass_obj
def roots(catalog: ProjectCatalog) -> None:
    """List registered scan roots."""
    for root in catalog.config.load().roots:
        click.echo(format_path(root))


@click.command()
@click.pass_obj
def refresh(catalog: ProjectCatalog) -> None:
    """Rescan roots and rebuild the project cache."""
    click.echo(f"Found {len(catalog.refresh())} projects.")


project_commands = (project, init, uninit, add, remove, list_projects, roots, refresh)
