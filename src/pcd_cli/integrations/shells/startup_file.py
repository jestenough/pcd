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
    return _state_from_content(_read(path), shell)


def install(path: Path, shell: Shell, startup_command: str) -> bool:
    with file_lock(path):
        content = _read(path)
        if _state_from_content(content, shell) is not ShellIntegrationState.ABSENT:
            return False

        separator = "" if not content or content.endswith("\n") else "\n"
        _write(path, f"{content}{separator}{managed_block(startup_command)}")

        return True


def uninstall(path: Path) -> bool:
    with file_lock(path):
        content = _read(path)
        bounds = _managed_block_bounds(content)
        if bounds is None:
            return False
        start, end = bounds
        _write(path, f"{content[:start]}{content[end:]}")
        return True


def managed_block(startup_command: str) -> str:
    return f"{_MANAGED_BLOCK_START}\n{startup_command}\n{_MANAGED_BLOCK_END}\n"


def _state_from_content(content: str, shell: Shell) -> ShellIntegrationState:
    if _managed_block_bounds(content) is not None:
        return ShellIntegrationState.MANAGED

    if f"pcd shell init {shell.value}" in content:
        return ShellIntegrationState.MANUAL

    return ShellIntegrationState.ABSENT


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except UnicodeError as exc:
        raise ShellIntegrationError(f"Shell config is not valid UTF-8: {path}") from exc


def _write(path: Path, content: str) -> None:
    target = path.resolve(strict=False) if path.is_symlink() else path
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    with atomic_write(target) as stream:
        stream.write(content)

    if mode is not None:
        target.chmod(mode)


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
