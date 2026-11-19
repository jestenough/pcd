from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pcd_cli.config import Config
from pcd_cli.filesystem import canonical_path
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
    from pathlib import Path

    from pcd_cli.catalog import ProjectCatalog
    from pcd_cli.models import ProjectSettings


def test_remove_discovered_survives_refresh_and_missing_root(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    path = root / "repo"
    (path / ".git").mkdir(parents=True)
    assert projects.add_scan_root(root)
    selected = projects.projects()[0]

    assert projects.remove_selected_project(selected)
    assert path.is_dir()
    assert projects.refresh() == []
    assert projects.resolve("repo").matches == []
    assert list(projects.completion_candidates("repo")) == []
    assert projects.remove_scan_root(root)
    assert projects.config.load().excluded_projects == frozenset({canonical_path(path)})
    assert projects.add_scan_root(root)
    assert projects.projects() == []


def test_exclusion_applies_to_symlink_aliases(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = tmp_path / "target"
    (path / ".git").mkdir(parents=True)
    alias = root / "alias"
    try:
        alias.symlink_to(path, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Directory symlinks unavailable: {error}")
    assert projects.config.add_root(root)
    assert projects.config.add_root(path)
    selected = projects.refresh()[0]
    assert len(selected.discoveries) == 2
    assert projects.remove_selected_project(selected)
    assert projects.refresh() == []
    manual = Project("alias", canonical_path(alias), alias, ProjectSource.MANUAL)
    assert projects.add_project(manual)
    assert projects.projects()[0].source is ProjectSource.DISCOVERED
    assert not projects.config.load().excluded_projects


def test_manual_add_clears_concurrent_exclusion_atomically(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "notes"
    path.mkdir()
    manual = Project("notes", canonical_path(path), path, ProjectSource.MANUAL)
    original_add = Config.add_manual

    def add(config: Config, project: Project) -> ProjectSettings | None:
        assert config.exclude_project(project.path)
        return original_add(config, project)

    monkeypatch.setattr(Config, "add_manual", add)
    assert projects.add_project(manual)
    assert projects.config.load().manual_projects == (manual,)
    assert not projects.config.load().excluded_projects
    assert not projects.config.exclude_project(path)


def test_restore_handles_competing_restore(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "notes"
    path.mkdir()
    manual = Project("notes", canonical_path(path), path, ProjectSource.MANUAL)
    assert projects.config.exclude_project(path)
    original_restore = Config.restore_project

    def restore(
        config: Config, project: Project, *, manual: bool, expected: ProjectSettings
    ) -> ProjectSettings | None:
        assert original_restore(config, project, manual=manual, expected=expected) is not None
        return None

    monkeypatch.setattr(Config, "restore_project", restore)
    assert not projects.add_project(manual)
    assert projects.config.load().manual_projects == (manual,)


@pytest.mark.parametrize("explicit_name", [False, True])
def test_add_restores_exclusion_with_requested_source(
    projects: ProjectCatalog, tmp_path: Path, explicit_name: bool
) -> None:
    root = tmp_path / "root"
    path = root / "repo"
    (path / ".git").mkdir(parents=True)
    assert projects.add_scan_root(root)
    assert projects.remove_selected_project(projects.projects()[0])
    manual = Project("custom", canonical_path(path), path, ProjectSource.MANUAL)

    assert projects.add_project(manual, explicit_name=explicit_name)
    result = projects.projects()
    assert len(result) == 1
    assert result[0].name == ("custom" if explicit_name else "repo")
    assert result[0].source == (ProjectSource.MANUAL if explicit_name else ProjectSource.DISCOVERED)
    settings = projects.config.load()
    assert not settings.excluded_projects
    assert settings.manual_projects == ((manual,) if explicit_name else ())


def test_add_restores_as_manual_if_no_longer_discoverable(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    path = root / "repo"
    (path / ".git").mkdir(parents=True)
    assert projects.add_scan_root(root)
    assert projects.remove_selected_project(projects.projects()[0])
    assert projects.remove_scan_root(root)
    manual = Project("repo", canonical_path(path), path, ProjectSource.MANUAL)

    assert projects.add_project(manual)
    assert projects.projects() == [manual]
    assert projects.config.load().manual_projects == (manual,)
    assert not projects.config.load().excluded_projects


def test_remove_stale_discovered_selection_preserves_exclusion(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    path = tmp_path / "missing"
    selected = Project("missing", canonical_path(path), path, ProjectSource.DISCOVERED)

    assert projects.remove_selected_project(selected)
    assert projects.config.load().excluded_projects == frozenset({canonical_path(path)})
    assert projects.refresh() == []
    assert not projects.remove_selected_project(selected)


def test_remove_manual_does_not_exclude_rediscovered_project(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    path = root / "repo"
    (path / ".git").mkdir(parents=True)
    manual = Project("custom", canonical_path(path), path, ProjectSource.MANUAL)
    assert projects.config.add_root(root)
    assert projects.config.add_project(manual)
    projects.refresh()

    assert projects.remove_selected_project(manual)
    assert projects.projects()[0].source is ProjectSource.DISCOVERED
    assert not projects.config.load().excluded_projects


def test_restore_rescans_after_concurrent_root_removal(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    path = root / "repo"
    (path / ".git").mkdir(parents=True)
    assert projects.add_scan_root(root)
    assert projects.remove_selected_project(projects.projects()[0])
    manual = Project("repo", canonical_path(path), path, ProjectSource.MANUAL)
    original_restore = Config.restore_project

    def restore(
        config: Config, project: Project, *, manual: bool, expected: ProjectSettings
    ) -> ProjectSettings | None:
        config.remove_root(root)
        return original_restore(config, project, manual=manual, expected=expected)

    monkeypatch.setattr(Config, "restore_project", restore)
    assert projects.add_project(manual)
    assert projects.config.load().roots == ()
    assert projects.projects() == [manual]


def test_exclusion_filters_all_discovery_routes(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "root"
    path = root / "repo"
    (path / ".git").mkdir(parents=True)
    assert projects.config.add_root(root)
    assert projects.config.add_root(path)
    selected = projects.refresh()[0]
    assert len(selected.discoveries) == 2

    assert projects.remove_selected_project(selected)
    assert projects.refresh() == []
    assert projects.config.load().excluded_projects == frozenset({canonical_path(path)})


def test_cache_loss_preserves_exclusion_and_same_named_checkout(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    first = root / "first" / "repo"
    second = root / "second" / "repo"
    for path in (first, second):
        (path / ".git").mkdir(parents=True)
    assert projects.add_scan_root(root)
    by_path = {item.path: item for item in projects.resolve("repo").matches}
    assert projects.remove_selected_project(by_path[canonical_path(first)])
    saved_config = projects.config.path.read_bytes()

    projects.cache.path.unlink()
    assert projects.resolve("repo").matches == [by_path[canonical_path(second)]]
    assert projects.config.path.read_bytes() == saved_config
    assert (first / ".git").is_dir()
