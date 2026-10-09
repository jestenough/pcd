"""Create prompt-toolkit output connected to the interactive terminal."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from prompt_toolkit.output import Output

from pcd_cli.environment import current_platform, Platform


@contextmanager
def terminal_output() -> Iterator[Output]:
    """Yield output for terminal UI while leaving stdout available for protocols."""
    match current_platform():
        case Platform.LINUX | Platform.MACOS:
            from pcd_cli.terminals.unix import open_output
        case Platform.WINDOWS:
            from pcd_cli.terminals.windows import open_output

    with open_output() as output:
        yield output
