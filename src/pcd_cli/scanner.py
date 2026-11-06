from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from pcd_cli.filesystem import canonical_path
from pcd_cli.models import Discovery, Project, ProjectSource

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pcd_cli.models import ProjectSettings


class ScanError(OSError):
    """A scan could not complete because filesystem paths failed."""

    def __init__(self, errors: list[tuple[Path, OSError]]) -> None:
        self.errors = tuple(errors)
        path, error = self.errors[0]
        suffix = f" ({len(self.errors)} filesystem errors)" if len(self.errors) > 1 else ""
        super().__init__(f"Scan incomplete at {path}: {error}{suffix}")


@dataclass(frozen=True, slots=True)
class ProjectScanner:
    settings: ProjectSettings

    def scan(self) -> Iterator[Project]:
        projects: dict[Path, Project] = {}
        roots = self._all_roots()
        selected = self._scan_roots(roots)

        # Walk nested roots once through their selected ancestor, then project
        # the same discovery routes onto every root below that ancestor.
        for root, display_path in selected:
            for project in self._walk(root, display_path):
                discovery = Discovery(display_path, project.display_path)
                existing = projects.get(project.path)
                if existing is None:
                    projects[project.path] = replace(project, discoveries=(discovery,))
                elif discovery not in existing.discoveries:
                    projects[project.path] = replace(
                        existing,
                        discoveries=(*existing.discoveries, discovery),
                    )

        self._add_nested_discoveries(projects, roots, selected)

        yield from projects.values()

    def _all_roots(self) -> list[tuple[Path, Path]]:
        roots = sorted(
            ((canonical_path(root), root.expanduser()) for root in self.settings.roots),
            key=lambda item: (len(item[0].parts), str(item[0]), str(item[1])),
        )
        return list(dict.fromkeys(roots))

    def _scan_roots(self, roots: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
        selected: dict[Path, Path] = {}

        for root, display_path in roots:
            parent = next((item for item in root.parents if item in selected), None)
            if parent is not None and self._reachable_from_parent(root, parent):
                continue
            selected[root] = display_path

        return list(selected.items())

    def _reachable_from_parent(self, root: Path, parent: Path) -> bool:
        relative = root.relative_to(parent)
        current = parent

        for part in relative.parts:
            blocked = part in self.settings.excluded_names
            hidden = not self.settings.include_hidden and part.startswith(".")
            if blocked or hidden or _is_git_repository(current):
                return False
            current /= part

        return True

    def _add_nested_discoveries(
        self,
        projects: dict[Path, Project],
        roots: list[tuple[Path, Path]],
        selected: list[tuple[Path, Path]],
    ) -> None:
        if len(selected) == len(roots):
            return

        selected_set = set(selected)
        selected_by_display = {display: canonical for canonical, display in selected}
        selected_canonical = set(selected_by_display.values())
        nested: dict[Path, list[tuple[Path, Path]]] = {}

        for canonical, display in roots:
            if (canonical, display) in selected_set:
                continue

            for ancestor in (canonical, *canonical.parents):
                if ancestor in selected_canonical:
                    relative = Path() if canonical == ancestor else canonical.relative_to(ancestor)
                    nested.setdefault(ancestor, []).append((relative, display))
                    break

        if not nested:
            return

        for path, project in projects.items():
            additions: list[Discovery] = []
            for discovery in project.discoveries:
                selected_ancestor = selected_by_display.get(discovery.root)
                if selected_ancestor is None:
                    continue

                route = discovery.display_path.relative_to(discovery.root)
                candidates = nested.get(selected_ancestor)
                if candidates is None:
                    continue

                for root_relative, root_display in candidates:
                    if root_relative.parts:
                        if not route.is_relative_to(root_relative):
                            continue
                        nested_route = route.relative_to(root_relative)
                    else:
                        nested_route = route

                    addition = Discovery(root_display, root_display / nested_route)
                    if addition not in project.discoveries and addition not in additions:
                        additions.append(addition)

            if additions:
                projects[path] = replace(
                    project,
                    discoveries=(*project.discoveries, *additions),
                )

    def _walk(self, root: Path, display_path: Path) -> Iterator[Project]:
        if not root.is_dir():
            return

        visited = set[tuple[int, int]]() if self.settings.follow_symlinks else None
        errors: list[tuple[Path, OSError]] = []

        def record_error(error: OSError) -> None:
            path = Path(error.filename) if error.filename is not None else root
            errors.append((path, error))

        for directory, directories, _files in os.walk(
            root,
            topdown=True,
            followlinks=self.settings.follow_symlinks,
            onerror=record_error,
        ):
            current = Path(directory)
            active = True if visited is None else _mark_visited(current, visited, errors)
            if not active:
                directories.clear()
                continue

            relative = current.relative_to(root)
            shown = display_path / relative

            if _is_git_repository(current):
                directories.clear()
                path = canonical_path(current) if self.settings.follow_symlinks else current
                yield Project(current.name, path, shown, ProjectSource.DISCOVERED)
                continue

            descend_into: list[str] = []
            for name in directories:
                child = current / name
                blocked = name == ".git" or name in self.settings.excluded_names
                hidden = not self.settings.include_hidden and name.startswith(".")
                linked = not self.settings.follow_symlinks and child.is_symlink()

                if blocked or hidden:
                    continue
                if linked:
                    if _is_git_repository(child):
                        yield Project(
                            name,
                            canonical_path(child),
                            shown / name,
                            ProjectSource.DISCOVERED,
                        )
                    continue
                descend_into.append(name)

            # os.walk descends only into names left in directories when topdown=True.
            directories[:] = descend_into

        if errors:
            raise ScanError(errors)


def _mark_visited(
    path: Path,
    visited: set[tuple[int, int]],
    errors: list[tuple[Path, OSError]] | None = None,
) -> bool:
    try:
        stat = path.stat()
    except OSError as exc:
        if errors is not None:
            errors.append((path, exc))
        return False

    key = (stat.st_dev, stat.st_ino)
    if key in visited:
        return False

    visited.add(key)
    return True


def _is_git_repository(path: Path) -> bool:
    return (path / ".git").exists()
