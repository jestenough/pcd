from __future__ import annotations

from typing import TYPE_CHECKING

import click
from click.shell_completion import CompletionItem

from pcd_cli.catalog import ProjectCatalog
from pcd_cli.display import terminal_text
from pcd_cli.filesystem import format_path
from pcd_cli.integrations.shell import (
    inactive_shell_message,
    SHELL_CD_EXIT_CODE,
    shell_integration_active,
)
from pcd_cli.models import ExitCode, ProjectSource

if TYPE_CHECKING:
    from pcd_cli.lookup import LookupRequest, LookupResult
    from pcd_cli.models import Project


def navigate_to_project(
    catalog: ProjectCatalog,
    query: str | None = None,
    exact_only: bool = False,
) -> None:
    result = catalog.resolve(query or "", exact_only=exact_only)
    if not result.matches:
        suffix = "" if query is None else f": {terminal_text(query)}"
        click.echo(f"Project not found{suffix}", err=True)
        raise click.exceptions.Exit(ExitCode.NOT_FOUND)

    selected = select_project(catalog, result, query or "", exact_only=exact_only)
    if selected is not None:
        _open_project(catalog, selected)


def select_project(
    catalog: ProjectCatalog,
    result: LookupResult,
    query: str = "",
    *,
    exact_only: bool = False,
) -> Project | None:
    if result.total == 1:
        return result.matches[0]
    # Import lazily so completion never initializes terminal UI machinery.
    from pcd_cli.lookup import LookupMode
    from pcd_cli.picker import ProjectPicker

    usage = catalog.history.load() if result.usage is None else result.usage

    def resolve(request: LookupRequest) -> LookupResult:
        return catalog.lookup(request, refresh_on_miss=False, usage=usage)

    mode = LookupMode.EXACT if exact_only else LookupMode.RESOLVE
    return ProjectPicker(result, resolve, query, mode=mode).run()


def project_completions(value: str) -> list[CompletionItem]:
    names: set[str] = set()
    completions: list[CompletionItem] = []

    for project in ProjectCatalog.create().completion_candidates(value):
        if project.name in names or terminal_text(project.name) != project.name:
            continue
        names.add(project.name)
        completions.append(CompletionItem(project.name, help=format_path(project.display_path)))

    return completions


def _open_project(catalog: ProjectCatalog, project: Project) -> None:
    if not project.path.is_dir():
        if project.source is ProjectSource.MANUAL:
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

    click.echo(terminal_text(str(project.path)))
    click.echo(inactive_shell_message(), err=True)
