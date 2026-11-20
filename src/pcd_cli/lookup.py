"""Storage-independent lookup, ranking and bounded result windows."""

from __future__ import annotations

import heapq
import os
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import chain, islice
from pathlib import Path
from typing import Protocol, TYPE_CHECKING

from pcd_cli.models import Project, ProjectSource, ProjectUsage
from pcd_cli.search import score_path

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from pcd_cli.search import ProjectRank

type UsageLoader = Callable[[], Mapping[Path, ProjectUsage]]
type CandidateUsageLoader = Callable[[Iterator[Path]], Mapping[Path, ProjectUsage]]
type DirectoryLoader = Callable[[], Iterator[DirectoryEntry]]
RESULT_LIMIT = 100


class LookupMode(StrEnum):
    RESOLVE = "resolve"
    EXACT = "exact"
    PREFIX = "prefix"


@dataclass(frozen=True, slots=True)
class LookupRequest:
    query: str
    mode: LookupMode = LookupMode.RESOLVE
    limit: int | None = RESULT_LIMIT
    after: ProjectRank | None = None
    before: ProjectRank | None = None
    last: bool = False
    directories: UsageLoader | None = None
    usage_for: CandidateUsageLoader | None = None
    directory_entries: DirectoryLoader | None = None

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit < 1:
            raise ValueError("lookup limit must be positive")
        if sum((self.after is not None, self.before is not None, self.last)) > 1:
            raise ValueError("lookup accepts one window direction")


@dataclass(frozen=True, slots=True)
class LookupEntry[Reference]:
    reference: Reference
    name_key: str
    name_length: int
    path: str


# Keep SQLite rows in their native representation until the ranked window is known.
type DirectoryEntry = tuple[str, str, int, float, float, str]


@dataclass(frozen=True, slots=True)
class LookupResult:
    matches: list[Project]
    total: int
    offset: int = 0
    usage: Mapping[Path, ProjectUsage] | None = None
    first_rank: ProjectRank | None = None
    last_rank: ProjectRank | None = None
    generation: int = 0


class ProjectSnapshot[Reference](Protocol):
    @property
    def generation(self) -> int: ...

    def records(self) -> Iterator[LookupEntry[Reference]]: ...

    def exact(self, key: str) -> Iterator[LookupEntry[Reference]]: ...

    def prefix(self, key: str) -> Iterator[LookupEntry[Reference]]:
        """Yield matching metadata ordered by casefolded name and path."""
        ...

    def read(self, entry: LookupEntry[Reference]) -> Project: ...


class ProjectStore(Protocol):
    @property
    def path(self) -> Path: ...

    def load(self) -> list[Project] | None: ...

    def save(self, projects: Iterable[Project], *, started_at_ns: int | None = None) -> bool: ...

    def lookup(
        self, request: LookupRequest, load_usage: UsageLoader | None = None
    ) -> LookupResult | None: ...


def lookup_snapshot[Reference](
    snapshot: ProjectSnapshot[Reference],
    request: LookupRequest,
    load_usage: UsageLoader | None = None,
) -> LookupResult:
    try:
        return _lookup_snapshot(snapshot, request, load_usage)
    except OSError as exc:
        import sys

        from pcd_cli.display import terminal_text
        from pcd_cli.visits import DirectoryHistoryError

        if not isinstance(exc, DirectoryHistoryError):
            raise
        print(
            f"Warning: {terminal_text(str(exc))}. Searching registered projects only.",
            file=sys.stderr,
        )
        project_request = replace(request, directories=None, directory_entries=None, usage_for=None)
        result = _lookup_snapshot(snapshot, project_request, None)
        # Invalidate cursors from the previous, history-ranked result set.
        return replace(result, generation=~snapshot.generation)


def _lookup_snapshot[Reference](
    snapshot: ProjectSnapshot[Reference],
    request: LookupRequest,
    load_usage: UsageLoader | None,
) -> LookupResult:
    needle = request.query.casefold()
    if (
        request.mode is LookupMode.PREFIX
        and request.limit is None
        and request.after is None
        and request.before is None
        and not request.last
    ):
        # Completion returns the store's name/path order, without ranking every
        # candidate or retaining a second full catalog of metadata.
        matches = [snapshot.read(entry) for entry in snapshot.prefix(needle)]
        return LookupResult(matches, len(matches), generation=snapshot.generation)
    if request.mode is LookupMode.PREFIX:
        return _rank_window(snapshot, request, snapshot.prefix(needle), None)

    if needle or request.mode is LookupMode.EXACT:
        exact = snapshot.exact(needle)
        first = next(exact, None)
        if first is not None:
            return _rank_window(snapshot, request, chain((first,), exact), load_usage)
        if request.mode is LookupMode.EXACT:
            return LookupResult([], 0, generation=snapshot.generation)

    directories = None
    if request.directory_entries is not None:
        directories = request.directory_entries()
    elif request.directories is not None:
        directories = (
            (
                str(path),
                path.name.casefold(),
                usage.count,
                usage.used_at,
                usage.preference,
                os.path.normcase(str(path)),
            )
            for path, usage in request.directories().items()
        )
    return _rank_window(snapshot, request, snapshot.records(), load_usage, directories)


