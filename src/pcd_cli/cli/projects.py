from __future__ import annotations

from pathlib import Path
from typing import assert_never, ClassVar, Literal, NamedTuple, TYPE_CHECKING

import click

from pcd_cli.filesystem import canonical_path, format_path
from pcd_cli.models import ExitCode, Project, ProjectSource
from pcd_cli.navigation import navigate_to_project, project_completions, select_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcd_cli.catalog import ProjectCatalog

type ProjectSourceLabel = Literal["manual", "scanned"]
type ProjectStatusLabel = Literal["available", "missing"]


class ProjectListRow(NamedTuple):
    name: str
    path: str
    source: ProjectSourceLabel
    status: ProjectStatusLabel

    @classmethod
    def from_project(cls, project: Project) -> ProjectListRow:
        return cls(
            name=project.name,
            path=format_path(project.display_path),
            source=cls._source_label(project.source),
            status=cls._status_label(project.path),
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

    @staticmethod
    def _status_label(path: Path) -> ProjectStatusLabel:
        if path.is_dir():
            return "available"
        else:
            return "missing"


class ProjectListTable:
    HEADERS: ClassVar[tuple[str, str, str, str]] = ("NAME", "PATH", "SOURCE", "STATUS")

    _rows: tuple[ProjectListRow, ...]

    def __init__(self, projects: Sequence[Project]) -> None:
        self._rows = tuple(ProjectListRow.from_project(project) for project in projects)

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
@click.argument("query")
@click.pass_obj
def project(catalog: ProjectCatalog, query: str) -> None:
    """Resolve a project name routed by the root command."""
    navigate_to_project(catalog, query)


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

    selected = select_project(catalog, matches, query)
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
    table = ProjectListTable(catalog.projects())
    click.echo(table.render())


project_commands = (project, add, remove, list_projects)
