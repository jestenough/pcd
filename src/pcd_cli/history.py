from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, TypeGuard

from pcd_cli.filesystem import atomic_write, file_lock
from pcd_cli.models import ProjectUsage

DEFAULT_HISTORY_LIMIT = 2048


class UsageHistoryEntry(TypedDict):
    used_at: int | float
    count: int


@dataclass(frozen=True, slots=True)
class UsageHistory:
    """Bounded usage history used as a tie-breaker during search."""

    path: Path
    limit: int = DEFAULT_HISTORY_LIMIT

    def load(self) -> dict[Path, ProjectUsage]:
        if not self.path.is_file():
            return {}

        try:
            with self.path.open(encoding="utf-8") as stream:
                raw: object = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}

        return _decode_usage(raw)

    def record(self, path: Path) -> None:
        with file_lock(self.path):
            usage = self.load()
            previous = usage.get(path)
            count = 1 if previous is None else previous.count + 1
            usage[path] = ProjectUsage(used_at=time.time(), count=count)
            retained = _retain_recent(usage, self.limit)

            payload: dict[str, UsageHistoryEntry] = {
                str(project_path): {"used_at": item.used_at, "count": item.count}
                for project_path, item in retained.items()
            }
            with atomic_write(self.path) as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")


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
    valid_time = not isinstance(used_at, bool) and isinstance(used_at, int | float)
    valid_count = not isinstance(count, bool) and isinstance(count, int) and count > 0
    return valid_time and valid_count


def _retain_recent(
    usage: dict[Path, ProjectUsage],
    limit: int,
) -> dict[Path, ProjectUsage]:
    if limit <= 0:
        return {}
    if len(usage) <= limit:
        return usage

    newest = sorted(
        usage.items(),
        key=lambda item: (item[1].used_at, item[1].count, str(item[0])),
        reverse=True,
    )[:limit]
    return dict(newest)
