"""Small typed adapter for current-user Windows Registry values."""

from __future__ import annotations

import sys
from typing import Protocol, TYPE_CHECKING

from pcd_cli.integrations.shells.common import ShellIntegrationError

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

if TYPE_CHECKING and sys.platform != "win32":

    class _Winreg(Protocol):
        HKEY_CURRENT_USER: object
        REG_SZ: int

        def OpenKey(  # noqa: N802
            self, key: object, subkey: str
        ) -> AbstractContextManager[object]: ...

        def CreateKey(  # noqa: N802
            self, key: object, subkey: str
        ) -> AbstractContextManager[object]: ...

        def QueryValueEx(self, key: object, name: str) -> tuple[object, int]: ...  # noqa: N802

        def SetValueEx(  # noqa: N802
            self,
            key: object,
            name: str,
            reserved: int,
            value_type: int,
            value: str,
        ) -> None: ...

        def DeleteValue(self, key: object, name: str) -> None: ...  # noqa: N802

    winreg: _Winreg
elif sys.platform == "win32":
    import winreg


def read(key_path: str, name: str) -> tuple[str, int] | None:
    _require_windows()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, value_type = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return None
    return str(value), value_type


def write(key_path: str, name: str, value: str, value_type: int) -> None:
    _require_windows()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, name, 0, value_type, value)


def delete(key_path: str, name: str) -> None:
    _require_windows()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.DeleteValue(key, name)


def string_value_type() -> int:
    _require_windows()
    return winreg.REG_SZ


def _require_windows() -> None:
    if sys.platform != "win32":
        raise ShellIntegrationError("Windows Registry is only available on Windows")
