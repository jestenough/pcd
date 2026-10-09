"""Detect the supported platform and active shell."""

from __future__ import annotations

import os
import sys
from enum import StrEnum
from pathlib import Path

from pcd_cli.integrations.shells.common import (
    Shell,
    SHELL_WRAPPER_ENV,
    ShellIntegrationError,
)


class Platform(StrEnum):
    LINUX = "linux"
    MACOS = "darwin"
    WINDOWS = "win32"


def current_platform() -> Platform:
    try:
        return Platform(sys.platform)
    except ValueError as exc:
        raise RuntimeError(f"Unsupported platform: {sys.platform}") from exc


def invoking_shell() -> Shell | None:
    """Return the shell wrapper for this invocation, if present."""
    value = os.environ.get(SHELL_WRAPPER_ENV)
    if not value:
        return None
    try:
        return _parse_shell(value)
    except ValueError:
        return None


def detect_shell(platform: Platform | None = None) -> Shell:
    """Detect the wrapper or parent shell in the current environment."""
    wrapper = invoking_shell()
    if wrapper is not None:
        return wrapper

    platform = platform or current_platform()
    match platform:
        case Platform.LINUX | Platform.MACOS:
            executable = os.environ.get("SHELL", "")
            name = Path(executable).name
            source = f"SHELL={executable!r}"
        case Platform.WINDOWS:
            from pcd_cli.integrations.shells import windows_shell

            name = windows_shell.detect_shell()
            source = "the parent process"

    try:
        return _parse_shell(name)
    except ValueError as exc:
        supported = ", ".join(shell.value for shell in Shell)
        raise ShellIntegrationError(
            f"Cannot detect a supported shell from {source}; choose one of: {supported}"
        ) from exc


def _parse_shell(name: str) -> Shell:
    normalized = name.casefold()
    for shell in Shell:
        if normalized in (shell.value, f"{shell.value}.exe"):
            return shell
    raise ValueError(f"Unsupported shell: {name}")
