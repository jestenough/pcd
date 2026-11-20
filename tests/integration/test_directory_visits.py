from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from typing import TYPE_CHECKING

from pcd_cli.cli import cli
from pcd_cli.history import UsageHistory
from pcd_cli.visits import DirectoryVisits, is_excluded

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from click.testing import CliRunner

    from pcd_cli.catalog import ProjectCatalog


def test_visit_entrypoint_records_once_without_importing_click(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    script = (
        "import sys; from pcd_cli.entrypoint import main; main(); assert 'click' not in sys.modules"
    )
    for args in (["_visit", "--project-only"], ["_visit"]):
        result = subprocess.run(
            [sys.executable, "-c", script, *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert not result.stdout and not result.stderr
    assert projects.history.load()[tmp_path.resolve()].count == 2


def test_project_only_visit_works_without_opening_catalog(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pcd_cli.catalog import ProjectCatalog
    from pcd_cli.filesystem import ApplicationPaths

    def unexpected_catalog() -> ProjectCatalog:
        raise AssertionError("Recording a visit should not open the project catalog")

    monkeypatch.setattr(ProjectCatalog, "create", staticmethod(unexpected_catalog))
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["_visit", "--project-only"]).exit_code == 0
    paths = ApplicationPaths.resolve()
    assert UsageHistory(paths.history).load()[tmp_path.resolve()].count == 1
    assert not paths.cache.exists()


def test_tracking_survives_cache_loss_and_refresh(
    projects: ProjectCatalog, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    child = root / "tests"
    child.mkdir(parents=True)
    monkeypatch.chdir(child)
    assert runner.invoke(cli, ["_visit"]).exit_code == 0
    store = DirectoryVisits(projects.history.path)
    store.merge({child: 83.5})
    before = store.entries()
    projects.refresh()
    projects.cache.path.unlink()
    projects.refresh()
    projects.refresh()
    assert store.entries() == before
    assert before[child].count == 1
    assert before[child].used_at > 0
    assert before[child].preference == 83.5
    assert store.resume(root) == child


def test_parallel_visits_and_resume_boundary(tmp_path: Path) -> None:
    store = DirectoryVisits(tmp_path / "state.sqlite3")
    project = tmp_path / "api"
    child = project / "src"
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(store.record, [child] * 10))
    store.record(tmp_path / "api-server")
    store.record(project)
    assert store.entries()[child].count == 10
    assert store.resume(project) == child


def test_private_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    private = tmp_path / "private"
    monkeypatch.setenv("PCD_EXCLUDE_DIRS", str(private / "*"))
    assert is_excluded(private / "client", frozenset())
    assert is_excluded(private, frozenset({private}))
    assert is_excluded(private / "client", frozenset({private}))
    assert not is_excluded(tmp_path / "public", frozenset({private}))


def test_import_preview_merge_and_invalid_rows(
    projects: ProjectCatalog, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "directory with spaces"
    other = tmp_path / "other"
    data = subprocess.CompletedProcess(["zoxide"], 0, f"  9.5 {first}\n  2.0 {other}\n", "")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: data)
    store = DirectoryVisits(projects.history.path)
    command = ["import", "zoxide"]
    assert "Would merge 2" in runner.invoke(cli, [*command, "--dry-run"]).output
    assert not store.path.exists()
    store.record(first)
    assert runner.invoke(cli, command).exit_code == 0
    before = store.entries()
    assert runner.invoke(cli, command).exit_code == 0
    assert before == store.entries()
    assert before[first].count == 1 and before[other].count == 0
    assert before[first].preference > before[other].preference
    data.stdout += "nan relative\n"
    assert runner.invoke(cli, command).exit_code == 1
    assert store.entries() == before


def test_import_failure(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    assert "Cannot find zoxide" in runner.invoke(cli, ["import", "zoxide"]).output


def test_real_zoxide_import(
    projects: ProjectCatalog, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    import pytest

    executable = shutil.which("zoxide")
    if not executable:
        pytest.skip("zoxide executable unavailable")
    monkeypatch.setenv("_ZO_DATA_DIR", str(tmp_path / "zoxide-state"))
    folder = tmp_path / "external directory"
    folder.mkdir()
    subprocess.run([executable, "add", "--", str(folder)], check=True)
    result = runner.invoke(cli, ["import", "zoxide"])
    assert result.exit_code == 0, result.output
    store = DirectoryVisits(projects.history.path)
    imported = store.entries()[folder]
    assert imported.preference > 0 and imported.count == 0


def test_directory_history_initialization_and_future_schema(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    import pytest

    store = DirectoryVisits(tmp_path / "state.sqlite3")
    with closing(sqlite3.connect(store.path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("CREATE TABLE future(value INTEGER)")
        assert store.entries() == {}
        assert store.resume(tmp_path) is None
        writer.execute("PRAGMA user_version = 4")
        writer.commit()
    with pytest.raises(OSError, match="Unsupported"):
        store.record(tmp_path)
    with closing(sqlite3.connect(store.path)) as reader:
        assert reader.execute("PRAGMA user_version").fetchone() == (4,)


def test_excluded_directories_are_never_recorded_or_imported(
    projects: ProjectCatalog, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setenv("PCD_EXCLUDE_DIRS", str(private))
    monkeypatch.chdir(private)
    store = DirectoryVisits(projects.history.path)
    assert runner.invoke(cli, ["_visit"]).exit_code == 0
    assert runner.invoke(cli, ["_visit", "--project-only"]).exit_code == 0
    assert projects.history.load() == {}
    data = subprocess.CompletedProcess(["zoxide"], 0, f"1.0 {private}\n", "")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: data)
    assert "Merged 0 directories" in runner.invoke(cli, ["import", "zoxide"]).output
    assert not store.path.exists()


def test_broken_zoxide_output_is_reported(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = ["import", "zoxide"]
    data = subprocess.CompletedProcess(["zoxide"], 19, "", "")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: data)
    assert "exit 19" in runner.invoke(cli, command).output

    def invalid_encoding(*args: object, **kwargs: object) -> None:
        raise UnicodeError

    monkeypatch.setattr(subprocess, "run", invalid_encoding)
    assert "Invalid UTF-8" in runner.invoke(cli, command).output


def test_resume_with_reserved_project_name(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "info"
    child = repo / "lib"
    child.mkdir(parents=True)
    assert runner.invoke(cli, ["add", str(repo)]).exit_code == 0
    monkeypatch.chdir(child)
    assert runner.invoke(cli, ["_visit"]).exit_code == 0
    result = runner.invoke(cli, ["--project", "info", "--resume"], env={"PCD_SHELL": "1"})
    assert result.exit_code == 10 and result.stdout.strip() == str(child.resolve())
    assert runner.invoke(cli, ["--resume"]).exit_code == 2


def test_project_visits_and_tracking_share_counts_without_implicit_learning(
    projects: ProjectCatalog, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    child = root / "notes"
    child.mkdir(parents=True)
    assert runner.invoke(cli, ["add", str(root)]).exit_code == 0
    monkeypatch.chdir(child)
    store = DirectoryVisits(projects.history.path)
    for _ in range(7):
        assert runner.invoke(cli, ["_visit", "--project-only"]).exit_code == 0
    assert projects.history.load()[child].count == 7
    assert store.entries() == {}
    assert store.resume(root) is None
    assert runner.invoke(cli, ["notes"]).exit_code == 3
    for _ in range(5):
        assert runner.invoke(cli, ["_visit"]).exit_code == 0
    assert store.entries()[child].count == 12
    assert projects.history.load()[child].count == 12
    assert store.resume(root) == child
    assert runner.invoke(cli, ["notes"]).stdout.strip() == str(child)
    assert not projects.history.path.with_name("history.json").exists()


def _legacy_directory_database(path: Path, child: Path) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TABLE visits (identity TEXT PRIMARY KEY, path TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0, used REAL NOT NULL DEFAULT 0,
            preference REAL NOT NULL DEFAULT 0)""")
        import os

        db.execute(
            "INSERT INTO visits VALUES (?,?,5,10,25)",
            (os.path.normcase(str(child)), str(child)),
        )
        db.execute("PRAGMA user_version=1")


def test_legacy_import_adds_to_existing_sqlite_once_and_preserves_resume(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    tracked = root / "tracked"
    untracked = root / "untracked"
    legacy = tmp_path / "history.json"
    legacy.write_text(
        json.dumps(
            {
                str(tracked): {"count": 7, "used_at": 50},
                str(untracked): {"count": 9, "used_at": 100},
            }
        ),
        encoding="utf-8",
    )
    before = legacy.read_bytes()
    store = DirectoryVisits(tmp_path / "directories.sqlite3")
    _legacy_directory_database(store.path, tracked)
    with ThreadPoolExecutor(4) as pool:
        snapshots = list(pool.map(lambda _: store.usage(), range(4)))
    assert all(snapshot[tracked].count == 12 for snapshot in snapshots)
    assert store.usage()[untracked].count == 9
    assert store.entries()[tracked].preference == 25
    assert untracked not in store.entries()
    assert store.resume(root) == tracked
    assert legacy.read_bytes() == before
    # A retained legacy file must never be imported again, even after it changes.
    legacy.write_text(json.dumps({str(tracked): {"count": 99, "used_at": 200}}), encoding="utf-8")
    assert UsageHistory(store.path).load()[tracked].count == 12


def test_legacy_import_and_schema_upgrade_roll_back_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytest

    child = tmp_path / "repo" / "child"
    store = DirectoryVisits(tmp_path / "directories.sqlite3")
    _legacy_directory_database(store.path, child)
    legacy = tmp_path / "history.json"
    legacy.write_text(json.dumps({str(child): {"count": 7, "used_at": 50}}), encoding="utf-8")
    original = DirectoryVisits._import_legacy

    def fail_after_import(self: DirectoryVisits, db: sqlite3.Connection) -> None:
        original(self, db)
        raise OSError("interrupted migration")

    monkeypatch.setattr(DirectoryVisits, "_import_legacy", fail_after_import)
    with pytest.raises(OSError, match="interrupted migration"):
        store.usage()
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (1,)
        assert db.execute("SELECT count FROM visits").fetchone() == (5,)
    monkeypatch.setattr(DirectoryVisits, "_import_legacy", original)
    assert store.usage()[child].count == 12
    assert store.usage()[child].count == 12


def test_project_only_visit_does_not_change_tracked_resume_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    first, second = root / "first", root / "second"
    store = DirectoryVisits(tmp_path / "directories.sqlite3")
    times = iter((10.0, 20.0, 30.0))
    monkeypatch.setattr("pcd_cli.visits.time.time", lambda: next(times))
    store.record(first)
    store.record(second)
    UsageHistory(store.path).record(first)
    assert store.usage()[first].count == 2
    assert store.resume(root) == second


def test_first_migration_failure_leaves_retryable_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytest

    child = tmp_path / "child"
    store = DirectoryVisits(tmp_path / "directories.sqlite3")
    legacy = tmp_path / "history.json"
    legacy.write_text(json.dumps({str(child): {"count": 7, "used_at": 50}}), encoding="utf-8")
    original = DirectoryVisits._import_legacy

    def fail_after_import(self: DirectoryVisits, db: sqlite3.Connection) -> None:
        original(self, db)
        raise OSError("interrupted first migration")

    monkeypatch.setattr(DirectoryVisits, "_import_legacy", fail_after_import)
    with pytest.raises(OSError, match="interrupted first migration"):
        store.usage()
    assert store.path.exists()
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (0,)
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []
    monkeypatch.setattr(DirectoryVisits, "_import_legacy", original)
    assert store.usage()[child].count == 7
    assert store.usage()[child].count == 7
    assert store.entries() == {}


def test_out_of_order_visits_do_not_move_recency_backwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = tmp_path / "child"
    store = DirectoryVisits(tmp_path / "directories.sqlite3")
    times = iter((20.0, 10.0))
    monkeypatch.setattr("pcd_cli.visits.time.time", lambda: next(times))
    store.record(child)
    store.record(child)
    assert store.entries()[child].used_at == 20
    assert store.entries()[child].count == 2
