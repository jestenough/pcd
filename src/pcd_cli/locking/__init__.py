"""Cross-platform inter-process file locking."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

if sys.platform == "win32":
    from pcd_cli.locking.windows import acquire, release
elif os.name == "posix":
    from pcd_cli.locking.unix import acquire, release
else:
    raise RuntimeError(f"File locking is not supported on {sys.platform}")

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
