from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from typing import Self, TYPE_CHECKING

from pcd_cli.config import Config
from pcd_cli.filesystem import ApplicationPaths, canonical_path, file_lock
from pcd_cli.history import UsageHistory
from pcd_cli.models import ProjectSource, ProjectUsage
from pcd_cli.scanner import ProjectScanner

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping
    from pathlib import Path

    from pcd_cli.lookup import DirectoryEntry, LookupRequest, LookupResult, ProjectStore
    from pcd_cli.models import Project, ProjectSettings


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
            config=Config(paths.config, cache_path=paths.cache),
            cache=SQLiteProjectStore(paths.cache, config_path=paths.config),
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
        return self.lookup(LookupRequest(query, mode), include_directories=not exact_only)

    def lookup(
        self,
        request: LookupRequest,
        *,
        refresh_on_miss: bool = True,
        usage: Mapping[Path, ProjectUsage] | None = None,
        include_directories: bool = False,
    ) -> LookupResult:
        from pcd_cli.resolver import ProjectResolver

        history_revision: int | None = None

        def capture_revision() -> None:
            nonlocal history_revision
            if history_revision is None:
                from pcd_cli.visits import DirectoryVisits

                history_revision = DirectoryVisits(self.history.path).revision()

        def directories() -> Iterator[DirectoryEntry]:
            from itertools import chain
            from pathlib import Path

            from pcd_cli.visits import DirectoryVisits, is_excluded

            capture_revision()
            entries = DirectoryVisits(self.history.path).candidates(request.query)
            first = next(entries, None)
            if first is None:
                return
            excluded = self.config.load().excluded_projects
            has_exclusions = bool(excluded or os.environ.get("PCD_EXCLUDE_DIRS"))
            candidates = chain((first,), entries)
            if not has_exclusions:
                yield from candidates
                return
            for entry in candidates:
                if not is_excluded(Path(entry[0]), excluded):
                    yield entry

        if include_directories:
            from pcd_cli.visits import DirectoryVisits

            def candidate_usage(paths: Iterator[Path]) -> Mapping[Path, ProjectUsage]:
                capture_revision()
                return DirectoryVisits(self.history.path).usage_for(paths)

            # Each picker query needs its own candidate set; a previous narrowed
            # set cannot answer a later edit which broadens the query.
            request = replace(
                request,
                directory_entries=directories,
                usage_for=candidate_usage,
            )

        def has_configured_projects() -> bool:
            settings = self.config.load()
            return bool(settings.roots or settings.manual_projects)

        result = ProjectResolver(
            self.cache, self.refresh, self.history.load, has_configured_projects
        ).resolve(
            request,
            refresh_on_miss=refresh_on_miss,
            usage=usage,
        )
        if history_revision is not None:
            # Keep the revision captured before any candidates/usage were read:
            # a concurrent visit invalidates the next cursor, rather than making
            # stale ranking appear to belong to the new history generation.
            return replace(result, generation=(result.generation << 64) + history_revision)
        return result

    def directory_usage(self, query: str | None = None) -> dict[Path, ProjectUsage]:
        from pcd_cli.visits import DirectoryVisits, is_excluded

        entries = DirectoryVisits(self.history.path).entries(query)
        if not entries:
            return {}
        excluded = self.config.load().excluded_projects
        has_exclusions = bool(excluded or os.environ.get("PCD_EXCLUDE_DIRS"))
        return {
            path: ProjectUsage(visit.used_at, visit.count, visit.preference)
            for path, visit in entries.items()
            if not has_exclusions or not is_excluded(path, excluded)
        }

    def refresh(self) -> list[Project]:
        """Rebuild the catalog from config and scan, then publish it to the cache."""
        while True:
            started_at_ns = time.time_ns()
            settings = self.config.load()
            projects = self._build_projects(settings)
            published = self._publish(projects, settings, started_at_ns)
            if published is None:
                continue
            if published:
                return projects
            latest = self.cache.load()
            if latest is not None:
                return latest
            # A newer cache may disappear between rejection and this read.
            # Keep the completed scan usable, provided its settings still apply.
            with file_lock(self.config.path):
                if self.config.load() == settings:
                    return projects

    def _publish(
        self, projects: list[Project], settings: ProjectSettings, started_at_ns: int
    ) -> bool | None:
        # Lock order is always configuration -> cache. A writer which scanned
        # old settings must rescan, even if its refresh started more recently.
        from pcd_cli.storage import SQLiteProjectStore

        with file_lock(self.config.path):
            cache = self.cache
            if isinstance(cache, SQLiteProjectStore):
                cache = cache.capture_configuration()
            if self.config.load() != settings:
                return None
            published = cache.save(projects, started_at_ns=started_at_ns)
            if isinstance(cache, SQLiteProjectStore) and not cache.configuration_matches():
                # Editors are not obliged to acquire our lock. Persisting the
                # verified stamp keeps disk readers safe; retry also protects
                # the completed-scan fallback in the current lookup.
                return None
            return published

    def refresh_with_report(self) -> RefreshReport:
        """Rescan roots, persist the new snapshot, and diff it against the cache."""
        previous = {project.path for project in discovered(self.cache.load() or ())}
        projects = self.refresh()
        current = {project.path for project in discovered(projects)}
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

    def _refresh_after_change(self) -> None:
        try:
            self.refresh()
        except OSError as exc:
            raise self._partial_change_error(exc) from exc

    @staticmethod
    def _partial_change_error(exc: OSError) -> OSError:
        return OSError(
            "Configuration saved, but the project cache could not be rebuilt. "
            "The next ordinary lookup retries; "
            f"run `pcd refresh` after resolving the error: {exc}"
        )

    def _publish_after_change(
        self, projects: list[Project], settings: ProjectSettings, started_at_ns: int
    ) -> bool | None:
        try:
            return self._publish(projects, settings, started_at_ns)
        except OSError as exc:
            raise self._partial_change_error(exc) from exc

    def add_scan_root(self, root: Path) -> bool:
        if not self.config.add_root(root):
            return False

        self._refresh_after_change()

        return True

    def remove_scan_root(self, root: Path) -> bool:
        if not self.config.remove_root(root):
            return False

        self._refresh_after_change()

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
            self._refresh_after_change()
        else:
            projects.append(project)
            projects.sort(key=lambda item: (item.name.casefold(), str(item.path)))
            if not self._publish_after_change(projects, committed, started_at_ns):
                # A newer refresh may not include this manual registration yet.
                self._refresh_after_change()

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
                if committed != restored or not self._publish_after_change(
                    projects, committed, started_at_ns
                ):
                    self._refresh_after_change()
                return True
            latest = self.config.load()
            if latest == settings or project.path not in latest.excluded_projects:
                return False
            settings = latest

    def remove_project(self, path: Path) -> bool:
        if not self.config.remove_project(path):
            return False

        self._refresh_after_change()

        return True

    def remove_selected_project(self, project: Project) -> bool:
        """Deregister a manual selection or persistently exclude a discovered one."""
        if project.source is ProjectSource.MANUAL:
            return self.remove_project(project.path)
        if not self.config.exclude_project(project.path):
            return False
        self._refresh_after_change()
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
