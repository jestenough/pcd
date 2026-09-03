from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.output.defaults import create_output

from pcd_cli.filesystem import format_path
from pcd_cli.search import rank_matches, rank_recent

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from prompt_toolkit.formatted_text import StyleAndTextTuples
    from prompt_toolkit.input.base import Input
    from prompt_toolkit.key_binding import KeyPressEvent
    from prompt_toolkit.output import Output

    from pcd_cli.models import Project, ProjectUsage


PAGE_SIZE = 12


@dataclass(slots=True)
class ProjectPicker:
    """Project-specific interactive picker built on prompt-toolkit primitives."""

    projects: Sequence[Project]
    usage: Mapping[Path, ProjectUsage]
    query: str = ""
    input_stream: Input | None = None
    output: Output | None = None

    matches: list[Project] = field(default_factory=list, init=False)
    selected_index: int = field(default=0, init=False)
    application: Application[Project | None] | None = field(default=None, init=False)

    def run(self) -> Project | None:
        self.matches = self._matching_projects(self.query)
        buffer = Buffer(
            document=Document(self.query, cursor_position=len(self.query)),
            multiline=False,
            on_text_changed=self._filter,
        )
        bindings = KeyBindings()
        bindings.add("up")(self._move_up)
        bindings.add("down")(self._move_down)
        bindings.add("enter")(self._select)
        bindings.add("escape")(self._cancel)
        bindings.add("c-c")(self._cancel)

        search = Window(
            BufferControl(buffer=buffer, input_processors=[BeforeInput("Search: ")]),
            height=1,
        )
        results = Window(content=FormattedTextControl(self._render), dont_extend_height=True)
        output = self.output if self.output is not None else create_output(stdout=sys.stderr)
        application: Application[Project | None] = Application(
            layout=Layout(HSplit([search, results]), focused_element=search),
            key_bindings=bindings,
            full_screen=False,
            erase_when_done=True,
            input=self.input_stream,
            output=output,
        )
        self.application = application

        try:
            return application.run()
        finally:
            self.application = None

    def _matching_projects(self, query: str) -> list[Project]:
        if query:
            return rank_matches(self.projects, query, self.usage)
        return rank_recent(self.projects, self.usage)

    def _filter(self, buffer: Buffer) -> None:
        selected_path = self.matches[self.selected_index].path if self.matches else None
        self.matches = self._matching_projects(buffer.text)
        selected_index = next(
            (index for index, project in enumerate(self.matches) if project.path == selected_path),
            None,
        )
        self.selected_index = 0 if selected_index is None else selected_index

        if self.application is not None:
            self.application.invalidate()

    def _move_up(self, event: KeyPressEvent) -> None:
        self._move(event, -1)

    def _move_down(self, event: KeyPressEvent) -> None:
        self._move(event, 1)

    def _move(self, event: KeyPressEvent, step: int) -> None:
        self.selected_index = (
            (self.selected_index + step) % len(self.matches) if self.matches else 0
        )
        event.app.invalidate()

    def _select(self, event: KeyPressEvent) -> None:
        selected = self.matches[self.selected_index] if self.matches else None
        event.app.exit(result=selected)

    def _cancel(self, event: KeyPressEvent) -> None:
        event.app.exit(result=None)

    def _render(self) -> StyleAndTextTuples:
        if self.matches:
            return self._lines()

        return [("italic", "\nNo matching projects\n")]

    def _lines(self) -> StyleAndTextTuples:
        start = max(0, self.selected_index - PAGE_SIZE // 2)
        stop = min(len(self.matches), start + PAGE_SIZE)
        if stop - start < PAGE_SIZE:
            start = max(0, stop - PAGE_SIZE)

        lines: StyleAndTextTuples = [("", "\n")]
        for index, project in enumerate(self.matches[start:stop], start=start):
            selected = index == self.selected_index
            marker = "> " if selected else "  "
            style = "reverse" if selected else ""
            lines.append(
                (style, f"{marker}{project.name:<24} {format_path(project.display_path)}\n")
            )

        if len(self.matches) > PAGE_SIZE:
            lines.append(("dim", f"\n{self.selected_index + 1}/{len(self.matches)}\n"))
        return lines
