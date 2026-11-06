from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from pcd_cli.filesystem import canonical_path
from pcd_cli.models import Project, ProjectSource
from pcd_cli.scanner import ProjectScanner

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest

    from pcd_cli.catalog import ProjectCatalog


def test_concurrent_root_mutations_preserve_config_and_cache(
    projects: ProjectCatalog,
    tmp_path: Path,
) -> None:
    roots = [tmp_path / f"root-{index}" for index in range(4)]
    for index, root in enumerate(roots):
        (root / f"repo-{index}" / ".git").mkdir(parents=True)
    barrier = threading.Barrier(len(roots))

    def add(root: Path) -> bool:
        barrier.wait(timeout=10)
        return projects.add_scan_root(root)

    with ThreadPoolExecutor(max_workers=len(roots)) as pool:
        futures = [pool.submit(add, root) for root in roots]
        results = [future.result(timeout=30) for future in futures]

    assert results == [True] * len(roots)
    assert set(projects.config.load().roots) == set(roots)
    cached = projects.cache.load()
    assert cached is not None
    assert {project.name for project in cached} == {f"repo-{index}" for index in range(len(roots))}


def test_older_refresh_cannot_publish_after_concurrent_root_addition(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "first-repo" / ".git").mkdir(parents=True)
    (second / "second-repo" / ".git").mkdir(parents=True)
    assert projects.config.add_root(first)
    started = threading.Event()
    resume = threading.Event()
    original_scan = ProjectScanner.scan

    def controlled_scan(scanner: ProjectScanner) -> Iterator[Project]:
        if scanner.settings.roots == (first,):
            started.set()
            if not resume.wait(timeout=10):
                raise AssertionError("timed out waiting to resume the older refresh")
        yield from original_scan(scanner)

    monkeypatch.setattr(ProjectScanner, "scan", controlled_scan)

    with ThreadPoolExecutor(max_workers=1) as pool:
        older = pool.submit(projects.refresh)
        try:
            assert started.wait(timeout=10)
            assert projects.add_scan_root(second)
        finally:
            resume.set()

        stale_result = older.result(timeout=30)

    cached = projects.cache.load()
    assert cached is not None
    assert {project.name for project in cached} == {"first-repo", "second-repo"}
    assert stale_result == cached


def test_concurrent_manual_additions_preserve_config_and_cache(
    projects: ProjectCatalog,
    tmp_path: Path,
) -> None:
    paths = [tmp_path / f"manual-{index}" for index in range(4)]
    for path in paths:
        path.mkdir()
    barrier = threading.Barrier(len(paths))

    def add(path: Path) -> bool:
        item = Project(path.name, canonical_path(path), path, ProjectSource.MANUAL)
        barrier.wait(timeout=10)
        return projects.add_project(item)

    with ThreadPoolExecutor(max_workers=len(paths)) as pool:
        futures = [pool.submit(add, path) for path in paths]
        results = [future.result(timeout=30) for future in futures]

    assert results == [True] * len(paths)
    assert {project.path for project in projects.config.load().manual_projects} == {
        canonical_path(path) for path in paths
    }
    cached = projects.cache.load()
    assert cached is not None
    assert {project.path for project in cached} == {canonical_path(path) for path in paths}
