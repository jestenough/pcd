from __future__ import annotations

import sqlite3
import subprocess
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Event
from typing import TYPE_CHECKING

import pytest

import pcd_cli.storage as storage
from pcd_cli.cache import ProjectCache
from pcd_cli.lookup import lookup_snapshot, LookupMode, LookupRequest, MemorySnapshot
from pcd_cli.models import Discovery, Project, ProjectSource, ProjectUsage
from pcd_cli.search import rank_matches, rank_recent
from pcd_cli.storage import SQLiteProjectStore, SQLiteSnapshot

if TYPE_CHECKING:
    import os
    from collections.abc import Iterable
    from pathlib import Path

    from pcd_cli.lookup import LookupEntry


def item(path: Path, name: str = "repo") -> Project:
    return Project(name, path, path, ProjectSource.MANUAL)


@pytest.fixture
def store(tmp_path: Path) -> SQLiteProjectStore:
    return SQLiteProjectStore(tmp_path / "projects.sqlite3")


def test_database_path_with_uri_special_characters(tmp_path: Path) -> None:
    name = "café # %2F" if sys.platform == "win32" else "café # %2F ?mode=ro"
    store = SQLiteProjectStore(tmp_path / name / "projects.sqlite3")
    project = item(tmp_path / "repo")
    assert store.save([project])
    assert store.load() == [project]
    assert store.save([])
    assert store.load() == []


def test_round_trip_preserves_provenance(store: SQLiteProjectStore, tmp_path: Path) -> None:
    project = Project(
        "Repo",
        tmp_path / "repo",
        tmp_path / "link",
        ProjectSource.DISCOVERED,
        (Discovery(tmp_path, tmp_path / "link"),),
    )
    assert store.save([project], started_at_ns=123)
    assert store.load() == [project]
    result = store.lookup(LookupRequest("REPO"))
    assert result is not None and result.matches == [project] and result.generation == 123
    assert result.usage is None


@pytest.mark.parametrize("query", ["STRASSE", "ss", "\u03c3", "\U0010ffff", "", "absent"])
@pytest.mark.parametrize("mode", list(LookupMode))
def test_unicode_lookup_matches_reference(
    store: SQLiteProjectStore,
    tmp_path: Path,
    query: str,
    mode: LookupMode,
) -> None:
    projects = [
        item(tmp_path / str(i), name)
        for i, name in enumerate(
            ["Straße", "STRASSE", "ßeta", "Σ", "ς", "\U0010ffff", "\U0010ffffmore", "alpha"]
        )
    ]
    usage = {projects[1].path: ProjectUsage(123.0, 4)}
    store.save(projects)
    result = store.lookup(LookupRequest(query, mode), lambda: usage)
    assert result is not None
    key = query.casefold()
    exact = [p for p in projects if p.name.casefold() == key]
    if mode is LookupMode.PREFIX:
        expected = rank_matches([p for p in projects if p.name.casefold().startswith(key)], query)
        assert result.usage is None
    elif not query and mode is LookupMode.RESOLVE:
        expected = rank_recent(projects, usage)
    else:
        expected = rank_matches(
            exact if exact or mode is LookupMode.EXACT else projects, query, usage
        )
    assert result.matches == expected
    assert result.total == len(expected)


def test_missing_and_empty_are_distinct(store: SQLiteProjectStore) -> None:
    assert store.load() is None
    assert store.lookup(LookupRequest("")) is None
    store.save([])
    assert store.load() == []
    result = store.lookup(LookupRequest(""))
    assert result is not None and result.matches == [] and result.total == 0


def test_exact_uses_index_and_one_decode(
    store: SQLiteProjectStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = [item(tmp_path / str(i), f"repo-{i:04d}") for i in range(4096)]
    store.save(projects)
    reads = 0
    original_read = SQLiteSnapshot.read

    def read(snapshot: SQLiteSnapshot, entry: LookupEntry[int]) -> Project:
        nonlocal reads
        reads += 1
        return original_read(snapshot, entry)

    def fail_records(_snapshot: SQLiteSnapshot) -> None:
        raise AssertionError("exact lookup must not enumerate the catalog")

    def fail_history() -> dict[Path, ProjectUsage]:
        raise AssertionError("unique exact lookup must not read history")

    monkeypatch.setattr(SQLiteSnapshot, "read", read)
    monkeypatch.setattr(SQLiteSnapshot, "records", fail_records)
    result = store.lookup(LookupRequest("repo-4095"), fail_history)
    assert result is not None and result.matches == projects[-1:]
    assert reads == 1
    with closing(sqlite3.connect(store.path)) as connection, connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id, name_key, name_length, path "
            "FROM projects WHERE name_key = ? ORDER BY name_key, path",
            (b"repo-4095",),
        ).fetchall()
    assert any("SEARCH" in row[3] and "by_name" in row[3] for row in plan)


