"""Storage-independent lookup, ranking and bounded result windows."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from enum import StrEnum
from itertools import chain
from pathlib import Path
from typing import Protocol, TYPE_CHECKING

from pcd_cli.search import _score

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from pcd_cli.models import Project, ProjectUsage
    from pcd_cli.search import ProjectRank

type UsageLoader = Callable[[], Mapping[Path, ProjectUsage]]
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
    recent = not needle and request.mode is LookupMode.RESOLVE
    usage = None
    if request.mode is LookupMode.PREFIX:
        entries = snapshot.prefix(needle)
    elif recent:
        entries = snapshot.records()
    else:
        exact = snapshot.exact(needle)
        first = next(exact, None)
        second = next(exact, None)
        if first is not None and second is None:
            entries = iter((first,))
        elif first is not None and second is not None:
            entries = chain((first, second), exact)
            usage = {} if load_usage is None else load_usage()
        elif request.mode is LookupMode.EXACT:
            entries = iter(())
        else:
            entries = snapshot.records()
            usage = {} if load_usage is None else load_usage()
    if recent:
        usage = {} if load_usage is None else load_usage()

    total = 0
    eligible = 0
    excluded_before = 0

    def ranked() -> Iterator[tuple[ProjectRank, LookupEntry[Reference]]]:
        nonlocal total, eligible, excluded_before
        for entry in entries:
            score = (0, 0) if recent else _score(needle, entry.name_key)
            if score is None:
                continue
            item = usage.get(Path(entry.path)) if usage else None
            rank: ProjectRank = (
                score[0],
                -score[1],
                0.0 if item is None else -item.used_at,
                0 if item is None else -item.count,
                0 if recent else entry.name_length,
                entry.name_key,
                entry.path,
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
    return LookupResult(
        [snapshot.read(entry) for _, entry in selected],
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
