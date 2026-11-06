from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from pcd_cli.cache import ProjectCache
from pcd_cli.catalog import ProjectCatalog
from pcd_cli.cli import cli
from pcd_cli.cli.roots import RootListRow, RootListTable
from pcd_cli.models import Discovery, Project, ProjectSource

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from click.testing import CliRunner


def reloaded_projects(catalog: ProjectCatalog) -> list[Project]:
    return ProjectCache(catalog.cache.path).load() or []


def counts_by_root(roots: Sequence[Path], projects: Sequence[Project]) -> list[str]:
    return [RootListRow.from_root(root, projects).projects for root in roots]


def write_roots_config(
    catalog: ProjectCatalog,
    roots: Sequence[Path],
    *,
    follow_symlinks: bool = False,
) -> None:
    root_values = ", ".join(json.dumps(str(root)) for root in roots)
    catalog.config.path.parent.mkdir(parents=True, exist_ok=True)
    catalog.config.path.write_text(
        f"roots = [{root_values}]\n[scan]\nfollow_symlinks = {str(follow_symlinks).lower()}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("symlink_root", [False, True])
def test_roots_count_cached_projects_in_each_containing_root(
    projects: ProjectCatalog,
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    symlink_root: bool,
) -> None:
    outer = tmp_path / "outer"
    nested = outer / "nested"
    first = outer / "first"
    shared = nested / "shared"
    (first / ".git").mkdir(parents=True)
    (shared / ".git").mkdir(parents=True)
    shown = tmp_path / "linked" if symlink_root else outer
    if symlink_root:
        shown.symlink_to(outer, target_is_directory=True)
    assert projects.config.add_root(shown)
    assert projects.config.add_root(nested)
    expected_projects = [
        Project("first", first, shown / "first", ProjectSource.DISCOVERED),
        Project("shared", shared, shown / "nested" / "shared", ProjectSource.DISCOVERED),
    ]
    assert projects.refresh() == expected_projects
    assert projects.cache.load() == expected_projects
    cached = reloaded_projects(projects)
    shared_project = next(project for project in cached if project.name == "shared")
    assert {discovery.root for discovery in shared_project.discoveries} == {shown, nested}
    assert counts_by_root((shown, nested), cached) == ["2", "1"]

    def fail_refresh(_catalog: ProjectCatalog) -> list[Project]:
        raise AssertionError("roots must use the existing cache snapshot")

    monkeypatch.setattr(ProjectCatalog, "refresh", fail_refresh)
    result = runner.invoke(cli, ["roots"])

    width = max(len(str(shown)), len(str(nested)))
    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout == (
        f"{'PATH':<{width}}  PROJECTS  STATUS\n"
        f"{shown!s:<{width}}  2         available\n"
        f"{nested!s:<{width}}  1         available\n"
    )


def test_shared_symlink_targets_keep_each_root_route(
    projects: ProjectCatalog,
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    (target / ".git").mkdir(parents=True)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "alpha").symlink_to(target, target_is_directory=True)
    (second / "beta").symlink_to(target, target_is_directory=True)
    assert projects.config.add_root(first)
    assert projects.config.add_root(second)

    refreshed = projects.refresh()
    cached = reloaded_projects(projects)

    assert len(refreshed) == len(cached) == 1
    project = cached[0]
    assert project.path == target.resolve()
    assert {(item.root, item.display_path) for item in project.discoveries} == {
        (first, first / "alpha"),
        (second, second / "beta"),
    }
    assert counts_by_root((first, second), cached) == ["1", "1"]


def test_shared_symlink_targets_under_one_root_count_once(
    projects: ProjectCatalog,
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    (target / ".git").mkdir(parents=True)
    root = tmp_path / "root"
    root.mkdir()
    (root / "alpha").symlink_to(target, target_is_directory=True)
    (root / "beta").symlink_to(target, target_is_directory=True)
    assert projects.config.add_root(root)

    projects.refresh()
    cached = reloaded_projects(projects)

    assert len(cached) == 1
    assert {(item.root, item.display_path) for item in cached[0].discoveries} == {
        (root, root / "alpha"),
        (root, root / "beta"),
    }
    assert counts_by_root((root,), cached) == ["1"]


def test_followed_symlink_tree_counts_under_the_following_root(
    projects: ProjectCatalog,
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    (target / "packages" / "repo" / ".git").mkdir(parents=True)
    root = tmp_path / "root"
    root.mkdir()
    (root / "tree").symlink_to(target, target_is_directory=True)
    write_roots_config(projects, (root,), follow_symlinks=True)

    projects.refresh()
    cached = reloaded_projects(projects)

    assert len(cached) == 1
    project = cached[0]
    assert project.path == (target / "packages" / "repo").resolve()
    assert project.display_path == root / "tree" / "packages" / "repo"
    assert project.discoveries == (Discovery(root, root / "tree" / "packages" / "repo"),)
    assert counts_by_root((root,), cached) == ["1"]


def test_nested_root_separated_by_exclusion_uses_only_its_own_route(
    projects: ProjectCatalog,
    tmp_path: Path,
) -> None:
    outer = tmp_path / "outer"
    visible = outer / "visible"
    (visible / ".git").mkdir(parents=True)
    nested = outer / "node_modules" / "nested"
    (nested / "repo" / ".git").mkdir(parents=True)
    assert projects.config.add_root(outer)
    assert projects.config.add_root(nested)

    projects.refresh()
    cached = reloaded_projects(projects)

    assert {project.name for project in cached} == {"visible", "repo"}
    repo = next(project for project in cached if project.name == "repo")
    assert {discovery.root for discovery in repo.discoveries} == {nested}
    assert counts_by_root((outer, nested), cached) == ["1", "1"]


def test_nested_root_separated_by_repository_boundary_uses_only_its_own_route(
    projects: ProjectCatalog,
    tmp_path: Path,
) -> None:
    outer = tmp_path / "outer"
    boundary = outer / "monorepo"
    (boundary / ".git").mkdir(parents=True)
    nested = boundary / "packages"
    (nested / "child" / ".git").mkdir(parents=True)
    assert projects.config.add_root(outer)
    assert projects.config.add_root(nested)

    projects.refresh()
    cached = reloaded_projects(projects)

    assert {project.name for project in cached} == {"monorepo", "child"}
    child = next(project for project in cached if project.name == "child")
    assert {discovery.root for discovery in child.discoveries} == {nested}
    assert counts_by_root((outer, nested), cached) == ["1", "1"]


def test_roots_command_reads_discovery_provenance_after_cache_reload(
    projects: ProjectCatalog,
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    (target / ".git").mkdir(parents=True)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "one").symlink_to(target, target_is_directory=True)
    (second / "two").symlink_to(target, target_is_directory=True)
    assert projects.config.add_root(first)
    assert projects.config.add_root(second)
    projects.refresh()

    def fail_refresh(_catalog: ProjectCatalog) -> list[Project]:
        raise AssertionError("roots must use the reloaded cache snapshot")

    monkeypatch.setattr(ProjectCatalog, "refresh", fail_refresh)
    result = runner.invoke(cli, ["roots"])

    width = max(len(str(first)), len(str(second)))
    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout == (
        f"{'PATH':<{width}}  PROJECTS  STATUS\n"
        f"{first!s:<{width}}  1         available\n"
        f"{second!s:<{width}}  1         available\n"
    )

    assert (
        RootListTable((first, second), reloaded_projects(projects)).render() + "\n" == result.stdout
    )
