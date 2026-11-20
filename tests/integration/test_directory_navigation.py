from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pcd_cli.catalog import ProjectCatalog
from pcd_cli.cli import cli
from pcd_cli.filesystem import canonical_path
from pcd_cli.history import UsageHistory
from pcd_cli.lookup import LookupRequest
from pcd_cli.models import Project, ProjectSource, ProjectUsage
from pcd_cli.picker import ProjectPicker
from pcd_cli.storage import SQLiteSnapshot
from pcd_cli.visits import DirectoryVisits

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner

    from pcd_cli.lookup import LookupEntry


def visits(catalog: ProjectCatalog) -> DirectoryVisits:
    return DirectoryVisits(catalog.history.path)


def test_multiword_project_components(runner: CliRunner, tmp_path: Path) -> None:
    # Distinctive tokens prevent a developer/pytest ancestor directory from
    # accidentally providing a fuzzy match for the reversed query.
    company, name = "acme-7zx9q", "api-8kv6j"
    wanted = tmp_path / company / name
    other = tmp_path / "other" / name
    for path in (wanted, other):
        path.mkdir(parents=True)
        assert runner.invoke(cli, ["add", str(path)]).exit_code == 0
    result = runner.invoke(cli, [company, name])
    assert result.exit_code == 0
    assert result.stdout == f"{canonical_path(wanted)}\n"
    assert runner.invoke(cli, [name, company]).exit_code == 3


def test_exact_name_with_spaces(runner: CliRunner, tmp_path: Path) -> None:
    path = tmp_path / "checkout"
    path.mkdir()
    assert runner.invoke(cli, ["add", str(path), "--name", "acme api"]).exit_code == 0
    result = runner.invoke(cli, ["acme", "api"])
    assert result.exit_code == 0
    assert result.stdout == f"{canonical_path(path)}\n"


