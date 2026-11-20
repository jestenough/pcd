from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import assert_never, ClassVar, Literal, NamedTuple, TYPE_CHECKING

import click

from pcd_cli.display import terminal_text
from pcd_cli.filesystem import canonical_path, format_path
from pcd_cli.models import ExitCode, Project, ProjectSource
from pcd_cli.navigation import navigate_to_project, project_completions, select_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcd_cli.catalog import ProjectCatalog

type ProjectSourceLabel = Literal["manual", "scanned"]


class ProjectStatus(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"

    @classmethod
    def for_path(cls, path: Path) -> ProjectStatus:
        return cls.AVAILABLE if path.is_dir() else cls.MISSING


class ProjectListRow(NamedTuple):
    name: str
    path: str
    source: ProjectSourceLabel
    status: ProjectStatus

    @classmethod
    def from_project(cls, project: Project) -> ProjectListRow:
        return cls(
            name=terminal_text(project.name),
            path=format_path(project.display_path),
            source=cls._source_label(project.source),
            status=ProjectStatus.for_path(project.path),
        )

    @staticmethod
    def _source_label(source: ProjectSource) -> ProjectSourceLabel:
        match source:
            case ProjectSource.DISCOVERED:
                return "scanned"
            case ProjectSource.MANUAL:
                return "manual"
            case _:
                assert_never(source)


class ProjectListTable:
    HEADERS: ClassVar[tuple[str, str, str, str]] = ("NAME", "PATH", "SOURCE", "STATUS")

    _rows: tuple[ProjectListRow, ...]

    def __init__(self, projects: Sequence[Project]) -> None:
        self._rows = tuple(ProjectListRow.from_project(project) for project in projects)

    @classmethod
    def from_rows(cls, rows: Sequence[ProjectListRow]) -> ProjectListTable:
        table = cls(())
        table._rows = tuple(rows)
        return table

    def render(self) -> str:
        if self._rows:
            return self._render_table()
        else:
            return "No projects found."

    def _render_table(self) -> str:
        table = (self.HEADERS, *self._rows)
        columns = zip(*table, strict=True)
        widths = tuple(max(len(value) for value in column) for column in columns)

        return "\n".join(self._render_row(row, widths) for row in table)

    @staticmethod
    def _render_row(row: tuple[str, ...], widths: tuple[int, ...]) -> str:
        return "  ".join(
            value.ljust(width) for value, width in zip(row, widths, strict=True)
        ).rstrip()


@click.command(hidden=True)
@click.argument("words", nargs=-1, required=True)
@click.option("--resume", is_flag=True, help="Open the last visited subdirectory of the project.")
@click.pass_obj
def project(catalog: ProjectCatalog, words: tuple[str, ...], resume: bool) -> None:
    """Resolve a project name routed by the root command."""
    navigate_to_project(catalog, " ".join(words), resume=resume)


@click.command()
@click.argument("path", type=click.Path(path_type=Path, file_okay=False), required=False)
@click.option("--name", help="Custom name for a manual project.")
@click.pass_obj
def add(catalog: ProjectCatalog, path: Path | None, name: str | None) -> None:
    """Register PATH (default: current directory), or restore an excluded project."""
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
    if catalog.add_project(item, explicit_name=name is not None):
        # A restored checkout may be discovered under a different symlink name.
        destination = format_path(item.display_path)
        if name is not None:
            destination = f"{terminal_text(item.name)} -> {destination}"
        click.echo(f"Added project: {destination}")
        return

    click.echo(
        f"Project already exists: {terminal_text(item.name)} -> {format_path(item.display_path)}"
    )


@click.command()
@click.argument(
    "query",
    shell_complete=lambda _ctx, _param, value: project_completions(value),
)
@click.pass_obj
def remove(catalog: ProjectCatalog, query: str) -> None:
    """Unregister a manual project or exclude a discovered one. Keep its directory."""
    selected = _select_named_project(catalog, query)
    if selected is None:
        return
    if catalog.remove_selected_project(selected):
        action = "Excluded" if selected.source is ProjectSource.DISCOVERED else "Removed"
        click.echo(f"{action} project: {terminal_text(selected.name)}")
        return
    click.echo("Project is no longer registered.", err=True)
    raise click.exceptions.Exit(ExitCode.ERROR)


def _select_named_project(catalog: ProjectCatalog, query: str) -> Project | None:
    result = catalog.resolve(query, exact_only=True)
    if not result.matches:
        click.echo(f"Project not found: {terminal_text(query)}", err=True)
        raise click.exceptions.Exit(ExitCode.NOT_FOUND)
    return select_project(catalog, result, query, exact_only=True)


@click.command()
@click.argument("query", shell_complete=lambda _ctx, _param, value: project_completions(value))
@click.pass_obj
def info(catalog: ProjectCatalog, query: str) -> None:
    """Show a project's paths, source, discovery routes and current status."""
    selected = _select_named_project(catalog, query)
    if selected is None:
        return

    click.echo(f"Name: {terminal_text(selected.name)}")
    click.echo(f"Path: {terminal_text(str(selected.path))}")
    if selected.display_path != selected.path:
        click.echo(f"Display path: {terminal_text(str(selected.display_path))}")
    click.echo(f"Source: {selected.source.value}")
    click.echo(f"Status: {ProjectStatus.for_path(selected.path)}")
    for route in selected.discoveries:
        click.echo(f"Root: {format_path(route.root)} -> {format_path(route.display_path)}")


@click.command("list")
@click.option("--manual", is_flag=True, help="Show manually registered projects.")
@click.option("--discovered", is_flag=True, help="Show automatically discovered projects.")
@click.option("--missing", is_flag=True, help="Show projects whose directories are missing.")
@click.option("--json", "as_json", is_flag=True, help="Print projects as JSON.")
@click.pass_obj
def list_projects(
    catalog: ProjectCatalog,
    manual: bool,
    discovered: bool,
    missing: bool,
    as_json: bool,
) -> None:
    """List and filter known projects."""
    entries: list[tuple[Project, ProjectListRow]] = []
    for project in catalog.projects():
        source_matches = (
            not (manual or discovered)
            or (manual and project.source is ProjectSource.MANUAL)
            or (discovered and project.source is ProjectSource.DISCOVERED)
        )
        if not source_matches:
            continue

        row = ProjectListRow.from_project(project)
        if missing and row.status is not ProjectStatus.MISSING:
            continue

        entries.append((project, row))

    if as_json:
        items = [
            {
                "name": project.name,
                "path": str(project.display_path),
                "source": project.source.value,
                "status": row.status,
            }
            for project, row in entries
        ]
        click.echo(json.dumps(items, indent=2))
        return

    table = ProjectListTable.from_rows(tuple(row for _, row in entries))
    click.echo(table.render())


project_commands = (project, add, remove, info, list_projects)
