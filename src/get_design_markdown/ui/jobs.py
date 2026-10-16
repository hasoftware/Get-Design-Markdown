"""Jobs of the local UI: one pipeline run at a time in a worker thread, with a polled event log.

Frozen interface (implemented here): JobRequest, JobEvent, JobStatus, JobError, JobSnapshot,
JobRunner, capture_options_for(), run_pipeline() (the default runner), error_code(),
job_error(), JobBusyError, JobNotFoundError and the constants.
To implement (the stubs raise NotImplementedYetError): JobManager.

Life of a job
-------------
1. The server validates the request (JobRequest; storage.normalize_url for the URL -> 422
   "invalid_url") and resolves its settings: `request.apply_to(saved settings)` with
   output_dir replaced by the server's output root.
2. JobManager.start() refuses a second job while one is queued or running (JobBusyError ->
   409 "busy"), creates the job (status "queued", id = 12 lowercase hex chars,
   site = storage.domain_of(url)) and starts a daemon thread named "gdm-job-<id>".
3. The thread sets status "running" and calls `runner(request, settings, sink)` INSIDE
   `console.use_sink(sink)` (context variables do not cross threads, so the thread itself must
   enter it). `sink` appends a JobEvent per ConsoleEvent; before appending it raises
   JobCancelledError when cancel() was requested, so the pipeline unwinds at its next message
   (Playwright closes the browser through its `async with` blocks).
4. When the runner returns: status "succeeded", `files` = names of the files present in the
   site folder among DESIGN.md, preview.html, tokens.json, prompt.md, analysis.json,
   synthesis.json, raw.json, and a final "success" event with FINAL_SUCCESS_MESSAGE.
   JobCancelledError: status "cancelled", error code "cancelled" and a final "warning" event
   with the error's message and hint. Any other exception:
   status "failed", `error = job_error(exc)` and a final "error" event carrying the same
   message and hint (an unexpected exception is also logged with its traceback).
5. Events are numbered from 1 (`seq`); a poll asks for the events after the last seq it saw
   (`since`) and gets at most MAX_EVENTS_PER_RESPONSE of them, oldest first. A job keeps at most
   MAX_EVENTS_PER_JOB events (the oldest "progress" events are dropped first; seq numbers are
   never reused). The manager remembers the last MAX_JOBS_KEPT jobs.

Cancellation is cooperative: it takes effect at the next message of the pipeline (at worst
after the page-load timeout); until then the snapshot shows cancel_requested = true with the
status still "running".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from get_design_markdown import pipeline
from get_design_markdown.console import ConsoleEvent, EventKind, MessageSink, use_sink
from get_design_markdown.errors import (
    AnalysisError,
    BotBlockedError,
    BrowserNotInstalledError,
    CollectError,
    ConfigError,
    ExportError,
    GdmError,
    InvalidDataError,
    InvalidUrlError,
    JobCancelledError,
    LoginRequiredError,
    NavigationError,
    NotImplementedYetError,
    OutputNotFoundError,
    PageTimeoutError,
    SynthesisValidationError,
)
from get_design_markdown.models.base import GdmModel
from get_design_markdown.models.raw import BrowserChoice, CaptureOptions
from get_design_markdown.ui.settings import (
    MAX_DELTA_E,
    MAX_MAX_ELEMENTS,
    MAX_PAGES,
    MAX_TIMEOUT_S,
    MIN_DELTA_E,
    MIN_MAX_ELEMENTS,
    MIN_PAGES,
    MIN_TIMEOUT_S,
    UiLang,
    UiSettings,
)

#: Steps of pipeline.run_get (print_step(i, 4, ...)): collect, analyze, DESIGN.md, export.
PIPELINE_STEPS = 4
MAX_URL_LENGTH = 2048
MAX_EVENTS_PER_JOB = 5000
MAX_EVENTS_PER_RESPONSE = 500
MAX_JOBS_KEPT = 20
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
#: Message of the last event of a successful job.
FINAL_SUCCESS_MESSAGE = "Hoàn tất: đã tạo DESIGN.md, tokens.json và preview.html."

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
#: Statuses after which a job never changes again.
FINISHED_STATUSES: frozenset[JobStatus] = frozenset({"succeeded", "failed", "cancelled"})

#: Machine-readable error codes of JobError.code (the frontend picks its help text from them).
ErrorCode = Literal[
    "cancelled",
    "browser_missing",
    "timeout",
    "bot_blocked",
    "login_required",
    "navigation",
    "collect",
    "invalid_url",
    "analysis",
    "synthesis",
    "export",
    "config",
    "data",
    "error",
    "unexpected",
]


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobRequest(GdmModel):
    """Body of POST /api/jobs: the URL plus optional one-off overrides of UiSettings.

    A field left out (or null) uses the saved setting; output_dir cannot be overridden per job
    (results always go to the server's output root, where history and /files look).
    """

    url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)
    pages: int | None = Field(default=None, ge=MIN_PAGES, le=MAX_PAGES)
    dark: bool | None = None
    lang: UiLang | None = None
    prompt_only: bool | None = None
    examples: bool | None = None
    max_elements: int | None = Field(default=None, ge=MIN_MAX_ELEMENTS, le=MAX_MAX_ELEMENTS)
    timeout_s: float | None = Field(default=None, ge=MIN_TIMEOUT_S, le=MAX_TIMEOUT_S)
    delta_e: float | None = Field(default=None, ge=MIN_DELTA_E, le=MAX_DELTA_E)
    browser: BrowserChoice | None = None

    def apply_to(self, settings: UiSettings) -> UiSettings:
        """`settings` with this request's non-null overrides (validated again)."""
        overrides = {
            key: value
            for key, value in self.model_dump(exclude={"url"}).items()
            if value is not None
        }
        return UiSettings.model_validate({**settings.model_dump(), **overrides})


class JobEvent(GdmModel):
    """One line of a job's log (a console.ConsoleEvent with a number and a time)."""

    #: 1, 2, 3... in emission order, unique within the job.
    seq: int = Field(ge=1)
    kind: EventKind
    message: str
    hint: str | None = None
    #: Pipeline step (kind "step"): step 1..total.
    step: int | None = None
    total: int | None = None
    #: UTC time the event was recorded.
    at: datetime

    @classmethod
    def from_console(cls, event: ConsoleEvent, *, seq: int, at: datetime) -> JobEvent:
        return cls(
            seq=seq,
            kind=event.kind,
            message=event.message,
            hint=event.hint,
            step=event.step,
            total=event.total,
            at=at,
        )


class JobError(GdmModel):
    """Why a job failed or stopped: a code plus the Vietnamese message and hint."""

    code: ErrorCode
    message: str
    hint: str | None = None


class JobSnapshot(GdmModel):
    """State of a job as GET /api/jobs/{id} returns it (`events` only after `since`)."""

    id: str
    #: URL as typed by the user.
    url: str
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    #: cancel() was called; the status changes once the pipeline reaches its next message.
    cancel_requested: bool = False
    #: Latest "step" event: step number, step count (4) and its message.
    step: int | None = None
    total: int | None = None
    step_message: str | None = None
    #: Events with seq > since (at most MAX_EVENTS_PER_RESPONSE, oldest first).
    events: list[JobEvent] = Field(default_factory=list)
    #: seq of the newest event of the job (0 when it has none yet).
    last_seq: int = 0
    #: Settings the job runs with (saved settings + request overrides + output root).
    settings: UiSettings
    #: Folder name of the result under the output root (storage.domain_of(url)).
    site: str
    #: Set when status is "failed" or "cancelled".
    error: JobError | None = None
    #: After success: files present in the site folder (names relative to it).
    files: list[str] = Field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.status in FINISHED_STATUSES


#: The work a job does: called in the job thread with use_sink(sink) already active; returns
#: the site folder it wrote. Raises GdmError (JobCancelledError included) or anything else.
JobRunner = Callable[[JobRequest, UiSettings, MessageSink], Path]


def capture_options_for(settings: UiSettings) -> CaptureOptions:
    """The CaptureOptions `gdm get` would build for the same choices."""
    return CaptureOptions(
        max_elements=settings.max_elements,
        dark=settings.dark,
        pages=settings.pages,
        timeout_s=settings.timeout_s,
        browser=settings.browser,
    )


def run_pipeline(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
    """Default runner: exactly `gdm get <url> --out <output_dir> ...` (same DESIGN.md as the
    CLI), never opening an external browser. Enters use_sink(sink) itself as well, so it also
    works when called directly."""
    with use_sink(sink):
        result = pipeline.run_get(
            request.url,
            out_root=settings.output_dir,
            options=capture_options_for(settings),
            lang=settings.lang,
            prompt_only=settings.prompt_only,
            examples=settings.examples,
            open_browser=False,
            threshold=settings.delta_e,
        )
    return result.folder


#: Checked in order, so subclasses come before their base classes.
_ERROR_CODES: tuple[tuple[type[BaseException], ErrorCode], ...] = (
    (JobCancelledError, "cancelled"),
    (BrowserNotInstalledError, "browser_missing"),
    (PageTimeoutError, "timeout"),
    (BotBlockedError, "bot_blocked"),
    (LoginRequiredError, "login_required"),
    (NavigationError, "navigation"),
    (CollectError, "collect"),
    (InvalidUrlError, "invalid_url"),
    (AnalysisError, "analysis"),
    (SynthesisValidationError, "synthesis"),
    (ExportError, "export"),
    (ConfigError, "config"),
    (OutputNotFoundError, "data"),
    (InvalidDataError, "data"),
    (GdmError, "error"),
)

UNEXPECTED_HINT = (
    "Hãy thử lại. Nếu lỗi lặp lại, gửi kèm file nhật ký ui.log (hoặc kết quả của lệnh "
    "'gdm get <url> --debug') khi báo lỗi."
)


def error_code(error: BaseException) -> ErrorCode:
    """JobError.code for an exception raised by a runner."""
    for kind, code in _ERROR_CODES:
        if isinstance(error, kind):
            return code
    return "unexpected"


def job_error(error: BaseException) -> JobError:
    """JobError for an exception: GdmError keeps its Vietnamese message and hint; anything
    else becomes "Lỗi không mong muốn: <Type>: <first line>" with UNEXPECTED_HINT."""
    if isinstance(error, GdmError):
        return JobError(code=error_code(error), message=error.message, hint=error.hint)
    text = str(error).strip()
    detail = f"{type(error).__name__}: {text.splitlines()[0]}" if text else type(error).__name__
    return JobError(
        code="unexpected", message=f"Lỗi không mong muốn: {detail}", hint=UNEXPECTED_HINT
    )


class JobBusyError(GdmError):
    """A job is already queued or running (HTTP 409 "busy")."""

    def __init__(self, job_id: str) -> None:
        super().__init__(
            "Đang có một lượt phân tích chạy.",
            hint="Chờ lượt hiện tại xong hoặc bấm Hủy rồi thử lại.",
        )
        self.job_id = job_id


class JobNotFoundError(GdmError):
    """No job with this id (HTTP 404 "job_not_found")."""

    def __init__(self, job_id: str) -> None:
        super().__init__(
            "Không tìm thấy lượt phân tích này.",
            hint="Có thể giao diện đã khởi động lại; hãy chạy lại phân tích.",
        )
        self.job_id = job_id


# ─── To implement ────────────────────────────────────────────────────────────


class JobManager:
    """Runs at most one job at a time (see the module docstring); thread-safe.

    `runner` is injectable (tests use scripted fakes); `clock` gives the UTC times of events.
    """

    def __init__(
        self,
        runner: JobRunner = run_pipeline,
        *,
        clock: Callable[[], datetime] = utc_now,
        max_jobs: int = MAX_JOBS_KEPT,
        max_events: int = MAX_EVENTS_PER_JOB,
    ) -> None:
        self.runner = runner
        self.clock = clock
        self.max_jobs = max_jobs
        self.max_events = max_events

    @property
    def busy(self) -> bool:
        """True while a job is queued or running."""
        raise NotImplementedYetError("Chưa chạy được phân tích từ giao diện.")

    def start(self, request: JobRequest, settings: UiSettings) -> JobSnapshot:
        """Create and start a job with the already-resolved `settings`; returns its first
        snapshot (status "queued" or "running", no events). Raises JobBusyError when a job is
        queued or running, InvalidUrlError when request.url cannot be normalized."""
        raise NotImplementedYetError("Chưa chạy được phân tích từ giao diện.")

    def get(self, job_id: str, since: int = 0) -> JobSnapshot:
        """Snapshot with the events whose seq > since; JobNotFoundError for an unknown id."""
        raise NotImplementedYetError("Chưa chạy được phân tích từ giao diện.")

    def current(self, since: int = 0) -> JobSnapshot | None:
        """The queued/running job, else the most recent finished one, else None."""
        raise NotImplementedYetError("Chưa chạy được phân tích từ giao diện.")

    def cancel(self, job_id: str) -> JobSnapshot:
        """Request cancellation (idempotent; no-op for a finished job); JobNotFoundError for an
        unknown id. Returns the snapshot without events."""
        raise NotImplementedYetError("Chưa chạy được phân tích từ giao diện.")

    def wait(self, job_id: str, timeout: float | None = None) -> JobSnapshot:
        """Block until the job finished (or `timeout` seconds passed); returns its snapshot
        with all events. For tests and shutdown."""
        raise NotImplementedYetError("Chưa chạy được phân tích từ giao diện.")

    def shutdown(self, timeout: float = 10.0) -> None:
        """Cancel the running job, if any, and wait up to `timeout` seconds for its thread."""
        raise NotImplementedYetError("Chưa chạy được phân tích từ giao diện.")
