from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from pcd_cli.filesystem import format_path
from pcd_cli.models import ExitCode

if TYPE_CHECKING:
    from pcd_cli.catalog import ProjectCatalog


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
    """List registered scan roots."""
    for root in catalog.config.load().roots:
        click.echo(format_path(root))


@click.command()
@click.pass_obj
def refresh(catalog: ProjectCatalog) -> None:
    """Rescan roots and rebuild the project cache."""
    click.echo(f"Found {len(catalog.refresh())} projects.")


root_commands = (init, uninit, roots, refresh)
