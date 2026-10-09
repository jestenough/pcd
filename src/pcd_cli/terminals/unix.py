"""Interactive terminal output for Linux and macOS."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

from prompt_toolkit.output.defaults import create_output

if TYPE_CHECKING:
    from collections.abc import Iterator

    from prompt_toolkit.output import Output


@contextmanager
def open_output() -> Iterator[Output]:
    yield create_output(stdout=sys.stderr)
