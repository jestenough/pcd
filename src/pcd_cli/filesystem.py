import fcntl
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Self, TextIO, TYPE_CHECKING

from platformdirs import PlatformDirs

if TYPE_CHECKING:
    from collections.abc import Generator

APP_NAME = "pcd-cli"
CONFIG_FILENAME = "config.toml"
CACHE_FILENAME = "projects.jsonl"
HISTORY_FILENAME = "history.json"


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    config: Path
    cache: Path
    history: Path

    @classmethod
    def resolve(cls) -> Self:
        directories = PlatformDirs(APP_NAME, appauthor=False)
        return cls(
            config=directories.user_config_path / CONFIG_FILENAME,
            cache=directories.user_cache_path / CACHE_FILENAME,
            history=directories.user_state_path / HISTORY_FILENAME,
        )


def canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def format_path(path: Path) -> str:
    home = Path.home()
    if not path.is_relative_to(home):
        return str(path)

    relative = path.relative_to(home)
    return "~" if not relative.parts else f"~/{relative}"


@contextmanager
def file_lock(path: Path) -> Generator[None]:
    """Prevent concurrent writers from overwriting each other's changes."""
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def atomic_write(path: Path) -> Generator[TextIO]:
    """Write through a temporary file so readers never observe partial data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