def test_learned_hit_without_cache_does_not_scan(
    projects: ProjectCatalog, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "acme" / "notes"
    path.mkdir(parents=True)
    visits(projects).record(canonical_path(path))

    def fail_refresh(_catalog: ProjectCatalog) -> list[Project]:
        raise AssertionError("A learned hit must not scan")

    monkeypatch.setattr(ProjectCatalog, "refresh", fail_refresh)
    result = runner.invoke(cli, ["acme", "notes"])
    assert result.exit_code == 0
    assert result.stdout == f"{canonical_path(path)}\n"
    assert not projects.cache.path.exists()


def test_unique_project_exact_does_not_read_directory_history(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "api"
    path.mkdir()
    assert runner.invoke(cli, ["add", str(path)]).exit_code == 0

    def fail_entries(_visits: DirectoryVisits) -> dict[Path, object]:
        raise AssertionError("Unique project exact lookup must avoid directory history")

    monkeypatch.setattr(DirectoryVisits, "entries", fail_entries)
    assert runner.invoke(cli, ["api"]).exit_code == 0


def test_frequency_then_recency_and_pcd_picker(
    projects: ProjectCatalog, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [tmp_path / name / "api" for name in ("first", "second")]
    for path in paths:
        path.mkdir(parents=True)
        assert runner.invoke(cli, ["add", str(path)]).exit_code == 0
    learned = visits(projects)
    learned.record(canonical_path(paths[0]))
    learned.record(canonical_path(paths[0]))
    learned.record(canonical_path(paths[1]))
    selected: list[Path] = []

    def cancel(picker: ProjectPicker) -> None:
        selected.extend(item.path for item in picker.matches)
        return None

    monkeypatch.setattr(ProjectPicker, "run", cancel)
    before = learned.entries()
    result = runner.invoke(cli, ["api"])
    assert result.exit_code == 0 and result.stdout == ""
    assert selected == [canonical_path(path) for path in paths]
    assert learned.entries() == before


@pytest.mark.parametrize("missing", [False, True])
def test_resume_and_root_default(
    projects: ProjectCatalog, runner: CliRunner, tmp_path: Path, missing: bool
) -> None:
    root = tmp_path / "repo"
    nested = root / "src" / "lib"
    nested.mkdir(parents=True)
    assert runner.invoke(cli, ["add", str(root)]).exit_code == 0
    visits(projects).record(canonical_path(nested))
    if missing:
        nested.rmdir()
    result = runner.invoke(cli, ["repo", "--resume"])
    assert result.exit_code == 0
    assert result.stdout == f"{canonical_path(root if missing else nested)}\n"
    assert ("no longer exists" in result.stderr) is missing
    assert runner.invoke(cli, ["repo"]).stdout == f"{canonical_path(root)}\n"


def test_excluded_resume_falls_back_to_root(
    projects: ProjectCatalog, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    nested = root / "private"
    nested.mkdir(parents=True)
    assert runner.invoke(cli, ["add", str(root)]).exit_code == 0
    visits(projects).record(canonical_path(nested))
    monkeypatch.setenv("PCD_EXCLUDE_DIRS", str(canonical_path(nested)))
    result = runner.invoke(cli, ["repo", "--resume"])
    assert result.exit_code == 0
    assert result.stdout == f"{canonical_path(root)}\n"
    assert "is excluded" in result.stderr
    assert runner.invoke(cli, ["private"]).exit_code == 3


def test_directory_union_keeps_project_payload_decode_bounded(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = []
    for index in range(230):
        path = tmp_path / f"repo-{index:04d}"
        entries.append(Project(path.name, path, path, ProjectSource.MANUAL))
    projects.cache.save(entries)
    learned = tmp_path / "repo-learned"
    learned.mkdir()
    visits(projects).record(canonical_path(learned))
    decoded = 0
    original_read = SQLiteSnapshot.read

    def read(snapshot: SQLiteSnapshot, entry: LookupEntry[int]) -> Project:
        nonlocal decoded
        decoded += 1
        return original_read(snapshot, entry)

    monkeypatch.setattr(SQLiteSnapshot, "read", read)
    result = projects.lookup(LookupRequest("repo"), include_directories=True)
    assert result.total == 231
    assert len(result.matches) == 100
    assert decoded <= 100


def test_import_preference_does_not_reset_legacy_usage(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    path = canonical_path(tmp_path / "notes")
    path.mkdir()
    other = canonical_path(tmp_path / "second")
    other.mkdir()
    projects.cache.save(
        [
            Project("notes", path, path, ProjectSource.MANUAL),
            Project("notes", other, other, ProjectSource.MANUAL),
        ]
    )
    UsageHistory(projects.history.path).record(path)
    visits(projects).merge({path: 15.0})
    result = projects.lookup(LookupRequest("note"), include_directories=True)
    assert result.usage is not None
    assert result.usage[path].count == 1
    assert result.usage[path].used_at > 0
    assert result.usage[path].preference == 15.0


def test_usage_ties_use_canonical_path(tmp_path: Path) -> None:
    from pcd_cli.search import rank_matches

    a = Project("same", tmp_path / "a", tmp_path / "a", ProjectSource.MANUAL)
    b = Project("same", tmp_path / "b", tmp_path / "b", ProjectSource.MANUAL)
    usage = {a.path: ProjectUsage(2, 3), b.path: ProjectUsage(2, 3)}
    assert rank_matches([b, a], "same", usage) == [a, b]


def test_pcd_prefers_final_component_over_frequently_visited_descendant(
    projects: ProjectCatalog, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "acme" / "api"
    nested = root / "service" / "tests"
    nested.mkdir(parents=True)
    assert runner.invoke(cli, ["add", str(root)]).exit_code == 0
    for _ in range(3):
        visits(projects).record(canonical_path(nested))
    monkeypatch.setattr(ProjectPicker, "run", lambda picker: picker.matches[0])
    result = runner.invoke(cli, ["acme", "api"])
    assert result.exit_code == 0
    assert result.stdout == f"{canonical_path(root)}\n"


def test_multiword_later_exact_component_beats_early_fuzzy_ancestor(tmp_path: Path) -> None:
    from pcd_cli.search import rank_matches

    wanted = tmp_path / "a-c-m-e" / "acme" / "api"
    other = tmp_path / "acme" / "api-extra"
    a = Project("api", wanted, wanted, ProjectSource.MANUAL)
    b = Project("api-extra", other, other, ProjectSource.MANUAL)
    assert rank_matches([b, a], "acme api") == [a, b]


def test_resume_rejects_subdirectory_replaced_by_outside_symlink(
    projects: ProjectCatalog, runner: CliRunner, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    nested = root / "linked"
    outside = tmp_path / "outside"
    nested.mkdir(parents=True)
    outside.mkdir()
    assert runner.invoke(cli, ["add", str(root)]).exit_code == 0
    visits(projects).record(canonical_path(nested))
    nested.rmdir()
    try:
        nested.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is unavailable")
    result = runner.invoke(cli, ["repo", "--resume"])
    assert result.exit_code == 0
    assert result.stdout == f"{canonical_path(root)}\n"
    assert "is outside the project" in result.stderr
