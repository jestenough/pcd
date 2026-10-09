"""Detect the surrounding shell on native Windows."""

from importlib import import_module
from typing import Protocol, runtime_checkable

from pcd_cli.integrations.shells.common import ShellIntegrationError


@runtime_checkable
class _ShellDetector(Protocol):
    def detect_shell(self) -> tuple[str, str]: ...


def detect_shell() -> str:
    try:
        module = import_module("shellingham")
    except ModuleNotFoundError as exc:
        if exc.name != "shellingham":
            raise
        raise ShellIntegrationError("Windows shell detection is unavailable") from exc

    if not isinstance(module, _ShellDetector):
        raise ShellIntegrationError("Windows shell detector has an incompatible API")

    detector: _ShellDetector = module
    try:
        name, _executable = detector.detect_shell()
    except OSError as exc:
        raise ShellIntegrationError("Cannot detect the surrounding Windows shell") from exc
    return name
