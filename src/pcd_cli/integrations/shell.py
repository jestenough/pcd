"""Generate, install, and inspect shell integration for pcd."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from pcd_cli.integrations.shells import startup_file
from pcd_cli.integrations.shells.common import (
    Shell as Shell,
    SHELL_CD_EXIT_CODE as SHELL_CD_EXIT_CODE,
    SHELL_MODE_ENV,
    SHELL_WRAPPER_ENV,
    ShellIntegrationError as ShellIntegrationError,
    ShellIntegrationState as ShellIntegrationState,
)
from pcd_cli.integrations.shells.registry import backend_for


@dataclass(frozen=True, slots=True)
class ShellIntegration:
    shell: Shell
    config_path: Path

    @classmethod
    def detect(cls) -> Self:
        return cls.for_shell(detect_shell())

    @classmethod
    def for_shell(cls, shell: Shell) -> Self:
        return cls(shell=shell, config_path=shell_config_path(shell))

    def state(self) -> ShellIntegrationState:
        return backend_for(self.shell).state(self.shell, self.config_path)

    def reload_command(self) -> str:
        return backend_for(self.shell).reload_command(self.shell, self.config_path)

    def install(self) -> bool:
        """Install managed integration. Return whether persistent state changed."""
        return backend_for(self.shell).install(self.shell, self.config_path)

    def uninstall(self) -> bool:
        """Remove only integration installed by pcd, leaving manual setup untouched."""
        return backend_for(self.shell).uninstall(self.shell, self.config_path)


def detect_shell() -> Shell:
    """Detect the active wrapper or surrounding shell."""
    wrapper = invoking_shell()
    if wrapper is not None:
        return wrapper

    if sys.platform == "win32":
        name = _detect_windows_shell()
        source = "the parent process"
    else:
        executable = os.environ.get("SHELL", "")
        name = Path(executable).name.casefold().removesuffix(".exe")
        source = f"SHELL={executable!r}"

    try:
        return Shell(name)
    except ValueError as exc:
        supported = ", ".join(shell.value for shell in Shell)
        raise ShellIntegrationError(
            f"Cannot detect a supported shell from {source}; choose one of: {supported}"
        ) from exc


def _detect_windows_shell() -> str:
    """Load Shellingham only when native Windows detection is required."""
    try:
        import shellingham  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise ShellIntegrationError("Windows shell detection is unavailable") from exc

    try:
        detected: tuple[str, str] = shellingham.detect_shell()
    except OSError as exc:
        raise ShellIntegrationError("Cannot detect the surrounding Windows shell") from exc

    name, _executable = detected
    return name.casefold().removesuffix(".exe")


def shell_config_path(shell: Shell) -> Path:
    """Return the persistent integration file for a shell."""
    return backend_for(shell).config_path(shell, Path.home())


def shell_integration_active() -> bool:
    return os.environ.get(SHELL_MODE_ENV) == "1"


def invoking_shell() -> Shell | None:
    """Identify the wrapper for this invocation, not the parent shell's state."""
    try:
        return Shell(os.environ.get(SHELL_WRAPPER_ENV, ""))
    except ValueError:
        return None


def inactive_shell_message() -> str:
    """Explain how to activate directory changes when the wrapper is not running."""
    install_command = "pcd shell install"
    if sys.platform == "win32" and invoking_shell() is None:
        return (
            "Shell integration is not active, so pcd cannot change this shell's directory. "
            f"If you have not configured it manually, run: {install_command}"
        )

    try:
        integration = ShellIntegration.detect()
        state = integration.state()
    except (OSError, ShellIntegrationError):
        return (
            "Shell integration is not active, so pcd cannot change this shell's directory. "
            f"If you have not configured it manually, run: {install_command}"
        )

    if state is ShellIntegrationState.ABSENT:
        return (
            "Shell integration is not installed, so pcd cannot change this shell's directory. "
            f"Install it with: {install_command}"
        )

    return (
        f"Shell integration is configured in {integration.config_path} but is not active in "
        f"this shell. Reload it with: {integration.reload_command()}"
    )


def render_managed_block(shell: Shell) -> str:
    """Render the small persistent block written into a shell startup file."""
    command = backend_for(shell).startup_command(shell)
    if command is None:
        raise ValueError(f"Shell does not use a startup file: {shell}")
    return startup_file.managed_block(command)


def render_shell_integration(shell: Shell) -> str:
    """Generate shell integration for directory changes and completion."""
    return backend_for(shell).render(shell)
