"""Generate, install, and inspect shell integration for pcd."""

import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from textwrap import dedent
from typing import Literal, Self

from pcd_cli.filesystem import atomic_write, file_lock

SHELL_MODE_ENV = "PCD_SHELL"
# Internal success status: stdout contains the destination path when this is returned.
SHELL_CD_EXIT_CODE = 10
_MANAGED_BLOCK_START = "# >>> pcd shell integration >>>"
_MANAGED_BLOCK_END = "# <<< pcd shell integration <<<"


class ShellIntegrationError(Exception):
    """Persistent shell integration cannot be inspected or managed safely."""


class Shell(StrEnum):
    BASH = "bash"
    ZSH = "zsh"
    FISH = "fish"


class ShellIntegrationState(StrEnum):
    ABSENT = "not installed"
    MANUAL = "configured manually"
    MANAGED = "installed by pcd"


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
        return _integration_state(self._read(), self.shell)

    def install(self) -> bool:
        """Install the managed block. Return whether the config changed."""
        with file_lock(self.config_path):
            content = self._read()
            if _integration_state(content, self.shell) is not ShellIntegrationState.ABSENT:
                return False

            separator = "" if not content or content.endswith("\n") else "\n"
            self._write(f"{content}{separator}{render_managed_block(self.shell)}")
            return True

    def uninstall(self) -> bool:
        """Remove only a pcd-managed block, leaving manual setup untouched."""
        with file_lock(self.config_path):
            content = self._read()
            bounds = _managed_block_bounds(content)
            if bounds is None:
                return False

            start, end = bounds
            self._write(f"{content[:start]}{content[end:]}")
            return True

    def _read(self) -> str:
        try:
            return self.config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except UnicodeError as exc:
            raise ShellIntegrationError(
                f"Shell config is not valid UTF-8: {self.config_path}"
            ) from exc

    def _write(self, content: str) -> None:
        target = (
            self.config_path.resolve(strict=False)
            if self.config_path.is_symlink()
            else self.config_path
        )
        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None

        with atomic_write(target) as stream:
            stream.write(content)

        if mode is not None:
            target.chmod(mode)


def detect_shell() -> Shell:
    """Detect the user's login shell from the standard SHELL environment variable."""
    executable = os.environ.get("SHELL", "")
    name = Path(executable).name.casefold()
    try:
        return Shell(name)
    except ValueError as exc:
        supported = ", ".join(shell.value for shell in Shell)
        raise ShellIntegrationError(
            f"Cannot detect a supported shell from SHELL={executable!r}; choose one of: {supported}"
        ) from exc


def shell_config_path(shell: Shell) -> Path:
    """Return the startup file where persistent integration should be installed."""
    home = Path.home()
    if shell is Shell.BASH:
        return home / ".bashrc"
    if shell is Shell.ZSH:
        zdotdir = os.environ.get("ZDOTDIR")
        return (Path(zdotdir).expanduser() if zdotdir else home) / ".zshrc"

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg_config_home).expanduser() if xdg_config_home else home / ".config"
    return config_home / "fish" / "config.fish"


def shell_integration_active() -> bool:
    return os.environ.get(SHELL_MODE_ENV) == "1"


def inactive_shell_message() -> str:
    """Explain how to activate directory changes when the wrapper is not running."""
    try:
        integration = ShellIntegration.detect()
        state = integration.state()
    except (OSError, ShellIntegrationError):
        return (
            "Shell integration is not active, so pcd cannot change this shell's directory. "
            "If you have not configured it manually, run: pcd shell install"
        )

    if state is ShellIntegrationState.ABSENT:
        return (
            "Shell integration is not installed, so pcd cannot change this shell's directory. "
            "Install it with: pcd shell install"
        )
    return (
        f"Shell integration is configured in {integration.config_path} but is not active in "
        f"this shell. Restart it or run: exec {integration.shell.value}"
    )


