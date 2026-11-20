from __future__ import annotations

import os
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from pcd_cli.filesystem import canonical_path
from pcd_cli.lookup import LookupRequest
from pcd_cli.models import Project, ProjectSource
from pcd_cli.navigation import select_project
from pcd_cli.picker import ProjectPicker
from pcd_cli.search import rank_matches, rank_recent
from pcd_cli.visits import DirectoryVisits

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pcd_cli.catalog import ProjectCatalog
    from pcd_cli.lookup import DirectoryEntry


@pytest.mark.parametrize(
    "query",
    [
        "api",
        "ap",
        "ai",
        "acme api",
        "ame ai",
        "api acme",
        "STRASSE",
        "\u03c3",
        "%",
        "_",
        "[",
        "",
        "x" * 300,
        "\0",
    ],
)
def test_learned_lookup_preserves_reference_ranking(
    projects: ProjectCatalog, tmp_path: Path, query: str
) -> None:
    paths = [
        tmp_path / parent / name
        for parent, name in [
            ("acme", "api"),
            ("other", "api"),
            ("acme", "api-extra"),
            ("a-c-m-e", "a-p-i"),
            ("unicode", "Straße"),
            ("unicode", "ς"),
            ("punct", "percent%"),
            ("punct", "under_score"),
            ("punct", "[literal]"),
            ("api", "acme"),
            ("spaced", "acme api"),
        ]
    ]
    store = DirectoryVisits(projects.history.path)
    for index, path in enumerate(paths):
        path.mkdir(parents=True)
        paths[index] = canonical_path(path)
    store.merge({path: float(index) for index, path in enumerate(paths)})
    # An alias shadows the learned basename of the same canonical directory.
    registered = Project("renamed", paths[0], paths[0], ProjectSource.MANUAL)
    projects.cache.save([registered])
    all_projects = [registered] + [
        Project(path.name, path, path, ProjectSource.MANUAL) for path in paths[1:]
    ]
    usage = store.usage()
    expected = (
        rank_matches(all_projects, query, usage) if query else rank_recent(all_projects, usage)
    )
    result = projects.lookup(
        LookupRequest(query, limit=None), include_directories=True, refresh_on_miss=False
    )
    assert result.matches == expected
    assert result.total == len(expected)