def test_fuzzy_decodes_only_window(
    store: SQLiteProjectStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = [item(tmp_path / str(i), f"app-{i:04d}") for i in range(2000)]
    store.save(projects)
    reads = 0
    original_read = SQLiteSnapshot.read

    def read(snapshot: SQLiteSnapshot, entry: LookupEntry[int]) -> Project:
        nonlocal reads
        reads += 1
        return original_read(snapshot, entry)

    monkeypatch.setattr(SQLiteSnapshot, "read", read)
    result = store.lookup(LookupRequest("ap"))
    assert result is not None and result.total == 2000
    assert result.matches == projects[:100]
    assert reads == 100


def test_all_duplicate_windows_preserve_rank(store: SQLiteProjectStore, tmp_path: Path) -> None:
    projects = [item(tmp_path / f"{i:04d}", "same") for i in range(237)]
    usage = {projects[-1].path: ProjectUsage(42.0, 3)}
    store.save(projects)
    expected = rank_matches(projects, "same", usage)
    seen: list[Project] = []
    request = LookupRequest("same")
    while True:
        result = store.lookup(request, lambda: usage)
        assert result is not None and result.total == 237
        assert len(result.matches) <= 100
        assert result.offset == len(seen)
        seen.extend(result.matches)
        if len(seen) == result.total:
            break
        request = replace(request, after=result.last_rank)
    assert seen == expected
    previous = store.lookup(LookupRequest("same", before=result.first_rank), lambda: usage)
    assert previous is not None and previous.offset == 100
    assert previous.matches == expected[100:200]
    last = store.lookup(LookupRequest("same", last=True), lambda: usage)
    assert last is not None and last.matches == expected[-100:] and last.offset == 137


def test_legacy_import_preserves_timestamp_and_file(
    store: SQLiteProjectStore, tmp_path: Path
) -> None:
    project = item(tmp_path / "repo")
    legacy = ProjectCache(store.legacy_path)
    legacy.save([project], started_at_ns=123)
    before = legacy.path.read_bytes()
    result = store.lookup(LookupRequest("repo"))
    assert result is not None and result.generation == 123 and result.matches == [project]
    assert legacy.path.read_bytes() == before
    assert store.initialized_path.is_file()


def test_completion_never_migrates_or_loads_history(
    store: SQLiteProjectStore, tmp_path: Path
) -> None:
    project = item(tmp_path / "repo")
    ProjectCache(store.legacy_path).save([project])
    before = set(tmp_path.iterdir())

    def fail() -> dict[Path, ProjectUsage]:
        raise AssertionError("completion must not read history")

    result = store.lookup(LookupRequest("re", LookupMode.PREFIX, limit=None), fail)
    assert result is not None and result.matches == [project]
    assert set(tmp_path.iterdir()) == before


def test_sqlite_loss_does_not_resurrect_legacy(store: SQLiteProjectStore, tmp_path: Path) -> None:
    ProjectCache(store.legacy_path).save([item(tmp_path / "removed")], started_at_ns=1)
    assert store.load()
    assert store.save([], started_at_ns=2)
    store.path.unlink()
    assert store.load() is None
    assert store.lookup(LookupRequest("re", LookupMode.PREFIX)) is None
    assert store.lookup(LookupRequest("repo")) is None


@pytest.mark.parametrize(
    "bad", ["timestamp", "duplicate", "row", "header", "encoding", "surrogate"]
)
def test_incompatible_legacy_does_not_block_recovery(
    store: SQLiteProjectStore,
    tmp_path: Path,
    bad: str,
) -> None:
    project = item(tmp_path / "repo")
    legacy = ProjectCache(store.legacy_path)
    legacy.save(
        [project, project] if bad == "duplicate" else [project],
        started_at_ns=2**64 if bad == "timestamp" else 1,
    )
    if bad == "row":
        with legacy.path.open("a") as stream:
            stream.write("{}\n")
    elif bad == "header":
        legacy.path.write_text("{}\n")
    elif bad == "encoding":
        legacy.path.write_bytes(b"\xff")
    elif bad == "surrogate":
        legacy.path.write_text(legacy.path.read_text().replace('"repo"', '"\\ud800"'))
    assert store.load() is None
    assert not store.path.exists()
    assert store.save([project])
    assert store.load() == [project]


def test_migration_rechecks_state_under_lock(store: SQLiteProjectStore, tmp_path: Path) -> None:
    assert store.save([], started_at_ns=2)
    assert not store._save([item(tmp_path / "old")], 1, importing=True)
    assert store.load() == []


def test_older_refresh_cannot_replace_newer(store: SQLiteProjectStore, tmp_path: Path) -> None:
    newer, older = item(tmp_path / "new"), item(tmp_path / "old")
    assert store.save([newer], started_at_ns=200)
    assert not store.save([older], started_at_ns=100)
    assert store.load() == [newer]
    store.path.write_bytes(b"damaged SQLite database")
    assert store.save([older], started_at_ns=100)
    assert store.load() == [older]


@pytest.mark.parametrize("timestamp", [-1, True, 2**64])
def test_invalid_write_timestamp(store: SQLiteProjectStore, timestamp: int) -> None:
    with pytest.raises(ValueError, match="timestamp"):
        store.save([], started_at_ns=timestamp)
    assert not store.path.exists()


@pytest.mark.parametrize("names", [("repo", "repo"), ("ß", "ss")])
def test_duplicate_identity_preserves_previous_snapshot(
    store: SQLiteProjectStore, tmp_path: Path, names: tuple[str, str]
) -> None:
    project = item(tmp_path / "repo", names[0])
    duplicate = item(project.path, names[1])
    store.save([project])
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="Duplicate"):
        store.save([project, duplicate])
    assert store.path.read_bytes() == before
    assert not store.path.with_name(store.path.name + "-journal").exists()


