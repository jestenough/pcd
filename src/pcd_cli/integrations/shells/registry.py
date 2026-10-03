"""Backend definitions and shell-to-backend registry."""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING

from pcd_cli.integrations.shells.common import Shell

if TYPE_CHECKING:
    from pathlib import Path


class ShellBackend(Protocol):
    def config_path(self, shell: Shell, home: Path) -> Path: ...

    def reload_command(self, shell: Shell, path: Path) -> str: ...

    def startup_command(self, shell: Shell) -> str | None: ...

    def render(self, shell: Shell) -> str: ...


def backend_for(shell: Shell) -> ShellBackend:
    """Load only the requested backend; Python caches the module itself."""
    if shell in (Shell.BASH, Shell.ZSH, Shell.FISH):
        from pcd_cli.integrations.shells import unix

        return unix
    if shell in (Shell.POWERSHELL, Shell.PWSH):
        from pcd_cli.integrations.shells import powershell

        return powershell
    if shell is Shell.CMD:
        from pcd_cli.integrations.shells import cmd

        return cmd
    raise ValueError(f"Unsupported shell: {shell}")
