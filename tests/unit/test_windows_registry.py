from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

import pcd_cli.integrations.shells.windows_registry as windows_registry
from pcd_cli.integrations.shells.common import ShellIntegrationError

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def clear_registry_api_cache() -> Iterator[None]:
    windows_registry._api.cache_clear()
    yield
    windows_registry._api.cache_clear()


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
    monkeypatch.setattr(windows_registry, "import_module", lambda _name: registry)

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

    registry = SimpleNamespace(
        HKEY_CURRENT_USER="current-user",
        REG_SZ=1,
        OpenKey=missing_key,
        CreateKey=lambda _root, _path: nullcontext(),
        QueryValueEx=lambda _key, _name: ("", 1),
        SetValueEx=lambda *_args: None,
        DeleteValue=lambda *_args: None,
    )
    monkeypatch.setattr(windows_registry, "import_module", lambda _name: registry)

    assert windows_registry.read(r"Software\pcd", "Value") is None


def test_registry_rejects_incompatible_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows_registry, "import_module", lambda _name: SimpleNamespace())

    with pytest.raises(ShellIntegrationError, match="incompatible API"):
        windows_registry.read(r"Software\pcd", "Value")
