from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pcd_cli.cli.projects import ProjectListRow, ProjectListTable
from pcd_cli.filesystem import format_path
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
    from pathlib import Path


def test_empty_project_list() -> None:
    assert ProjectListTable(()).render() == "No projects found."


def test_project_list_row_maps_project(tmp_path: Path) -> None:
    project = Project("repo", tmp_path, tmp_path, ProjectSource.DISCOVERED)

    row = ProjectListRow.from_project(project)

    assert row.name == "repo"
    assert row.path
    assert row.source == "scanned"
    assert row.status == "available"


def test_project_list_row_rejects_unknown_source(tmp_path: Path) -> None:
    project = Project("repo", tmp_path, tmp_path, "unknown")  # type: ignore[arg-type]

    with pytest.raises(AssertionError, match="Expected code to be unreachable"):
        ProjectListRow.from_project(project)


def test_project_list_uses_aligned_columns_and_clear_status(tmp_path: Path) -> None:
    available = tmp_path / "available"
    available.mkdir()
    missing = tmp_path / "missing"
    projects = (
        Project("long-project", available, available, ProjectSource.MANUAL),
        Project("short", missing, missing, ProjectSource.DISCOVERED),
    )

    available_path = format_path(available)
    missing_path = format_path(missing)
    path_width = max(len(available_path), len(missing_path), len("PATH"))
    expected = "\n".join(
        (
            f"NAME          {'PATH':<{path_width}}  SOURCE   STATUS",
            f"long-project  {available_path:<{path_width}}  manual   available",
            f"short         {missing_path:<{path_width}}  scanned  missing",
        )
    )

    assert ProjectListTable(projects).render() == expected