def render_managed_block(shell: Shell) -> str:
    """Render the small persistent block written into the shell startup file."""
    command = (
        f'eval "$(command pcd shell print {shell.value})"'
        if shell is not Shell.FISH
        else f"command pcd shell print {shell.value} | source"
    )
    return f"{_MANAGED_BLOCK_START}\n{command}\n{_MANAGED_BLOCK_END}\n"


def render_shell_integration(shell: Shell, direct_commands: tuple[str, ...] = ()) -> str:
    """Generate shell integration for directory changes and Click completion."""
    commands = tuple(sorted(set(direct_commands)))
    match shell:
        case Shell.FISH:
            return _fish(commands)
        case Shell.BASH:
            return _posix("bash_source", commands)
        case Shell.ZSH:
            return _posix("zsh_source", commands)
        case _:
            raise ValueError(f"Unsupported shell: {shell}")


def _integration_state(content: str, shell: Shell) -> ShellIntegrationState:
    bounds = _managed_block_bounds(content)
    if bounds is not None:
        return ShellIntegrationState.MANAGED

    legacy = f"pcd shell-init {shell.value}"
    current = f"pcd shell print {shell.value}"
    if legacy in content or current in content:
        return ShellIntegrationState.MANUAL
    return ShellIntegrationState.ABSENT


def _managed_block_bounds(content: str) -> tuple[int, int] | None:
    starts = content.count(_MANAGED_BLOCK_START)
    ends = content.count(_MANAGED_BLOCK_END)
    if starts == 0 and ends == 0:
        return None
    if starts != 1 or ends != 1:
        raise ShellIntegrationError(
            "Shell config contains an invalid pcd-managed integration block"
        )

    start = content.index(_MANAGED_BLOCK_START)
    end_marker = content.index(_MANAGED_BLOCK_END)
    if end_marker < start:
        raise ShellIntegrationError(
            "Shell config contains an invalid pcd-managed integration block"
        )

    end = end_marker + len(_MANAGED_BLOCK_END)
    if end < len(content) and content[end] == "\n":
        end += 1
    return start, end


def _posix(completion: Literal["bash_source", "zsh_source"], commands: tuple[str, ...]) -> str:
    direct_patterns = "|".join(("-*", *commands))
    return dedent(
        f"""\
        pcd() {{
            if [ -n "${{_PCD_COMPLETE:-}}" ]; then
                command pcd "$@"
                return $?
            fi

            if [ "$#" -eq 0 ]; then
                command pcd
                return $?
            fi
            case "$1" in
                --project|--project=*) : ;;
                {direct_patterns})
                    command pcd "$@"
                    return $?
                    ;;
            esac

            local output code
            if output="$({SHELL_MODE_ENV}=1 command pcd "$@")"; then
                code=0
            else
                code=$?
            fi

            if [ "$code" -eq {SHELL_CD_EXIT_CODE} ]; then
                builtin cd -- "$output"
                return $?
            fi
            if [ "$code" -ne 0 ]; then
                return "$code"
            fi
            if [ -n "$output" ]; then
                printf '%s\n' "$output"
            fi
        }}

        eval "$(_PCD_COMPLETE={completion} command pcd)"
        """
    )


def _fish(commands: tuple[str, ...]) -> str:
    direct_patterns = " ".join(("'-*'", *commands))
    return dedent(
        f"""\
        function pcd
            if set -q _PCD_COMPLETE
                command pcd $argv
                return $status
            end

            if test (count $argv) -eq 0
                command pcd
                return $status
            end
            switch $argv[1]
                case '--project' '--project=*'
                    true
                case {direct_patterns}
                    command pcd $argv
                    return $status
            end

            set -l output (env {SHELL_MODE_ENV}=1 command pcd $argv)
            set -l code $status
            if test $code -eq {SHELL_CD_EXIT_CODE}
                cd -- "$output"
                return $status
            end
            if test $code -ne 0
                return $code
            end
            if test -n "$output"
                printf '%s\n' "$output"
            end
        end

        set -lx _PCD_COMPLETE fish_source
        command pcd | source
        set -e _PCD_COMPLETE
        """
    )
