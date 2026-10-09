"""Interactive terminal output for native Windows consoles."""

from __future__ import annotations

import ctypes
import msvcrt
from contextlib import contextmanager
from ctypes import wintypes
from typing import TYPE_CHECKING

from prompt_toolkit.output.win32 import Win32Output  # type: ignore[attr-defined, unused-ignore]

if TYPE_CHECKING:
    from collections.abc import Iterator
    from io import TextIOWrapper

    from prompt_toolkit.output import Output


@contextmanager
def open_output() -> Iterator[Output]:
    with open("CONOUT$", "w", encoding="utf-8", errors="replace") as console:
        yield _win32_output(console)


def _win32_output(console: TextIOWrapper) -> Output:
    """Create output for ``console`` and restore the process handle immediately."""
    stdout_handle = wintypes.DWORD(-11)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    kernel32.SetStdHandle.restype = wintypes.BOOL

    original_handle = kernel32.GetStdHandle(stdout_handle)
    console_handle = wintypes.HANDLE(msvcrt.get_osfhandle(console.fileno()))  # type: ignore[attr-defined, unused-ignore]
    if not kernel32.SetStdHandle(stdout_handle, console_handle):
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined, unused-ignore]

    try:
        # Win32Output reads STD_OUTPUT_HANDLE instead of deriving a handle from
        # the supplied stream. PowerShell replaces that handle with a pipe while
        # capturing pcd's destination path, so expose CONOUT$ during construction.
        output: Output = Win32Output(console)
        return output
    finally:
        if not kernel32.SetStdHandle(stdout_handle, original_handle):
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined, unused-ignore]
