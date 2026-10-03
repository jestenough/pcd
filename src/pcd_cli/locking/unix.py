"""POSIX file-lock backend."""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import BinaryIO

    # Typeshed exposes a reduced fcntl stub when mypy runs on Windows.
    class _Fcntl(Protocol):
        LOCK_EX: int
        LOCK_UN: int

        def flock(self, fd: int, operation: int) -> None: ...

    fcntl: _Fcntl
else:
    import fcntl


def acquire(stream: BinaryIO) -> None:
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def release(stream: BinaryIO) -> None:
    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
