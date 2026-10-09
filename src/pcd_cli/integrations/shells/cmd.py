"""CMD wrapper backend and installation lifecycle."""

import os
import stat
from pathlib import Path
from textwrap import dedent

from pcd_cli.filesystem import atomic_write, file_lock
from pcd_cli.integrations.shells import cmd_autorun
from pcd_cli.integrations.shells.base import ShellDriver
from pcd_cli.integrations.shells.common import (
    SHELL_CD_EXIT_CODE,
    SHELL_MODE_ENV,
    SHELL_WRAPPER_ENV,
    ShellChange,
    ShellIntegrationError,
    ShellIntegrationState,
)

_MANAGED_HEADER = "@echo off\nrem Managed by pcd. Changes will be overwritten.\n"


class CmdDriver(ShellDriver):
    _SCRIPT = dedent(
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

    def config_path(self, home: Path) -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        data_home = Path(local_app_data) if local_app_data else home / "AppData" / "Local"
        return data_home / "pcd-cli" / "cmd" / "pcd.cmd"

    def reload_command(self, path: Path) -> str:
        return cmd_autorun.doskey_command(path)

    def state(self, path: Path) -> ShellIntegrationState:
        content = _read(path)
        if content is None:
            if cmd_autorun.configured(path):
                raise ShellIntegrationError(
                    "CMD integration is incomplete: the registered wrapper is missing"
                )

            return ShellIntegrationState.ABSENT

        if not _is_managed(content):
            return ShellIntegrationState.MANUAL

        if not cmd_autorun.configured(path):
            raise ShellIntegrationError(
                "CMD integration is incomplete: AutoRun registration is missing"
            )

        return ShellIntegrationState.MANAGED

    def install(self, path: Path) -> ShellChange:
        content = self.render()

        with file_lock(path):
            previous = _read(path)
            if previous is not None and not _is_managed(previous):
                return ShellChange.MANUAL

            wrapper_changed = previous != content
            try:
                if wrapper_changed:
                    _write(path, content)
                autorun_changed = cmd_autorun.add(path)
            except BaseException as error:
                if wrapper_changed:
                    try:
                        if previous is None:
                            path.unlink(missing_ok=True)
                        else:
                            _write(path, previous)
                    except OSError as rollback_error:
                        raise ShellIntegrationError(
                            f"CMD installation failed: {error}; "
                            f"could not restore wrapper {path}: {rollback_error}"
                        ) from error
                raise

            if wrapper_changed or autorun_changed:
                return ShellChange.CHANGED
            return ShellChange.UNCHANGED

    def uninstall(self, path: Path) -> ShellChange:
        with file_lock(path):
            content = _read(path)
            if content is None:
                if cmd_autorun.remove(path):
                    return ShellChange.CHANGED
                return ShellChange.UNCHANGED

            if not _is_managed(content):
                return ShellChange.MANUAL

            cmd_autorun.remove(path)
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                try:
                    cmd_autorun.add(path)
                except (OSError, ShellIntegrationError) as rollback_error:
                    raise ShellIntegrationError(
                        f"Could not remove CMD wrapper {path}: {error}; "
                        f"could not restore AutoRun: {rollback_error}"
                    ) from error
                raise

            return ShellChange.CHANGED

    def render(self) -> str:
        return self._SCRIPT


def _write(path: Path, content: str) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = None
    with atomic_write(path) as stream:
        stream.write(content)

    if mode is not None:
        path.chmod(mode)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeError as exc:
        raise ShellIntegrationError(f"CMD wrapper is not valid UTF-8: {path}") from exc


def _is_managed(content: str) -> bool:
    return content.startswith(_MANAGED_HEADER)
