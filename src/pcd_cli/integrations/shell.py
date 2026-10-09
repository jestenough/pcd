"""Generate, install, and inspect shell integration for pcd."""

import os
from pathlib import Path
from typing import Self

from pcd_cli.environment import (
    current_platform,
    detect_shell as detect_shell,
    invoking_shell as invoking_shell,
    Platform,
)
from pcd_cli.integrations.shells.base import ShellDriver
from pcd_cli.integrations.shells.common import (
    Shell as Shell,
    SHELL_CD_EXIT_CODE as SHELL_CD_EXIT_CODE,
    SHELL_MODE_ENV,
    ShellChange as ShellChange,
    ShellIntegrationError as ShellIntegrationError,
    ShellIntegrationState as ShellIntegrationState,
)
from pcd_cli.integrations.shells.registry import driver_for


class ConfiguredShell:
    __slots__ = ("_driver", "config_path")

    def __init__(
        self,
        driver: ShellDriver,
        config_path: Path,
    ) -> None:
        self._driver = driver
        self.config_path = config_path

    @property
    def shell(self) -> Shell:
        return self._driver.shell

    @classmethod
    def detect(cls, platform: Platform | None = None) -> Self:
        platform = platform or current_platform()
        return cls.for_shell(detect_shell(platform), platform)

    @classmethod
    def for_shell(cls, shell: Shell, platform: Platform | None = None) -> Self:
        driver = driver_for(shell, platform)
        path = driver.config_path(Path.home())
        return cls(driver, path)

    @classmethod
    def for_path(
        cls,
        shell: Shell,
        path: Path,
        platform: Platform | None = None,
    ) -> Self:
        return cls(driver_for(shell, platform), path)

    def state(self) -> ShellIntegrationState:
        return self._driver.state(self.config_path)

    def reload_command(self) -> str:
        return self._driver.reload_command(self.config_path)

    def install(self) -> ShellChange:
        return self._driver.install(self.config_path)

    def uninstall(self) -> ShellChange:
        return self._driver.uninstall(self.config_path)


def shell_integration_active() -> bool:
    return os.environ.get(SHELL_MODE_ENV) == "1"


def inactive_shell_message() -> str:
    """Explain how to activate directory changes when the wrapper is not running."""
    install_command = "pcd shell install"
    platform = current_platform()
    wrapper = invoking_shell() if platform is Platform.WINDOWS else None
    if platform is Platform.WINDOWS and wrapper is None:
        return (
            "Shell integration is not active, so pcd cannot change this shell's directory. "
            f"If you have not configured it manually, run: {install_command}"
        )

    try:
        integration = (
            ConfiguredShell.for_shell(wrapper, platform)
            if wrapper is not None
            else ConfiguredShell.detect(platform)
        )
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
