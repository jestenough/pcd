from typing import TYPE_CHECKING

from pcd_cli.filesystem import canonical_path
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
    from pathlib import Path

    from pcd_cli.catalog import ProjectCatalog


def test_refresh_prefers_manual_on_duplicate(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    manual = Project("custom", canonical_path(repo), repo, ProjectSource.MANUAL)

    assert projects.config.add_root(root)
    assert projects.config.add_project(manual)

    assert projects.refresh() == [manual]


def test_projects_build_cache_when_missing(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "repo/.git").mkdir(parents=True)
    assert projects.config.add_root(root)

    items = projects.projects()

    assert [item.name for item in items] == ["repo"]
    assert projects.cache.load() == items


def test_matches_refreshes_after_cache_miss(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert projects.config.add_root(root)
    projects.refresh()

    repo = root / "new"
    (repo / ".git").mkdir(parents=True)

    assert [item.name for item in projects.search("new")] == ["new"]


def test_exact_only_does_not_use_fuzzy(projects: ProjectCatalog, tmp_path: Path) -> None:
    path = tmp_path / "medcab"
    path.mkdir()
    project = Project("medcab", canonical_path(path), path, ProjectSource.MANUAL)
    assert projects.config.add_project(project)
    projects.refresh()

    assert projects.search("med", exact_only=True) == []


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


def test_fuzzy_match_refreshes_after_cache_miss(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert projects.config.add_root(root)
    projects.refresh()

    repo = root / "medcab"
    (repo / ".git").mkdir(parents=True)

    assert [item.name for item in projects.search("mdc")] == ["medcab"]


def test_parent_root_prefers_nearest_root(projects: ProjectCatalog, tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    child = inner / "child"
    child.mkdir(parents=True)

    assert projects.config.add_root(outer)
    assert projects.config.add_root(inner)

    assert projects.find_parent_root(child) == inner
    assert projects.find_parent_root(tmp_path / "elsewhere") is None


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

    assert projects.search("same") == [items[1], items[0]]