@pytest.mark.parametrize(
    "damage", ["header", "truncated", "version", "schema", "generation", "count"]
)
def test_corruption_is_cache_miss(store: SQLiteProjectStore, tmp_path: Path, damage: str) -> None:
    store.save([item(tmp_path / "repo")])
    if damage in {"header", "truncated"}:
        data = bytearray(store.path.read_bytes())
        if damage == "truncated":
            data = data[:20]
        else:
            data[0] ^= 0xFF
        store.path.write_bytes(data)
    else:
        statements = {
            "version": "PRAGMA user_version = 999",
            "schema": "DROP TABLE projects",
            "generation": "UPDATE metadata SET generation = 'invalid'",
            "count": "UPDATE metadata SET project_count = -1",
        }
        with closing(sqlite3.connect(store.path)) as connection, connection:
            connection.execute(statements[damage])
    assert store.load() is None
    assert store.lookup(LookupRequest("repo")) is None
    assert store.lookup(LookupRequest("", LookupMode.PREFIX)) is None


def test_failed_transaction_preserves_previous_snapshot(
    store: SQLiteProjectStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = item(tmp_path / "repo")
    assert store.save([project], started_at_ns=1)
    write = SQLiteProjectStore._write

    def fail(
        cache: SQLiteProjectStore,
        connection: sqlite3.Connection,
        projects: Iterable[Project],
        generation: int,
    ) -> None:
        write(cache, connection, projects, generation)
        raise OSError("disk full")

    monkeypatch.setattr(SQLiteProjectStore, "_write", fail)
    with pytest.raises(OSError, match="disk full"):
        store.save([item(tmp_path / "replacement")], started_at_ns=2)
    assert store.load() == [project]
    result = store.lookup(LookupRequest("repo"))
    assert result is not None and result.generation == 1
    assert not store.path.with_name(store.path.name + "-journal").exists()


def test_failed_first_publication_cannot_import_stale_legacy(
    store: SQLiteProjectStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ProjectCache(store.legacy_path).save([item(tmp_path / "removed")], started_at_ns=1)

    def fail(_temporary: Path, _destination: Path) -> None:
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_publish", fail)
        with pytest.raises(OSError, match="disk full"):
            store.save([], started_at_ns=2)
    assert store.initialized_path.exists()
    assert store.load() is None
    assert store.save([], started_at_ns=3)
    assert store.load() == []


def test_read_handles_are_closed(store: SQLiteProjectStore, tmp_path: Path) -> None:
    store.save([item(tmp_path / "repo")])
    for _ in range(50):
        assert store.lookup(LookupRequest("repo")) is not None
    # On Windows leaked database handles prevent this replacement/deletion.
    store.path.rename(tmp_path / "moved")
    (tmp_path / "moved").unlink()


@pytest.mark.parametrize(
    "after,before,last,limit", [(None, None, False, 0), ((0, 0, 0.0, 0, 0, "", ""), None, True, 1)]
)
def test_lookup_rejects_invalid_window(
    after: tuple[int, int, float, int, int, str, str] | None,
    before: tuple[int, int, float, int, int, str, str] | None,
    last: bool,
    limit: int,
) -> None:
    with pytest.raises(ValueError):
        LookupRequest("", limit=limit, after=after, before=before, last=last)


def test_memory_fallback_supports_prefix(tmp_path: Path) -> None:
    project = item(tmp_path / "repo")
    assert lookup_snapshot(
        MemorySnapshot([project]), LookupRequest("re", LookupMode.PREFIX)
    ).matches == [project]


def test_completion_preserves_catalog_order_without_ranking(
    store: SQLiteProjectStore,
    tmp_path: Path,
) -> None:
    projects = [item(tmp_path / str(i), name) for i, name in enumerate(["ab", "aaaaaaaa", "a"])]
    store.save(projects)
    request = LookupRequest("a", LookupMode.PREFIX, limit=None)
    result = store.lookup(request)
    assert result is not None and result.matches == [projects[2], projects[1], projects[0]]
    assert result.total == 3 and result.usage is None
    assert result.first_rank is None and result.last_rank is None


@pytest.mark.parametrize("change", ["name", "source", "checksum", "json"])
def test_structurally_invalid_records_are_rejected(
    store: SQLiteProjectStore,
    tmp_path: Path,
    change: str,
) -> None:
    store.save([item(tmp_path / "repo")])
    with closing(sqlite3.connect(store.path)) as connection, connection:
        row = connection.execute("SELECT payload FROM projects").fetchone()
        assert row is not None
        payload = str(row[0])
        if change == "name":
            payload = payload.replace('"repo"', '"evil"')
        elif change == "source":
            payload = payload.replace('"manual"', '"broken"')
        elif change == "json":
            payload = "{}"
        checksum = zlib.crc32(payload.encode("utf-8"))
        if change == "checksum":
            checksum ^= 1
        connection.execute("UPDATE projects SET payload = ?, payload_crc = ?", (payload, checksum))
    assert store.load() is None
    assert store.lookup(LookupRequest("repo")) is None


def test_exact_does_not_decode_unrelated_payloads(
    store: SQLiteProjectStore, tmp_path: Path
) -> None:
    first, second = item(tmp_path / "first", "first"), item(tmp_path / "second", "second")
    store.save([first, second])
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("UPDATE projects SET payload = '{}' WHERE name_key = ?", (b"second",))
    result = store.lookup(LookupRequest("first"))
    assert result is not None and result.matches == [first]
    assert store.lookup(LookupRequest("second")) is None
    assert store.load() is None


def test_completion_is_read_only_for_sqlite(store: SQLiteProjectStore, tmp_path: Path) -> None:
    project = item(tmp_path / "repo")
    store.save([project])
    before = {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    result = store.lookup(LookupRequest("re", LookupMode.PREFIX, limit=None))
    assert result is not None and result.matches == [project]
    after = {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    assert after == before


def test_concurrent_reader_sees_complete_snapshot(
    store: SQLiteProjectStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = item(tmp_path / "repo")
    store.save([project], started_at_ns=1)
    written = Event()
    write = SQLiteProjectStore._write

    def signal(
        cache: SQLiteProjectStore,
        connection: sqlite3.Connection,
        projects: Iterable[Project],
        generation: int,
    ) -> None:
        write(cache, connection, projects, generation)
        written.set()

    monkeypatch.setattr(SQLiteProjectStore, "_write", signal)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with store._snapshot() as reader:
            future = executor.submit(store.save, [], started_at_ns=2)
            assert written.wait(5)
            assert [reader.read(entry) for entry in reader.records()] == [project]
            assert reader.generation == 1
        assert future.result(timeout=5)
    assert store.load() == []


def test_independent_store_writers_reject_stale_snapshot(
    store: SQLiteProjectStore, tmp_path: Path
) -> None:
    newer, older = item(tmp_path / "newer"), item(tmp_path / "older")
    other_store = SQLiteProjectStore(store.path)
    assert store.save([newer], started_at_ns=2)
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert not executor.submit(other_store.save, [older], started_at_ns=1).result(timeout=5)
    assert other_store.load() == store.load() == [newer]


def test_empty_legacy_migration_preserves_generation_zero(store: SQLiteProjectStore) -> None:
    assert ProjectCache(store.legacy_path).save([], started_at_ns=0)
    assert store.load() == []
    result = store.lookup(LookupRequest(""))
    assert result is not None and result.generation == 0 and result.total == 0


def test_timestamp_preserves_unsigned_64_bit_boundary(store: SQLiteProjectStore) -> None:
    generation = 2**64 - 1
    assert store.save([], started_at_ns=generation)
    result = store.lookup(LookupRequest(""))
    assert result is not None and result.generation == generation
    assert not store.save([], started_at_ns=generation - 1)


def test_corrupt_newer_payload_does_not_block_recovery(
    store: SQLiteProjectStore, tmp_path: Path
) -> None:
    newer, older = item(tmp_path / "newer"), item(tmp_path / "older")
    assert store.save([newer], started_at_ns=200)
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("UPDATE projects SET payload = '{}' ")
    assert store.load() is None
    assert store.save([older], started_at_ns=100)
    assert store.load() == [older]


def test_locked_database_does_not_trigger_destructive_recovery(
    store: SQLiteProjectStore, tmp_path: Path
) -> None:
    project = item(tmp_path / "repo")
    assert store.save([project], started_at_ns=1)
    before = store.path.read_bytes()
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(OSError, match="locked"):
                store.save([], started_at_ns=2)
        finally:
            connection.rollback()
    assert store.path.read_bytes() == before
    assert store.load() == [project]


def test_completion_treats_stat_permission_failure_as_unavailable_cache(
    store: SQLiteProjectStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.save([item(tmp_path / "repo")])
    before = store.path.read_bytes()
    path_type = type(store.path)
    original_stat = path_type.stat

    def denied(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == store.path:
            raise PermissionError("cache access denied")
        return original_stat(path, follow_symlinks=follow_symlinks)

    with monkeypatch.context() as patch:
        patch.setattr(path_type, "stat", denied)
        assert store.lookup(LookupRequest("re", LookupMode.PREFIX, limit=None)) is None
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("operation", ["load", "exact", "save"])
def test_crash_journal_keeps_completion_read_only_and_recovers_committed_snapshot(
    store: SQLiteProjectStore, tmp_path: Path, operation: str
) -> None:
    projects = [item(tmp_path / str(i), f"repo-{i:02d}") for i in range(16)]
    assert store.save(projects, started_at_ns=200)
    crash = """
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.execute("PRAGMA journal_mode = DELETE")
connection.execute("PRAGMA synchronous = FULL")
connection.execute("PRAGMA cache_size = 5")
connection.execute("BEGIN IMMEDIATE")
connection.execute("UPDATE projects SET payload = payload || zeroblob(20000)")
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-I", "-c", crash, str(store.path)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        timeout=10,
    )
    journal = store.path.with_name(store.path.name + "-journal")
    assert journal.is_file() and journal.stat().st_size > 512
    before = {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    assert store.lookup(LookupRequest("repo", LookupMode.PREFIX, limit=None)) is None
    assert {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()} == before

    if operation == "load":
        assert store.load() == projects
    elif operation == "exact":
        result = store.lookup(LookupRequest("repo-00"))
        assert result is not None and result.matches == projects[:1] and result.generation == 200
    else:
        # Recovery must happen before comparing generations, preserving the newer commit.
        assert not store.save([], started_at_ns=100)
    assert store.load() == projects
    assert not journal.exists()
