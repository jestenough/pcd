from dataclasses import dataclass
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
class Project:
    name: str
    path: Path
    display_path: Path
    source: ProjectSource


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    roots: tuple[Path, ...]
    manual_projects: tuple[Project, ...]
    include_hidden: bool
    follow_symlinks: bool
    excluded_names: frozenset[str]


@dataclass(frozen=True, slots=True)
class ProjectUsage:
    used_at: float
    count: int
