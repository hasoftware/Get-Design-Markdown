"""Shared Rich consoles and small message helpers."""

from __future__ import annotations

from rich.console import Console

from get_design_markdown.errors import GdmError

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)


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
