from __future__ import annotations

import asyncio
import threading

import pytest
from rich.console import Console

from get_design_markdown import console as console_module
from get_design_markdown import pipeline
from get_design_markdown.errors import CollectError, GdmError, JobCancelledError
from tests.fixture_data import load_raw_sample


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> Console:
    recorder = Console(record=True, width=200, highlight=False)
    monkeypatch.setattr(console_module, "console", recorder)
    monkeypatch.setattr(console_module, "err_console", recorder)
    return recorder


def test_helpers_print_square_brackets_literally(recorded: Console) -> None:
    console_module.print_warning("[home] Phát hiện [bold]lớp phủ[/bold]")
    console_module.print_info("Selector a[href] [link=x]")
    console_module.print_success("Đã ghi C:\\out\\[site]\\raw.json")
    console_module.print_step(2, 4, "Phân tích [beta]")
    console_module.print_error(CollectError("Lỗi tại [main]", hint="Thử lại [sau]"))
    text = recorded.export_text()
    for literal in (
        "[home] Phát hiện [bold]lớp phủ[/bold]",
        "Selector a[href] [link=x]",
        "[site]",
        "[2/4]",
        "Phân tích [beta]",
        "Lỗi tại [main]",
        "Thử lại [sau]",
    ):
        assert literal in text


def test_page_warnings_keep_the_page_name(recorded: Console) -> None:
    raw = load_raw_sample()
    page = raw.pages[0].model_copy(update={"warnings": ["Phát hiện lớp phủ cookie."]})
    raw = raw.model_copy(update={"pages": [page], "warnings": []})
    pipeline.summarize_capture(raw)
    assert f"[{page.name}] Phát hiện lớp phủ cookie." in recorded.export_text()


# ─── Message sink ────────────────────────────────────────────────────────────


def _emit_everything() -> None:
    console_module.print_step(2, 4, "Phân tích [beta]")
    console_module.print_info("Selector a[href]")
    console_module.print_warning("[home] lớp phủ")
    console_module.print_success("Đã ghi raw.json")
    console_module.print_error(CollectError("Lỗi tại [main]", hint="Thử lại [sau]"))
    console_module.print_error(CollectError("Không có gợi ý"))


def test_output_without_a_sink_is_unchanged(recorded: Console) -> None:
    """No sink: the helpers print exactly the markup they printed before the sink existed."""
    _emit_everything()
    reference = Console(record=True, width=200, highlight=False)
    reference.print("\n[bold cyan][2/4][/] [bold]Phân tích \\[beta][/]")
    reference.print("[cyan]•[/] Selector a\\[href]")
    reference.print("[bold yellow]Cảnh báo:[/] \\[home] lớp phủ")
    reference.print("[bold green]✓[/] Đã ghi raw.json")
    reference.print("[bold red]Lỗi:[/] Lỗi tại \\[main]")
    reference.print("[dim]Gợi ý: Thử lại \\[sau][/]")
    reference.print("[bold red]Lỗi:[/] Không có gợi ý")
    assert recorded.export_text(styles=True) == reference.export_text(styles=True)


def test_sink_receives_structured_events_instead_of_printing(recorded: Console) -> None:
    events: list[console_module.ConsoleEvent] = []
    with console_module.use_sink(events.append):
        _emit_everything()
    assert recorded.export_text() == ""
    assert events == [
        console_module.ConsoleEvent("step", "Phân tích [beta]", step=2, total=4),
        console_module.ConsoleEvent("info", "Selector a[href]"),
        console_module.ConsoleEvent("warning", "[home] lớp phủ"),
        console_module.ConsoleEvent("success", "Đã ghi raw.json"),
        console_module.ConsoleEvent("error", "Lỗi tại [main]", hint="Thử lại [sau]"),
        console_module.ConsoleEvent("error", "Không có gợi ý"),
    ]
    assert {event.kind for event in events} <= set(console_module.EVENT_KINDS)
    # Leaving the block restores printing.
    console_module.print_info("sau khối")
    assert "• sau khối" in recorded.export_text()
    assert console_module.current_sink() is None


