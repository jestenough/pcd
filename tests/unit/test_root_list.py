from __future__ import annotations

from typing import TYPE_CHECKING

from pcd_cli.cli.roots import RootListTable
from pcd_cli.filesystem import format_path
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
    from pathlib import Path


def test_empty_root_list() -> None:
    assert RootListTable((), ()).render() == "No roots found."


def test_root_list_shows_project_counts_and_status(tmp_path: Path) -> None:
    available = tmp_path / "available"
    repository = available / "repository"
    outside = tmp_path / "outside"
    missing = tmp_path / "missing"
    repository.mkdir(parents=True)
    outside.mkdir()
    projects = (
        Project("repository", repository, repository, ProjectSource.DISCOVERED),
        Project("outside", outside, outside, ProjectSource.DISCOVERED),
        Project("manual", available / "manual", available / "manual", ProjectSource.MANUAL),
    )
    available_path = format_path(available)
    missing_path = format_path(missing)
    path_width = max(len(available_path), len(missing_path), len("PATH"))
    expected = "\n".join(
        (
            f"{'PATH':<{path_width}}  PROJECTS  STATUS",
            f"{available_path:<{path_width}}  1         available",
            f"{missing_path:<{path_width}}  0         missing",
        )
    )

    assert RootListTable((available, missing), projects).render() == expected
