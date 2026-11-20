"""Explicit, read-only checks. Never load through catalog refresh/recovery paths."""

from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

from pcd_cli.config import Config, InvalidConfigError
from pcd_cli.integrations.shell import ConfiguredShell, ShellIntegrationError, ShellIntegrationState
from pcd_cli.models import ProjectSource
from pcd_cli.storage import SQLiteProjectStore
from pcd_cli.visits import DirectoryVisits

if TYPE_CHECKING:
    from pathlib import Path

    from pcd_cli.filesystem import ApplicationPaths
    from pcd_cli.models import Project, ProjectSettings


@dataclass(frozen=True, slots=True)
class Finding:
    status: Literal["OK", "ISSUE", "UNKNOWN"]
    area: str
    message: str


def _directory(area: str, path: Path) -> Finding | None:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return Finding("ISSUE", area, f"Missing directory: {path}")
    except (OSError, ValueError) as exc:
        return Finding("ISSUE", area, f"Cannot access {path}: {exc}")
    if not stat.S_ISDIR(mode):
        return Finding("ISSUE", area, f"Not a directory: {path}")
    return None


def _configuration(path: Path, findings: list[Finding]) -> ProjectSettings | None:
    try:
        path.stat()
        settings = Config(path).load()
    except FileNotFoundError:
        findings.append(Finding("ISSUE", "Config", f"Not found: {path}"))
    except InvalidConfigError as exc:
        findings.append(Finding("ISSUE", "Config", str(exc)))
    except OSError as exc:
        findings.append(Finding("ISSUE", "Config", f"Cannot read {path}: {exc}"))
    else:
        findings.append(Finding("OK", "Config", str(path)))
        return settings
    return None


def _cache(path: Path, findings: list[Finding]) -> list[Project] | None:
    try:
        projects = SQLiteProjectStore(path).inspect()
    except FileNotFoundError:
        legacy = path.with_name("projects.jsonl")
        try:
            legacy.stat()
        except FileNotFoundError:
            message = f"Not found: {path}; run pcd refresh"
        except OSError as exc:
            message = f"Not found: {path}; cannot inspect legacy cache: {exc}"
        else:
            message = f"SQLite cache not found; legacy cache at {legacy}; run pcd refresh"
        findings.append(Finding("ISSUE", "Cache", message))
    except OSError as exc:
        findings.append(Finding("ISSUE", "Cache", f"Cannot read {path}: {exc}"))
    else:
        findings.append(Finding("OK", "Cache", f"{len(projects)} project(s); integrity verified"))
        return projects
    return None


def _history(path: Path) -> Finding:
    try:
        version, count = DirectoryVisits(path).inspect()
    except FileNotFoundError:
        legacy = path.with_name("history.json")
        try:
            legacy.stat()
        except FileNotFoundError:
            return Finding("OK", "History", "Not created yet")
        except OSError as exc:
            return Finding("ISSUE", "History", f"Cannot inspect legacy history: {exc}")
        return Finding("OK", "History", f"Legacy history at {legacy}; not migrated")
    except OSError as exc:
        return Finding("ISSUE", "History", f"Cannot read {path}: {exc}")
    migration = "; legacy schema retained" if version < 3 else ""
    return Finding("OK", "History", f"{count} visit record(s); integrity verified{migration}")


def _shell() -> Finding:
    try:
        integration = ConfiguredShell.detect()
    except ShellIntegrationError as exc:
        return Finding("UNKNOWN", "Shell", f"Cannot detect shell integration: {exc}")
    except OSError as exc:
        return Finding("ISSUE", "Shell", f"Cannot inspect shell integration: {exc}")
    try:
        state = integration.state()
        if state is ShellIntegrationState.ABSENT:
            return Finding(
                "ISSUE",
                "Shell",
                f"{integration.shell.value}: not installed in {integration.config_path}",
            )
        if issue := integration.installation_issue():
            return Finding("ISSUE", "Shell", issue)
        return Finding(
            "OK", "Shell", f"{integration.shell.value}: {state.value} in {integration.config_path}"
        )
    except (OSError, ShellIntegrationError) as exc:
        return Finding("ISSUE", "Shell", f"Cannot inspect shell integration: {exc}")


def _reconcile(settings: ProjectSettings, projects: list[Project]) -> list[Finding]:
    manual = {project.path: project for project in settings.manual_projects}
    cached = {project.path: project for project in projects}
    stale: list[str] = []
    for path, project in manual.items():
        entry = cached.get(path)
        if entry is None:
            stale.append(f"Manual project {project.name} absent from cache: {path}")
        elif entry.source is not ProjectSource.MANUAL or entry.name != project.name:
            stale.append(f"Manual project {project.name} mismatches cache: {path}")
    for project in projects:
        if project.source is ProjectSource.MANUAL and project.path not in manual:
            stale.append(f"Cached manual project no longer configured: {project.path}")
        elif (
            project.source is ProjectSource.DISCOVERED
            and project.path in settings.excluded_projects
        ):
            stale.append(f"Excluded project still cached: {project.path}")
    return [Finding("ISSUE", "Cache", f"{message}; run pcd refresh") for message in sorted(stale)]


def diagnose(paths: ApplicationPaths) -> list[Finding]:
    """Aggregate independent checks, preserving unavailable user references."""
    findings: list[Finding] = []
    settings = _configuration(paths.config, findings)
    projects = _cache(paths.cache, findings)
    findings.append(_history(paths.history))
    if settings is not None and projects is not None:
        findings.extend(_reconcile(settings, projects))
    if settings is not None:
        root_issues = [
            finding for root in settings.roots if (finding := _directory("Root", root)) is not None
        ]
        findings.extend(root_issues)
        findings.extend(
            finding
            for path in sorted(settings.excluded_projects, key=str)
            if (finding := _directory("Exclusion", path)) is not None
        )
        if not root_issues:
            findings.append(Finding("OK", "Roots", f"Checked {len(settings.roots)} root(s)"))
    else:
        findings.append(Finding("UNKNOWN", "Roots", "Configuration unavailable"))

    references = {project.path: project for project in projects or ()}
    if settings is not None:
        references.update((project.path, project) for project in settings.manual_projects)
    missing_projects = False
    for path, project in sorted(references.items(), key=lambda item: str(item[0])):
        if finding := _directory("Project", path):
            missing_projects = True
            findings.append(
                Finding(finding.status, finding.area, f"{project.name}: {finding.message}")
            )
    if settings is None or projects is None:
        findings.append(
            Finding(
                "UNKNOWN", "Projects", "Only available references checked; config/cache unavailable"
            )
        )
    elif not missing_projects:
        findings.append(
            Finding("OK", "Projects", f"Checked {len(references)} project reference(s)")
        )

    executable = shutil.which("pcd")
    findings.append(
        Finding("OK", "PATH", executable)
        if executable is not None
        else Finding("ISSUE", "PATH", "pcd executable not found")
    )
    findings.append(_shell())
    return findings
