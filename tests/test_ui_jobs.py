"""JobManager (ui/jobs.py): one pipeline run at a time in a worker thread, polled events."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from get_design_markdown import pipeline
from get_design_markdown.console import (
    ConsoleEvent,
    MessageSink,
    current_sink,
    print_info,
    print_step,
    print_success,
    print_warning,
    progress_status,
)
from get_design_markdown.errors import (
    BotBlockedError,
    GdmError,
    InvalidUrlError,
    JobCancelledError,
)
from get_design_markdown.storage import domain_of
from get_design_markdown.ui.jobs import (
    FINAL_SUCCESS_MESSAGE,
    JOB_ID_RE,
    MAX_EVENTS_PER_RESPONSE,
    OUTPUT_DIR_JOB_HINT,
    PIPELINE_STEPS,
    UNEXPECTED_HINT,
    JobBusyError,
    JobManager,
    JobNotFoundError,
    JobRequest,
    JobRunner,
    JobSnapshot,
    run_pipeline,
)
from get_design_markdown.ui.settings import UiSettings
from tests.conftest import FIXED_TIME

UI_LOGGER = "get_design_markdown.ui"
WAIT_S = 10.0


@pytest.fixture
def settings(tmp_path: Path) -> UiSettings:
    return UiSettings(output_dir=tmp_path / "output")


def _wait_until(condition: Callable[[], bool], timeout: float = WAIT_S) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.01)


def _finished(manager: JobManager, job_id: str) -> JobSnapshot:
    snapshot = manager.wait(job_id, timeout=WAIT_S)
    assert snapshot.finished, snapshot.status
    return snapshot


def _write_results(settings: UiSettings, url: str, names: tuple[str, ...]) -> Path:
    folder = settings.output_dir / domain_of(url)
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_text("x", encoding="utf-8")
    return folder


def pipeline_like(names: tuple[str, ...] = ("DESIGN.md", "tokens.json", "raw.json")) -> JobRunner:
    """A runner that talks only through the console helpers (never the sink argument), like
    pipeline.run_get: it works only if the job thread entered use_sink itself."""

    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        print_step(1, 4, "Thu thập dữ liệu trang (Tầng 1)")
        with progress_status("Đang thu thập") as progress:
            progress("Mở trình duyệt…")
        print_success("robots.txt cho phép truy cập /.")
        print_step(2, 4, "Phân tích thiết kế (Tầng 2)")
        print_warning("[home] Phát hiện banner cookie.")
        print_step(3, 4, "Dựng DESIGN.md (Tầng 3)")
        print_info(f"Luồng: {threading.current_thread().name}")
        print_step(4, 4, "Xuất tokens.json và preview.html")
        return _write_results(settings, request.url, names)

    return runner


class Looping:
    """A runner that reports progress until it is cancelled (or `seconds` pass)."""

    def __init__(self, seconds: float = 30.0) -> None:
        self.seconds = seconds
        self.started = threading.Event()
        self.cleaned_up = threading.Event()
        self.events_after_cancel: list[str] = []

    def __call__(self, request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        try:
            print_step(1, 4, "Thu thập dữ liệu trang (Tầng 1)")
            self.started.set()
            deadline = time.monotonic() + self.seconds
            index = 0
            with progress_status("Đang thu thập") as progress:
                while time.monotonic() < deadline:
                    index += 1
                    progress(f"chờ trang… {index}")
                    time.sleep(0.01)
            return settings.output_dir
        finally:
            # Like Playwright's `async with`: cleanup runs while the cancellation unwinds.
            self.cleaned_up.set()


@pytest.fixture
def managers() -> Iterator[list[JobManager]]:
    """Every JobManager made by a test is shut down afterwards (no thread outlives it)."""
    made: list[JobManager] = []
    yield made
    for manager in made:
        manager.shutdown(timeout=WAIT_S)


@pytest.fixture
def make(managers: list[JobManager]) -> Callable[..., JobManager]:
    def factory(runner: JobRunner, **kwargs: object) -> JobManager:
        manager = JobManager(runner, **kwargs)  # type: ignore[arg-type]
        managers.append(manager)
        return manager

    return factory


# ─── Success ─────────────────────────────────────────────────────────────────


def test_successful_job(make: Callable[..., JobManager], settings: UiSettings) -> None:
    manager = make(pipeline_like(("DESIGN.md", "raw.json", "tokens.json", "notes.txt")))
    first = manager.start(JobRequest(url="https://www.Stripe.com/pricing"), settings)
    assert JOB_ID_RE.fullmatch(first.id)
    assert first.status in ("queued", "running") and first.events == []
    assert first.site == "stripe.com" and first.url == "https://www.Stripe.com/pricing"
    assert first.settings == settings and first.total == PIPELINE_STEPS
    assert manager.busy or manager.get(first.id).finished

    done = _finished(manager, first.id)
    assert done.status == "succeeded" and done.error is None and not manager.busy
    assert done.started_at is not None and done.finished_at is not None
    assert [event.seq for event in done.events] == list(range(1, len(done.events) + 1))
    assert done.last_seq == len(done.events)
    assert [(event.step, event.total) for event in done.events if event.kind == "step"] == [
        (1, 4),
        (2, 4),
        (3, 4),
        (4, 4),
    ]
    assert [event.kind for event in done.events[:4]] == ["step", "progress", "progress", "success"]
    assert done.events[1].message == "Đang thu thập"
    assert done.events[2].message == "Đang thu thập — Mở trình duyệt…"
    assert done.events[-1].kind == "success" and done.events[-1].message == FINAL_SUCCESS_MESSAGE
    assert (done.step, done.total, done.step_message) == (4, 4, "Xuất tokens.json và preview.html")
    # Result files in the documented order, only those that exist.
    assert done.files == ["DESIGN.md", "tokens.json", "raw.json"]
    # The runner ran in the job's own daemon thread.
    assert f"Luồng: gdm-job-{first.id}" in [event.message for event in done.events]
    # The sink never leaks into the caller's thread.
    assert current_sink() is None


def test_runner_may_use_the_sink_argument(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        sink(ConsoleEvent("info", "direct", hint="h"))
        return settings.output_dir

    manager = make(runner)
    done = _finished(manager, manager.start(JobRequest(url="x.example"), settings).id)
    assert [(event.kind, event.message, event.hint) for event in done.events] == [
        ("info", "direct", "h"),
        ("success", FINAL_SUCCESS_MESSAGE, None),
    ]
    assert done.files == []


def test_clock_is_used_for_every_time(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    manager = make(pipeline_like(), clock=lambda: FIXED_TIME)
    done = _finished(manager, manager.start(JobRequest(url="x.example"), settings).id)
    assert done.created_at == done.started_at == done.finished_at == FIXED_TIME
    assert {event.at for event in done.events} == {FIXED_TIME}


def test_default_runner_is_the_pipeline() -> None:
    assert JobManager().runner is run_pipeline


# ─── Failures ────────────────────────────────────────────────────────────────


def test_gdm_error_fails_the_job_with_its_vietnamese_message(
    make: Callable[..., JobManager], settings: UiSettings, caplog: pytest.LogCaptureFixture
) -> None:
    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        print_step(1, 4, "Thu thập dữ liệu trang (Tầng 1)")
        raise BotBlockedError("Trang chặn trình duyệt tự động.", hint="Thử lại sau.")

    manager = make(runner)
    with caplog.at_level(logging.DEBUG, logger=UI_LOGGER):
        done = _finished(manager, manager.start(JobRequest(url="x.example"), settings).id)
    assert done.status == "failed" and done.files == []
    assert done.error is not None
    assert (done.error.code, done.error.message, done.error.hint) == (
        "bot_blocked",
        "Trang chặn trình duyệt tự động.",
        "Thử lại sau.",
    )
    last = done.events[-1]
    assert (last.kind, last.message, last.hint) == ("error", done.error.message, done.error.hint)
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_unexpected_exception_is_logged_but_never_shown_as_a_traceback(
    make: Callable[..., JobManager], settings: UiSettings, caplog: pytest.LogCaptureFixture
) -> None:
    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        raise RuntimeError("boom\nFile secret.py, line 3")

    manager = make(runner)
    with caplog.at_level(logging.ERROR, logger=UI_LOGGER):
        done = _finished(manager, manager.start(JobRequest(url="x.example"), settings).id)
    assert done.status == "failed"
    assert done.error is not None and done.error.code == "unexpected"
    assert done.error.message == "Lỗi không mong muốn: RuntimeError: boom"
    assert done.error.hint and "ui.log" in done.error.hint
    shown = done.model_dump_json()
    assert "Traceback" not in shown and "secret.py" not in shown
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1 and errors[0].exc_info is not None
    assert "Traceback" in caplog.text and "secret.py" in caplog.text


def test_invalid_url_is_refused_before_a_job_exists(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    manager = make(pipeline_like())
    with pytest.raises(InvalidUrlError):
        manager.start(JobRequest(url="ftp://files.example"), settings)
    with pytest.raises(InvalidUrlError):
        manager.start(JobRequest(url="no-dot"), settings)
    assert manager.current() is None and not manager.busy


def test_unknown_job(make: Callable[..., JobManager]) -> None:
    manager = make(pipeline_like())
    for call in (manager.get, manager.cancel, manager.wait):
        with pytest.raises(JobNotFoundError) as caught:
            call("0123456789ab")
        assert caught.value.job_id == "0123456789ab" and caught.value.hint


# ─── One job at a time ───────────────────────────────────────────────────────


def test_a_second_job_is_refused_while_one_runs(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    looping = Looping()
    manager = make(looping)
    running = manager.start(JobRequest(url="slow.example"), settings)
    assert looping.started.wait(WAIT_S)
    assert manager.busy and manager.active_job_id == running.id
    with pytest.raises(JobBusyError) as caught:
        manager.start(JobRequest(url="other.example"), settings)
    assert caught.value.job_id == running.id
    manager.cancel(running.id)
    _finished(manager, running.id)
    assert not manager.busy and manager.active_job_id is None
    # Free again.
    second = manager.start(JobRequest(url="other.example"), settings)
    manager.cancel(second.id)
    _finished(manager, second.id)


def test_current_job(make: Callable[..., JobManager], settings: UiSettings) -> None:
    looping = Looping()
    manager = make(looping)
    assert manager.current() is None
    first = manager.start(JobRequest(url="one.example"), settings)
    assert looping.started.wait(WAIT_S)
    current = manager.current()
    assert current is not None and current.id == first.id and current.status == "running"
    manager.cancel(first.id)
    _finished(manager, first.id)
    current = manager.current()
    assert current is not None and current.id == first.id and current.status == "cancelled"

    manager.runner = pipeline_like()
    second = manager.start(JobRequest(url="two.example"), settings)
    _finished(manager, second.id)
    current = manager.current(since=2)
    assert current is not None and current.id == second.id
    assert current.events and current.events[0].seq == 3


def test_old_jobs_are_forgotten(make: Callable[..., JobManager], settings: UiSettings) -> None:
    manager = make(pipeline_like(), max_jobs=2)
    ids = []
    for index in range(3):
        job = manager.start(JobRequest(url=f"site{index}.example"), settings)
        _finished(manager, job.id)
        ids.append(job.id)
    with pytest.raises(JobNotFoundError):
        manager.get(ids[0])
    assert manager.get(ids[1]).finished and manager.get(ids[2]).finished


# ─── Cancellation ────────────────────────────────────────────────────────────


def test_cancel_stops_the_runner_at_its_next_message(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    looping = Looping()
    manager = make(looping)
    job = manager.start(JobRequest(url="slow.example"), settings)
    assert looping.started.wait(WAIT_S)
    requested = manager.cancel(job.id)
    assert requested.cancel_requested and requested.events == []
    assert requested.status in ("running", "cancelled")
    done = _finished(manager, job.id)
    assert done.status == "cancelled" and done.cancel_requested
    assert done.error is not None and done.error.code == "cancelled"
    expected = JobCancelledError()
    assert (done.error.message, done.error.hint) == (expected.message, expected.hint)
    last = done.events[-1]
    assert (last.kind, last.message, last.hint) == ("warning", expected.message, expected.hint)
    assert looping.cleaned_up.is_set()
    # Nothing was recorded between the request and the final warning.
    assert all(event.kind != "warning" for event in done.events[:-1])
    assert done.files == []
    # Cancelling again, or a finished job, changes nothing.
    again = manager.cancel(job.id)
    assert again.status == "cancelled" and again.last_seq == done.last_seq


def test_cancel_before_the_thread_runs_never_calls_the_runner(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    calls: list[str] = []

    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        calls.append(request.url)
        return settings.output_dir

    manager = make(runner)
    with manager._lock:  # the job thread cannot start running before the cancel
        job = manager.start(JobRequest(url="x.example"), settings)
        assert job.status == "queued"
        manager.cancel(job.id)
    done = _finished(manager, job.id)
    assert done.status == "cancelled" and calls == []
    assert done.started_at is None and done.finished_at is not None
    assert [event.kind for event in done.events] == ["warning"]


def test_cancelling_a_finished_job_is_a_no_op(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    manager = make(pipeline_like())
    job = manager.start(JobRequest(url="x.example"), settings)
    done = _finished(manager, job.id)
    after = manager.cancel(job.id)
    assert after.status == "succeeded" and not after.cancel_requested and after.events == []
    assert manager.get(job.id).last_seq == done.last_seq


def test_an_error_raised_while_unwinding_a_cancel_counts_as_cancelled(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    started = threading.Event()

    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        started.set()
        try:
            while True:
                print_info("tick")
                time.sleep(0.01)
        except JobCancelledError:
            raise RuntimeError("cleanup failed") from None

    manager = make(runner)
    job = manager.start(JobRequest(url="x.example"), settings)
    assert started.wait(WAIT_S)
    manager.cancel(job.id)
    done = _finished(manager, job.id)
    assert done.status == "cancelled" and done.error is not None
    assert done.error.code == "cancelled"


def test_a_runner_that_finishes_despite_a_cancel_request_succeeds(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    release = threading.Event()
    started = threading.Event()

    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        started.set()
        release.wait(WAIT_S)  # no message: the cancel never takes effect
        return settings.output_dir

    manager = make(runner)
    job = manager.start(JobRequest(url="x.example"), settings)
    assert started.wait(WAIT_S)
    assert manager.cancel(job.id).cancel_requested
    assert manager.get(job.id).status == "running"
    release.set()
    done = _finished(manager, job.id)
    assert done.status == "succeeded" and done.cancel_requested


def test_job_cancelled_error_from_the_runner_itself(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        raise JobCancelledError("Dừng.", hint=None)

    manager = make(runner)
    done = _finished(manager, manager.start(JobRequest(url="x.example"), settings).id)
    assert done.status == "cancelled" and done.error is not None
    assert (done.error.code, done.error.message) == ("cancelled", "Dừng.")


def test_shutdown_cancels_and_joins_the_running_job(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    looping = Looping()
    manager = make(looping)
    manager.shutdown()  # nothing running: no-op
    job = manager.start(JobRequest(url="x.example"), settings)
    assert looping.started.wait(WAIT_S)
    started = time.monotonic()
    manager.shutdown(timeout=WAIT_S)
    assert time.monotonic() - started < WAIT_S
    snapshot = manager.get(job.id)
    assert snapshot.status == "cancelled"
    assert not [t for t in threading.enumerate() if t.name == f"gdm-job-{job.id}"]


def test_shutdown_gives_up_after_its_timeout(
    make: Callable[..., JobManager], settings: UiSettings, caplog: pytest.LogCaptureFixture
) -> None:
    release = threading.Event()
    started = threading.Event()

    def stuck(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        started.set()
        release.wait(WAIT_S)  # e.g. a page load that ignores the cancel until its timeout
        print_info("finally a message")
        return settings.output_dir

    manager = make(stuck)
    job = manager.start(JobRequest(url="x.example"), settings)
    assert started.wait(WAIT_S)
    with caplog.at_level(logging.WARNING, logger=UI_LOGGER):
        begin = time.monotonic()
        manager.shutdown(timeout=0.2)
        assert time.monotonic() - begin < 2
    assert job.id in caplog.text
    release.set()
    assert _finished(manager, job.id).status == "cancelled"


def test_wait_with_a_timeout_returns_the_running_job(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    looping = Looping()
    manager = make(looping)
    job = manager.start(JobRequest(url="x.example"), settings)
    assert looping.started.wait(WAIT_S)
    snapshot = manager.wait(job.id, timeout=0.05)
    assert snapshot.status == "running" and snapshot.events
    manager.cancel(job.id)
    _finished(manager, job.id)


# ─── Events: paging and caps ─────────────────────────────────────────────────


def test_since_paging(make: Callable[..., JobManager], settings: UiSettings) -> None:
    def chatty(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        for index in range(1200):
            print_info(f"message {index + 1}")
        return settings.output_dir

    manager = make(chatty)
    job = manager.start(JobRequest(url="x.example"), settings)
    done = _finished(manager, job.id)
    assert done.last_seq == 1201 and len(done.events) == 1201  # wait() returns them all

    pages = [manager.get(job.id, since) for since in (0, 500, 1000, 1201, 5000)]
    assert [len(page.events) for page in pages] == [MAX_EVENTS_PER_RESPONSE, 500, 201, 0, 0]
    assert [page.events[0].seq for page in pages[:3]] == [1, 501, 1001]
    assert pages[2].events[-1].message == FINAL_SUCCESS_MESSAGE
    assert {page.last_seq for page in pages} == {1201}
    assert manager.get(job.id, -7).events[0].seq == 1


def test_event_cap_drops_the_oldest_progress_events_first(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        print_step(1, 4, "Thu thập")
        with progress_status("Đang thu thập") as progress:
            for index in range(19):
                progress(f"p{index}")
        print_info("info")
        print_warning("warning")
        return settings.output_dir

    manager = make(runner, max_events=10)
    done = _finished(manager, manager.start(JobRequest(url="x.example"), settings).id)
    # 1 step + 20 progress + info + warning + final success = 24 events, 10 kept.
    assert done.last_seq == 24 and len(done.events) == 10
    kinds = [event.kind for event in done.events]
    assert kinds == ["step"] + ["progress"] * 6 + ["info", "warning", "success"]
    assert [event.seq for event in done.events] == [1, 16, 17, 18, 19, 20, 21, 22, 23, 24]


def test_event_cap_without_progress_drops_the_oldest_events(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        for index in range(15):
            print_info(f"m{index}")
        with progress_status("late") as progress:
            progress("x")
        return settings.output_dir

    manager = make(runner, max_events=10)
    done = _finished(manager, manager.start(JobRequest(url="x.example"), settings).id)
    # 15 infos, 2 progress, final success = 18 events.
    assert done.last_seq == 18
    assert [event.seq for event in done.events] == [7, 8, 9, 10, 11, 12, 13, 14, 15, 18]
    assert manager.get(done.id, since=8).events[0].seq == 9


def test_events_are_thread_safe_while_polled(
    make: Callable[..., JobManager], settings: UiSettings
) -> None:
    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        for index in range(3000):
            print_info(str(index))
        return settings.output_dir

    manager = make(runner)
    job = manager.start(JobRequest(url="x.example"), settings)
    seen: list[int] = []
    since = 0
    while True:
        snapshot = manager.get(job.id, since)
        seen.extend(event.seq for event in snapshot.events)
        if snapshot.events:
            since = snapshot.events[-1].seq
        if snapshot.finished and since == snapshot.last_seq:
            break
    assert seen == list(range(1, 3002))


def test_failed_snapshot_times(make: Callable[..., JobManager], settings: UiSettings) -> None:
    times = iter([datetime(2026, 1, 1, 0, 0, second, tzinfo=UTC) for second in range(10)])

    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        raise GdmError("Lỗi.")

    manager = make(runner, clock=lambda: next(times))
    done = _finished(manager, manager.start(JobRequest(url="x.example"), settings).id)
    assert done.created_at.second == 0 and done.started_at is not None
    assert done.finished_at is not None and done.finished_at > done.started_at


# ─── Result files of this run ────────────────────────────────────────────────


@pytest.mark.parametrize("prompt_only", [False, True])
def test_prompt_md_is_listed_only_when_the_job_wrote_it(
    make: Callable[..., JobManager], settings: UiSettings, prompt_only: bool
) -> None:
    # The folder also holds a prompt.md from an earlier --prompt-only run.
    manager = make(pipeline_like(("DESIGN.md", "prompt.md", "synthesis.json")))
    request = JobRequest(url="x.example", prompt_only=prompt_only)
    done = _finished(manager, manager.start(request, request.apply_to(settings)).id)
    assert done.status == "succeeded"
    if prompt_only:
        assert done.files == ["DESIGN.md", "prompt.md", "synthesis.json"]
    else:
        assert done.files == ["DESIGN.md", "synthesis.json"]


# ─── Cancelling a stage that waits in an event loop ──────────────────────────


@pytest.fixture
def thread_errors(monkeypatch: pytest.MonkeyPatch) -> list[BaseException]:
    """Exceptions that escaped a thread (a job thread must never let one escape)."""
    caught: list[BaseException] = []

    def hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None:
            caught.append(args.exc_value)

    monkeypatch.setattr(threading, "excepthook", hook)
    return caught


class Hanging:
    """Like the collector on a page that never answers: messages from inside an event loop,
    then an await that does not return; cleanup that still awaits (Playwright closing the
    browser in its `async with` blocks)."""

    def __init__(self, *, sub_task: bool = False) -> None:
        self.sub_task = sub_task
        self.waiting = threading.Event()
        self.cleaned_up = threading.Event()

    async def _never(self) -> None:
        await asyncio.sleep(3600)

    async def _stage(self) -> None:
        try:
            print_info("Mở trình duyệt…")
            print_info("Mở trang…")
            self.waiting.set()
            if self.sub_task:
                await asyncio.gather(self._never(), self._never())
            else:
                await self._never()
        finally:
            await asyncio.sleep(0)
            self.cleaned_up.set()

    def __call__(self, request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        print_step(1, 4, "Thu thập dữ liệu trang (Tầng 1)")
        asyncio.run(self._stage())
        print_step(2, 4, "Phân tích thiết kế (Tầng 2)")  # never reached after a cancel
        return settings.output_dir


@pytest.mark.parametrize("sub_task", [False, True])
def test_cancel_interrupts_a_stage_waiting_in_an_event_loop(
    make: Callable[..., JobManager],
    settings: UiSettings,
    thread_errors: list[BaseException],
    sub_task: bool,
) -> None:
    hanging = Hanging(sub_task=sub_task)
    manager = make(hanging)
    job = manager.start(JobRequest(url="hang.example"), settings)
    assert hanging.waiting.wait(WAIT_S)
    begin = time.monotonic()
    manager.cancel(job.id)
    done = _finished(manager, job.id)
    assert time.monotonic() - begin < 5  # not when the await would have ended (an hour)
    assert done.status == "cancelled" and done.error is not None
    assert done.error.code == "cancelled"
    assert done.error.message == JobCancelledError().message
    assert hanging.cleaned_up.is_set()
    assert [event.step for event in done.events if event.kind == "step"] == [1]
    assert thread_errors == []


def test_cancel_after_the_event_loop_closed_waits_for_the_next_message(
    make: Callable[..., JobManager], settings: UiSettings, thread_errors: list[BaseException]
) -> None:
    looping = Looping()

    async def collect() -> None:
        print_info("Mở trình duyệt…")  # remembered: its loop is closed once collect() ends

    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        asyncio.run(collect())
        return looping(request, settings, sink)

    manager = make(runner)
    job = manager.start(JobRequest(url="x.example"), settings)
    assert looping.started.wait(WAIT_S)
    manager.cancel(job.id)
    done = _finished(manager, job.id)
    assert done.status == "cancelled" and looping.cleaned_up.is_set()
    assert thread_errors == []


def test_a_cancelled_error_without_a_cancel_fails_the_job(
    make: Callable[..., JobManager], settings: UiSettings, thread_errors: list[BaseException]
) -> None:
    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        raise asyncio.CancelledError()

    manager = make(runner)
    done = _finished(manager, manager.start(JobRequest(url="x.example"), settings).id)
    assert done.status == "failed" and done.error is not None
    assert done.error.code == "unexpected"
    assert thread_errors == []


class _HangingSite:
    """HTTP server on 127.0.0.1 that answers /robots.txt (404) at once and never answers any
    other request (a site that hangs while loading)."""

    def __init__(self) -> None:
        self._socket = socket.create_server(("127.0.0.1", 0))
        self.port = int(self._socket.getsockname()[1])
        self.page_requested = threading.Event()
        self._connections: list[socket.socket] = []
        threading.Thread(target=self._serve, name="hanging-site", daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return
            self._connections.append(connection)
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection: socket.socket) -> None:
        data = b""
        try:
            while b"\r\n\r\n" not in data:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                data += chunk
            if b" /robots.txt " in data.split(b"\r\n", 1)[0]:
                connection.sendall(
                    b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                connection.close()
                return
            self.page_requested.set()  # and never answer
        except OSError:
            return

    def close(self) -> None:
        self._socket.close()
        for connection in self._connections:
            with contextlib.suppress(OSError):
                connection.close()


@pytest.mark.browser
def test_cancel_abandons_a_page_that_never_answers(
    make: Callable[..., JobManager], tmp_path: Path, thread_errors: list[BaseException]
) -> None:
    site = _HangingSite()
    try:
        manager = make(run_pipeline)
        settings = UiSettings(output_dir=tmp_path / "output", timeout_s=60.0, browser="chromium")
        job = manager.start(JobRequest(url=site.url), settings)
        assert site.page_requested.wait(60)
        time.sleep(0.5)  # the browser is waiting for the answer
        begin = time.monotonic()
        manager.cancel(job.id)
        done = manager.wait(job.id, timeout=60)
        elapsed = time.monotonic() - begin
    finally:
        site.close()
    assert done.status == "cancelled", done.error
    # Before, the page load ran to its end: up to twice timeout_s (2 minutes here).
    assert elapsed < 15
    assert thread_errors == []


# ─── The default runner ──────────────────────────────────────────────────────


def test_the_default_runner_refuses_an_unusable_output_folder(
    make: Callable[..., JobManager], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(*args: object, **kwargs: object) -> object:
        raise AssertionError("the pipeline must not start")

    monkeypatch.setattr(pipeline, "run_get", never)
    blocker = tmp_path / "a-file.txt"
    blocker.write_text("x", encoding="utf-8")
    manager = make(run_pipeline)
    for folder in (blocker, blocker / "inside"):
        job = manager.start(JobRequest(url="x.example"), UiSettings(output_dir=folder))
        done = _finished(manager, job.id)
        assert done.status == "failed" and done.error is not None
        assert done.error.code == "config"
        assert str(blocker) in done.error.message
        assert done.error.message.startswith("Không dùng được thư mục lưu kết quả.")
        assert done.error.hint == OUTPUT_DIR_JOB_HINT
        assert "Thư mục lưu kết quả" in done.error.hint


def test_unexpected_errors_point_to_the_log_not_to_a_command() -> None:
    assert "ui.log" in UNEXPECTED_HINT
    assert "gdm --debug" not in UNEXPECTED_HINT and "get <url>" not in UNEXPECTED_HINT
