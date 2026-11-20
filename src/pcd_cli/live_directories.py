"""Query-local directory checks with bounded enumeration of dense siblings."""

from __future__ import annotations

import os
from itertools import groupby, islice
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


def live_directories[Entry](
    entries: Iterator[Entry], path_of: Callable[[Entry], str]
) -> Iterator[Entry]:
    # Canonical stored paths use native separators. Keep the trailing separator so
    # a Windows drive root is checked as C:\ rather than the drive-relative C:.
    def parent(entry: Entry) -> str:
        path = path_of(entry)
        return path[: path.rfind(os.sep) + 1]

    for folder, siblings in groupby(entries, key=parent):
        fallback = False
        while batch := list(islice(siblings, 4096)):
            live = None
            if len(batch) >= 16 and not fallback:
                live = _enumerate(folder, {path_of(item)[len(folder) :] for item in batch})
                fallback = live is None
            for item in batch:
                path = path_of(item)
                known = None if live is None else live.get(path[len(folder) :])
                # A filesystem can accept spellings different from its directory listing.
                if known is None:
                    known = Path(path).is_dir()
                if known:
                    yield item


def _enumerate(parent: str, wanted: set[str]) -> dict[str, bool] | None:
    result: dict[str, bool] = {}
    scan_limit = max(256, len(wanted) * 4)
    try:
        with os.scandir(parent) as children:
            for index, child in enumerate(children):
                if index >= scan_limit:
                    return None
                if child.name in wanted:
                    try:
                        result[child.name] = child.is_dir()
                    except OSError:
                        result[child.name] = Path(child.path).is_dir()
                    if len(result) == len(wanted):
                        break
    except (OSError, NotImplementedError):
        return None
    return result
