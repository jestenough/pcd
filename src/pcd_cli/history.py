from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, TypeGuard

from pcd_cli.models import ProjectUsage

DEFAULT_HISTORY_LIMIT = 2048


class UsageHistoryEntry(TypedDict):
    used_at: int | float
    count: int


@dataclass(frozen=True, slots=True)
class UsageHistory:
    """Project-ranking facade over the shared navigation history store."""

    path: Path
    limit: int = DEFAULT_HISTORY_LIMIT

    def load(self) -> dict[Path, ProjectUsage]:
        from pcd_cli.visits import DirectoryVisits

        return DirectoryVisits(self.path).usage()

    def record(self, path: Path) -> None:
        from pcd_cli.visits import DirectoryVisits

        DirectoryVisits(self.path).record(path, project_only=True, limit=self.limit)


def load_legacy(path: Path) -> dict[Path, ProjectUsage]:
    """Read the former JSON history without modifying the recovery source."""
    try:
        with path.open(encoding="utf-8") as stream:
            raw: object = json.load(stream)
    except (FileNotFoundError, ValueError, RecursionError):
        return {}
    return _decode_usage(raw)


def _decode_usage(value: object) -> dict[Path, ProjectUsage]:
    if not _is_usage_mapping(value):
        return {}

    return {
        Path(path): ProjectUsage(float(entry["used_at"]), entry["count"])
        for path, entry in value.items()
    }


def _is_usage_mapping(value: object) -> TypeGuard[dict[str, UsageHistoryEntry]]:
    return isinstance(value, dict) and all(
        isinstance(path, str) and _is_usage_entry(entry) for path, entry in value.items()
    )


def _is_usage_entry(value: object) -> TypeGuard[UsageHistoryEntry]:
    if not isinstance(value, dict):
        return False

    used_at = value.get("used_at")
    count = value.get("count")
    if isinstance(used_at, bool) or not isinstance(used_at, int | float):
        return False
    try:
        valid_time = math.isfinite(used_at)
    except OverflowError:
        return False
    valid_count = not isinstance(count, bool) and isinstance(count, int) and 0 < count <= 2**63 - 1
    return valid_time and valid_count
