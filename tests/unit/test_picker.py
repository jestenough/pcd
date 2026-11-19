from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

import pcd_cli.picker as picker_module
from pcd_cli.lookup import lookup_snapshot, LookupMode, LookupRequest, LookupResult, MemorySnapshot
from pcd_cli.models import Project, ProjectSource
from pcd_cli.picker import ProjectPicker

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest
    from prompt_toolkit.input.base import Input
    from prompt_toolkit.output import Output


def project(name: str) -> Project:
    path = Path("/tmp") / name
    return Project(name, path, path, ProjectSource.DISCOVERED)


def make_picker(
    projects: Sequence[Project],
    *,
    query: str = "",
    input_stream: Input | None = None,
    output: Output | None = None,
) -> ProjectPicker:
    snapshot = MemorySnapshot(projects)

    def resolve(request: LookupRequest) -> LookupResult:
        return lookup_snapshot(snapshot, request)

    return ProjectPicker(
        resolve(LookupRequest(query)), resolve, query, input_stream=input_stream, output=output
    )


def test_down_and_enter() -> None:
    projects = (project("alpha"), project("beta"))

    with create_pipe_input() as pipe:
        pipe.send_text("\x1b[B\r")
        selected = make_picker(projects, input_stream=pipe, output=DummyOutput()).run()

    assert selected == projects[1]


def test_up_wraps() -> None:
    projects = (project("alpha"), project("beta"))

    with create_pipe_input() as pipe:
        pipe.send_text("\x1b[A\r")
        selected = make_picker(projects, input_stream=pipe, output=DummyOutput()).run()

    assert selected == projects[1]


def test_typing_filters() -> None:
    projects = (project("alpha"), project("beta"))

    with create_pipe_input() as pipe:
        pipe.send_text("bet\r")
        selected = make_picker(projects, input_stream=pipe, output=DummyOutput()).run()

    assert selected == projects[1]


def test_initial_window_is_not_resolved_again() -> None:
    projects = [project("beta"), project("alpha")]

    def fail(_request: LookupRequest) -> LookupResult:
        raise AssertionError("initial window must be reused")

    with create_pipe_input() as pipe:
        pipe.send_text("\r")
        selected = ProjectPicker(
            LookupResult(projects, 2),
            fail,
            "a",
            input_stream=pipe,
            output=DummyOutput(),
        ).run()
    assert selected == projects[0]


def test_edited_query_searches_outside_initial_matches() -> None:
    projects = (project("alpha"), project("alpine"), project("beta"))
    picker = make_picker(projects, query="al")
    assert picker.matches == list(projects[:2])
    picker._filter(Buffer(document=Document("beta")))
    assert picker.matches == [projects[2]]
    picker._filter(Buffer(document=Document("missing")))
    assert picker.matches == []
    picker._filter(Buffer(document=Document("")))
    assert picker.matches == list(projects)
    picker._filter(Buffer(document=Document("al")))
    assert picker.matches == list(projects[:2])


def test_escape_returns_none() -> None:
    with create_pipe_input() as pipe:
        pipe.send_text("\x1b")
        selected = make_picker(
            (project("alpha"),),
            input_stream=pipe,
            output=DummyOutput(),
        ).run()

    assert selected is None


def test_empty_can_cancel() -> None:
    with create_pipe_input() as pipe:
        pipe.send_text("\x1b")
        selected = make_picker((), input_stream=pipe, output=DummyOutput()).run()

    assert selected is None


def test_run_creates_terminal_output(monkeypatch: pytest.MonkeyPatch) -> None:
    projects = (project("alpha"), project("beta"))
    output = DummyOutput()
    monkeypatch.setattr(picker_module, "terminal_output", lambda: nullcontext(output))

    with create_pipe_input() as pipe:
        pipe.send_text("\r")
        selected = make_picker(projects, input_stream=pipe).run()

    assert selected == projects[0]