class _NoSpinnerConsole:
    def status(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("progress_status started a spinner while a sink was active")

    def print(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("printed while a sink was active")


def test_progress_status_with_a_sink_sends_progress_events_without_spinner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(console_module, "console", _NoSpinnerConsole())
    events: list[console_module.ConsoleEvent] = []
    with (
        console_module.use_sink(events.append),
        console_module.progress_status("Đang thu thập") as progress,
    ):
        progress("Mở trình duyệt…")
        progress("Trang [home]")
    assert [(event.kind, event.message) for event in events] == [
        ("progress", "Đang thu thập"),
        ("progress", "Đang thu thập — Mở trình duyệt…"),
        ("progress", "Đang thu thập — Trang [home]"),
    ]


class _Status:
    def __init__(self, updates: list[str]) -> None:
        self.updates = updates

    def __enter__(self) -> _Status:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def update(self, text: str) -> None:
        self.updates.append(text)


class _SpinnerConsole:
    def __init__(self) -> None:
        self.titles: list[str] = []
        self.updates: list[str] = []

    def status(self, title: str) -> _Status:
        self.titles.append(title)
        return _Status(self.updates)


def test_progress_status_without_a_sink_uses_the_spinner(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _SpinnerConsole()
    monkeypatch.setattr(console_module, "console", fake)
    with console_module.progress_status("Đang [x]") as progress:
        progress("chi tiết [y]")
    assert fake.titles == ["[bold]Đang \\[x][/]"]
    assert fake.updates == ["[bold]Đang \\[x][/] — chi tiết \\[y]"]


def _cancel(event: console_module.ConsoleEvent) -> None:
    raise JobCancelledError()


def _enter_progress() -> None:
    with console_module.progress_status("Đang chạy"):
        pass  # pragma: no cover - the title event already raises


@pytest.mark.parametrize(
    "call",
    [
        lambda: console_module.print_step(1, 4, "x"),
        lambda: console_module.print_info("x"),
        lambda: console_module.print_warning("x"),
        lambda: console_module.print_success("x"),
        lambda: console_module.print_error(CollectError("x")),
        _enter_progress,
    ],
)
def test_a_raising_sink_propagates_out_of_every_helper(recorded: Console, call: object) -> None:
    assert callable(call)
    with console_module.use_sink(_cancel), pytest.raises(JobCancelledError):
        call()
    assert console_module.current_sink() is None
    assert recorded.export_text() == ""


def test_progress_callback_raises_from_inside_a_stage() -> None:
    calls: list[str] = []

    def sink(event: console_module.ConsoleEvent) -> None:
        calls.append(event.message)
        if len(calls) > 1:
            raise JobCancelledError()

    with (
        console_module.use_sink(sink),
        console_module.progress_status("Đang chạy") as progress,
        pytest.raises(JobCancelledError) as info,
    ):
        progress("bước 1")
    assert calls == ["Đang chạy", "Đang chạy — bước 1"]
    assert info.value.message and info.value.hint


def test_sinks_nest_and_restore() -> None:
    outer: list[str] = []
    inner: list[str] = []
    with console_module.use_sink(lambda event: outer.append(event.message)):
        console_module.print_info("a")
        with console_module.use_sink(lambda event: inner.append(event.message)):
            console_module.print_info("b")
        console_module.print_info("c")
    assert outer == ["a", "c"]
    assert inner == ["b"]


def test_sink_reaches_asyncio_tasks_but_not_other_threads() -> None:
    """asyncio.run copies the context (the collector runs there); a new thread does not, so a
    job thread must enter use_sink itself."""
    events: list[str] = []
    seen_in_thread: list[object] = []

    async def stage() -> None:
        await asyncio.sleep(0)
        console_module.print_info("trong asyncio")

    def worker() -> None:
        seen_in_thread.append(console_module.current_sink())

    with console_module.use_sink(lambda event: events.append(event.message)):
        asyncio.run(stage())
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
    assert events == ["trong asyncio"]
    assert seen_in_thread == [None]


def test_job_cancelled_error_is_a_vietnamese_gdm_error() -> None:
    error = JobCancelledError()
    assert isinstance(error, GdmError)
    assert "hủy" in error.message
    assert error.hint
