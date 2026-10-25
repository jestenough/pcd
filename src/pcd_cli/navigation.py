from __future__ import annotations

from typing import TYPE_CHECKING

import click
from click.shell_completion import CompletionItem

from pcd_cli.catalog import ProjectCatalog
from pcd_cli.filesystem import format_path
from pcd_cli.integrations.shell import (
    inactive_shell_message,
    SHELL_CD_EXIT_CODE,
    shell_integration_active,
)
from pcd_cli.models import ExitCode, ProjectSource

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from pcd_cli.models import Project, ProjectUsage


def navigate_to_project(
    catalog: ProjectCatalog,
    query: str | None = None,
    exact_only: bool = False,
) -> None:
    result = None if query is None else catalog.resolve(query, exact_only=exact_only)
    matches = catalog.projects() if result is None else result.matches
    if not matches:
        suffix = "" if query is None else f": {query}"
        click.echo(f"Project not found{suffix}", err=True)
        raise click.exceptions.Exit(ExitCode.NOT_FOUND)

    selected = select_project(
        catalog,
        matches,
        query or "",
        usage=None if result is None else result.usage,
        ranked=result is not None,
    )
    if selected is not None:
        _open_project(catalog, selected)


def select_project(
    catalog: ProjectCatalog,
    projects: Sequence[Project],
    query: str = "",
    *,
    usage: Mapping[Path, ProjectUsage] | None = None,
    ranked: bool = False,
) -> Project | None:
    if len(projects) == 1:
        return projects[0]
    else:
        # Import lazily so shell completion never initializes terminal UI machinery.
        from pcd_cli.picker import ProjectPicker

        return ProjectPicker(
            projects,
            catalog.history.load() if usage is None else usage,
            query,
            ranked=ranked,
        ).run()


def project_completions(value: str) -> list[CompletionItem]:
    names: set[str] = set()
    completions: list[CompletionItem] = []

    for project in ProjectCatalog.create().completion_candidates(value):
        if project.name in names:
            continue
        names.add(project.name)
        completions.append(CompletionItem(project.name, help=format_path(project.display_path)))

    return completions


def _open_project(catalog: ProjectCatalog, project: Project) -> None:
    if project.path.is_dir():
        try:
            catalog.record_usage(project)
        except OSError as exc:
            click.echo(f"Warning: could not write usage history: {exc}", err=True)
    elif project.source is ProjectSource.MANUAL:
        click.echo(
            f"Project path no longer exists: {format_path(project.display_path)}",
            err=True,
        )
        raise click.exceptions.Exit(ExitCode.ERROR)
    else:
        catalog.refresh()
        click.echo("Selected project no longer exists.", err=True)
        raise click.exceptions.Exit(ExitCode.ERROR)

    if shell_integration_active():
        click.echo(project.path)
        raise click.exceptions.Exit(SHELL_CD_EXIT_CODE)

    click.echo(project.path)
    click.echo(inactive_shell_message(), err=True)
