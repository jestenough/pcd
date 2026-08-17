from pathlib import Path

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from pcd_cli.models import Project, ProjectSource
from pcd_cli.picker import ProjectPicker


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


def test_render_paginates_long_list() -> None:
    projects = tuple(project(f"project-{index}") for index in range(20))
    picker = ProjectPicker(projects, {})
    picker.matches = list(projects)
    picker.selected_index = 15

    lines = picker._render()

    assert any("16/20" in fragment[1] for fragment in lines)


def test_render_empty_results() -> None:
    picker = ProjectPicker((), {})

    assert "No matching projects" in picker._render()[0][1]
