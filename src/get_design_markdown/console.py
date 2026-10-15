"""Shared Rich consoles and small message helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from rich.console import Console

from get_design_markdown.errors import GdmError

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

#: Callback a long-running stage uses to report what it is doing (Vietnamese text).
ProgressCallback = Callable[[str], None]


def print_error(error: GdmError) -> None:
    err_console.print(f"[bold red]Lỗi:[/] {error.message}")
    if error.hint:
        err_console.print(f"[dim]Gợi ý: {error.hint}[/]")


def print_warning(message: str) -> None:
    console.print(f"[bold yellow]Cảnh báo:[/] {message}")


def print_success(message: str) -> None:
    console.print(f"[bold green]✓[/] {message}")


def print_info(message: str) -> None:
    console.print(f"[cyan]•[/] {message}")


def print_step(index: int, total: int, message: str) -> None:
    """Pipeline step header, e.g. "[2/4] Phân tích…"."""
    console.print(f"\n[bold cyan][{index}/{total}][/] [bold]{message}[/]")


def silent_progress(message: str) -> None:
    """A ProgressCallback that ignores messages (tests, library use)."""


@contextmanager
def progress_status(title: str) -> Iterator[ProgressCallback]:
    """Spinner with a title; the yielded callback updates the detail text."""
    with console.status(f"[bold]{title}[/]") as status:

        def update(message: str) -> None:
            status.update(f"[bold]{title}[/] — {message}")

        yield update
