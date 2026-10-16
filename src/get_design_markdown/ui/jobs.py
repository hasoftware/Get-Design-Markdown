"""Jobs of the local UI: one pipeline run at a time in a worker thread, with a polled event log.

Frozen interface (implemented here): JobRequest, JobEvent, JobStatus, JobError, JobSnapshot,
JobRunner, capture_options_for(), run_pipeline() (the default runner), error_code(),
job_error(), JobBusyError, JobNotFoundError, the constants and JobManager's signatures.

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

import bisect
import logging
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
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
    OutputNotFoundError,
    PageTimeoutError,
    SynthesisValidationError,
)
from get_design_markdown.models.base import GdmModel
from get_design_markdown.models.raw import BrowserChoice, CaptureOptions
from get_design_markdown.storage import (
    ANALYSIS_FILE,
    DESIGN_FILE,
    PREVIEW_FILE,
    PROMPT_FILE,
    RAW_FILE,
    SYNTHESIS_FILE,
    TOKENS_FILE,
    domain_of,
)
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
    "'gdm --debug get <url>') khi báo lỗi."
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


# ─── JobManager ──────────────────────────────────────────────────────────────

logger = logging.getLogger("get_design_markdown.ui")

#: Result files listed in JobSnapshot.files after a success, in this order.
RESULT_FILES = (
    DESIGN_FILE,
    PREVIEW_FILE,
    TOKENS_FILE,
    PROMPT_FILE,
    ANALYSIS_FILE,
    SYNTHESIS_FILE,
    RAW_FILE,
)


@dataclass(eq=False)
class _Job:
    """Mutable state of one job; only touched with JobManager._lock held."""

    id: str
    request: JobRequest
    settings: UiSettings
    site: str
    created_at: datetime
    status: JobStatus = "queued"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested: bool = False
    step: int | None = None
    total: int | None = PIPELINE_STEPS
    step_message: str | None = None
    events: list[JobEvent] = field(default_factory=list)
    last_seq: int = 0
    #: Every event before this index is known not to be a "progress" event (see _trim).
    progress_scan_from: int = 0
    error: JobError | None = None
    files: list[str] = field(default_factory=list)
    thread: threading.Thread | None = None
    done: threading.Event = field(default_factory=threading.Event)


def _result_files(folder: Path) -> list[str]:
    files: list[str] = []
    for name in RESULT_FILES:
        try:
            if (folder / name).is_file():
                files.append(name)
        except OSError:
            continue
    return files


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
        self.max_jobs = max(1, max_jobs)
        self.max_events = max(1, max_events)
        self._lock = threading.RLock()
        #: Jobs by id, oldest first (dicts keep insertion order).
        self._jobs: dict[str, _Job] = {}

    # queries -------------------------------------------------------------------

    def _newest(self) -> _Job | None:
        return next(reversed(self._jobs.values()), None)

    def _active(self) -> _Job | None:
        """The queued or running job (always the newest one), or None."""
        newest = self._newest()
        return newest if newest is not None and newest.status not in FINISHED_STATUSES else None

    @property
    def busy(self) -> bool:
        """True while a job is queued or running."""
        with self._lock:
            return self._active() is not None

    @property
    def active_job_id(self) -> str | None:
        """Id of the queued or running job, or None."""
        with self._lock:
            active = self._active()
            return active.id if active is not None else None

    def _find(self, job_id: str) -> _Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    def _snapshot(self, job: _Job, since: int | None, *, limit: int | None) -> JobSnapshot:
        """JobSnapshot of `job` with the events whose seq > since (None: no events), at most
        `limit` of them (None: all)."""
        events: list[JobEvent] = []
        if since is not None:
            start = bisect.bisect_right(job.events, max(since, 0), key=lambda event: event.seq)
            stop = len(job.events) if limit is None else start + limit
            events = job.events[start:stop]
        return JobSnapshot(
            id=job.id,
            url=job.request.url,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            cancel_requested=job.cancel_requested,
            step=job.step,
            total=job.total,
            step_message=job.step_message,
            events=events,
            last_seq=job.last_seq,
            settings=job.settings,
            site=job.site,
            error=job.error,
            files=list(job.files),
        )

    def get(self, job_id: str, since: int = 0) -> JobSnapshot:
        """Snapshot with the events whose seq > since; JobNotFoundError for an unknown id."""
        with self._lock:
            return self._snapshot(self._find(job_id), since, limit=MAX_EVENTS_PER_RESPONSE)

    def current(self, since: int = 0) -> JobSnapshot | None:
        """The queued/running job, else the most recent finished one, else None."""
        with self._lock:
            newest = self._newest()
            if newest is None:
                return None
            return self._snapshot(newest, since, limit=MAX_EVENTS_PER_RESPONSE)

    # starting ------------------------------------------------------------------

    def _new_id(self) -> str:
        while True:
            job_id = secrets.token_hex(6)
            if job_id not in self._jobs:
                return job_id

    def start(self, request: JobRequest, settings: UiSettings) -> JobSnapshot:
        """Create and start a job with the already-resolved `settings`; returns its first
        snapshot (status "queued" or "running", no events). Raises JobBusyError when a job is
        queued or running, InvalidUrlError when request.url cannot be normalized."""
        site = domain_of(request.url)  # InvalidUrlError for an unusable URL
        with self._lock:
            active = self._active()
            if active is not None:
                raise JobBusyError(active.id)
            job = _Job(
                id=self._new_id(),
                request=request,
                settings=settings,
                site=site,
                created_at=self.clock(),
            )
            self._jobs[job.id] = job
            while len(self._jobs) > self.max_jobs:  # the new job is the newest: never dropped
                del self._jobs[next(iter(self._jobs))]
            job.thread = threading.Thread(
                target=self._run, args=(job,), name=f"gdm-job-{job.id}", daemon=True
            )
            snapshot = self._snapshot(job, None, limit=None)
            job.thread.start()
            return snapshot

    # the job thread ------------------------------------------------------------

    def _append(self, job: _Job, event: ConsoleEvent) -> None:
        """Record `event` as the job's next JobEvent (lock held)."""
        job.last_seq += 1
        job.events.append(JobEvent.from_console(event, seq=job.last_seq, at=self.clock()))
        if event.kind == "step":
            job.step = event.step
            if event.total is not None:
                job.total = event.total
            job.step_message = event.message
        if len(job.events) > self.max_events:
            self._trim(job)

    def _trim(self, job: _Job) -> None:
        """Drop the oldest "progress" event, or the oldest event when none is left.

        Events before job.progress_scan_from are known to be of other kinds, so each scan
        resumes where the previous one stopped and trimming stays cheap on long jobs."""
        events = job.events
        index = job.progress_scan_from
        while index < len(events) and events[index].kind != "progress":
            index += 1
        if index < len(events):
            del events[index]
            job.progress_scan_from = index
        else:
            del events[0]
            job.progress_scan_from = len(events)

    def _sink_for(self, job: _Job) -> MessageSink:
        def sink(event: ConsoleEvent) -> None:
            with self._lock:
                if job.cancel_requested:
                    raise JobCancelledError()
                self._append(job, event)

        return sink

    def _run(self, job: _Job) -> None:
        """Body of the job thread "gdm-job-<id>"."""
        with self._lock:
            if job.cancel_requested:  # cancelled before the thread got going
                self._finish_cancelled(job, JobCancelledError())
                return
            job.status = "running"
            job.started_at = self.clock()
        sink = self._sink_for(job)
        try:
            with use_sink(sink):
                folder = self.runner(job.request, job.settings, sink)
        except Exception as error:
            self._finish_failed(job, error)
        except BaseException as error:  # SystemExit & co. must not leave the job "running"
            self._finish_failed(job, error)
            raise
        else:
            self._finish_succeeded(job, folder)

    def _finish(self, job: _Job, status: JobStatus) -> None:
        """Final status (lock held); wakes up wait()."""
        job.status = status
        job.finished_at = self.clock()
        job.done.set()

    def _finish_succeeded(self, job: _Job, folder: Path) -> None:
        files = _result_files(Path(folder))
        with self._lock:
            job.files = files
            self._append(job, ConsoleEvent("success", FINAL_SUCCESS_MESSAGE))
            self._finish(job, "succeeded")

    def _finish_cancelled(self, job: _Job, error: JobCancelledError) -> None:
        with self._lock:
            job.error = job_error(error)
            self._append(job, ConsoleEvent("warning", error.message, hint=error.hint))
            self._finish(job, "cancelled")

    def _finish_failed(self, job: _Job, error: BaseException) -> None:
        with self._lock:
            cancelled = job.cancel_requested
        if isinstance(error, JobCancelledError):
            self._finish_cancelled(job, error)
            return
        if cancelled:
            # Whatever the pipeline raised while unwinding after a cancel is the cancel.
            logger.debug("Job %s raised after its cancellation", job.id, exc_info=error)
            self._finish_cancelled(job, JobCancelledError())
            return
        if not isinstance(error, GdmError):
            logger.error(
                "Lỗi không mong muốn trong lượt phân tích %s (%s)",
                job.id,
                job.request.url,
                exc_info=error,
            )
        failure = job_error(error)
        with self._lock:
            job.error = failure
            self._append(job, ConsoleEvent("error", failure.message, hint=failure.hint))
            self._finish(job, "failed")

    # control -------------------------------------------------------------------

    def cancel(self, job_id: str) -> JobSnapshot:
        """Request cancellation (idempotent; no-op for a finished job); JobNotFoundError for an
        unknown id. Returns the snapshot without events."""
        with self._lock:
            job = self._find(job_id)
            if job.status not in FINISHED_STATUSES:
                job.cancel_requested = True
            return self._snapshot(job, None, limit=None)

    def wait(self, job_id: str, timeout: float | None = None) -> JobSnapshot:
        """Block until the job finished (or `timeout` seconds passed); returns its snapshot
        with all events. For tests and shutdown."""
        with self._lock:
            job = self._find(job_id)
        job.done.wait(timeout)
        with self._lock:
            return self._snapshot(job, 0, limit=None)

    def shutdown(self, timeout: float = 10.0) -> None:
        """Cancel the running job, if any, and wait up to `timeout` seconds for its thread."""
        with self._lock:
            active = self._active()
            if active is None:
                return
            active.cancel_requested = True
            thread = active.thread
        if thread is None or thread is threading.current_thread():
            return
        thread.join(timeout)
        if thread.is_alive():
            logger.warning(
                "Lượt phân tích %s chưa dừng sau %.0f giây; vẫn tiếp tục tắt.", active.id, timeout
            )
