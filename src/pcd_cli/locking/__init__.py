"""Cross-platform inter-process file locking."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from pcd_cli.environment import current_platform, Platform

match current_platform():
    case Platform.LINUX | Platform.MACOS:
        from pcd_cli.locking.unix import acquire, release
    case Platform.WINDOWS:
        from pcd_cli.locking.windows import acquire, release

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@contextmanager
def file_lock(path: Path) -> Generator[None]:
    """Serialize access to a resource using a persistent sibling lock file."""
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+b") as stream:
        acquire(stream)
        try:
            yield
        finally:
            release(stream)
