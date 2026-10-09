"""Shared shell integration types and protocol constants."""

from enum import StrEnum

SHELL_MODE_ENV = "PCD_SHELL"
SHELL_WRAPPER_ENV = "PCD_WRAPPER"
# Internal success status: stdout contains the destination path when this is returned.
SHELL_CD_EXIT_CODE = 10


class ShellIntegrationError(Exception):
    """Persistent shell integration cannot be inspected or managed safely."""


class Shell(StrEnum):
    BASH = "bash"
    ZSH = "zsh"
    FISH = "fish"
    POWERSHELL = "powershell"
    PWSH = "pwsh"
    CMD = "cmd"


class ShellIntegrationState(StrEnum):
    ABSENT = "not installed"
    MANUAL = "configured manually"
    MANAGED = "installed by pcd"


class ShellChange(StrEnum):
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    MANUAL = "manual"
