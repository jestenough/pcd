"""Windows file-lock backend."""

from __future__ import annotations

import sys
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING and sys.platform != "win32":
    from typing import BinaryIO

    class _Msvcrt(Protocol):
        LK_LOCK: int
        LK_UNLCK: int

        def locking(self, file_handle: int, mode: int, nbytes: int) -> None: ...

    msvcrt: _Msvcrt
else:
    import msvcrt

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
