from typing import TYPE_CHECKING

import pytest

from pcd_cli.config import InvalidConfigError
from pcd_cli.filesystem import canonical_path
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
    from pathlib import Path

    from pcd_cli.catalog import ProjectCatalog


def test_defaults(projects: ProjectCatalog) -> None:
    settings = projects.config.load()

    assert settings.roots == ()
    assert settings.manual_projects == ()
    assert settings.include_hidden is True
    assert settings.follow_symlinks is False
    assert "node_modules" in settings.excluded_names


def test_add_and_remove_root(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()

    assert projects.config.add_root(root) is True
    assert projects.config.add_root(root) is False
    assert projects.config.load().roots == (root,)
    assert projects.config.remove_root(root) is True
    assert projects.config.remove_root(root) is False


def test_add_and_remove_project(projects: ProjectCatalog, tmp_path: Path) -> None:
    display = tmp_path / "notes"
    display.mkdir()
    project = Project("notes", canonical_path(display), display, ProjectSource.MANUAL)

    assert projects.config.add_project(project) is True
    assert projects.config.add_project(project) is False
    assert projects.config.load().manual_projects == (project,)
    assert projects.config.remove_project(project.path) is True
    assert projects.config.remove_project(project.path) is False


def test_rejects_discovered_project(projects: ProjectCatalog, tmp_path: Path) -> None:
    path = tmp_path / "repo"
    project = Project("repo", canonical_path(path), path, ProjectSource.DISCOVERED)

    with pytest.raises(ValueError, match="manual"):
        projects.config.add_project(project)


def test_preserves_comments(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text(
        "# keep me\nroots = []\nprojects = []\n\n[scan]\nhidden = true\nfollow_symlinks = false\nexclude = []\n",
        encoding="utf-8",
    )

    projects.config.add_root(root)

    assert "# keep me" in projects.config.path.read_text(encoding="utf-8")


def test_reads_scan_settings(projects: ProjectCatalog) -> None:
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text(
        "roots = []\nprojects = []\n\n[scan]\nhidden = false\nfollow_symlinks = true\nexclude = ['tmp']\n",
        encoding="utf-8",
    )

    settings = projects.config.load()

    assert settings.include_hidden is False
    assert settings.follow_symlinks is True
    assert settings.excluded_names == frozenset({"tmp"})


@pytest.mark.parametrize(
    "text",
    [
        "roots = 'bad'\n",
        "projects = 'bad'\n",
        "[scan]\nhidden = 'yes'\n",
        "[scan]\nfollow_symlinks = 1\n",
        "[scan]\nexclude = [1]\n",
        "not = [",
        "roots=[]\nprojects=[1]\n",
        "roots=[]\nprojects=[{name=1,path='x'}]\n",
        "roots=[]\nprojects=[{name='x',path=1}]\n",
        "roots=[]\nprojects=[]\nscan='x'\n",
    ],
)
def test_invalid_config(projects: ProjectCatalog, text: str) -> None:
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text(text, encoding="utf-8")

    with pytest.raises(InvalidConfigError):
        projects.config.load()


def test_duplicate_roots_rejected(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text(
        f"roots = ['{root}', '{root}']\nprojects = []\n",
        encoding="utf-8",
    )

    with pytest.raises(InvalidConfigError, match="duplicate roots"):
        projects.config.load()


def test_duplicate_projects_rejected(projects: ProjectCatalog, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text(
        f"roots = []\nprojects = [{{name='one', path='{root}'}}, {{name='two', path='{root}'}}]\n",
        encoding="utf-8",
    )

    with pytest.raises(InvalidConfigError, match="duplicate manual"):
        projects.config.load()


@pytest.mark.parametrize(
    "text",
    [
        "roots = ['relative']\nprojects = []\n",
        "roots = []\nprojects = [{name='x', path='relative'}]\n",
    ],
)
def test_relative_config_paths_are_rejected(projects: ProjectCatalog, text: str) -> None:
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text(text, encoding="utf-8")

    with pytest.raises(InvalidConfigError, match="absolute"):
        projects.config.load()


def test_unknown_home_directory_is_invalid_config(projects: ProjectCatalog) -> None:
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text(
        'roots = ["~pcd-user-that-does-not-exist/projects"]\n',
        encoding="utf-8",
    )

    with pytest.raises(InvalidConfigError, match="unknown home directory"):
        projects.config.load()


@pytest.mark.parametrize(
    "text, key",
    [
        ("roots = []\ntyop = true\n", "tyop"),
        ("roots = []\n[scan]\nfollow_symbolinks = true\n", "follow_symbolinks"),
        ('[[projects]]\nname = "repo"\npath = "/repo"\nextra = true\n', "extra"),
    ],
)
def test_unknown_config_keys_are_rejected(projects: ProjectCatalog, text: str, key: str) -> None:
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text(text, encoding="utf-8")

    with pytest.raises(InvalidConfigError, match=key):
        projects.config.load()
