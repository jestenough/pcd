from pathlib import Path

import pytest

from pcd_cli.models import Project, ProjectSource, ProjectUsage
from pcd_cli.search import _score, rank_matches, rank_recent


def project(name: str) -> Project:
    path = Path("/tmp") / name
    return Project(name, path, path, ProjectSource.DISCOVERED)


def test_match_priority() -> None:
    projects = (
        project("old-medcab"),
        project("medical"),
        project("medcab-api"),
        project("medcab"),
    )

    assert [item.name for item in rank_matches(projects, "medcab")] == [
        "medcab",
        "medcab-api",
        "old-medcab",
    ]


def test_fuzzy_subsequence() -> None:
    item = project("medcab")

    assert rank_matches((item,), "mdc") == [item]


def test_fuzzy_miss() -> None:
    assert rank_matches((project("medcab"),), "xyz") == []


@pytest.mark.parametrize(
    "query,name,score",
    [
        ("ab", "a-b", (3, 17)),
        ("ab", "a__b", (3, 15)),
        ("ab", "axxab", (2, 17)),
        ("aa", "a-a", (3, 17)),
        ("abc", "a-x-ab-c", (3, 15)),
        ("aaa", "a-a", None),
    ],
)
def test_fuzzy_scoring_keeps_boundaries_gaps_and_greedy_order(
    query: str, name: str, score: tuple[int, int] | None
) -> None:
    assert _score(query, name) == score


def test_search_casefolds_unicode() -> None:
    item = project("Straße")
    assert rank_matches((item,), "STRASSE") == [item]


def test_history_breaks_equal_text_score() -> None:
    first = project("app-one")
    second = project("app-two")
    usage = {second.path: ProjectUsage(used_at=20.0, count=2)}

    assert rank_matches((first, second), "app", usage)[0] == second


def test_recent_order() -> None:
    first = project("first")
    second = project("second")
    third = project("third")
    usage = {
        first.path: ProjectUsage(used_at=10.0, count=10),
        second.path: ProjectUsage(used_at=20.0, count=1),
    }

    assert rank_recent((third, first, second), usage) == [second, first, third]


def test_recent_count_breaks_equal_time() -> None:
    first = project("first")
    second = project("second")
    usage = {
        first.path: ProjectUsage(used_at=10.0, count=1),
        second.path: ProjectUsage(used_at=10.0, count=2),
    }

    assert rank_recent((first, second), usage) == [second, first]
