from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Self, TYPE_CHECKING

from pcd_cli.config import Config
from pcd_cli.filesystem import ApplicationPaths, canonical_path
from pcd_cli.history import UsageHistory
from pcd_cli.models import ProjectSource
from pcd_cli.scanner import ProjectScanner

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping
    from pathlib import Path

    from pcd_cli.lookup import LookupRequest, LookupResult, ProjectStore
    from pcd_cli.models import Project, ProjectSettings, ProjectUsage


@dataclass(frozen=True, slots=True)
class RefreshReport:
    added: int
    removed: int
    total: int


def discovered(projects: Iterable[Project]) -> Iterator[Project]:
    """Yield projects discovered by the scanner, excluding manual registrations."""
    return (project for project in projects if project.source is ProjectSource.DISCOVERED)


def count_discovered_by_root(projects: Iterable[Project]) -> dict[Path, int]:
    """Count each project once per canonical root in a single pass."""
    resolved: dict[Path, Path] = {}
    counts: dict[Path, int] = {}
    for project in discovered(projects):
        roots: set[Path] = set()
        for discovery in project.discoveries:
            if discovery.root not in resolved:
                resolved[discovery.root] = canonical_path(discovery.root)
            roots.add(resolved[discovery.root])
        for root in roots:
            counts[root] = counts.get(root, 0) + 1
    return counts


@dataclass(frozen=True, slots=True)
class ProjectCatalog:
    """Coordinate project configuration, discovery, cache, and usage history."""

    config: Config
    cache: ProjectStore
    history: UsageHistory

    @classmethod
    def create(cls) -> Self:
        from pcd_cli.storage import SQLiteProjectStore

        paths = ApplicationPaths.resolve()
        return cls(
            config=Config(paths.config),
            cache=SQLiteProjectStore(paths.cache),
            history=UsageHistory(paths.history),
        )

    def projects(self) -> list[Project]:
        if (cached := self.cache.load()) is not None:
            return cached

        return self.refresh()

    def completion_candidates(self, query: str) -> Iterator[Project]:
        from pcd_cli.lookup import LookupMode, LookupRequest

        result = self.cache.lookup(LookupRequest(query, LookupMode.PREFIX, limit=None))
        if result is not None:
            yield from result.matches

    def resolve(self, query: str, exact_only: bool = False) -> LookupResult:
        from pcd_cli.lookup import LookupMode, LookupRequest

        mode = LookupMode.EXACT if exact_only else LookupMode.RESOLVE
        return self.lookup(LookupRequest(query, mode))

    def lookup(
        self,
        request: LookupRequest,
        *,
        refresh_on_miss: bool = True,
        usage: Mapping[Path, ProjectUsage] | None = None,
    ) -> LookupResult:
        from pcd_cli.resolver import ProjectResolver

        return ProjectResolver(self.cache, self.refresh, self.history.load).resolve(
            request,
            refresh_on_miss=refresh_on_miss,
            usage=usage,
        )

    def refresh(self) -> list[Project]:
        """Rebuild the catalog from config and scan, then publish it to the cache."""
        started_at_ns = time.time_ns()
        projects = self._build_projects()

        if self.cache.save(projects, started_at_ns=started_at_ns):
            return projects

        latest = self.cache.load()
        return projects if latest is None else latest

    def refresh_with_report(self) -> RefreshReport:
        """Rescan roots, persist the new snapshot, and diff it against the cache."""
        started_at_ns = time.time_ns()
        previous = {project.path for project in discovered(self.cache.load() or ())}
        projects = self._build_projects()
        current = {project.path for project in discovered(projects)}

        if not self.cache.save(projects, started_at_ns=started_at_ns):
            latest = self.cache.load()
            if latest is not None:
                return RefreshReport(added=0, removed=0, total=len(latest))

        return RefreshReport(
            added=len(current - previous),
            removed=len(previous - current),
            total=len(projects),
        )

    def _build_projects(self, settings: ProjectSettings | None = None) -> list[Project]:
        if settings is None:
            settings = self.config.load()

        projects = list(settings.manual_projects)
        known_paths = {project.path for project in projects}

        for project in ProjectScanner(settings).scan():
            if project.path in known_paths or project.path in settings.excluded_projects:
                continue

            known_paths.add(project.path)
            projects.append(project)

        projects.sort(key=lambda project: (project.name.casefold(), str(project.path)))

        return projects

    def record_usage(self, project: Project) -> None:
        self.history.record(project.path)

    def add_scan_root(self, root: Path) -> bool:
        if not self.config.add_root(root):
            return False

        self.refresh()

        return True

    def remove_scan_root(self, root: Path) -> bool:
        if not self.config.remove_root(root):
            return False

        self.refresh()

        return True

    def add_project(self, project: Project, *, explicit_name: bool = False) -> bool:
        # An authoritative scan prevents stale cache data from hiding a repository.
        started_at_ns = time.time_ns()
        settings = self.config.load()
        if project.path in settings.excluded_projects:
            return self._restore_project(project, settings, explicit_name=explicit_name)
        projects = self._build_projects(settings)

        if any(item.path == project.path for item in projects):
            return False

        committed = self.config.add_manual(project)
        if committed is None:
            return False

        expected = replace(settings, manual_projects=(*settings.manual_projects, project))
        if committed != expected:
            # Configuration changed while scanning; the old snapshot is not safe to publish.
            self.refresh()
        else:
            projects.append(project)
            projects.sort(key=lambda item: (item.name.casefold(), str(item.path)))
            if not self.cache.save(projects, started_at_ns=started_at_ns):
                # A newer refresh may not include this manual registration yet.
                self.refresh()

        return True

    def _restore_project(
        self, project: Project, settings: ProjectSettings, *, explicit_name: bool
    ) -> bool:
        while True:
            started_at_ns = time.time_ns()
            restored = replace(
                settings, excluded_projects=settings.excluded_projects - {project.path}
            )
            projects = self._build_projects(restored)
            manual = explicit_name or not any(item.path == project.path for item in projects)
            if manual:
                restored = replace(restored, manual_projects=(*restored.manual_projects, project))
                projects = [item for item in projects if item.path != project.path]
                projects.append(project)
                projects.sort(key=lambda item: (item.name.casefold(), str(item.path)))
            committed = self.config.restore_project(project, manual=manual, expected=settings)
            if committed is not None:
                if committed != restored or not self.cache.save(
                    projects, started_at_ns=started_at_ns
                ):
                    self.refresh()
                return True
            latest = self.config.load()
            if latest == settings or project.path not in latest.excluded_projects:
                return False
            settings = latest

    def remove_project(self, path: Path) -> bool:
        if not self.config.remove_project(path):
            return False

        self.refresh()

        return True

    def remove_selected_project(self, project: Project) -> bool:
        """Deregister a manual selection or persistently exclude a discovered one."""
        if project.source is ProjectSource.MANUAL:
            return self.remove_project(project.path)
        if not self.config.exclude_project(project.path):
            return False
        self.refresh()
        return True

    def find_parent_root(self, path: Path) -> Path | None:
        target = canonical_path(path)
        closest_root: Path | None = None
        closest_depth = -1

        for root in self.config.load().roots:
            resolved_root = canonical_path(root)
            if resolved_root == target or not target.is_relative_to(resolved_root):
                continue

            root_depth = len(resolved_root.parts)
            if root_depth > closest_depth:
                closest_root = root
                closest_depth = root_depth

        return closest_root
