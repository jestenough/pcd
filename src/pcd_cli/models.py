from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class ProjectSource(StrEnum):
    DISCOVERED = "discovered"
    MANUAL = "manual"


class ExitCode(IntEnum):
    ERROR = 1
    NOT_FOUND = 3


@dataclass(frozen=True, slots=True)
class Discovery:
    """A root route through which a canonical project was discovered."""

    root: Path
    display_path: Path


@dataclass(frozen=True, slots=True)
class Project:
    name: str
    path: Path
    display_path: Path
    source: ProjectSource
    # Provenance is metadata: the same canonical project remains one entry even
    # when several roots or symlink routes discover it.
    discoveries: tuple[Discovery, ...] = field(default=(), compare=False)


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    roots: tuple[Path, ...]
    manual_projects: tuple[Project, ...]
    include_hidden: bool
    follow_symlinks: bool
    excluded_names: frozenset[str]
    editor: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectUsage:
    used_at: float
    count: int