def test_render_paginates_long_list() -> None:
    projects = tuple(project(f"project-{index:02d}") for index in range(20))
    picker = make_picker(projects)
    picker.matches = list(projects)
    picker.selected_index = 15

    lines = picker._render()

    assert any("16/20" in fragment[1] for fragment in lines)


def test_render_uses_first_full_page() -> None:
    projects = tuple(project(f"project-{index:02d}") for index in range(20))
    picker = make_picker(projects)
    picker.matches = list(projects)
    picker.selected_index = 2

    output = "".join(fragment[1] for fragment in picker._render())

    assert "project-00" in output
    assert "project-11" in output
    assert "project-12" not in output


def test_filter_without_running_application() -> None:
    projects = (project("alpha"), project("beta"))
    picker = make_picker(projects)
    picker.matches = list(projects)

    picker._filter(Buffer(document=Document("beta")))

    assert picker.matches == [projects[1]]
    assert picker.selected_index == 0


def test_render_empty_results() -> None:
    picker = make_picker(())

    assert "No matching projects" in picker._render()[0][1]


def test_render_escapes_terminal_controls() -> None:
    item = project("unsafe\x1b]52;c;payload\x07\r\n")
    picker = make_picker((item,))
    picker.matches = [item]
    output = "".join(fragment[1] for fragment in picker._render())
    assert "\x1b" not in output
    assert "\x07" not in output
    assert r"unsafe\x1b]52;c;payload\x07\r\n" in output


def test_duplicate_windows_are_bounded_and_all_reachable() -> None:
    projects = tuple(
        Project("same", Path(f"/tmp/{i:03d}"), Path(f"/tmp/{i:03d}"), ProjectSource.MANUAL)
        for i in range(235)
    )
    with create_pipe_input() as pipe:
        pipe.send_text("\x1b[B" * 234 + "\r")
        picker = make_picker(projects, query="same", input_stream=pipe, output=DummyOutput())
        selected = picker.run()
    assert selected == projects[-1]
    assert len(picker.matches) <= 100
    assert "235/235" in "".join(fragment[1] for fragment in picker._render())


def test_previous_window_and_wrap() -> None:
    projects = tuple(project(f"project-{i:03d}") for i in range(235))
    with create_pipe_input() as pipe:
        pipe.send_text("\x1b[A" * 136 + "\r")
        picker = make_picker(projects, input_stream=pipe, output=DummyOutput())
        selected = picker.run()
    assert selected == projects[99]
    assert len(picker.matches) <= 100


def test_down_wraps_last_window() -> None:
    projects = tuple(project(f"project-{i:03d}") for i in range(101))
    with create_pipe_input() as pipe:
        pipe.send_text("\x1b[B" * 101 + "\r")
        selected = make_picker(projects, input_stream=pipe, output=DummyOutput()).run()
    assert selected == projects[0]


def test_generation_change_resets_window() -> None:
    original = MemorySnapshot(tuple(project(f"a-{i:03d}") for i in range(105)), 1)
    updated = MemorySnapshot((project("new"),), 2)
    with create_pipe_input() as pipe:
        pipe.send_text("\x1b[A\r")
        picker = ProjectPicker(
            lookup_snapshot(original, LookupRequest("")),
            lambda request: lookup_snapshot(updated, request),
            input_stream=pipe,
            output=DummyOutput(),
        )
        assert picker.run() == updated.projects[0]
    assert picker.result.generation == 2


def test_empty_arrow_navigation_and_enter() -> None:
    with create_pipe_input() as pipe:
        pipe.send_text("\x1b[A\x1b[B\r")
        assert make_picker((), input_stream=pipe, output=DummyOutput()).run() is None


def test_clearing_exact_picker_query_browses_catalog() -> None:
    picker = make_picker((project("alpha"), project("beta")), query="alpha")
    picker.mode = LookupMode.EXACT
    picker._filter(Buffer(document=Document("bet")))
    assert picker.matches == []
    picker._filter(Buffer(document=Document("")))
    assert [item.name for item in picker.matches] == ["alpha", "beta"]
