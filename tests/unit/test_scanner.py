from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from pcd_cli.models import Project, ProjectSettings
from pcd_cli.scanner import _mark_visited, ProjectScanner

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest

    from pcd_cli.models import Project


EXCLUDE = frozenset(
    {
        ".cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
        "vendor",
        "venv",
    }
)


def settings(
    *roots: Path,
    include_hidden: bool = True,
    follow_symlinks: bool = False,
    excluded_names: frozenset[str] = EXCLUDE,
) -> ProjectSettings:
    return ProjectSettings(
        roots=roots,
        manual_projects=(),
        include_hidden=include_hidden,
        follow_symlinks=follow_symlinks,
        excluded_names=excluded_names,
    )


def scan(settings: ProjectSettings) -> list[Project]:
    return list(ProjectScanner(settings).scan())


def test_git_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    projects = scan(settings(tmp_path))

    assert [item.name for item in projects] == ["repo"]


def test_git_file(tmp_path: Path) -> None:
    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: somewhere", encoding="utf-8")

    assert [item.name for item in scan(settings(tmp_path))] == ["worktree"]


def test_nested_repo_is_not_scanned(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    (parent / ".git").mkdir(parents=True)
    (parent / "child/.git").mkdir(parents=True)

    assert [item.name for item in scan(settings(tmp_path))] == ["parent"]


def test_deep_repo(tmp_path: Path) -> None:
    repo = tmp_path / "a/b/c/repo"
    (repo / ".git").mkdir(parents=True)

    assert [item.name for item in scan(settings(tmp_path))] == ["repo"]


def test_excluded_directory(tmp_path: Path) -> None:
    repo = tmp_path / "node_modules/repo"
    (repo / ".git").mkdir(parents=True)

    assert scan(settings(tmp_path)) == []


def test_hidden_directories_can_be_disabled(tmp_path: Path) -> None:
    repo = tmp_path / ".private/repo"
    (repo / ".git").mkdir(parents=True)

    assert scan(settings(tmp_path, include_hidden=False)) == []
    assert [item.name for item in scan(settings(tmp_path, include_hidden=True))] == ["repo"]


def test_symlink_project_is_discovered_without_following(tmp_path: Path) -> None:
    target = tmp_path / "target"
    (target / ".git").mkdir(parents=True)
    root = tmp_path / "root"
    root.mkdir()
    (root / "repo").symlink_to(target, target_is_directory=True)

    projects = scan(settings(root))

    assert len(projects) == 1
    assert projects[0].name == "repo"
    assert projects[0].path == target.resolve()


def test_symlink_tree_is_not_followed_by_default(tmp_path: Path) -> None:
    target = tmp_path / "target"
    (target / "repo/.git").mkdir(parents=True)
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(target, target_is_directory=True)

    assert scan(settings(root)) == []


def test_follow_symlink_without_cycle(tmp_path: Path) -> None:
    target = tmp_path / "target"
    (target / "repo/.git").mkdir(parents=True)
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(target, target_is_directory=True)

    projects = scan(settings(root, follow_symlinks=True))

    assert [item.name for item in projects] == ["repo"]


def test_symlink_cycle_does_not_loop(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "loop").symlink_to(root, target_is_directory=True)

    assert scan(settings(root, follow_symlinks=True)) == []


def test_overlapping_roots_are_not_duplicated(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    (nested / "repo/.git").mkdir(parents=True)

    projects = scan(settings(tmp_path, nested))

    assert [item.name for item in projects] == ["repo"]


def test_nested_root_inside_repo_is_kept(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    (parent / ".git").mkdir(parents=True)
    nested = parent / "tools"
    (nested / "child/.git").mkdir(parents=True)

    projects = scan(settings(tmp_path, nested))

    assert {item.name for item in projects} == {"parent", "child"}


def test_nested_root_inside_excluded_path_is_scanned(tmp_path: Path) -> None:
    nested = tmp_path / "node_modules"
    (nested / "repo/.git").mkdir(parents=True)

    projects = scan(settings(tmp_path, nested))

    assert [item.name for item in projects] == ["repo"]


def test_missing_root_is_ignored(tmp_path: Path) -> None:
    assert scan(settings(tmp_path / "missing")) == []


def test_permission_error_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    real_scandir = os.scandir

    def broken(path: str | os.PathLike[str]) -> Iterator[os.DirEntry[str]]:
        if Path(path) == root:
            raise PermissionError
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", broken)

    assert scan(settings(root)) == []


def test_disappeared_directory_is_not_marked_visited(tmp_path: Path) -> None:
    assert _mark_visited(tmp_path / "missing", set()) is False


def test_unrelated_roots_are_both_scanned(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "one/.git").mkdir(parents=True)
    (second / "two/.git").mkdir(parents=True)

    projects = scan(settings(first, second))

    assert {item.name for item in projects} == {"one", "two"}


def test_invalid_git_marker_does_not_hide_child_repo(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / ".git").symlink_to(tmp_path / "missing")
    (outer / "child/.git").mkdir(parents=True)

    projects = scan(settings(tmp_path))

    assert [item.name for item in projects] == ["child"]


def test_scanner_can_run_twice(tmp_path: Path) -> None:
    (tmp_path / "repo/.git").mkdir(parents=True)
    scanner = ProjectScanner(settings(tmp_path))

    first = list(scanner.scan())
    second = list(scanner.scan())

    assert second == first


def test_duplicate_roots_scan_once(tmp_path: Path) -> None:
    (tmp_path / "repo/.git").mkdir(parents=True)

    projects = scan(settings(tmp_path, tmp_path))

    assert [item.name for item in projects] == ["repo"]


def test_nested_root_that_is_repo_is_scanned_once(tmp_path: Path) -> None:
    nested = tmp_path / "repo"
    (nested / ".git").mkdir(parents=True)

    projects = scan(settings(tmp_path, nested))

    assert [item.name for item in projects] == ["repo"]
