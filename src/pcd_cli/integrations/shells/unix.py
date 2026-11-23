"""Bash, Zsh, and Fish integration backend."""

import os
import shlex
from pathlib import Path
from textwrap import dedent
from typing import Literal

from pcd_cli.integrations.shells.base import StartupDriver
from pcd_cli.integrations.shells.bash_worker import worker_functions
from pcd_cli.integrations.shells.common import (
    Shell,
    SHELL_CD_EXIT_CODE,
    SHELL_MODE_ENV,
    SHELL_WRAPPER_ENV,
)
from pcd_cli.integrations.shells.features import recorder


class UnixDriver(StartupDriver):
    def config_path(self, home: Path) -> Path:
        if self.shell is Shell.BASH:
            return home / ".bashrc"

        if self.shell is Shell.ZSH:
            zdotdir = os.environ.get("ZDOTDIR")
            return (Path(zdotdir).expanduser() if zdotdir else home) / ".zshrc"

        if self.shell is Shell.FISH:
            xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
            config_home = (
                Path(xdg_config_home).expanduser() if xdg_config_home else home / ".config"
            )
            return config_home / "fish" / "config.fish"

        raise ValueError(f"Unsupported Unix shell: {self.shell}")

    def reload_command(self, path: Path) -> str:
        return f"source {shlex.quote(str(path))}"

    def startup_command(self) -> str:
        if self.shell is Shell.FISH:
            return f"command pcd shell init {self.shell.value} | source"

        if self.shell in (Shell.BASH, Shell.ZSH):
            return f'eval "$(command pcd shell init {self.shell.value})"'

        raise ValueError(f"Unsupported Unix shell: {self.shell}")

    def render(self) -> str:
        if self.shell is Shell.FISH:
            return recorder(self.shell) + _fish()

        if self.shell is Shell.BASH:
            return recorder(self.shell) + worker_functions() + _posix("bash_source")

        if self.shell is Shell.ZSH:
            return recorder(self.shell) + _posix("zsh_source")

        raise ValueError(f"Unsupported Unix shell: {self.shell}")


def _posix(completion: Literal["bash_source", "zsh_source"]) -> str:
    fast_jump = '_pcd_fast_jump "$@" && return 0' if completion == "bash_source" else ""
    wrapper = dedent(
        f"""\
        pcd() {{
            local -x {SHELL_WRAPPER_ENV}={completion.removesuffix("_source")}
            if [ -n "${{_PCD_COMPLETE:-}}" ]; then
                command pcd "$@"
                return $?
            fi

            local output code
            {fast_jump}
            if output="$({SHELL_MODE_ENV}=1 command pcd "$@")"; then
                code=0
            else
                code=$?
            fi

            if [ "$code" -eq {SHELL_CD_EXIT_CODE} ]; then
                _pcd_last_directory=$PWD
                builtin cd -- "$output" || return $?
                _pcd_record_visit
                return 0
            fi
            if [ -n "$output" ]; then
                printf '%s\\n' "$output"
            fi
            return "$code"
        }}

        """
    )
    setup = f'eval "$(_PCD_COMPLETE={completion} command pcd)"'
    if completion == "bash_source":
        return wrapper + (
            "if (( BASH_VERSINFO[0] > 4 || "
            "(BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4) )); then\n"
            f"    {setup}\nfi\n"
        )
    return wrapper + setup + "\n"


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
                set -g _pcd_last_directory "$PWD"
                cd -- "$output"
                set -l cd_code $status
                if test $cd_code -eq 0
                    _pcd_record_visit
                end
                return $cd_code
            end
            if test -n "$output"
                printf '%s\\n' $output
            end
            return $code
        end

        command env _PCD_COMPLETE=fish_source pcd | source
        """
    )
