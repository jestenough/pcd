from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pcd_cli.config import InvalidConfigError
from pcd_cli.filesystem import canonical_path
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
    import os
    from pathlib import Path

    from pcd_cli.catalog import ProjectCatalog


def test_defaults(projects: ProjectCatalog) -> None:
    settings = projects.config.load()

    assert settings.roots == ()
    assert settings.manual_projects == ()
    assert settings.include_hidden is True
    assert settings.follow_symlinks is False
    assert "node_modules" in settings.excluded_names
    assert settings.editor is None
    assert settings.excluded_projects == frozenset()


def test_reads_editor_setting(projects: ProjectCatalog) -> None:
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text('editor = "nvim --clean"\n', encoding="utf-8")

    assert projects.config.load().editor == "nvim --clean"


def test_visit_checks_only_excluded_paths(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    calls = []
    excluded = tmp_path / "private"
    config = projects.config
    config.path.parent.mkdir(parents=True)
    config.path.write_text(
        f"roots = [{json.dumps(str(tmp_path))}]\n"
        f"excluded_projects = [{json.dumps(str(excluded))}]\n"
        f"[[projects]]\nname='manual'\npath={json.dumps(str(tmp_path / 'manual'))}\n",
        encoding="utf-8",
    )

    def resolve(path: Path) -> Path:
        calls.append(path)
        return canonical_path(path)

    monkeypatch.setattr("pcd_cli.config.canonical_path", resolve)
    assert config.visit_exclusions() == {excluded.resolve()}
    assert calls == [excluded]

    config.path.write_text("excluded_projects = [42]\n", encoding="utf-8")
    with pytest.raises(InvalidConfigError):
        config.visit_exclusions()


def test_effective_config_includes_editor(projects: ProjectCatalog) -> None:
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text('editor = "nvim --clean"\n', encoding="utf-8")

    assert 'editor = "nvim --clean"' in projects.config.effective_toml()


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


def test_add_manual_returns_committed_settings(projects: ProjectCatalog, tmp_path: Path) -> None:
    path = tmp_path / "notes"
    project = Project("notes", canonical_path(path), path, ProjectSource.MANUAL)

    committed = projects.config.add_manual(project)

    assert committed == projects.config.load()
    assert committed is not None
    assert committed.manual_projects == (project,)
    assert projects.config.add_manual(project) is None


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
        "excluded_projects = 'bad'\n",
        "excluded_projects = [1]\n",
        "excluded_projects = ['']\n",
        'excluded_projects = ["~/repo\\u0000name"]\n',
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
        "excluded_projects = ['relative']\n",
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


def test_invalid_utf8_config(projects: ProjectCatalog) -> None:
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_bytes(b"\xff")

    with pytest.raises(InvalidConfigError, match="not valid UTF-8"):
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


@pytest.mark.parametrize("operation", ["load", "add", "ensure"])
def test_unreadable_config_is_not_treated_as_missing(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from pathlib import Path

    projects.config.path.parent.mkdir(parents=True)
    original = "roots = []\n# preserve this file\n"
    projects.config.path.write_text(original, encoding="utf-8")
    stat = Path.stat
    read_text = Path.read_text

    def denied_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == projects.config.path:
            raise PermissionError("config access denied")
        return stat(path, follow_symlinks=follow_symlinks)

    def denied_read(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if path == projects.config.path:
            raise PermissionError("config access denied")
        return read_text(path, encoding=encoding, errors=errors)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", denied_stat)
        patch.setattr(Path, "read_text", denied_read)
        with pytest.raises(PermissionError, match="config access denied"):
            if operation == "load":
                projects.config.load()
            elif operation == "add":
                projects.config.add_root(tmp_path / "new")
            else:
                projects.config.ensure_exists()
    assert projects.config.path.read_text(encoding="utf-8") == original


def test_excluded_projects_use_canonical_paths_and_preserve_comments(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    path = tmp_path / "missing"
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text("# keep exclusions\nroots = []\n", encoding="utf-8")

    assert projects.config.exclude_project(path)
    assert not projects.config.exclude_project(path / ".." / "missing")
    assert projects.config.load().excluded_projects == frozenset({canonical_path(path)})
    assert "# keep exclusions" in projects.config.path.read_text(encoding="utf-8")
    assert "excluded_projects" in projects.config.effective_toml()


def test_duplicate_excluded_projects_are_rejected(projects: ProjectCatalog, tmp_path: Path) -> None:
    path = tmp_path / "missing"
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text(
        f"excluded_projects = ['{path}', '{path / '..' / 'missing'}']\n", encoding="utf-8"
    )
    with pytest.raises(InvalidConfigError, match="duplicate excluded"):
        projects.config.load()


def test_manual_and_excluded_project_overlap_is_rejected(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    path = tmp_path / "notes"
    projects.config.path.parent.mkdir(parents=True)
    projects.config.path.write_text(
        f"excluded_projects = ['{path}']\nprojects = [{{name='notes', path='{path}'}}]\n",
        encoding="utf-8",
    )
    with pytest.raises(InvalidConfigError, match="cannot also be excluded"):
        projects.config.load()


def test_restore_requires_excluded_manual_candidate(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    path = tmp_path / "notes"
    discovered = Project("notes", canonical_path(path), path, ProjectSource.DISCOVERED)
    manual = Project("notes", canonical_path(path), path, ProjectSource.MANUAL)
    settings = projects.config.load()

    with pytest.raises(ValueError, match="manual"):
        projects.config.restore_project(discovered, manual=True, expected=settings)
    assert projects.config.restore_project(manual, manual=True, expected=settings) is None
