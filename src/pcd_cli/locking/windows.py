"""Windows file-lock backend."""

from __future__ import annotations

import msvcrt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import BinaryIO

_LOCKED_BYTES = 1


def acquire(stream: BinaryIO) -> None:
    """Lock the first byte, creating it when the lock file is new."""
    stream.seek(0)
    if not stream.read(_LOCKED_BYTES):
        stream.write(b"\0")
        stream.flush()
    stream.seek(0)
    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, _LOCKED_BYTES)


def release(stream: BinaryIO) -> None:
    stream.seek(0)
    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, _LOCKED_BYTES)
