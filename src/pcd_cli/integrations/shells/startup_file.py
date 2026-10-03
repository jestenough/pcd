"""Safe management of pcd blocks in shell startup files."""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

from pcd_cli.filesystem import atomic_write, file_lock
from pcd_cli.integrations.shells.common import ShellIntegrationError, ShellIntegrationState

if TYPE_CHECKING:
    from pathlib import Path

    from pcd_cli.integrations.shells.common import Shell

_MANAGED_BLOCK_START = "# >>> pcd shell integration >>>"
_MANAGED_BLOCK_END = "# <<< pcd shell integration <<<"


def state(path: Path, shell: Shell) -> ShellIntegrationState:
    content = _read(path)
    if _block_bounds(content) is not None:
        return ShellIntegrationState.MANAGED
    if f"pcd shell init {shell.value}" in content:
        return ShellIntegrationState.MANUAL
    return ShellIntegrationState.ABSENT


def install(path: Path, shell: Shell, startup_command: str) -> bool:
    path = path.resolve(strict=False)
    with file_lock(path):
        content = _read(path)
        block = managed_block(startup_command)
        bounds = _block_bounds(content)
        if bounds is not None:
            start, end = bounds
            updated = f"{content[:start]}{block}{content[end:]}"
            if updated == content:
                return False
            _write(path, updated)
            return True

        if f"pcd shell init {shell.value}" in content:
            return False

        separator = "" if not content or content.endswith("\n") else "\n"
        _write(path, f"{content}{separator}{block}")

        return True


def uninstall(path: Path) -> bool:
    path = path.resolve(strict=False)
    with file_lock(path):
        content = _read(path)
        bounds = _block_bounds(content)
        if bounds is None:
            return False
        start, end = bounds
        _write(path, f"{content[:start]}{content[end:]}")
        return True


def managed_block(startup_command: str) -> str:
    return f"{_MANAGED_BLOCK_START}\n{startup_command}\n{_MANAGED_BLOCK_END}\n"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except UnicodeError as exc:
        raise ShellIntegrationError(f"Shell config is not valid UTF-8: {path}") from exc


def _write(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    with atomic_write(path) as stream:
        stream.write(content)

    if mode is not None:
        path.chmod(mode)


def _block_bounds(content: str) -> tuple[int, int] | None:
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
    for index, marker in ((start, _MANAGED_BLOCK_START), (end_marker, _MANAGED_BLOCK_END)):
        after = index + len(marker)
        if (
            end_marker < start
            or (index > 0 and content[index - 1] != "\n")
            or (after < len(content) and content[after] != "\n")
        ):
            raise ShellIntegrationError(
                "Shell config contains an invalid pcd-managed integration block"
            )

    end = end_marker + len(_MANAGED_BLOCK_END)
    if end < len(content) and content[end] == "\n":
        end += 1

    return start, end
