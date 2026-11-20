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


@click.group("import")
def import_commands() -> None:
    """Import navigation history from another tool."""


@import_commands.command()
@click.option("--dry-run", is_flag=True, help="Preview the merge without writing history.")
@click.pass_obj
def zoxide(catalog: ProjectCatalog, dry_run: bool) -> None:
    """Import directory preferences from zoxide query --list --score."""
    import math
    import subprocess

    from pcd_cli.visits import DirectoryVisits, is_excluded

    try:
        result = subprocess.run(
            ["zoxide", "query", "--list", "--score", "--all"],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as exc:
        raise click.ClickException("Cannot find zoxide on PATH") from exc
    except UnicodeError as exc:
        raise click.ClickException("Invalid UTF-8 from zoxide") from exc
    if result.returncode:
        raise click.ClickException(f"zoxide query failed (exit {result.returncode})")
    excluded = catalog.config.load().excluded_projects
    scores: dict[Path, float] = {}
    for number, line in enumerate(result.stdout.split("\n"), 1):
        if not line.strip():
            continue
        try:
            value, name = line.lstrip().split(maxsplit=1)
            score = float(value)
            path = Path(name)
            if not math.isfinite(score) or score < 0 or not path.is_absolute():
                raise ValueError
            path = path.resolve()
        except ValueError as exc:
            raise click.ClickException(f"Invalid zoxide entry on line {number}") from exc
        if not is_excluded(path, excluded):
            scores[path] = max(score, scores.get(path, 0))
    if not dry_run:
        DirectoryVisits(catalog.history.path).merge(scores)
    action = "Would merge" if dry_run else "Merged"
    click.echo(f"{action} {len(scores)} directories.")
