"""Bash, Zsh, and Fish integration backend."""

import os
import shlex
from pathlib import Path
from textwrap import dedent
from typing import Literal

from pcd_cli.integrations.shells.common import (
    Shell,
    SHELL_CD_EXIT_CODE,
    SHELL_MODE_ENV,
    SHELL_WRAPPER_ENV,
)


def config_path(shell: Shell, home: Path) -> Path:
    if shell is Shell.BASH:
        return home / ".bashrc"

    if shell is Shell.ZSH:
        zdotdir = os.environ.get("ZDOTDIR")
        return (Path(zdotdir).expanduser() if zdotdir else home) / ".zshrc"

    if shell is Shell.FISH:
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        config_home = Path(xdg_config_home).expanduser() if xdg_config_home else home / ".config"
        return config_home / "fish" / "config.fish"

    raise ValueError(f"Unsupported Unix shell: {shell}")


def reload_command(shell: Shell, path: Path) -> str:
    return f"source {shlex.quote(str(path))}"


def startup_command(shell: Shell) -> str:
    if shell is Shell.FISH:
        return f"command pcd shell init {shell.value} | source"

    if shell in (Shell.BASH, Shell.ZSH):
        return f'eval "$(command pcd shell init {shell.value})"'

    raise ValueError(f"Unsupported Unix shell: {shell}")


def render(shell: Shell) -> str:
    if shell is Shell.FISH:
        return _fish()

    if shell is Shell.BASH:
        return _posix("bash_source")

    if shell is Shell.ZSH:
        return _posix("zsh_source")

    raise ValueError(f"Unsupported Unix shell: {shell}")


def _posix(completion: Literal["bash_source", "zsh_source"]) -> str:
    return dedent(
        f"""\
        pcd() {{
            local -x {SHELL_WRAPPER_ENV}={completion.removesuffix("_source")}
            if [ -n "${{_PCD_COMPLETE:-}}" ]; then
                command pcd "$@"
                return $?
            fi

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
            if [ -n "$output" ]; then
                printf '%s\\n' "$output"
            fi
            return "$code"
        }}

        eval "$(_PCD_COMPLETE={completion} command pcd)"
        """
    )


def _fish() -> str:
    return dedent(
        f"""\
        function pcd
            if set -q _PCD_COMPLETE
                command env {SHELL_WRAPPER_ENV}=fish pcd $argv
                return $status
            end

            set -l output (command env {SHELL_WRAPPER_ENV}=fish {SHELL_MODE_ENV}=1 pcd $argv)
            set -l code $status
            if test $code -eq {SHELL_CD_EXIT_CODE}
                cd -- "$output"
                return $status
            end
            if test -n "$output"
                printf '%s\\n' $output
            end
            return $code
        end

        command env _PCD_COMPLETE=fish_source pcd | source
        """
    )
