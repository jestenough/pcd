from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from pcd_cli.cache import CACHE_SCHEMA_VERSION, ProjectCache
from pcd_cli.filesystem import canonical_path
from pcd_cli.models import Discovery, Project, ProjectSource

if TYPE_CHECKING:
    from pathlib import Path

CACHE_HEADER = f'{{"version":{CACHE_SCHEMA_VERSION},"refresh_started_ns":1}}'


def project(path: Path, name: str = "repo") -> Project:
    return Project(name, canonical_path(path), path, ProjectSource.DISCOVERED)


def test_cache_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "projects.jsonl"
    repo = tmp_path / "repo"
    repo.mkdir()
    item = project(repo)
    cache = ProjectCache(path)

    cache.save((item,))

    assert cache.load() == [item]
    assert cache.find_exact("REPO") == [item]
    assert cache.find_prefix("re") == [item]


def test_cache_round_trip_preserves_discovery_provenance(tmp_path: Path) -> None:
    cache = ProjectCache(tmp_path / "projects.jsonl")
    repo = tmp_path / "repo"
    repo.mkdir()
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    item = Project(
        "repo",
        canonical_path(repo),
        first_root / "linked",
        ProjectSource.DISCOVERED,
        discoveries=(
            Discovery(first_root, first_root / "linked"),
            Discovery(second_root, second_root / "repo"),
        ),
    )

    cache.save((item,))

    reloaded = ProjectCache(cache.path).load()
    assert reloaded is not None
    assert reloaded[0].discoveries == item.discoveries
    header = json.loads(cache.path.read_text(encoding="utf-8").splitlines()[0])
    assert header["version"] == CACHE_SCHEMA_VERSION
    assert isinstance(header["refresh_started_ns"], int)


@pytest.mark.parametrize(
    "snapshot",
    [
        '{"name":"repo","path":"/repo","display":"/repo","source":"discovered"}\n',
        (
            '{"version":1}\n'
            '{"name":"repo","path":"/repo","display":"/repo","source":"discovered",'
            '"discoveries":[]}\n'
        ),
    ],
    ids=["legacy-unversioned", "older-version"],
)
def test_cache_rejects_older_snapshots(tmp_path: Path, snapshot: str) -> None:
    cache = ProjectCache(tmp_path / "projects.jsonl")
    cache.path.write_text(snapshot, encoding="utf-8")

    assert cache.load() is None
    assert cache.find_exact("repo") is None
    assert cache.find_prefix("re") is None


@pytest.mark.parametrize(
    "header",
    [
        "",
        "[]",
        '{"version":"3"}',
        '{"version":true}',
        '{"version":2}',
        f'{{"version":{CACHE_SCHEMA_VERSION}}}',
        f'{{"version":{CACHE_SCHEMA_VERSION},"refresh_started_ns":"1"}}',
        f'{{"version":{CACHE_SCHEMA_VERSION},"refresh_started_ns":true}}',
        f'{{"version":{CACHE_SCHEMA_VERSION},"refresh_started_ns":-1}}',
    ],
)
def test_cache_rejects_invalid_headers(tmp_path: Path, header: str) -> None:
    cache = ProjectCache(tmp_path / "projects.jsonl")
    cache.path.write_text(f"{header}\n", encoding="utf-8")

    assert cache.load() is None


def test_cache_rejects_older_publication_without_overwriting_newer(tmp_path: Path) -> None:
    cache = ProjectCache(tmp_path / "projects.jsonl")
    newer = project(tmp_path / "newer", "newer")
    older = project(tmp_path / "older", "older")
    assert cache.save((newer,), started_at_ns=200)

    assert cache.save((older,), started_at_ns=100) is False
    assert cache.load() == [newer]

    assert cache.save((older,), started_at_ns=300) is True
    assert cache.load() == [older]


def test_cache_replaces_corrupt_newer_snapshot(tmp_path: Path) -> None:
    cache = ProjectCache(tmp_path / "projects.jsonl")
    item = project(tmp_path / "repo")
    cache.path.write_text(
        f'{{"version":{CACHE_SCHEMA_VERSION},"refresh_started_ns":200}}\n{{broken\n',
        encoding="utf-8",
    )

    assert cache.save((item,), started_at_ns=100) is True
    assert cache.load() == [item]


def test_exact_at_both_ends_and_complete_miss(tmp_path: Path) -> None:
    cache = ProjectCache(tmp_path / "projects.jsonl")
    first = project(tmp_path / "first", "first")
    last = project(tmp_path / "last", "last")
    cache.save((first, project(tmp_path / "middle", "middle"), last))

    assert cache.find_exact("FIRST") == [first]
    assert cache.find_exact("LAST") == [last]
    assert cache.find_exact("absent") == []


def test_cache_missing_or_corrupt(tmp_path: Path) -> None:
    cache = ProjectCache(tmp_path / "projects.jsonl")

    assert cache.load() is None
    assert cache.find_exact("repo") is None
    assert cache.find_prefix("re") is None

    cache.path.parent.mkdir(parents=True, exist_ok=True)
    cache.path.write_text("{broken", encoding="utf-8")

    assert cache.load() is None
    assert cache.find_exact("repo") is None
    assert cache.find_prefix("re") is None


@pytest.mark.parametrize(
    "raw",
    [
        "not-json\n",
        "1\n",
        '{"name":"x","path":1,"display":"/x","source":"manual","discoveries":[]}\n',
        '{"name":"x","path":"/x","display":1,"source":"manual","discoveries":[]}\n',
        '{"name":"x","path":"/x","display":"/x","source":"bad","discoveries":[]}\n',
        '{"name":"x","path":"/x","display":"/x","source":"manual"}\n',
        '{"name":"x","path":"/x","display":"/x","source":"manual","discoveries":[1]}\n',
        (
            '{"name":"x","path":"/x","display":"/x","source":"discovered",'
            '"discoveries":[{"root":1,"display":"/x"}]}\n'
        ),
        (
            '{"name":"x","path":"/x","display":"/x","source":"discovered",'
            '"discoveries":[{"root":"/root","display":1}]}\n'
        ),
    ],
)
def test_cache_rejects_invalid_rows(tmp_path: Path, raw: str) -> None:
    cache = ProjectCache(tmp_path / "projects.jsonl")
    cache.path.write_text(
        f"{CACHE_HEADER}\n{raw}",
        encoding="utf-8",
    )

    assert cache.load() is None


def test_cache_prefix_does_not_return_partial_data_from_corrupt_cache(tmp_path: Path) -> None:
    cache = ProjectCache(tmp_path / "projects.jsonl")
    cache.path.write_text(
        f"{CACHE_HEADER}\n"
        '{"name":"repo","path":"/repo","display":"/repo","source":"manual","discoveries":[]}\n'
        "{broken\n",
        encoding="utf-8",
    )

    assert cache.find_prefix("re") is None


def test_cache_ignores_invalid_utf8(tmp_path: Path) -> None:
    cache = ProjectCache(tmp_path / "projects.jsonl")
    cache.path.write_bytes(b"\xff")

    assert cache.load() is None
