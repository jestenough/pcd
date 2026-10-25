from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

import pcd_cli.picker as picker_module
from pcd_cli.models import Project, ProjectSource
from pcd_cli.picker import ProjectPicker
from pcd_cli.search import rank_matches

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    import pytest

    from pcd_cli.models import ProjectUsage


def project(name: str) -> Project:
    path = Path("/tmp") / name
    return Project(name, path, path, ProjectSource.DISCOVERED)


def test_down_and_enter() -> None:
    projects = (project("alpha"), project("beta"))

    with create_pipe_input() as pipe:
        pipe.send_text("\x1b[B\r")
        selected = ProjectPicker(projects, {}, input_stream=pipe, output=DummyOutput()).run()

    assert selected == projects[1]


def test_up_wraps() -> None:
    projects = (project("alpha"), project("beta"))

    with create_pipe_input() as pipe:
        pipe.send_text("\x1b[A\r")
        selected = ProjectPicker(projects, {}, input_stream=pipe, output=DummyOutput()).run()

    assert selected == projects[1]


def test_typing_filters() -> None:
    projects = (project("alpha"), project("beta"))

    with create_pipe_input() as pipe:
        pipe.send_text("bet\r")
        selected = ProjectPicker(projects, {}, input_stream=pipe, output=DummyOutput()).run()

    assert selected == projects[1]


def test_pre_ranked_query_is_not_ranked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    projects = (project("beta"), project("alpha"))
    rankings = 0

    def rank(
        items: Iterable[Project], query: str, usage: Mapping[Path, ProjectUsage]
    ) -> list[Project]:
        nonlocal rankings
        rankings += 1
        return rank_matches(items, query, usage)

    monkeypatch.setattr(picker_module, "rank_matches", rank)
    with create_pipe_input() as pipe:
        pipe.send_text("\r")
        selected = ProjectPicker(
            projects, {}, query="a", ranked=True, input_stream=pipe, output=DummyOutput()
        ).run()

    assert selected == projects[0]
    assert rankings == 0


def test_pre_ranked_picker_refilters_and_restores_initial_order() -> None:
    projects = (project("beta"), project("alpha"))
    picker = ProjectPicker(projects, {}, query="a", ranked=True)
    picker.matches = list(projects)

    picker._filter(Buffer(document=Document("al")))
    assert picker.matches == [projects[1]]
    picker._filter(Buffer(document=Document("")))
    assert picker.matches == [projects[1], projects[0]]
    picker._filter(Buffer(document=Document("a")))
    assert picker.matches == list(projects)
    assert picker.selected_index == 1


def test_escape_returns_none() -> None:
    with create_pipe_input() as pipe:
        pipe.send_text("\x1b")
        selected = ProjectPicker(
            (project("alpha"),),
            {},
            input_stream=pipe,
            output=DummyOutput(),
        ).run()

    assert selected is None


def test_empty_can_cancel() -> None:
    with create_pipe_input() as pipe:
        pipe.send_text("\x1b")
        selected = ProjectPicker((), {}, input_stream=pipe, output=DummyOutput()).run()

    assert selected is None


def test_run_creates_terminal_output(monkeypatch: pytest.MonkeyPatch) -> None:
    projects = (project("alpha"), project("beta"))
    output = DummyOutput()
    monkeypatch.setattr(picker_module, "terminal_output", lambda: nullcontext(output))

    with create_pipe_input() as pipe:
        pipe.send_text("\r")
        selected = ProjectPicker(projects, {}, input_stream=pipe).run()

    assert selected == projects[0]


def test_render_paginates_long_list() -> None:
    projects = tuple(project(f"project-{index}") for index in range(20))
    picker = ProjectPicker(projects, {})
    picker.matches = list(projects)
    picker.selected_index = 15

    lines = picker._render()

    assert any("16/20" in fragment[1] for fragment in lines)


def test_render_uses_first_full_page() -> None:
    projects = tuple(project(f"project-{index}") for index in range(20))
    picker = ProjectPicker(projects, {})
    picker.matches = list(projects)
    picker.selected_index = 2

    output = "".join(fragment[1] for fragment in picker._render())

    assert "project-0" in output
    assert "project-11" in output
    assert "project-12" not in output


def test_filter_without_running_application() -> None:
    projects = (project("alpha"), project("beta"))
    picker = ProjectPicker(projects, {})
    picker.matches = list(projects)

    picker._filter(Buffer(document=Document("beta")))

    assert picker.matches == [projects[1]]
    assert picker.selected_index == 0


def test_render_empty_results() -> None:
    picker = ProjectPicker((), {})

    assert "No matching projects" in picker._render()[0][1]
