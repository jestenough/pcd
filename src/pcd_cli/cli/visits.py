from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from pcd_cli.catalog import ProjectCatalog


@click.command("_visit", hidden=True)
@click.option("--project-only", is_flag=True)
@click.pass_obj
def visit(catalog: ProjectCatalog, project_only: bool) -> None:
    """Record the shell's actual working directory after a successful change."""
    from pcd_cli.visits import DirectoryVisits, is_excluded

    path = Path.cwd().resolve()
    try:
        if is_excluded(path, catalog.config.load().excluded_projects):
            return
        if project_only:
            catalog.history.record(path)
        else:
            DirectoryVisits(catalog.history.path).record(path)
    except OSError as exc:
        from pcd_cli.display import terminal_text

        click.echo(f"Warning: could not write usage history: {terminal_text(str(exc))}", err=True)
