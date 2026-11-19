from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import BeforeInput

from pcd_cli.display import terminal_text
from pcd_cli.filesystem import format_path
from pcd_cli.lookup import LookupMode, LookupRequest
from pcd_cli.terminal import terminal_output

if TYPE_CHECKING:
    from collections.abc import Callable

    from prompt_toolkit.formatted_text import StyleAndTextTuples
    from prompt_toolkit.input.base import Input
    from prompt_toolkit.key_binding import KeyPressEvent
    from prompt_toolkit.output import Output

    from pcd_cli.lookup import LookupResult
    from pcd_cli.models import Project


PAGE_SIZE = 12


@dataclass(slots=True)
class ProjectPicker:
    """Project-specific interactive picker built on prompt-toolkit primitives."""

    result: LookupResult
    resolve: Callable[[LookupRequest], LookupResult]
    query: str = ""
    mode: LookupMode = LookupMode.RESOLVE
    input_stream: Input | None = None
    output: Output | None = None
    matches: list[Project] = field(default_factory=list, init=False)
    selected_index: int = field(default=0, init=False)
    application: Application[Project | None] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.matches = self.result.matches

    def run(self) -> Project | None:
        self.selected_index = 0
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
        output_context = nullcontext(self.output) if self.output is not None else terminal_output()
        with output_context as output:
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

    def _filter(self, buffer: Buffer) -> None:
        selected_path = self.matches[self.selected_index].path if self.matches else None
        self.query = buffer.text
        self._resolve(LookupRequest(self.query, self._lookup_mode))
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
        target = self.selected_index + step
        if self.matches and not 0 <= target < len(self.matches):
            if step > 0:
                after = (
                    self.result.last_rank
                    if self.result.offset + len(self.matches) < self.result.total
                    else None
                )
                request = LookupRequest(self.query, self._lookup_mode, after=after)
            elif self.result.offset:
                request = LookupRequest(
                    self.query, self._lookup_mode, before=self.result.first_rank
                )
            else:
                request = LookupRequest(self.query, self._lookup_mode, last=True)
            generation = self.result.generation
            self._resolve(request)
            if self.result.generation != generation:
                self._resolve(LookupRequest(self.query, self._lookup_mode))
                target = 0
            else:
                target = 0 if step > 0 else len(self.matches) - 1
        self.selected_index = max(0, target) if self.matches else 0
        event.app.invalidate()

    @property
    def _lookup_mode(self) -> LookupMode:
        return self.mode if self.query else LookupMode.RESOLVE

    def _resolve(self, request: LookupRequest) -> None:
        self.result = self.resolve(request)
        self.matches = self.result.matches

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
                (
                    style,
                    f"{marker}{terminal_text(project.name):<24} {format_path(project.display_path)}\n",
                )
            )

        if self.result.total > PAGE_SIZE:
            lines.append(
                ("dim", f"\n{self.result.offset + self.selected_index + 1}/{self.result.total}\n")
            )

        return lines
