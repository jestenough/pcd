"""Manage the pcd DOSKEY command in the CMD AutoRun registry value."""

from __future__ import annotations

import sys
from contextlib import suppress
from typing import TYPE_CHECKING

from pcd_cli.integrations.shells import windows_registry
from pcd_cli.integrations.shells.common import ShellIntegrationError

if TYPE_CHECKING:
    from pathlib import Path

_COMMAND_PROCESSOR_KEY = r"Software\Microsoft\Command Processor"


def doskey_command(wrapper: Path) -> str:
    return f'doskey pcd=call "{wrapper}" $*'


def add(wrapper: Path) -> bool:
    """Add the pcd command, returning whether AutoRun was changed."""
    value, value_type = _read()
    managed_command = doskey_command(wrapper)

    if _without_command(value, managed_command) is not None:
        return False

    separator = "" if not value.strip() else " & "
    _write(f"{value}{separator}{managed_command}", value_type)

    return True


def configured(wrapper: Path) -> bool:
    value, _value_type = _read()
    return _without_command(value, doskey_command(wrapper)) is not None


def remove(wrapper: Path) -> None:
    value, value_type = _read()
    updated = _without_command(value, doskey_command(wrapper))
    if updated is not None:
        _write(updated, value_type)


def _without_command(value: str, managed_command: str) -> str | None:
    """Return AutoRun without an exact managed command, wherever it appears."""
    index = value.casefold().find(managed_command.casefold())
    if index < 0:
        return None

    before = value[:index]
    after = value[index + len(managed_command) :]
    if before:
        if not before.endswith(" & "):
            return None

        return f"{before[:-3]}{after}"

    if after:
        if not after.startswith(" & "):
            return None

        return after[3:]

    return ""


def _read() -> tuple[str, int]:
    _require_windows()
    result = windows_registry.read(_COMMAND_PROCESSOR_KEY, "AutoRun")
    return result if result is not None else ("", windows_registry.string_value_type())


def _write(value: str, value_type: int) -> None:
    _require_windows()
    if value:
        windows_registry.write(_COMMAND_PROCESSOR_KEY, "AutoRun", value, value_type)
    else:
        with suppress(FileNotFoundError):
            windows_registry.delete(_COMMAND_PROCESSOR_KEY, "AutoRun")


def _require_windows() -> None:
    if sys.platform != "win32":
        raise ShellIntegrationError("CMD AutoRun can only be managed on Windows")
