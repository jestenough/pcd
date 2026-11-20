"""Resolve a catalog query and perform at most one refresh/retry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pcd_cli.lookup import lookup_snapshot, MemorySnapshot

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from pcd_cli.lookup import LookupRequest, LookupResult, ProjectStore, UsageLoader
    from pcd_cli.models import Project, ProjectUsage


@dataclass(frozen=True, slots=True)
class ProjectResolver:
    store: ProjectStore
    refresh: Callable[[], list[Project]]
    load_usage: UsageLoader

    def resolve(
        self,
        request: LookupRequest,
        *,
        refresh_on_miss: bool = True,
        usage: Mapping[Path, ProjectUsage] | None = None,
    ) -> LookupResult:
        snapshot_usage = usage

        def history() -> Mapping[Path, ProjectUsage]:
            nonlocal snapshot_usage
            if snapshot_usage is None:
                snapshot_usage = self.load_usage()
            return snapshot_usage

        result = self.store.lookup(request, history)
        if result is None and (
            request.directories is not None or request.directory_entries is not None
        ):
            result = lookup_snapshot(MemorySnapshot(()), request, history)
        if result is not None and (result.total or not refresh_on_miss):
            return result
        if not refresh_on_miss:
            return lookup_snapshot(MemorySnapshot(()), request)
        projects = self.refresh()
        result = self.store.lookup(request, history)
        # The cache can be removed after publication. The completed scan is still usable.
        return (
            result
            if result is not None
            else lookup_snapshot(MemorySnapshot(projects), request, history)
        )
