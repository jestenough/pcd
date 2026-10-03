"""CMD wrapper backend and installation lifecycle."""

import os
import stat
import sys
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
    local_app_data = os.environ.get("LOCALAPPDATA")
    data_home = Path(local_app_data) if local_app_data else home / "AppData" / "Local"
    return data_home / "pcd-cli" / "cmd" / "pcd.cmd"


def reload_command(shell: Shell, wrapper: Path) -> str:
    return cmd_autorun.doskey_command(wrapper)


def startup_command(shell: Shell) -> None:
    return None


def state(wrapper: Path) -> ShellIntegrationState:
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


def install(wrapper: Path) -> bool:
    _require_windows("installed")

    content = render(Shell.CMD)

    with file_lock(wrapper):
        previous = _read(wrapper) if wrapper.exists() else None
        if previous is not None and not _is_managed(previous):
            return False

        wrapper_changed = previous != content
        try:
            if wrapper_changed:
                _write(wrapper, content)
            autorun_changed = cmd_autorun.add(wrapper)
        except BaseException as error:
            if wrapper_changed:
                try:
                    if previous is None:
                        wrapper.unlink(missing_ok=True)
                    else:
                        _write(wrapper, previous)
                except OSError as rollback_error:
                    raise ShellIntegrationError(
                        f"CMD installation failed: {error}; "
                        f"could not restore wrapper {wrapper}: {rollback_error}"
                    ) from error
            raise

        return wrapper_changed or autorun_changed


def uninstall(wrapper: Path) -> bool:
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
        try:
            wrapper.unlink(missing_ok=True)
        except OSError as error:
            try:
                cmd_autorun.add(wrapper)
            except (OSError, ShellIntegrationError) as rollback_error:
                raise ShellIntegrationError(
                    f"Could not remove CMD wrapper {wrapper}: {error}; "
                    f"could not restore AutoRun: {rollback_error}"
                ) from error
            raise

        return True


def render(shell: Shell) -> str:
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
        type "%pcd_output%"
        del /q "%pcd_output%"
        endlocal & exit /b %pcd_code%

        :pcd_change_directory
        set /p "pcd_destination=" < "%pcd_output%"
        del /q "%pcd_output%"
        endlocal & cd /d "%pcd_destination%"
        exit /b %ERRORLEVEL%

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
