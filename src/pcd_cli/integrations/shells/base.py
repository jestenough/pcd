"""Shared interface and lifecycle for shell integrations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from pcd_cli.environment import Platform
    from pcd_cli.integrations.shells.common import Shell, ShellChange, ShellIntegrationState


@dataclass(frozen=True, slots=True)
class ShellDriver(ABC):
    shell: Shell
    platform: Platform

    @abstractmethod
    def config_path(self, home: Path) -> Path: ...

    @abstractmethod
    def render(self) -> str: ...

    @abstractmethod
    def reload_command(self, path: Path) -> str: ...

    @abstractmethod
    def state(self, path: Path) -> ShellIntegrationState: ...

    @abstractmethod
    def install(self, path: Path) -> ShellChange: ...

    @abstractmethod
    def uninstall(self, path: Path) -> ShellChange: ...


class StartupDriver(ShellDriver, ABC):
    @abstractmethod
    def startup_command(self) -> str: ...

    def state(self, path: Path) -> ShellIntegrationState:
        from pcd_cli.integrations.shells import startup_file

        return startup_file.state(path, self.shell)

    def install(self, path: Path) -> ShellChange:
        from pcd_cli.integrations.shells import startup_file

        return startup_file.install(path, self.shell, self.startup_command())

    def uninstall(self, path: Path) -> ShellChange:
        from pcd_cli.integrations.shells import startup_file

        return startup_file.uninstall(path, self.shell)
