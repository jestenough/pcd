from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pcd_cli.catalog import ProjectCatalog
from pcd_cli.cli import cli
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner


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
