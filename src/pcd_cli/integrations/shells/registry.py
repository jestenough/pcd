"""Backend definitions and shell-to-backend registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pcd_cli.integrations.shells import cmd, powershell, unix
from pcd_cli.integrations.shells.common import Shell

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pcd_cli.integrations.shells.common import ShellIntegrationState


@dataclass(frozen=True, slots=True)
class ShellBackend:
    config_path: Callable[[Shell, Path], Path]
    reload_command: Callable[[Shell, Path], str]
    startup_command: Callable[[Shell], str | None]
    render: Callable[[Shell], str]
    state: Callable[[Shell, Path], ShellIntegrationState]
    install: Callable[[Shell, Path], bool]
    uninstall: Callable[[Shell, Path], bool]


_UNIX = ShellBackend(
    config_path=unix.config_path,
    reload_command=unix.reload_command,
    startup_command=unix.startup_command,
    render=unix.render,
    state=unix.state,
    install=unix.install,
    uninstall=unix.uninstall,
)
_POWERSHELL = ShellBackend(
    config_path=powershell.config_path,
    reload_command=powershell.reload_command,
    startup_command=powershell.startup_command,
    render=powershell.render,
    state=powershell.state,
    install=powershell.install,
    uninstall=powershell.uninstall,
)
_CMD = ShellBackend(
    config_path=cmd.config_path,
    reload_command=cmd.reload_command,
    startup_command=cmd.startup_command,
    render=cmd.render,
    state=cmd.state,
    install=cmd.install,
    uninstall=cmd.uninstall,
)


_BACKENDS: dict[Shell, ShellBackend] = {
    Shell.BASH: _UNIX,
    Shell.ZSH: _UNIX,
    Shell.FISH: _UNIX,
    Shell.POWERSHELL: _POWERSHELL,
    Shell.PWSH: _POWERSHELL,
    Shell.CMD: _CMD,
}


def backend_for(shell: Shell) -> ShellBackend:
    try:
        return _BACKENDS[shell]
    except KeyError as exc:
        raise ValueError(f"Unsupported shell: {shell}") from exc
