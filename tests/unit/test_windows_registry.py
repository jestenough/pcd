from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import pcd_cli.integrations.shells.windows_registry as windows_registry
from pcd_cli.integrations.shells.common import ShellIntegrationError


def test_registry_value_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[object, ...]] = []
    key_path = r"Software\pcd"
    registry = SimpleNamespace(
        HKEY_CURRENT_USER="current-user",
        REG_SZ=1,
        OpenKey=lambda root, path: nullcontext((root, path)),
        CreateKey=lambda root, path: nullcontext((root, path)),
        QueryValueEx=lambda key, name: (f"{key}:{name}", 2),
        SetValueEx=lambda *args: events.append(args),
        DeleteValue=lambda *args: events.append(args),
    )
    monkeypatch.setattr("pcd_cli.integrations.shells.windows_registry.sys.platform", "win32")
    monkeypatch.setattr(windows_registry, "winreg", registry, raising=False)

    assert windows_registry.read(key_path, "Value") == (f"{('current-user', key_path)}:Value", 2)
    windows_registry.write(key_path, "Value", "text", 2)
    windows_registry.delete(key_path, "Value")

    assert events == [
        (("current-user", key_path), "Value", 0, 2, "text"),
        (("current-user", key_path), "Value"),
    ]
    assert windows_registry.string_value_type() == 1


def test_registry_missing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_key(_root: object, _path: str) -> None:
        raise FileNotFoundError

    registry = SimpleNamespace(HKEY_CURRENT_USER="current-user", OpenKey=missing_key)
    monkeypatch.setattr("pcd_cli.integrations.shells.windows_registry.sys.platform", "win32")
    monkeypatch.setattr(windows_registry, "winreg", registry, raising=False)

    assert windows_registry.read(r"Software\pcd", "Value") is None


def test_registry_rejects_other_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pcd_cli.integrations.shells.windows_registry.sys.platform", "linux")

    with pytest.raises(ShellIntegrationError, match="only available on Windows"):
        windows_registry.read(r"Software\pcd", "Value")
