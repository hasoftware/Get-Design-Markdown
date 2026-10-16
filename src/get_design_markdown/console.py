"""Shared Rich consoles, small message helpers and the structured message sink.

Messages are plain text, not Rich markup: page names, URLs, CSS and page text can contain square
brackets, so every helper escapes the message it prints.

Message sink (used by the local web UI, `gdm ui`):

- `use_sink(sink)` installs a callback for the current context. While it is active,
  print_step / print_info / print_warning / print_success / print_error and the callback yielded
  by progress_status() send a `ConsoleEvent` to the sink *instead of* printing, and
  progress_status() does not start the Rich spinner.
- Without a sink the CLI output is exactly what it always was (tests/test_console.py).
- A sink may raise; the exception propagates out of the helper into the stage that called it.
  That is how a UI job is cancelled: its sink raises JobCancelledError on the next event and the
  `async with` blocks of the collector close Playwright on the way out.
- The sink lives in a ContextVar. Context variables are copied into asyncio tasks (asyncio.run
  inside the job thread sees the sink) but NOT into other threads: a worker thread that runs a
  pipeline must enter `use_sink(...)` itself, inside the thread.
- Only these helpers are forwarded. Direct `console.print(...)` calls (the Rich summary tables of
  the analyzer report, the "Kết quả" table of `gdm get`, the synthesizer's issue list) still go
  to the console, i.e. to stdout (the log file in the windowed exe).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from rich.console import Console
from rich.markup import escape

from get_design_markdown.errors import GdmError

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

#: Callback a long-running stage uses to report what it is doing (Vietnamese text).
ProgressCallback = Callable[[str], None]

#: What a ConsoleEvent reports; one kind per helper (progress = progress_status()).
EventKind = Literal["step", "info", "warning", "success", "error", "progress"]
EVENT_KINDS: tuple[EventKind, ...] = ("step", "info", "warning", "success", "error", "progress")


@dataclass(frozen=True)
class ConsoleEvent:
    """One user-facing message, as plain Vietnamese text (never Rich markup).

    - step:     print_step(index, total, message) -> step=index, total=total.
    - info / warning / success: the message of the matching print_* helper.
    - error:    print_error(error) -> message=error.message, hint=error.hint.
    - progress: progress_status(title) sends `title` when it starts, then
                "<title> — <detail>" for every update (the text the spinner would show).
    """

    kind: EventKind
    message: str
    hint: str | None = None
    #: Pipeline step number and step count (kind "step" only).
    step: int | None = None
    total: int | None = None


#: Receives every ConsoleEvent while installed with use_sink(); may raise to abort the stage.
MessageSink = Callable[[ConsoleEvent], None]

_active_sink: ContextVar[MessageSink | None] = ContextVar("gdm_message_sink", default=None)


def current_sink() -> MessageSink | None:
    """The sink installed in this context (None: messages are printed)."""
    return _active_sink.get()


@contextmanager
def use_sink(sink: MessageSink) -> Iterator[MessageSink]:
    """Send the helpers' messages to `sink` until the block exits (nesting is allowed; the
    previous sink, or printing, comes back on exit). Enter it inside the thread that runs the
    stages: context variables do not cross threads."""
    token = _active_sink.set(sink)
    try:
        yield sink
    finally:
        _active_sink.reset(token)


def _emit(event: ConsoleEvent) -> bool:
    """Send `event` to the active sink; False when there is none (the caller prints)."""
    sink = _active_sink.get()
    if sink is None:
        return False
    sink(event)
    return True


def print_error(error: GdmError) -> None:
    if _emit(ConsoleEvent("error", error.message, hint=error.hint)):
        return
    err_console.print(f"[bold red]Lỗi:[/] {escape(error.message)}")
    if error.hint:
        err_console.print(f"[dim]Gợi ý: {escape(error.hint)}[/]")


def print_warning(message: str) -> None:
    if _emit(ConsoleEvent("warning", message)):
        return
    console.print(f"[bold yellow]Cảnh báo:[/] {escape(message)}")


def print_success(message: str) -> None:
    if _emit(ConsoleEvent("success", message)):
        return
    console.print(f"[bold green]✓[/] {escape(message)}")


def print_info(message: str) -> None:
    if _emit(ConsoleEvent("info", message)):
        return
    console.print(f"[cyan]•[/] {escape(message)}")


def print_step(index: int, total: int, message: str) -> None:
    """Pipeline step header, e.g. "[2/4] Phân tích…"."""
    if _emit(ConsoleEvent("step", message, step=index, total=total)):
        return
    console.print(f"\n[bold cyan][{index}/{total}][/] [bold]{escape(message)}[/]")


def silent_progress(message: str) -> None:
    """A ProgressCallback that ignores messages (tests, library use)."""


@contextmanager
def progress_status(title: str) -> Iterator[ProgressCallback]:
    """Spinner with a title; the yielded callback updates the detail text.

    With a sink installed there is no spinner: the title and every update become "progress"
    events of the sink that was active when the block was entered.
    """
    sink = _active_sink.get()
    if sink is not None:
        sink(ConsoleEvent("progress", title))

        def forward(message: str) -> None:
            sink(ConsoleEvent("progress", f"{title} — {message}"))

        yield forward
        return

    with console.status(f"[bold]{escape(title)}[/]") as status:

        def update(message: str) -> None:
            status.update(f"[bold]{escape(title)}[/] — {escape(message)}")

        yield update
