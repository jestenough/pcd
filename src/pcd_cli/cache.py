from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, TypeGuard

from pcd_cli.filesystem import atomic_write, file_lock
from pcd_cli.models import Discovery, Project, ProjectSource

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

CACHE_SCHEMA_VERSION = 2


class _CacheReadError(Exception):
    """The on-disk cache cannot be read as a valid cache snapshot."""


class CacheHeader(TypedDict):
    version: int


class DiscoveryCacheEntry(TypedDict):
    root: str
    display: str


class ProjectCacheEntry(TypedDict):
    name: str
    path: str
    display: str
    source: str
    discoveries: list[DiscoveryCacheEntry]


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
            header: CacheHeader = {"version": CACHE_SCHEMA_VERSION}
            json.dump(header, stream, separators=(",", ":"))
            stream.write("\n")

            for project in projects:
                entry: ProjectCacheEntry = {
                    "name": project.name,
                    "path": str(project.path),
                    "display": str(project.display_path),
                    "source": project.source.value,
                    "discoveries": [
                        {"root": str(discovery.root), "display": str(discovery.display_path)}
                        for discovery in project.discoveries
                    ],
                }
                json.dump(entry, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")

    def _entries(self) -> Iterator[ProjectCacheEntry]:
        try:
            with self.path.open(encoding="utf-8") as stream:
                _validate_header(_read_json(stream.readline()))
                for line in stream:
                    raw = _read_json(line)
                    if not _is_cache_entry(raw):
                        raise _CacheReadError("Invalid cache entry")
                    yield raw
        except (OSError, UnicodeError) as exc:
            raise _CacheReadError("Cannot read cache") from exc


def _read_json(line: str) -> object:
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise _CacheReadError("Invalid cache JSON") from exc


def _validate_header(value: object) -> None:
    if not isinstance(value, dict):
        raise _CacheReadError("Invalid cache header")

    version = value.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise _CacheReadError("Invalid cache version")
    if version != CACHE_SCHEMA_VERSION:
        raise _CacheReadError("Unsupported cache version")


def _is_cache_entry(value: object) -> TypeGuard[ProjectCacheEntry]:
    if not isinstance(value, dict):
        return False

    return (
        isinstance(value.get("name"), str)
        and isinstance(value.get("path"), str)
        and isinstance(value.get("display"), str)
        and isinstance(value.get("source"), str)
        and _is_discovery_list(value.get("discoveries"))
    )


def _is_discovery_list(value: object) -> bool:
    if not isinstance(value, list):
        return False

    return all(
        isinstance(item, dict)
        and isinstance(item.get("root"), str)
        and isinstance(item.get("display"), str)
        for item in value
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
        discoveries=tuple(
            Discovery(Path(item["root"]), Path(item["display"])) for item in entry["discoveries"]
        ),
    )