def _rank_window[Reference](
    snapshot: ProjectSnapshot[Reference],
    request: LookupRequest,
    entries: Iterator[LookupEntry[Reference]],
    load_usage: UsageLoader | None,
    directories: Iterator[DirectoryEntry] | None = None,
) -> LookupResult:
    needle = request.query.casefold()
    words = needle.split()
    recent = not needle and request.mode is LookupMode.RESOLVE
    # Retain identities only if we need to suppress learned copies of projects.
    first_directory = next(directories, None) if directories is not None else None
    registered: set[str] = set()

    def scored_projects() -> Iterator[tuple[tuple[int, int], LookupEntry[Reference]]]:
        for entry in entries:
            if first_directory is not None:
                registered.add(os.path.normcase(entry.path))
            score = (0, 0) if recent else score_path(needle, entry.name_key, entry.path, words)
            if score is not None:
                yield score, entry

    def scored_directories() -> Iterator[tuple[tuple[int, int], DirectoryEntry]]:
        if first_directory is None or directories is None:
            return
        for row in chain((first_directory,), directories):
            path, name, _, _, _, identity = row
            if identity in registered:
                continue
            score = (0, 0) if recent else score_path(needle, name, path, words)
            if score is not None:
                yield score, row

    def read(entry: LookupEntry[Reference] | DirectoryEntry) -> Project:
        if isinstance(entry, LookupEntry):
            return snapshot.read(entry)
        path = Path(entry[0])
        return Project(path.name, path, path, ProjectSource.MANUAL)

    def all_scores() -> Iterator[tuple[tuple[int, int], LookupEntry[Reference] | DirectoryEntry]]:
        yield from scored_projects()
        if first_directory is not None:
            from pcd_cli.live_directories import live_directories

            yield from live_directories(scored_directories(), lambda item: item[1][0])

    matches = all_scores()
    initial = list(islice(matches, 2))
    if len(initial) < 2 and request.after is None and request.before is None:
        projects = [read(entry) for _, entry in initial]
        return LookupResult(projects, len(projects), generation=snapshot.generation)
    candidates = chain(initial, matches)
    usage = load_usage() if load_usage and request.usage_for is None else None
    total = 0
    eligible = 0
    excluded_before = 0

    def ranked() -> Iterator[tuple[ProjectRank, LookupEntry[Reference] | DirectoryEntry]]:
        nonlocal total, eligible, excluded_before
        while batch := list(islice(candidates, 500)):
            batch_usage = usage
            if request.usage_for and isinstance(batch[0][1], LookupEntry):
                project_paths = (
                    Path(entry.path) for _, entry in batch if isinstance(entry, LookupEntry)
                )
                batch_usage = request.usage_for(project_paths)
            for score, entry in batch:
                if isinstance(entry, LookupEntry):
                    path = entry.path
                    item = batch_usage.get(Path(path)) if batch_usage else None
                    count, used, preference = (
                        (item.count, item.used_at, item.preference) if item else (0, 0.0, 0.0)
                    )
                else:
                    path, _, count, used, preference, _ = entry
                rank: ProjectRank = (
                    score[0],
                    -score[1],
                    -used if recent else -(count + preference),
                    -float(count) if recent else -used,
                    0,
                    "",
                    path,
                )
                total += 1
                if request.after is not None and rank <= request.after:
                    excluded_before += 1
                    continue
                if request.before is not None and rank >= request.before:
                    continue
                eligible += 1
                yield rank, entry

    reverse = request.before is not None or request.last
    if request.limit is None:
        selected = sorted(ranked(), key=lambda item: item[0])
    elif reverse:
        selected = heapq.nlargest(request.limit, ranked(), key=lambda item: item[0])
        selected.reverse()
    else:
        selected = heapq.nsmallest(request.limit, ranked(), key=lambda item: item[0])
    offset = eligible - len(selected) if reverse else excluded_before
    if request.usage_for is not None:
        selected_usage: dict[Path, ProjectUsage] = {}
        missing: list[Path] = []
        for _, entry in selected:
            if isinstance(entry, LookupEntry):
                missing.append(Path(entry.path))
            else:
                path, _, count, used, preference, _ = entry
                selected_usage[Path(path)] = ProjectUsage(used, count, preference)
        if missing:
            selected_usage.update(request.usage_for(iter(missing)))
        usage = selected_usage
    return LookupResult(
        [read(entry) for _, entry in selected],
        total,
        offset,
        usage,
        selected[0][0] if selected else None,
        selected[-1][0] if selected else None,
        snapshot.generation,
    )


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    projects: Sequence[Project]
    generation: int = 0

    def records(self) -> Iterator[LookupEntry[int]]:
        for index, project in enumerate(self.projects):
            yield LookupEntry(index, project.name.casefold(), len(project.name), str(project.path))

    def exact(self, key: str) -> Iterator[LookupEntry[int]]:
        return (entry for entry in self.records() if entry.name_key == key)

    def prefix(self, key: str) -> Iterator[LookupEntry[int]]:
        return iter(
            sorted(
                (entry for entry in self.records() if entry.name_key.startswith(key)),
                key=lambda entry: (entry.name_key, entry.path),
            )
        )

    def read(self, entry: LookupEntry[int]) -> Project:
        return self.projects[entry.reference]
