"""Safe management of pcd blocks in shell startup files."""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

from pcd_cli.filesystem import atomic_write, file_lock
from pcd_cli.integrations.shells.common import (
    ShellChange,
    ShellIntegrationError,
    ShellIntegrationState,
)

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Never

    from pcd_cli.integrations.shells.common import Shell

_MANAGED_BLOCK_START = "# >>> pcd shell integration >>>"
_MANAGED_BLOCK_END = "# <<< pcd shell integration <<<"


def state(path: Path, shell: Shell) -> ShellIntegrationState:
    starts = 0
    ends = 0
    manual = False
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                starts += _marker_count(line, _MANAGED_BLOCK_START)
                ends += _marker_count(line, _MANAGED_BLOCK_END)
                if ends > starts:
                    _invalid_block()
                manual = manual or f"pcd shell init {shell.value}" in line
    except FileNotFoundError:
        return ShellIntegrationState.ABSENT
    except UnicodeError as exc:
        raise ShellIntegrationError(f"Shell config is not valid UTF-8: {path}") from exc

    if starts == 0 and ends == 0:
        return ShellIntegrationState.MANUAL if manual else ShellIntegrationState.ABSENT
    if starts != 1 or ends != 1:
        _invalid_block()
    return ShellIntegrationState.MANAGED


def install(path: Path, shell: Shell, startup_command: str) -> ShellChange:
    path = path.resolve(strict=False)
    with file_lock(path):
        content = _read(path)
        block = managed_block(startup_command)
        bounds = _block_bounds(content)
        if bounds is not None:
            start, end = bounds
            updated = f"{content[:start]}{block}{content[end:]}"
            if updated == content:
                return ShellChange.UNCHANGED
            _write(path, updated)
            return ShellChange.CHANGED

        if f"pcd shell init {shell.value}" in content:
            return ShellChange.MANUAL

        separator = "" if not content or content.endswith("\n") else "\n"
        _write(path, f"{content}{separator}{block}")

        return ShellChange.CHANGED


def uninstall(path: Path, shell: Shell) -> ShellChange:
    path = path.resolve(strict=False)
    with file_lock(path):
        content = _read(path)
        bounds = _block_bounds(content)
        if bounds is None:
            if f"pcd shell init {shell.value}" in content:
                return ShellChange.MANUAL
            return ShellChange.UNCHANGED
        start, end = bounds
        _write(path, f"{content[:start]}{content[end:]}")
        return ShellChange.CHANGED


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
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = None
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
        _invalid_block()

    start = content.index(_MANAGED_BLOCK_START)
    end_marker = content.index(_MANAGED_BLOCK_END)
    for index, marker in ((start, _MANAGED_BLOCK_START), (end_marker, _MANAGED_BLOCK_END)):
        after = index + len(marker)
        if (
            end_marker < start
            or (index > 0 and content[index - 1] != "\n")
            or (after < len(content) and content[after] != "\n")
        ):
            _invalid_block()

    end = end_marker + len(_MANAGED_BLOCK_END)
    if end < len(content) and content[end] == "\n":
        end += 1

    return start, end


def _marker_count(line: str, marker: str) -> int:
    count = line.count(marker)
    if count and line.rstrip("\r\n") != marker:
        _invalid_block()
    return count


def _invalid_block() -> Never:
    raise ShellIntegrationError("Shell config contains an invalid pcd-managed integration block")
