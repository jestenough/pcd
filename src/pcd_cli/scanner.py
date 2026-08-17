import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pcd_cli.filesystem import canonical_path
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pcd_cli.models import ProjectSettings


@dataclass(frozen=True, slots=True)
class ProjectScanner:
    settings: ProjectSettings

    def scan(self) -> Iterator[Project]:
        for root, display_path in self._scan_roots():
            yield from self._walk(root, display_path)

    def _scan_roots(self) -> list[tuple[Path, Path]]:
        roots = sorted(
            ((canonical_path(root), root.expanduser()) for root in self.settings.roots),
            key=lambda item: (len(item[0].parts), str(item[0])),
        )
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

    def _walk(self, root: Path, display_path: Path) -> Iterator[Project]:
        if not root.is_dir():
            return

        visited = set[tuple[int, int]]() if self.settings.follow_symlinks else None

        for directory, directories, _files in os.walk(
            root,
            topdown=True,
            followlinks=self.settings.follow_symlinks,
        ):
            current = Path(directory)
            active = True if visited is None else _mark_visited(current, visited)
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


def _mark_visited(path: Path, visited: set[tuple[int, int]]) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False

    key = (stat.st_dev, stat.st_ino)
    if key in visited:
        return False

    visited.add(key)
    return True


def _is_git_repository(path: Path) -> bool:
    return (path / ".git").exists()
