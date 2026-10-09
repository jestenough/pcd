"""Small typed adapter for current-user Windows Registry values."""

from __future__ import annotations

from functools import cache
from importlib import import_module
from typing import Protocol, runtime_checkable, TYPE_CHECKING

from pcd_cli.integrations.shells.common import ShellIntegrationError

if TYPE_CHECKING:
    from contextlib import AbstractContextManager


@runtime_checkable
class _RegistryApi(Protocol):
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


def read(key_path: str, name: str) -> tuple[str, int] | None:
    registry = _api()
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, key_path) as key:
            value, value_type = registry.QueryValueEx(key, name)
    except FileNotFoundError:
        return None
    return str(value), value_type


def write(key_path: str, name: str, value: str, value_type: int) -> None:
    registry = _api()
    with registry.CreateKey(registry.HKEY_CURRENT_USER, key_path) as key:
        registry.SetValueEx(key, name, 0, value_type, value)


def delete(key_path: str, name: str) -> None:
    registry = _api()
    with registry.CreateKey(registry.HKEY_CURRENT_USER, key_path) as key:
        registry.DeleteValue(key, name)


def string_value_type() -> int:
    return _api().REG_SZ


@cache
def _api() -> _RegistryApi:
    api = import_module("winreg")
    if not isinstance(api, _RegistryApi):
        raise ShellIntegrationError("Windows Registry adapter has an incompatible API")
    return api
