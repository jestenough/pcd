from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, TypeGuard

from pcd_cli.filesystem import atomic_write, file_lock
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


class _CacheReadError(Exception):
    """The on-disk cache cannot be read as a valid cache snapshot."""


class ProjectCacheEntry(TypedDict):
    name: str
    path: str
    display: str
    source: str


@dataclass(frozen=True, slots=True)
class ProjectCache:
    """Disposable JSONL index of known projects."""

    path: Path

    def load(self) -> list[Project] | None:
        if not self.path.is_file():
            return None

        try:
            return [_decode_project(entry) for entry in self._entries()]
        except _CacheReadError:
            return None

    def find_exact(self, query: str) -> list[Project] | None:
        if not self.path.is_file():
            return None

        needle = query.casefold()
        try:
            return [
                _decode_project(entry)
                for entry in self._entries()
                if entry["name"].casefold() == needle
            ]
        except _CacheReadError:
            return None

    def find_prefix(self, query: str) -> list[Project] | None:
        if not self.path.is_file():
            return None

        needle = query.casefold()
        try:
            return [
                _decode_project(entry)
                for entry in self._entries()
                if entry["name"].casefold().startswith(needle)
            ]
        except _CacheReadError:
            return None

    def save(self, projects: Iterable[Project]) -> None:
        # JSONL keeps refresh streaming and avoids a second in-memory JSON tree.
        with file_lock(self.path), atomic_write(self.path) as stream:
            for project in projects:
                entry: ProjectCacheEntry = {
                    "name": project.name,
                    "path": str(project.path),
                    "display": str(project.display_path),
                    "source": project.source.value,
                }
                json.dump(entry, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")

    def _entries(self) -> Iterator[ProjectCacheEntry]:
        try:
            with self.path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        raw: object = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise _CacheReadError("Invalid cache JSON") from exc
                    if not _is_cache_entry(raw):
                        raise _CacheReadError("Invalid cache entry")
                    yield raw
        except (OSError, UnicodeError) as exc:
            raise _CacheReadError("Cannot read cache") from exc


def _is_cache_entry(value: object) -> TypeGuard[ProjectCacheEntry]:
    if not isinstance(value, dict):
        return False

    return (
        isinstance(value.get("name"), str)
        and isinstance(value.get("path"), str)
        and isinstance(value.get("display"), str)
        and isinstance(value.get("source"), str)
    )


def _decode_project(entry: ProjectCacheEntry) -> Project:
    try:
        source = ProjectSource(entry["source"])
    except ValueError as exc:
        raise _CacheReadError("Invalid project source") from exc

    return Project(
        name=entry["name"],
        path=Path(entry["path"]),
        display_path=Path(entry["display"]),
        source=source,
    )
