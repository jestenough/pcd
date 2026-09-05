from pathlib import Path

from pcd_cli.models import Project, ProjectSource, ProjectUsage
from pcd_cli.search import rank_matches, rank_recent


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
