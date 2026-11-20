from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pcd_cli.cache import ProjectCache
from pcd_cli.catalog import discovered, ProjectCatalog, RefreshReport
from pcd_cli.config import Config
from pcd_cli.filesystem import canonical_path
from pcd_cli.lookup import LookupRequest
from pcd_cli.models import Project, ProjectSource
from pcd_cli.scanner import ProjectScanner, ScanError
from pcd_cli.storage import SQLiteProjectStore, SQLiteSnapshot

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from sqlite3 import Connection

    from pcd_cli.lookup import LookupEntry
    from pcd_cli.models import ProjectSettings


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


def test_refresh_does_not_publish_older_snapshot(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newer = Project("newer", tmp_path / "newer", tmp_path / "newer", ProjectSource.MANUAL)
    assert projects.cache.save((newer,), started_at_ns=200)
    monkeypatch.setattr("pcd_cli.catalog.time.time_ns", lambda: 100)

    assert projects.refresh() == [newer]
    assert projects.cache.load() == [newer]


def test_stale_refresh_returns_newer_empty_snapshot(
    projects: ProjectCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert projects.cache.save((), started_at_ns=200)
    monkeypatch.setattr("pcd_cli.catalog.time.time_ns", lambda: 100)

    assert projects.refresh() == []
    assert projects.cache.load() == []


def test_refresh_report_does_not_count_stale_scan(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newer = Project("newer", tmp_path / "newer", tmp_path / "newer", ProjectSource.MANUAL)
    assert projects.cache.save((newer,), started_at_ns=200)
    monkeypatch.setattr("pcd_cli.catalog.time.time_ns", lambda: 100)

    assert projects.refresh_with_report() == RefreshReport(added=0, removed=0, total=1)
    assert projects.cache.load() == [newer]


def test_refresh_does_not_publish_partial_scan(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    (root / "repo" / ".git").mkdir(parents=True)
    assert projects.config.add_root(root)
    first = projects.refresh()
    cache_before = projects.cache.path.read_bytes()
    real_scandir = os.scandir

    def broken(path: str | os.PathLike[str]) -> Iterator[os.DirEntry[str]]:
        if os.fspath(path) == os.fspath(root):
            raise PermissionError
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", broken)

    with pytest.raises(ScanError):
        projects.refresh_with_report()

    assert projects.cache.path.read_bytes() == cache_before
    assert projects.cache.load() == first


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


@pytest.mark.parametrize("unreadable", ["root", "git-marker"])
def test_refresh_preserves_cache_when_metadata_is_unreadable(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unreadable: str,
) -> None:
    root = tmp_path / "root"
    marker = root / "repo" / ".git"
    marker.mkdir(parents=True)
    assert projects.config.add_root(root)
    projects.refresh()
    previous = projects.cache.path.read_bytes()
    blocked = root if unreadable == "root" else marker
    original_stat = Path.stat

    def stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == blocked:
            raise PermissionError(13, "Access denied", str(path))
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", stat)

    with pytest.raises(ScanError) as caught:
        projects.refresh()

    assert caught.value.errors[0][0] == blocked
    assert projects.cache.path.read_bytes() == previous


def test_projects_build_cache_when_missing(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "repo/.git").mkdir(parents=True)
    assert projects.config.add_root(root)

    items = projects.projects()

    assert [item.name for item in items] == ["repo"]
    assert projects.cache.load() == items


def test_projects_rebuilds_legacy_cache_without_changing_config(
    projects: ProjectCatalog,
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    assert projects.config.add_root(root)
    config_before = projects.config.path.read_text(encoding="utf-8")
    projects.cache.path.parent.mkdir(parents=True, exist_ok=True)
    projects.cache.path.write_text(
        '{"name":"stale","path":"/stale","display":"/stale","source":"discovered"}\n',
        encoding="utf-8",
    )

    items = projects.projects()

    assert [item.name for item in items] == ["repo"]
    assert projects.config.path.read_text(encoding="utf-8") == config_before
    assert SQLiteProjectStore(projects.cache.path).load() == items


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
    projects.cache.save(())

    manual = Project("custom", canonical_path(repo), repo, ProjectSource.MANUAL)

    assert projects.add_project(manual) is False
    assert projects.config.load().manual_projects == ()
    assert projects.cache.load() == []


def test_manual_add_does_not_publish_conflict_check(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "notes"
    path.mkdir()
    project = Project("notes", canonical_path(path), path, ProjectSource.MANUAL)
    writes: list[list[Project]] = []
    original_save = SQLiteProjectStore.save

    def save(
        cache: SQLiteProjectStore,
        items: list[Project],
        *,
        started_at_ns: int | None = None,
    ) -> bool:
        writes.append(list(items))
        return original_save(cache, items, started_at_ns=started_at_ns)

    monkeypatch.setattr(SQLiteProjectStore, "save", save)
    assert projects.add_project(project)
    assert writes == [[project]]


def test_manual_add_refreshes_when_publication_is_rejected(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "notes"
    path.mkdir()
    project = Project("notes", canonical_path(path), path, ProjectSource.MANUAL)
    original_save = SQLiteProjectStore.save
    saves = 0

    def save(
        cache: SQLiteProjectStore,
        items: list[Project],
        *,
        started_at_ns: int | None = None,
    ) -> bool:
        nonlocal saves
        saves += 1
        if saves == 1:
            return False
        return original_save(cache, items, started_at_ns=started_at_ns)

    monkeypatch.setattr(SQLiteProjectStore, "save", save)

    assert projects.add_project(project)
    assert saves == 2
    assert projects.config.load().manual_projects == (project,)
    assert projects.cache.load() == [project]


def test_manual_add_scans_once_and_publishes_fresh_cache(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    assert projects.config.add_root(root)
    projects.cache.save(())  # A stale cache cannot replace the authoritative scan.
    manual_path = tmp_path / "manual"
    manual_path.mkdir()
    manual = Project("manual", canonical_path(manual_path), manual_path, ProjectSource.MANUAL)
    scans = 0
    original_scan = ProjectScanner.scan

    def scan(scanner: ProjectScanner) -> Iterator[Project]:
        nonlocal scans
        scans += 1
        yield from original_scan(scanner)

    monkeypatch.setattr(ProjectScanner, "scan", scan)

    assert projects.add_project(manual)
    assert scans == 1
    assert projects.config.load().manual_projects == (manual,)
    assert projects.cache.load() == [manual, Project("repo", repo, repo, ProjectSource.DISCOVERED)]


def test_manual_add_rescans_if_config_changes_during_scan(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    first.mkdir()
    assert projects.config.add_root(first)
    second = tmp_path / "second"
    repo = second / "repo"
    (repo / ".git").mkdir(parents=True)
    manual_path = tmp_path / "manual"
    manual_path.mkdir()
    manual = Project("manual", canonical_path(manual_path), manual_path, ProjectSource.MANUAL)
    scans = 0
    original_scan = ProjectScanner.scan
    original_add = Config.add_manual

    def scan(scanner: ProjectScanner) -> Iterator[Project]:
        nonlocal scans
        scans += 1
        yield from original_scan(scanner)

    def add(config: Config, project: Project) -> ProjectSettings | None:
        assert config.add_root(second)
        return original_add(config, project)

    monkeypatch.setattr(ProjectScanner, "scan", scan)
    monkeypatch.setattr(Config, "add_manual", add)

    assert projects.add_project(manual)
    assert scans == 2
    assert projects.config.load().roots == (first, second)
    assert projects.cache.load() == [manual, Project("repo", repo, repo, ProjectSource.DISCOVERED)]


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

    def save(_cache: SQLiteProjectStore, items: list[Project]) -> None:
        writes.append(list(items))

    monkeypatch.setattr(SQLiteProjectStore, "save", save)
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
    monkeypatch.setattr(SQLiteProjectStore, "lookup", lambda *_args: None)

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


def test_fuzzy_miss_scans_index_once_per_attempt(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert projects.config.add_root(root)
    projects.refresh()
    (root / "medcab" / ".git").mkdir(parents=True)
    reads = 0
    original_entries = SQLiteSnapshot.records

    def entries(cache: SQLiteSnapshot) -> Iterator[LookupEntry[int]]:
        nonlocal reads
        reads += 1
        return original_entries(cache)

    monkeypatch.setattr(SQLiteSnapshot, "records", entries)

    assert [item.name for item in projects.resolve("mdc").matches] == ["medcab"]
    assert reads == 2


def test_fuzzy_hit_scans_index_once(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [
        Project(name, tmp_path / name, tmp_path / name, ProjectSource.MANUAL)
        for name in ("alpha", "medcab", "beta")
    ]
    projects.cache.save(items)
    reads = 0
    original_entries = SQLiteSnapshot.records

    def entries(cache: SQLiteSnapshot) -> Iterator[LookupEntry[int]]:
        nonlocal reads
        reads += 1
        yield from original_entries(cache)

    monkeypatch.setattr(SQLiteSnapshot, "records", entries)
    assert projects.resolve("mdc").matches == [items[1]]
    assert reads == 1


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

    monkeypatch.setattr(SQLiteProjectStore, "load", fail)
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
    monkeypatch.setattr(Config, "add_manual", lambda _config, _project: None)

    assert projects.add_project(project) is False


def test_remove_missing_project_returns_false(projects: ProjectCatalog, tmp_path: Path) -> None:
    assert projects.remove_project(tmp_path / "missing") is False


def test_valid_legacy_import_does_not_scan_or_change_config(
    projects: ProjectCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = Project("manual", tmp_path / "manual", tmp_path / "manual", ProjectSource.MANUAL)
    projects.config.add_project(project)
    before = projects.config.path.read_bytes()
    legacy = ProjectCache(projects.cache.path.with_name("projects.jsonl"))
    legacy.save([project], started_at_ns=1)

    def fail(*_args: object) -> None:
        raise AssertionError("migration must not scan or load history")

    monkeypatch.setattr(ProjectScanner, "scan", fail)
    monkeypatch.setattr(type(projects.history), "load", fail)
    assert projects.resolve("manual").matches == [project]
    assert projects.config.path.read_bytes() == before
    assert legacy.load() == [project]


def test_picker_miss_does_not_refresh_missing_cache(
    projects: ProjectCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object) -> None:
        raise AssertionError("picker must not refresh or load config")

    monkeypatch.setattr(ProjectCatalog, "refresh", fail)
    monkeypatch.setattr(Config, "load", fail)
    result = projects.lookup(LookupRequest("missing"), refresh_on_miss=False, usage={})
    assert result.matches == [] and result.total == 0
    assert not projects.config.path.exists()
    assert not projects.cache.path.exists()


def test_resolver_refreshes_at_most_once_and_uses_one_lookup_per_attempt(
    projects: ProjectCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshes = 0
    lookups = 0
    original_refresh = ProjectCatalog.refresh
    original_lookup = SQLiteProjectStore.lookup

    def refresh(catalog: ProjectCatalog) -> list[Project]:
        nonlocal refreshes
        refreshes += 1
        return original_refresh(catalog)

    def lookup(store: SQLiteProjectStore, request: LookupRequest, *args: object) -> object:
        nonlocal lookups
        lookups += 1
        return original_lookup(store, request)

    monkeypatch.setattr(ProjectCatalog, "refresh", refresh)
    monkeypatch.setattr(SQLiteProjectStore, "lookup", lookup)
    assert projects.resolve("missing").matches == []
    assert refreshes == 1 and lookups == 2


@pytest.mark.parametrize("mutation", ["add-root", "remove-root", "remove-manual", "exclude"])
def test_config_mutation_failed_scan_hides_stale_cache(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = tmp_path / "root"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    manual = Project("manual", other, other, ProjectSource.MANUAL)
    projects.config.add_root(root)
    projects.config.add_project(manual)
    initial = projects.refresh()
    previous = projects.cache.path.read_bytes()

    def fail(_scanner: ProjectScanner) -> Iterator[Project]:
        raise ScanError([(root, PermissionError("blocked"))])

    monkeypatch.setattr(ProjectScanner, "scan", fail)
    with pytest.raises(OSError, match=r"Configuration saved.*next ordinary lookup retries"):
        if mutation == "add-root":
            projects.add_scan_root(other)
        elif mutation == "remove-root":
            projects.remove_scan_root(root)
        elif mutation == "remove-manual":
            projects.remove_project(other)
        else:
            projects.remove_selected_project(next(item for item in initial if item.name == "repo"))
    assert projects.cache.path.read_bytes() == previous
    assert projects.cache.load() is None
    assert list(projects.completion_candidates("")) == []
    store = SQLiteProjectStore(projects.cache.path, config_path=projects.config.path)
    assert store.unique_cached_path("repo") is None
    assert store.unique_cached_path("manual") is None
    with pytest.raises(ScanError):
        projects.resolve("repo", exact_only=True)


def test_config_initialization_invalidates_cache(projects: ProjectCatalog) -> None:
    projects.cache.save(())
    assert projects.cache.load() == []
    projects.config.ensure_exists()
    assert projects.cache.load() is None
    assert projects.projects() == []
    assert projects.cache.load() == []


def test_manual_config_edit_invalidates_cache(projects: ProjectCatalog, tmp_path: Path) -> None:
    path = tmp_path / "notes"
    path.mkdir()
    project = Project("notes", path, path, ProjectSource.MANUAL)
    projects.config.add_project(project)
    projects.refresh()
    text = projects.config.path.read_text(encoding="utf-8")
    projects.config.path.write_text(
        text.replace('name = "notes"', 'name = "renamed"'), encoding="utf-8"
    )
    assert projects.cache.load() is None
    assert projects.resolve("renamed").matches[0].name == "renamed"


def test_refresh_retries_config_changed_during_scan(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    (root / "repo" / ".git").mkdir(parents=True)
    projects.config.add_root(root)
    original_scan = ProjectScanner.scan
    scans = 0

    def scan(scanner: ProjectScanner) -> Iterator[Project]:
        nonlocal scans
        scans += 1
        yield from original_scan(scanner)
        if scans == 1:
            assert projects.config.remove_root(root)
            assert projects.refresh() == []

    monkeypatch.setattr(ProjectScanner, "scan", scan)
    assert projects.refresh() == []
    assert scans == 3
    assert projects.cache.load() == []


@pytest.mark.parametrize("unavailable", ["missing", "corrupt", "changed-config"])
def test_missing_catalog_refreshes_before_using_weaker_learned_match(
    projects: ProjectCatalog, tmp_path: Path, unavailable: str
) -> None:
    from pcd_cli.visits import DirectoryVisits

    root = tmp_path / "root"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    projects.config.add_root(root)
    learned = tmp_path / "repo-backup"
    learned.mkdir()
    DirectoryVisits(projects.history.path).record(learned)
    if unavailable == "corrupt":
        projects.cache.path.parent.mkdir(parents=True)
        projects.cache.path.write_bytes(b"broken")
    elif unavailable == "changed-config":
        projects.cache.save(())
        projects.config.ensure_exists()
        projects.config.path.write_text(
            projects.config.path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8"
        )
    result = projects.resolve("repo")
    assert [project.path for project in result.matches] == [repo]
    assert projects.cache.load() is not None


@pytest.mark.parametrize("mutation", ["add-root", "remove-root", "remove-manual", "exclude"])
def test_failed_mutation_cannot_revive_legacy_cache(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = tmp_path / "root"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    projects.config.add_root(root)
    manual = Project("manual", tmp_path / "manual", tmp_path / "manual", ProjectSource.MANUAL)
    projects.config.add_project(manual)
    legacy = ProjectCache(projects.cache.path.with_name("projects.jsonl"))
    initial = [manual, Project("repo", repo, repo, ProjectSource.DISCOVERED)]
    legacy.save(initial)

    def fail(_scanner: ProjectScanner) -> Iterator[Project]:
        raise ScanError([(root, PermissionError("blocked"))])

    monkeypatch.setattr(ProjectScanner, "scan", fail)
    with pytest.raises(OSError, match="Configuration saved"):
        if mutation == "add-root":
            projects.add_scan_root(tmp_path)
        elif mutation == "remove-root":
            projects.remove_scan_root(root)
        elif mutation == "remove-manual":
            projects.remove_project(manual.path)
        else:
            projects.remove_selected_project(initial[1])
    assert legacy.load() == initial
    assert list(projects.completion_candidates("")) == []
    assert projects.cache.load() is None


def test_config_write_failure_after_replace_reports_partial_change(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pcd_cli import filesystem

    projects.refresh()

    def fail(_path: Path) -> None:
        raise OSError("directory sync failed")

    monkeypatch.setattr(filesystem, "_sync_directory", fail)
    with pytest.raises(OSError, match="Configuration may have been saved"):
        projects.add_scan_root(tmp_path)
    assert projects.config.load().roots == (tmp_path,)
    assert projects.cache.load() is None


def test_cache_write_failure_reports_committed_config(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects.refresh()

    def fail(_store: SQLiteProjectStore, *_args: object, **_kwargs: object) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(SQLiteProjectStore, "save", fail)
    with pytest.raises(OSError, match=r"Configuration saved.*next ordinary lookup retries"):
        projects.add_scan_root(tmp_path)
    assert projects.config.load().roots == (tmp_path,)
    assert projects.cache.load() is None


def test_unstamped_sqlite_is_rebuilt_before_navigation(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    import sqlite3

    path = tmp_path / "notes"
    path.mkdir()
    project = Project("notes", path, path, ProjectSource.MANUAL)
    projects.config.add_project(project)
    projects.cache.save(())
    with sqlite3.connect(projects.cache.path) as connection:
        connection.execute("DROP TABLE configuration")
    connection.close()
    assert projects.cache.load() is None
    store = SQLiteProjectStore(projects.cache.path, config_path=projects.config.path)
    assert store.unique_cached_path("notes") is None
    assert projects.resolve("notes").matches == [project]
    assert store.unique_cached_path("notes") == path


def test_rejected_publication_without_cache_keeps_completed_scan(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "notes"
    path.mkdir()
    project = Project("notes", path, path, ProjectSource.MANUAL)
    projects.config.add_project(project)
    monkeypatch.setattr(SQLiteProjectStore, "save", lambda *_args, **_kwargs: False)
    assert projects.resolve("notes").matches == [project]


def test_editor_change_during_cache_write_cannot_certify_old_scan(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    (root / "repo" / ".git").mkdir(parents=True)
    projects.config.add_root(root)
    original_write = SQLiteProjectStore._write

    writes = 0

    def write(
        store: SQLiteProjectStore, connection: Connection, items: Iterable[Project], generation: int
    ) -> None:
        nonlocal writes
        writes += 1
        if writes == 1:
            # An external editor does not honor our configuration writer lock.
            text = projects.config.path.read_text(encoding="utf-8")
            lines = text.splitlines()
            lines = ["roots = []" if line.startswith("roots =") else line for line in lines]
            projects.config.path.write_text("\n".join(lines), encoding="utf-8")
        original_write(store, connection, items, generation)

    monkeypatch.setattr(SQLiteProjectStore, "_write", write)
    assert projects.refresh() == []
    assert writes == 2
    assert projects.cache.load() == []
    store = SQLiteProjectStore(projects.cache.path, config_path=projects.config.path)
    assert store.unique_cached_path("repo") is None


def test_manual_config_edit_before_legacy_import_skips_stale_snapshot(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    path = tmp_path / "notes"
    path.mkdir()
    project = Project("notes", path, path, ProjectSource.MANUAL)
    projects.config.add_project(project)
    legacy = ProjectCache(projects.cache.path.with_name("projects.jsonl"))
    legacy.save([project])
    text = projects.config.path.read_text(encoding="utf-8")
    projects.config.path.write_text(
        text.replace('name = "notes"', 'name = "renamed"'), encoding="utf-8"
    )
    # Give the editor write a distinct mtime even on coarse timestamp filesystems.
    legacy_time = legacy.path.stat().st_mtime_ns
    os.utime(projects.config.path, ns=(legacy_time + 1_000_000_000, legacy_time + 1_000_000_000))
    assert list(projects.completion_candidates("")) == []
    assert [item.name for item in projects.resolve("renamed").matches] == ["renamed"]
    assert legacy.load() == [project]
