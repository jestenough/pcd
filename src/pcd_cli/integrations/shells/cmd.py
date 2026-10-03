"""CMD wrapper backend and installation lifecycle."""

import os
import stat
import sys
from contextlib import suppress
from pathlib import Path
from textwrap import dedent

from pcd_cli.filesystem import atomic_write, file_lock
from pcd_cli.integrations.shells import cmd_autorun
from pcd_cli.integrations.shells.common import (
    Shell,
    SHELL_CD_EXIT_CODE,
    SHELL_MODE_ENV,
    SHELL_WRAPPER_ENV,
    ShellIntegrationError,
    ShellIntegrationState,
)

_MANAGED_HEADER = "@echo off\nrem Managed by pcd. Changes will be overwritten.\n"


def config_path(shell: Shell, home: Path) -> Path:
    _require_supported(shell)
    local_app_data = os.environ.get("LOCALAPPDATA")
    data_home = Path(local_app_data) if local_app_data else home / "AppData" / "Local"
    return data_home / "pcd-cli" / "cmd" / "pcd.cmd"


def reload_command(shell: Shell, wrapper: Path) -> str:
    _require_supported(shell)
    return cmd_autorun.doskey_command(wrapper)


def startup_command(shell: Shell) -> None:
    _require_supported(shell)
    return None


def state(shell: Shell, wrapper: Path) -> ShellIntegrationState:
    _require_supported(shell)
    _require_windows("inspected")

    if not wrapper.exists():
        if cmd_autorun.configured(wrapper):
            raise ShellIntegrationError(
                "CMD integration is incomplete: the registered wrapper is missing"
            )

        return ShellIntegrationState.ABSENT

    if not _is_managed(_read(wrapper)):
        return ShellIntegrationState.MANUAL

    if not cmd_autorun.configured(wrapper):
        raise ShellIntegrationError(
            "CMD integration is incomplete: AutoRun registration is missing"
        )

    return ShellIntegrationState.MANAGED


def install(shell: Shell, wrapper: Path) -> bool:
    _require_supported(shell)
    _require_windows("installed")

    content = render(shell)

    with file_lock(wrapper):
        if wrapper.exists():
            previous_content = _read(wrapper)
            if not _is_managed(previous_content):
                return False

            changed = False
            added_to_autorun = False

            try:
                if previous_content != content:
                    _write(wrapper, content)
                    changed = True

                if not cmd_autorun.configured(wrapper):
                    added_to_autorun = cmd_autorun.add(wrapper)
                    if added_to_autorun:
                        changed = True
            except BaseException:
                if added_to_autorun:
                    with suppress(OSError, ShellIntegrationError):
                        cmd_autorun.remove(wrapper)

                if changed:
                    with suppress(OSError):
                        _write(wrapper, previous_content)

                raise

            return changed

        added_to_autorun = False

        try:
            _write(wrapper, content)
            added_to_autorun = cmd_autorun.add(wrapper)
        except BaseException:
            if added_to_autorun:
                with suppress(OSError, ShellIntegrationError):
                    cmd_autorun.remove(wrapper)

            wrapper.unlink(missing_ok=True)
            raise

        return True


def uninstall(shell: Shell, wrapper: Path) -> bool:
    _require_supported(shell)
    _require_windows("uninstalled")

    with file_lock(wrapper):
        if not wrapper.exists():
            if not cmd_autorun.configured(wrapper):
                return False

            cmd_autorun.remove(wrapper)
            return True

        if not _is_managed(_read(wrapper)):
            return False

        cmd_autorun.remove(wrapper)
        wrapper.unlink(missing_ok=True)

        return True


def render(shell: Shell) -> str:
    _require_supported(shell)
    return dedent(
        f"""\
        @echo off
        rem Managed by pcd. Changes will be overwritten.
        setlocal
        set "{SHELL_WRAPPER_ENV}=cmd"
        if not defined _PCD_EXECUTABLE set "_PCD_EXECUTABLE=pcd.exe"

        if defined _PCD_COMPLETE goto pcd_direct

        set "{SHELL_MODE_ENV}=1"
        set "pcd_output=%TEMP%\\pcd-%RANDOM%-%RANDOM%.tmp"
        call "%_PCD_EXECUTABLE%" %* > "%pcd_output%"
        set "pcd_code=%ERRORLEVEL%"
        if "%pcd_code%"=="{SHELL_CD_EXIT_CODE}" goto pcd_change_directory
        if not "%pcd_code%"=="0" goto pcd_error
        type "%pcd_output%"
        del /q "%pcd_output%"
        endlocal & exit /b 0

        :pcd_change_directory
        set /p "pcd_destination=" < "%pcd_output%"
        del /q "%pcd_output%"
        endlocal & cd /d "%pcd_destination%"
        exit /b %ERRORLEVEL%

        :pcd_error
        del /q "%pcd_output%"
        endlocal & exit /b %pcd_code%

        :pcd_direct
        call "%_PCD_EXECUTABLE%" %*
        exit /b %ERRORLEVEL%
        """
    )


def _write(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    with atomic_write(path) as stream:
        stream.write(content)

    if mode is not None:
        path.chmod(mode)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ShellIntegrationError(f"CMD wrapper is not valid UTF-8: {path}") from exc


def _is_managed(content: str) -> bool:
    return content.startswith(_MANAGED_HEADER)


def _require_windows(action: str) -> None:
    if sys.platform != "win32":
        raise ShellIntegrationError(f"CMD integration can only be {action} on Windows")


def _require_supported(shell: Shell) -> None:
    if shell is not Shell.CMD:
        raise ValueError(f"Unsupported CMD shell: {shell}")
