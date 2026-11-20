from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from typing import TYPE_CHECKING

from pcd_cli.history import UsageHistory
from pcd_cli.visits import DirectoryVisits

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
