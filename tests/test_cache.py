from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pcd_cli.cache import ProjectCache
from pcd_cli.filesystem import canonical_path
from pcd_cli.models import Project, ProjectSource

if TYPE_CHECKING:
    from pathlib import Path


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
        '{"name":"x","path":1,"display":"/x","source":"manual"}\n',
        '{"name":"x","path":"/x","display":1,"source":"manual"}\n',
        '{"name":"x","path":"/x","display":"/x","source":"bad"}\n',
    ],
)
def test_cache_rejects_invalid_rows(tmp_path: Path, raw: str) -> None:
    cache = ProjectCache(tmp_path / "projects.jsonl")
    cache.path.write_text(raw, encoding="utf-8")

    assert cache.load() is None


def test_cache_prefix_does_not_return_partial_data_from_corrupt_cache(tmp_path: Path) -> None:
    cache = ProjectCache(tmp_path / "projects.jsonl")
    cache.path.write_text(
        '{"name":"repo","path":"/repo","display":"/repo","source":"manual"}\n{broken\n',
        encoding="utf-8",
    )

    assert cache.find_prefix("re") is None
