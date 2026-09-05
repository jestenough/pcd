from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal, NamedTuple, TYPE_CHECKING

import click

from pcd_cli.filesystem import canonical_path, format_path
from pcd_cli.models import ExitCode, ProjectSource

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcd_cli.catalog import ProjectCatalog
    from pcd_cli.models import Project

type RootStatusLabel = Literal["available", "missing"]


class RootListRow(NamedTuple):
    path: str
    projects: str
    status: RootStatusLabel

    @classmethod
    def from_root(cls, root: Path, projects: Sequence[Project]) -> RootListRow:
        resolved_root = canonical_path(root)
        project_count = sum(
            project.path.is_relative_to(resolved_root)
            for project in projects
            if project.source is ProjectSource.DISCOVERED
        )

        return cls(
            path=format_path(root),
            projects=str(project_count),
            status="available" if root.is_dir() else "missing",
        )


class RootListTable:
    HEADERS: ClassVar[tuple[str, str, str]] = ("PATH", "PROJECTS", "STATUS")

    _rows: tuple[RootListRow, ...]

    def __init__(self, roots: Sequence[Path], projects: Sequence[Project]) -> None:
        self._rows = tuple(RootListRow.from_root(root, projects) for root in roots)

    def render(self) -> str:
        if not self._rows:
            return "No roots found."

        table = (self.HEADERS, *self._rows)
        columns = zip(*table, strict=True)
        widths = tuple(max(len(value) for value in column) for column in columns)

        return "\n".join(self._render_row(row, widths) for row in table)

    @staticmethod
    def _render_row(row: tuple[str, ...], widths: tuple[int, ...]) -> str:
        return "  ".join(
            value.ljust(width) for value, width in zip(row, widths, strict=True)
        ).rstrip()


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
@click.pass_obj
def roots(catalog: ProjectCatalog) -> None:
    """List registered scan roots with project counts and status."""
    settings = catalog.config.load()
    table = RootListTable(settings.roots, catalog.projects())
    click.echo(table.render())


@click.command()
@click.pass_obj
def refresh(catalog: ProjectCatalog) -> None:
    """Rescan roots and rebuild the project cache."""
    click.echo(f"Found {len(catalog.refresh())} projects.")


root_commands = (init, uninit, roots, refresh)