def test_learned_exact_preserves_weaker_matches_and_rejects_stale_excluded(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [tmp_path / name for name in ("api", "api-extra", "a-p-i", "api-stale", "api-private")]
    for path in paths:
        path.mkdir()
    paths = [canonical_path(path) for path in paths]
    store = DirectoryVisits(projects.history.path)
    store.merge(dict.fromkeys(paths, 1.0))
    paths[3].rmdir()
    monkeypatch.setenv("PCD_EXCLUDE_DIRS", str(paths[4]))

    def fail_full_usage(_store: DirectoryVisits) -> dict[Path, object]:
        raise AssertionError("Queries must not read unrelated usage")

    monkeypatch.setattr(DirectoryVisits, "usage", fail_full_usage)
    result = projects.lookup(LookupRequest("api"), include_directories=True, refresh_on_miss=False)
    assert [project.path for project in result.matches] == paths[:3]


def test_directory_exact_sql_uses_name_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DirectoryVisits(tmp_path / "directories.sqlite3")
    path = tmp_path / "Straße"
    store.record(path)
    statements: list[str] = []
    original_connection = DirectoryVisits._connection

    @contextmanager
    def traced(self: DirectoryVisits, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        with original_connection(self, write=write) as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(DirectoryVisits, "_connection", traced)
    assert store.entries("STRASSE") == store.entries()
    exact_sql = next(
        statement
        for statement in statements
        if statement.startswith("SELECT path,name_key,count,used,preference,identity FROM visits")
        and "name_key='strasse'" in statement
    )
    with original_connection(store) as db:
        plan = db.execute("EXPLAIN QUERY PLAN " + exact_sql).fetchall()
        assert any(
            "SEARCH visits USING INDEX visits_by_name (name_key=?)" in row[3] for row in plan
        )


def test_version_two_upgrade_preserves_flags_counts_and_resume(tmp_path: Path) -> None:
    store = DirectoryVisits(tmp_path / "directories.sqlite3")
    learned, project = tmp_path / "Straße", tmp_path / "project"
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("""CREATE TABLE visits(identity TEXT PRIMARY KEY,path TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,used REAL NOT NULL DEFAULT 0,
            preference REAL NOT NULL DEFAULT 0,learned INTEGER NOT NULL DEFAULT 1,
            tracked_used REAL NOT NULL DEFAULT 0)""")
        db.execute("CREATE TABLE history_metadata(key TEXT PRIMARY KEY)")
        db.execute("INSERT INTO history_metadata VALUES('legacy_imported')")
        db.executemany(
            "INSERT INTO visits VALUES(?,?,?,?,?,?,?)",
            [
                (os.path.normcase(str(learned)), str(learned), 7, 50, 12, 1, 40),
                (os.path.normcase(str(project)), str(project), 9, 60, 0, 0, 0),
            ],
        )
        db.execute("PRAGMA user_version=2")
    assert store.entries("STRASSE")[learned].count == 7
    assert store.entries()[learned].preference == 12
    assert store.usage()[project].count == 9
    assert project not in store.entries()
    assert store.resume(tmp_path) == learned
    with store._connection() as db:
        assert db.execute("PRAGMA user_version").fetchone() == (3,)
    store.merge({project: 5})
    assert store.entries("project")[project].count == 9


def test_picker_queries_broaden_and_page_all_learned_duplicates(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [canonical_path(tmp_path / f"parent-{index:03d}" / "api") for index in range(137)]
    for path in paths:
        path.mkdir(parents=True)
    visits = DirectoryVisits(projects.history.path)
    visits.merge({path: float(index % 11) for index, path in enumerate(paths)})
    projects.cache.save([])
    expected = rank_matches(
        [Project("api", path, path, ProjectSource.MANUAL) for path in paths], "api", visits.usage()
    )
    initial = projects.lookup(LookupRequest("api"), include_directories=True)

    def run(picker: ProjectPicker) -> None:
        first = picker.resolve(LookupRequest("parent-000 api"))
        assert first.total == 1
        broadened = picker.resolve(LookupRequest("api"))
        assert broadened.total == 137
        assert broadened.matches == expected[:100]
        second = picker.resolve(LookupRequest("api", after=broadened.last_rank))
        assert second.matches == expected[100:] and second.offset == 100
        previous = picker.resolve(LookupRequest("api", before=second.first_rank))
        assert previous.matches == expected[:100]
        last = picker.resolve(LookupRequest("api", last=True))
        assert last.matches == expected[-100:]
        empty = picker.resolve(LookupRequest(""))
        assert empty.total == 137
        assert picker.resolve(replace(LookupRequest("api"), limit=None)).matches == expected
        return None

    monkeypatch.setattr(ProjectPicker, "run", run)
    assert select_project(projects, initial, "api") is None


@pytest.mark.parametrize("registered", [False, True])
def test_picker_restarts_page_after_concurrent_visit(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: bool
) -> None:
    from weakref import ref

    from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
    from prompt_toolkit.key_binding.key_processor import KeyProcessor

    paths = [canonical_path(tmp_path / str(index) / "api") for index in range(105)]
    for path in paths:
        path.mkdir(parents=True)
    items = [Project("api", path, path, ProjectSource.MANUAL) for path in paths]
    store = DirectoryVisits(projects.history.path)
    store.merge(dict.fromkeys(paths, 1))
    projects.cache.save(items if registered else [])
    initial = projects.lookup(LookupRequest("api"), include_directories=True)
    target = next(path for path in paths if path not in {item.path for item in initial.matches})
    store.record(target)

    def run(picker: ProjectPicker) -> Project | None:
        processor = KeyProcessor(KeyBindings())
        picker._move_up(KeyPressEvent(ref(processor), None, [], [], False))
        assert picker.result.offset == 0
        assert picker.result.generation != initial.generation
        return picker.matches[picker.selected_index]

    monkeypatch.setattr(ProjectPicker, "run", run)
    selected = select_project(projects, initial, "api")
    assert selected is not None and selected.path == target


def test_revision_precedes_candidate_read_and_idempotent_import(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [canonical_path(tmp_path / name) for name in ("api", "api-extra")]
    for path in paths:
        path.mkdir()
    store = DirectoryVisits(projects.history.path)
    store.merge(dict.fromkeys(paths, 1))
    revision = store.revision()
    store.merge(dict.fromkeys(paths, 1))
    assert store.revision() == revision
    projects.cache.save([])
    original = DirectoryVisits.candidates

    def entries(self: DirectoryVisits, query: str = "") -> Iterator[DirectoryEntry]:
        found = list(original(self, query))
        self.record(paths[1])
        return iter(found)

    monkeypatch.setattr(DirectoryVisits, "candidates", entries)
    initial = projects.lookup(LookupRequest("api"), include_directories=True)
    assert initial.generation & ((1 << 64) - 1) == revision
    monkeypatch.setattr(DirectoryVisits, "candidates", original)
    next_page = projects.lookup(
        LookupRequest("api", after=initial.last_rank), include_directories=True
    )
    assert next_page.generation != initial.generation


def test_dense_live_ranking_totals_and_pages_recheck_missing_and_replaced_paths(
    projects: ProjectCatalog, tmp_path: Path
) -> None:
    paths = [
        canonical_path(tmp_path / f"parent-{index // 60}" / f"api-{index:03d}")
        for index in range(180)
    ]
    for path in paths:
        path.mkdir(parents=True)
    visits = DirectoryVisits(projects.history.path)
    visits.merge({path: float(index % 7) for index, path in enumerate(paths)})
    projects.cache.save([])
    usage = visits.usage()
    for path in paths[::11]:
        path.rmdir()
    for path in paths[1::13]:
        if path.is_dir():
            path.rmdir()
        path.touch()

    def expected_live() -> list[Project]:
        return rank_matches(
            [
                Project(path.name, path, path, ProjectSource.MANUAL)
                for path in paths
                if path.is_dir()
            ],
            "api",
            usage,
        )

    expected = expected_live()
    first = projects.lookup(LookupRequest("api"), include_directories=True, refresh_on_miss=False)
    assert first.total == len(expected)
    assert first.matches == expected[:100]
    second = projects.lookup(
        LookupRequest("api", after=first.last_rank),
        include_directories=True,
        refresh_on_miss=False,
    )
    assert second.total == len(expected)
    assert second.offset == 100
    assert second.matches == expected[100:]
    previous = projects.lookup(
        LookupRequest("api", before=second.first_rank),
        include_directories=True,
        refresh_on_miss=False,
    )
    assert previous.matches == expected[:100]
    # Reusing the same catalog must still observe live changes on the next query.
    removed = expected[0].path
    removed.rmdir()
    removed.touch()
    paths[0].mkdir()
    expected = expected_live()
    fresh = projects.lookup(
        LookupRequest("api", limit=None), include_directories=True, refresh_on_miss=False
    )
    assert fresh.total == len(expected)
    assert fresh.matches == expected


def test_ranked_window_merges_project_and_directory_usage_across_batches(
    projects: ProjectCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [canonical_path(tmp_path / f"svc-{index:04d}") for index in range(635)]
    # Project metadata crosses the 500-entry usage batch boundary. Each alias
    # shadows a learned copy whose basename also matches the query.
    registered = [
        Project(f"svc-{501 - index:04d}", path, path, ProjectSource.MANUAL)
        for index, path in enumerate(paths[:502])
    ]
    projects.cache.save(registered)
    visits = DirectoryVisits(projects.history.path)
    visits.merge({path: float(index % 17) for index, path in enumerate(paths)})
    visits.record(paths[499])
    visits.record(paths[507])
    for path in paths[:-2]:
        path.mkdir()
    paths[-2].touch()
    monkeypatch.setenv("PCD_EXCLUDE_DIRS", str(paths[513]))
    learned = [
        Project(path.name, path, path, ProjectSource.MANUAL)
        for path in paths[502:-2]
        if path != paths[513]
    ]
    usage = visits.usage()
    expected = rank_matches([*registered, *learned], "svc", usage)
    request = LookupRequest("svc")
    first = projects.lookup(request, include_directories=True, refresh_on_miss=False)
    second = projects.lookup(
        replace(request, after=first.last_rank), include_directories=True, refresh_on_miss=False
    )
    previous = projects.lookup(
        replace(request, before=second.first_rank), include_directories=True, refresh_on_miss=False
    )
    assert [first.total, second.total, previous.total] == [len(expected)] * 3
    assert [first.offset, second.offset, previous.offset] == [0, 100, 0]
    assert first.matches == previous.matches == expected[:100]
    assert second.matches == expected[100:200]
    assert first.usage == {project.path: usage[project.path] for project in expected[:100]}
