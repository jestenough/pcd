from __future__ import annotations

from dataclasses import dataclass
from typing import Self, TYPE_CHECKING

from pcd_cli.cache import ProjectCache
from pcd_cli.config import Config
from pcd_cli.filesystem import ApplicationPaths, canonical_path
from pcd_cli.history import UsageHistory
from pcd_cli.models import ProjectSource
from pcd_cli.scanner import ProjectScanner
from pcd_cli.search import rank_matches

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping
    from pathlib import Path

    from pcd_cli.models import Project, ProjectUsage


@dataclass(frozen=True, slots=True)
class RefreshReport:
    added: int
    removed: int
    total: int


@dataclass(frozen=True, slots=True)
class Resolution:
    """Ordered results and the usage snapshot shared with the picker."""

    matches: list[Project]
    usage: Mapping[Path, ProjectUsage] | None = None


def discovered(projects: Iterable[Project]) -> Iterator[Project]:
    """Yield projects discovered by the scanner, excluding manual registrations."""
    return (project for project in projects if project.source is ProjectSource.DISCOVERED)


@dataclass(frozen=True, slots=True)
class ProjectCatalog:
    """Coordinate project configuration, discovery, cache, and usage history."""

    config: Config
    cache: ProjectCache
    history: UsageHistory

    @classmethod
    def create(cls) -> Self:
        paths = ApplicationPaths.resolve()
        return cls(
            config=Config(paths.config),
            cache=ProjectCache(paths.cache),
            history=UsageHistory(paths.history),
        )

    def projects(self) -> list[Project]:
        if (cached := self.cache.load()) is not None:
            return cached

        return self.refresh()

    def completion_candidates(self, query: str) -> Iterator[Project]:
        matches = self.cache.find_prefix(query)
        if matches is not None:
            yield from matches

    def resolve(self, query: str, exact_only: bool = False) -> Resolution:
        """Resolve from cache; refresh and retry when no result is found."""
        # The common exact-hit path avoids loading the full cache and history.
        exact_matches = self.cache.find_exact(query)
        if exact_matches is None:
            return self._resolve_projects(self.refresh(), query, exact_only)

        if exact_matches:
            if len(exact_matches) == 1:
                return Resolution(exact_matches)
            usage = self.history.load()
            return Resolution(rank_matches(exact_matches, query, usage), usage)

        if exact_only:
            return self._resolve_projects(self.refresh(), query, True)

        # Only fuzzy fallback needs full Project objects.
        cached = self.cache.load()
        if cached is None:
            return self._resolve_projects(self.refresh(), query, False)

        usage = self.history.load()
        matches = rank_matches(cached, query, usage)
        if matches:
            return Resolution(matches, usage)

        # A miss can mean a repository appeared after the last refresh.
        return self._resolve_projects(self.refresh(), query, False, usage)

    def refresh(self) -> list[Project]:
        """Rebuild the catalog from config and scan, then publish it to the cache."""
        projects = self._build_projects()
        self.cache.save(projects)

        return projects

    def refresh_with_report(self) -> RefreshReport:
        """Rescan roots, persist the new snapshot, and diff it against the cache."""
        previous = {project.path for project in discovered(self.cache.load() or ())}
        projects = self._build_projects()
        current = {project.path for project in discovered(projects)}

        self.cache.save(projects)

        return RefreshReport(
            added=len(current - previous),
            removed=len(previous - current),
            total=len(projects),
        )

    def _build_projects(self) -> list[Project]:
        settings = self.config.load()
        projects = list(settings.manual_projects)
        known_paths = {project.path for project in projects}

        for project in ProjectScanner(settings).scan():
            if project.path in known_paths:
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

    def add_project(self, project: Project) -> bool:
        # An authoritative scan prevents stale cache data from hiding a repository.
        if any(item.path == project.path for item in self._build_projects()):
            return False

        if not self.config.add_project(project):
            return False

        # Rebuild from committed config instead of extending a potentially stale snapshot.
        self.refresh()

        return True

    def remove_project(self, path: Path) -> bool:
        if not self.config.remove_project(path):
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

    def _resolve_projects(
        self,
        projects: list[Project],
        query: str,
        exact_only: bool,
        usage: Mapping[Path, ProjectUsage] | None = None,
    ) -> Resolution:
        needle = query.casefold()
        exact_matches = [project for project in projects if project.name.casefold() == needle]
        if len(exact_matches) > 1:
            usage = self.history.load() if usage is None else usage
            return Resolution(rank_matches(exact_matches, query, usage), usage)
        if exact_matches or exact_only:
            return Resolution(exact_matches)

        usage = self.history.load() if usage is None else usage
        return Resolution(rank_matches(projects, query, usage), usage)
