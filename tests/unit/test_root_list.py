from __future__ import annotations

from typing import TYPE_CHECKING

import pcd_cli.catalog as catalog_module
from pcd_cli.cli.roots import RootListTable
from pcd_cli.filesystem import canonical_path, format_path
from pcd_cli.models import Discovery, Project, ProjectSource

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
        Project(
            "repository",
            repository,
            repository,
            ProjectSource.DISCOVERED,
            discoveries=(Discovery(available, repository),),
        ),
        Project(
            "outside",
            outside,
            outside,
            ProjectSource.DISCOVERED,
            discoveries=(Discovery(tmp_path / "other", outside),),
        ),
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


def test_root_counts_resolve_each_discovery_root_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = [tmp_path / f"root-{index}" for index in range(8)]
    projects = [
        Project(
            str(index),
            root / str(index),
            root / str(index),
            ProjectSource.DISCOVERED,
            # Several routes under one root must still count as one project.
            (Discovery(root, root / str(index)), Discovery(root, root / "alias")),
        )
        for index in range(50)
        for root in roots
    ]
    resolved: list[Path] = []

    def resolve(path: Path) -> Path:
        resolved.append(path)
        return canonical_path(path)

    monkeypatch.setattr(catalog_module, "canonical_path", resolve)
    counts = catalog_module.count_discovered_by_root(iter(projects))
    assert counts == {canonical_path(root): 50 for root in roots}
    assert resolved == roots
