from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from pcd_cli.models import Project, ProjectUsage

ProjectRank = tuple[int, int, float, float, int, str, str]

EXACT_WEIGHT = 16
PREFIX_WEIGHT = 12
SUBSTRING_WEIGHT = 10
MATCH_POINTS = 4
BOUNDARY_BONUS = 5
ADJACENT_BONUS = 3
MAX_GAP_PENALTY = 4


def rank_matches(
    projects: Iterable[Project],
    query: str,
    usage: Mapping[Path, ProjectUsage] | None = None,
) -> list[Project]:
    needle = query.casefold()
    words = needle.split()
    matches: list[tuple[ProjectRank, Project]] = []

    for project in projects:
        name = project.name.casefold()
        score = score_path(needle, name, str(project.path), words)
        if score is None:
            continue

        match_kind, points = score
        project_usage = None if usage is None else usage.get(project.path)
        used_at = 0.0 if project_usage is None else project_usage.used_at
        count = 0 if project_usage is None else project_usage.count
        rank: ProjectRank = (
            match_kind,
            -points,
            -(count + (0.0 if project_usage is None else project_usage.preference)),
            -used_at,
            0,
            "",
            str(project.path),
        )
        matches.append((rank, project))

    matches.sort(key=lambda item: item[0])
    return [project for _, project in matches]


def rank_recent(
    projects: Iterable[Project],
    usage: Mapping[Path, ProjectUsage],
) -> list[Project]:
    ranked_projects = list(projects)

    def usage_rank(project: Project) -> tuple[float, int, str, str]:
        project_usage = usage.get(project.path)
        return (
            0.0 if project_usage is None else -project_usage.used_at,
            0 if project_usage is None else -project_usage.count,
            "",
            str(project.path),
        )

    ranked_projects.sort(key=usage_rank)
    return ranked_projects


def _score(query: str, name: str) -> tuple[int, int] | None:
    if name == query:
        return 0, len(query) * EXACT_WEIGHT
    if name.startswith(query):
        return 1, len(query) * PREFIX_WEIGHT - (len(name) - len(query))

    position = name.find(query)
    if position >= 0:
        return 2, len(query) * SUBSTRING_WEIGHT - position

    previous = -2
    points = 0
    start = 0

    for char in query:
        index = name.find(char, start)
        if index < 0:
            return None

        points += MATCH_POINTS
        points += BOUNDARY_BONUS if index == 0 or name[index - 1] in "-_./ " else 0

        if previous >= 0:
            points += (
                ADJACENT_BONUS
                if index == previous + 1
                else -min(index - previous - 1, MAX_GAP_PENALTY)
            )

        previous = index
        start = index + 1

    penalty = max(len(name) - len(query), 0) // 2
    return 3, points - penalty


def score_path(query: str, name: str, path: str, words: list[str]) -> tuple[int, int] | None:
    """Match separate words against distinct path components, in order."""
    if query == name or len(words) < 2:
        return _score(query, name)
    components = path.replace("\\", "/").casefold().split("/")
    # Evaluate each quality tier separately so an early fuzzy ancestor cannot
    # hide a later exact component. Keep only one score per ending component.
    for kind in range(4):
        previous: list[int | None] = [None] * len(components)
        for word_index, word in enumerate(words):
            current: list[int | None] = [None] * len(components)
            best: int | None = 0 if word_index == 0 else None
            for index, component in enumerate(components):
                if word_index and index and previous[index - 1] is not None:
                    candidate = previous[index - 1]
                    if candidate is not None:
                        best = candidate if best is None else max(best, candidate)
                score = _score(word, component)
                if best is not None and score is not None and score[0] <= kind:
                    current[index] = best + score[1]
            previous = current
        endings = [
            points - 4 * (len(components) - index - 1)
            for index, points in enumerate(previous)
            if points is not None
        ]
        if endings:
            return kind, max(endings)
    return None
