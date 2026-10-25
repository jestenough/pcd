from __future__ import annotations

from typing import TYPE_CHECKING

from pcd_cli.cache import ProjectCache
from pcd_cli.catalog import discovered, ProjectCatalog, RefreshReport
from pcd_cli.config import Config
from pcd_cli.filesystem import canonical_path
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest

    from pcd_cli.cache import ProjectCacheEntry


def test_refresh_prefers_manual_on_duplicate(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    manual = Project("custom", canonical_path(repo), repo, ProjectSource.MANUAL)

    assert projects.config.add_root(root)
    assert projects.config.add_project(manual)

    assert projects.refresh() == [manual]


def test_discovered_excludes_manual_projects(tmp_path: Path) -> None:
    manual = Project("manual", tmp_path / "manual", tmp_path / "manual", ProjectSource.MANUAL)
    scanned = Project(
        "scanned", tmp_path / "scanned", tmp_path / "scanned", ProjectSource.DISCOVERED
    )

    assert list(discovered((manual, scanned))) == [scanned]


def test_refresh_report_compares_discovered_paths(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    first = root / "first"
    (first / ".git").mkdir(parents=True)
    manual_path = tmp_path / "manual"
    manual_path.mkdir()
    manual = Project("manual", canonical_path(manual_path), manual_path, ProjectSource.MANUAL)
    assert projects.config.add_root(root)
    assert projects.config.add_project(manual)

    assert projects.refresh_with_report() == RefreshReport(added=1, removed=0, total=2)
    assert projects.refresh_with_report() == RefreshReport(added=0, removed=0, total=2)

    second = root / "second"
    (second / ".git").mkdir(parents=True)
    (first / ".git").rmdir()
    assert projects.refresh_with_report() == RefreshReport(added=1, removed=1, total=2)
    assert {project.path for project in discovered(projects.cache.load() or ())} == {second}


def test_refresh_report_uses_snapshot_from_automatic_scan(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert projects.config.add_root(root)
    projects.refresh()
    repo = root / "new"
    (repo / ".git").mkdir(parents=True)

    assert [item.name for item in projects.resolve("new").matches] == ["new"]
    assert projects.refresh_with_report() == RefreshReport(added=0, removed=0, total=1)


def test_projects_build_cache_when_missing(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "repo/.git").mkdir(parents=True)
    assert projects.config.add_root(root)

    items = projects.projects()

    assert [item.name for item in items] == ["repo"]
    assert projects.cache.load() == items


def test_projects_uses_valid_empty_cache(
    projects: ProjectCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects.cache.save(())

    def fail(_catalog: ProjectCatalog) -> list[Project]:
        raise AssertionError("valid empty cache must not refresh")

    monkeypatch.setattr(ProjectCatalog, "refresh", fail)

    assert projects.projects() == []


def test_completion_candidates_are_empty_without_cache(projects: ProjectCatalog) -> None:
    assert list(projects.completion_candidates("repo")) == []


def test_completion_uses_only_cache(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = Project("repo", tmp_path / "repo", tmp_path / "repo", ProjectSource.MANUAL)
    projects.cache.save((item,))

    def fail(*_args: object) -> None:
        raise AssertionError("completion must not scan or load history")

    monkeypatch.setattr("pcd_cli.catalog.ProjectScanner.scan", fail)
    monkeypatch.setattr("pcd_cli.catalog.UsageHistory.load", fail)
    assert list(projects.completion_candidates("re")) == [item]


def test_matches_refreshes_after_cache_miss(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert projects.config.add_root(root)
    projects.refresh()

    repo = root / "new"
    (repo / ".git").mkdir(parents=True)

    assert [item.name for item in projects.resolve("new").matches] == ["new"]


def test_exact_only_does_not_use_fuzzy(projects: ProjectCatalog, tmp_path: Path) -> None:
    path = tmp_path / "medcab"
    path.mkdir()
    project = Project("medcab", canonical_path(path), path, ProjectSource.MANUAL)
    assert projects.config.add_project(project)
    projects.refresh()

    assert projects.resolve("med", exact_only=True).matches == []


def test_record_updates_history(projects: ProjectCatalog, tmp_path: Path) -> None:
    path = tmp_path / "repo"
    path.mkdir()
    project = Project("repo", canonical_path(path), path, ProjectSource.MANUAL)

    projects.record_usage(project)

    assert projects.history.load()[project.path].count == 1


def test_parent_root(projects: ProjectCatalog, tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    assert projects.config.add_root(outer)

    assert projects.find_parent_root(inner) == outer
    assert projects.find_parent_root(outer) is None


def test_root_mutations_refresh_cache(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "repo/.git").mkdir(parents=True)

    assert projects.add_scan_root(root)
    assert [item.name for item in (projects.cache.load() or [])] == ["repo"]
    assert projects.remove_scan_root(root)
    assert projects.cache.load() == []


def test_manual_mutations_refresh_cache(projects: ProjectCatalog, tmp_path: Path) -> None:
    path = tmp_path / "notes"
    path.mkdir()
    project = Project("notes", canonical_path(path), path, ProjectSource.MANUAL)

    assert projects.add_project(project)
    assert projects.cache.load() == [project]
    assert projects.remove_project(project.path)
    assert projects.cache.load() == []


def test_manual_add_rejects_existing_git_project(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    assert projects.config.add_root(root)

    manual = Project("custom", canonical_path(repo), repo, ProjectSource.MANUAL)

    assert projects.add_project(manual) is False


def test_manual_add_does_not_publish_conflict_check(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "notes"
    path.mkdir()
    project = Project("notes", canonical_path(path), path, ProjectSource.MANUAL)
    writes: list[list[Project]] = []
    original_save = ProjectCache.save

    def save(cache: ProjectCache, items: list[Project]) -> None:
        writes.append(list(items))
        original_save(cache, items)

    monkeypatch.setattr(ProjectCache, "save", save)
    assert projects.add_project(project)
    assert writes == [[project]]


def test_manual_add_conflict_does_not_publish_cache(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    assert projects.config.add_root(root)
    manual = Project("custom", canonical_path(repo), repo, ProjectSource.MANUAL)
    writes: list[list[Project]] = []

    def save(_cache: ProjectCache, items: list[Project]) -> None:
        writes.append(list(items))

    monkeypatch.setattr(ProjectCache, "save", save)
    assert projects.add_project(manual) is False
    assert writes == []


def test_fuzzy_match_refreshes_after_cache_miss(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert projects.config.add_root(root)
    projects.refresh()

    repo = root / "medcab"
    (repo / ".git").mkdir(parents=True)

    assert [item.name for item in projects.resolve("mdc").matches] == ["medcab"]


def test_search_recovers_when_cache_disappears(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    (root / "repo/.git").mkdir(parents=True)
    assert projects.config.add_root(root)
    projects.cache.save(())
    monkeypatch.setattr(ProjectCache, "load", lambda _cache: None)

    assert [item.name for item in projects.resolve("repo").matches] == ["repo"]


def test_parent_root_prefers_nearest_root(projects: ProjectCatalog, tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    child = inner / "child"
    child.mkdir(parents=True)

    assert projects.config.add_root(outer)
    assert projects.config.add_root(inner)

    assert projects.find_parent_root(child) == inner
    assert projects.find_parent_root(tmp_path / "elsewhere") is None


def test_parent_root_ignores_shallower_match_after_nearest(
    projects: ProjectCatalog,
    tmp_path: Path,
) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    child = inner / "child"
    child.mkdir(parents=True)
    assert projects.config.add_root(inner)
    assert projects.config.add_root(outer)

    assert projects.find_parent_root(child) == inner


def test_exact_duplicates_use_history(projects: ProjectCatalog, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    items = (
        Project("same", canonical_path(first), first, ProjectSource.MANUAL),
        Project("same", canonical_path(second), second, ProjectSource.MANUAL),
    )
    projects.cache.save(items)
    projects.history.record(items[1].path)

    assert projects.resolve("same").matches == [items[1], items[0]]


def test_search_fuzzy_miss_keeps_streaming_exact_lookup(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert projects.config.add_root(root)
    projects.refresh()
    (root / "medcab" / ".git").mkdir(parents=True)
    reads = 0
    original_entries = ProjectCache._entries

    def entries(cache: ProjectCache) -> Iterator[ProjectCacheEntry]:
        nonlocal reads
        reads += 1
        return original_entries(cache)

    monkeypatch.setattr(ProjectCache, "_entries", entries)

    assert [item.name for item in projects.resolve("mdc").matches] == ["medcab"]
    assert reads == 2


def test_fuzzy_hit_reuses_one_full_cache_load(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [
        Project(name, tmp_path / name, tmp_path / name, ProjectSource.MANUAL)
        for name in ("alpha", "medcab", "beta")
    ]
    projects.cache.save(items)
    reads = 0
    original_entries = ProjectCache._entries

    def entries(cache: ProjectCache) -> Iterator[ProjectCacheEntry]:
        nonlocal reads
        reads += 1
        yield from original_entries(cache)

    monkeypatch.setattr(ProjectCache, "_entries", entries)
    assert projects.resolve("mdc").matches == [items[1]]
    assert reads == 2


def test_fuzzy_many_results_preserves_history_order(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    items = [
        Project(
            f"app-{index:02d}", tmp_path / str(index), tmp_path / str(index), ProjectSource.MANUAL
        )
        for index in range(50)
    ]
    projects.cache.save(items)
    projects.history.record(items[-1].path)

    matches = projects.resolve("app-").matches

    assert len(matches) == len(items)
    assert matches[0] == items[-1]


def test_exact_hit_avoids_full_decode_and_history(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "repo"
    project = Project("repo", path, path, ProjectSource.MANUAL)
    projects.cache.save((project,))

    def fail(*_args: object) -> None:
        raise AssertionError("exact hit must not load the full cache or history")

    monkeypatch.setattr(ProjectCache, "load", fail)
    monkeypatch.setattr(type(projects.history), "load", fail)
    monkeypatch.setattr(ProjectCatalog, "refresh", fail)
    assert projects.resolve("repo").matches == [project]


def test_duplicates_after_refresh_use_same_history_order(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    first = Project("same", tmp_path / "first", tmp_path / "first", ProjectSource.MANUAL)
    second = Project("same", tmp_path / "second", tmp_path / "second", ProjectSource.MANUAL)
    assert projects.config.add_project(first)
    assert projects.config.add_project(second)
    projects.history.record(second.path)

    fresh = projects.resolve("same", exact_only=True)
    cached = projects.resolve("same", exact_only=True)

    assert fresh.matches == cached.matches == [second, first]
    assert fresh.usage == cached.usage


def test_manual_add_handles_concurrent_config_change(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "repo"
    project = Project("repo", canonical_path(path), path, ProjectSource.MANUAL)
    monkeypatch.setattr(Config, "add_project", lambda _config, _project: False)

    assert projects.add_project(project) is False


def test_remove_missing_project_returns_false(projects: ProjectCatalog, tmp_path: Path) -> None:
    assert projects.remove_project(tmp_path / "missing") is False
