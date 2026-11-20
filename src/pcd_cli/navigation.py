from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click
from click.shell_completion import CompletionItem

from pcd_cli.catalog import ProjectCatalog
from pcd_cli.display import terminal_text
from pcd_cli.filesystem import canonical_path, format_path
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
    resume: bool = False,
) -> None:
    result = catalog.resolve(query or "", exact_only=exact_only)
    if not result.matches:
        suffix = "" if query is None else f": {terminal_text(query)}"
        click.echo(f"Project not found{suffix}", err=True)
        raise click.exceptions.Exit(ExitCode.NOT_FOUND)

    selected = select_project(catalog, result, query or "", exact_only=exact_only)
    if selected is not None:
        _open_project(catalog, selected, resume=resume)


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

    usage = result.usage

    def resolve(request: LookupRequest) -> LookupResult:
        return catalog.lookup(
            request,
            refresh_on_miss=False,
            usage=usage,
            include_directories=not exact_only,
        )

    mode = LookupMode.EXACT if exact_only else LookupMode.RESOLVE
    return ProjectPicker(result, resolve, query, mode=mode).run()


def project_completions(value: str) -> list[CompletionItem]:
    names: set[str] = set()
    completions: list[CompletionItem] = []
    home = None

    for project in ProjectCatalog.create().completion_candidates(value):
        if project.name in names or terminal_text(project.name) != project.name:
            continue
        if home is None:
            home = Path.home()
        names.add(project.name)
        completions.append(
            CompletionItem(project.name, help=format_path(project.display_path, home=home))
        )

    return completions


def _open_project(catalog: ProjectCatalog, project: Project, *, resume: bool = False) -> None:
    destination = project.path
    if project.path.is_dir():
        if resume:
            from pcd_cli.visits import DirectoryVisits, is_excluded

            latest = DirectoryVisits(catalog.history.path).resume(project.path)
            if latest is not None:
                resolved = canonical_path(latest)
                if resolved != project.path and not resolved.is_relative_to(project.path):
                    reason = "is outside the project"
                elif is_excluded(resolved, catalog.config.load().excluded_projects):
                    reason = "is excluded"
                elif not resolved.is_dir():
                    reason = "no longer exists"
                else:
                    destination = resolved
                    reason = ""
                if reason:
                    click.echo(
                        f"Last visited subdirectory {reason}: {format_path(latest)}. "
                        "Opening project root.",
                        err=True,
                    )
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
        click.echo(destination)
        raise click.exceptions.Exit(SHELL_CD_EXIT_CODE)

    click.echo(terminal_text(str(destination)))
    click.echo(inactive_shell_message(), err=True)
