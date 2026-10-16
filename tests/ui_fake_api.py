"""Fake HTTP server speaking the local UI API (ui/server.py) with canned data.

It lets the frontend be built and tested in a real browser before the real backend exists, and
stays useful afterwards for fast, deterministic UI tests. It follows the same contract: token
header on /api, Host/Origin checks, no CORS, JSON error envelope, /files confinement, CSP
headers; payloads are the real response models (server.py, jobs.py, history.py...).

Data: build_fake_output() runs the real synthesizer and exporters on
tests/fixtures/analysis_sample.json, so /files and /api/results serve a real DESIGN.md,
prompt.md, tokens.json and preview.html ("acme.example", English, prompt.md, light + dark
placeholder screenshots) plus a second, Vietnamese site ("beta.example", no prompt, no
screenshots).

Scripted jobs (POST /api/jobs), chosen by the URL:
- contains "slow"        progress events every 0.2 s for a minute (for cancel tests)
- contains "fail"        stops in step 1 with a bot wall (code "bot_blocked")
- contains "no-browser"  stops in step 1 with code "browser_missing"
- contains "robots"      succeeds, with a robots.txt warning in step 1
- anything else          succeeds (18 events); the result folder is a copy of acme.example
- a URL storage.normalize_url rejects -> 422 "invalid_url"
`event_delay_s` sets the pause between two events (default 0.05 s).
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from markdown_it import MarkdownIt
from PIL import Image, ImageDraw
from pydantic import BaseModel, ValidationError

from get_design_markdown import __version__
from get_design_markdown.console import ConsoleEvent, EventKind, use_sink
from get_design_markdown.errors import (
    BotBlockedError,
    BrowserNotInstalledError,
    GdmError,
    InvalidUrlError,
    JobCancelledError,
)
from get_design_markdown.exporters.export import run_export
from get_design_markdown.models.analysis import DesignAnalysis
from get_design_markdown.models.raw import BROWSER_CHOICES
from get_design_markdown.models.synthesis import SynthesisResult
from get_design_markdown.resources import resource_path
from get_design_markdown.storage import (
    ANALYSIS_FILE,
    DESIGN_FILE,
    PREVIEW_FILE,
    PROMPT_FILE,
    PROMPT_IMAGES_DIR,
    RAW_FILE,
    SCREENSHOTS_DIR,
    SYNTHESIS_FILE,
    TOKENS_FILE,
    describe_error,
    domain_of,
    normalize_url,
    save_model,
)
from get_design_markdown.synthesizer.synthesize import run_synthesize
from get_design_markdown.ui.browser_install import (
    AUTO_ORDER,
    BROWSER_LABELS,
    BrowserInfo,
    InstallStatus,
)
from get_design_markdown.ui.history import THUMBNAIL_PREFERENCE, HistoryEntry, is_valid_site_name
from get_design_markdown.ui.jobs import (
    FINAL_SUCCESS_MESSAGE,
    PIPELINE_STEPS,
    JobError,
    JobEvent,
    JobRequest,
    JobSnapshot,
    JobStatus,
    job_error,
)
from get_design_markdown.ui.server import (
    FILES_HTML_CSP,
    INDEX_CSP,
    MAX_BODY_BYTES,
    MAX_DRAIN_BYTES,
    PENDING_BODY_WAIT_S,
    TOKEN_HEADER,
    ApiError,
    ApiErrorCode,
    BrowserStatusResponse,
    CurrentJobResponse,
    ErrorResponse,
    HealthResponse,
    HeartbeatResponse,
    HistoryResponse,
    InstallResponse,
    JobResponse,
    OpenFolderRequest,
    OpenFolderResponse,
    RenderedDocument,
    ResultFile,
    ResultFileKind,
    ResultsResponse,
    ScreenshotInfo,
    SettingsLimits,
    SettingsResponse,
    ShutdownRequest,
    ShutdownResponse,
    UiMode,
    mixed_runs_note,
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
    UiSettings,
)
from tests.fixture_data import load_analysis_sample, load_raw_sample

FAKE_TOKEN = "fake-token-for-tests"
ACME_SITE = "acme.example"
BETA_SITE = "beta.example"
FAKE_EDGE_VERSION = "141.0.3537.71"

_KIND_BY_NAME: dict[str, ResultFileKind] = {
    DESIGN_FILE: "design",
    PREVIEW_FILE: "preview",
    TOKENS_FILE: "tokens",
    PROMPT_FILE: "prompt",
    ANALYSIS_FILE: "analysis",
    SYNTHESIS_FILE: "synthesis",
    RAW_FILE: "raw",
}
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".txt": "text/plain; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
_JOB_PATH_RE = re.compile(r"^/api/jobs/(?P<id>[0-9a-f]{12})(?P<cancel>/cancel)?$")
_RESULTS_PATH_RE = re.compile(r"^/api/results/(?P<site>[^/]+)$")


# ─── Fake output folder ──────────────────────────────────────────────────────


def _placeholder_screenshot(path: Path, width: int, dark: bool) -> None:
    """A flat drawing that looks vaguely like a landing page (no real site content)."""
    height = int(width * 1.25)
    canvas, ink, accent, surface = (
        ("#0b1020", "#e6e9f2", "#7c83ff", "#161c33")
        if dark
        else ("#ffffff", "#0f172a", "#4f46e5", "#f1f5f9")
    )
    image = Image.new("RGB", (width, height), canvas)
    draw = ImageDraw.Draw(image)
    unit = max(width // 48, 6)
    draw.rectangle((0, 0, width, unit * 3), fill=surface)
    draw.rectangle((unit * 2, unit, unit * 8, unit * 2), fill=ink)
    draw.rounded_rectangle(
        (width - unit * 9, unit // 2, width - unit * 2, unit * 2 + unit // 2),
        radius=unit // 2,
        fill=accent,
    )
    draw.rectangle((unit * 4, unit * 7, width - unit * 4, unit * 9), fill=ink)
    draw.rectangle((unit * 8, unit * 10, width - unit * 8, unit * 11), fill=surface)
    draw.rounded_rectangle(
        (width // 2 - unit * 5, unit * 13, width // 2 + unit * 5, unit * 15),
        radius=unit,
        fill=accent,
    )
    columns = 3 if width >= 768 else 1
    gap = unit * 2
    card_width = (width - unit * 8 - gap * (columns - 1)) // columns
    top = unit * 19
    for _row in range(2 if columns == 3 else 4):
        for column in range(columns):
            left = unit * 4 + column * (card_width + gap)
            draw.rounded_rectangle(
                (left, top, left + card_width, top + unit * 10), radius=unit, fill=surface
            )
        top += unit * 12
    image.save(path, format="PNG", optimize=True)


def _quiet(event: ConsoleEvent) -> None:
    """Message sink that drops the stage messages while the fixture output is built."""


def build_fake_output(root: Path) -> list[str]:
    """Create the fake output root (acme.example + beta.example); returns the site names."""
    acme = root / ACME_SITE
    (acme / SCREENSHOTS_DIR).mkdir(parents=True, exist_ok=True)
    analysis = load_analysis_sample()
    for shot in analysis.screenshots:
        _placeholder_screenshot(acme / shot.path, shot.viewport_width, shot.color_scheme == "dark")
    save_model(analysis, acme / ANALYSIS_FILE)
    save_model(load_raw_sample(), acme / RAW_FILE)
    with use_sink(_quiet):
        run_synthesize(acme, lang="en", prompt_only=True)
        run_export(acme)

    beta = root / BETA_SITE
    beta.mkdir(parents=True, exist_ok=True)
    site = analysis.site.model_copy(
        update={"url": "https://beta.example/", "name": "Beta Studio", "lang": "vi"}
    )
    beta_analysis = analysis.model_copy(
        update={
            "site": site,
            "screenshots": [],
            "analyzed_at": analysis.analyzed_at - timedelta(days=2),
            "warnings": ["Trang có lớp phủ cookie — đã ẩn trước khi đo."],
        }
    )
    save_model(beta_analysis, beta / ANALYSIS_FILE)
    with use_sink(_quiet):
        run_synthesize(beta, lang="vi")
        run_export(beta)
    return [ACME_SITE, BETA_SITE]


# ─── Fake data views (what the real backend computes) ────────────────────────

_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")


def _split_front_matter(text: str) -> tuple[str | None, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    return None, text


def _render_document(path: Path) -> RenderedDocument | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    front, body = _split_front_matter(text)
    return RenderedDocument(markdown=text, front_matter=front, html=_MARKDOWN.render(body))


def _load_analysis(folder: Path) -> DesignAnalysis | None:
    try:
        return DesignAnalysis.model_validate_json(
            (folder / ANALYSIS_FILE).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError):
        return None


def _files_url(site: str, relative: str) -> str:
    return f"/files/{quote(site)}/{quote(relative)}"


def history_entry(folder: Path) -> HistoryEntry | None:
    names = {name for name in (RAW_FILE, ANALYSIS_FILE, DESIGN_FILE) if (folder / name).is_file()}
    if not names or not is_valid_site_name(folder.name):
        return None
    analysis = _load_analysis(folder)
    thumbnail = next((path for path in THUMBNAIL_PREFERENCE if (folder / path).is_file()), None)
    return HistoryEntry(
        site=folder.name,
        name=analysis.site.name if analysis else folder.name,
        url=analysis.site.url if analysis else None,
        analyzed_at=analysis.analyzed_at if analysis else None,
        pages=list(analysis.site.pages_analyzed) if analysis else [],
        has_raw=RAW_FILE in names,
        has_analysis=ANALYSIS_FILE in names,
        has_design=DESIGN_FILE in names,
        has_preview=(folder / PREVIEW_FILE).is_file(),
        has_prompt=(folder / PROMPT_FILE).is_file(),
        has_tokens=(folder / TOKENS_FILE).is_file(),
        thumbnail=thumbnail,
    )


def results_for(folder: Path) -> ResultsResponse | None:
    entry = history_entry(folder)
    if entry is None:
        return None
    site = folder.name
    files: list[ResultFile] = []
    # Case-insensitive order on every OS, like the real server (Path order differs by OS).
    for path in sorted(
        folder.rglob("*"),
        key=lambda found: (
            found.relative_to(folder).as_posix().casefold(),
            found.relative_to(folder).as_posix(),
        ),
    ):
        if not path.is_file() or path.name.startswith("."):
            continue
        relative = path.relative_to(folder).as_posix()
        kind: ResultFileKind = _KIND_BY_NAME.get(relative) or (
            "image" if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") else "other"
        )
        files.append(
            ResultFile(
                name=path.name,
                path=relative,
                size=path.stat().st_size,
                url=_files_url(site, relative),
                kind=kind,
            )
        )
    analysis = _load_analysis(folder)
    screenshots = [
        ScreenshotInfo(
            page=shot.name.rsplit("-", 1)[0].removesuffix("-dark"),
            viewport_width=shot.viewport_width,
            color_scheme=shot.color_scheme,
            path=shot.path,
            url=_files_url(site, shot.path),
        )
        for shot in (analysis.screenshots if analysis else [])
        if (folder / shot.path).is_file()
    ]
    prompt_dir = folder / PROMPT_IMAGES_DIR
    prompt_images = (
        [
            _files_url(site, f"{PROMPT_IMAGES_DIR}/{path.name}")
            for path in sorted(prompt_dir.glob("*.png"))
        ]
        if prompt_dir.is_dir()
        else []
    )
    token_counts: dict[str, int] | None = None
    if (folder / SYNTHESIS_FILE).is_file():
        synthesis = SynthesisResult.model_validate_json(
            (folder / SYNTHESIS_FILE).read_text(encoding="utf-8")
        )
        token_counts = dict(synthesis.token_counts)
    warnings = list(analysis.warnings) if analysis else []
    mixed = mixed_runs_note(folder)  # same rule as the real server
    if mixed is not None:
        warnings.insert(0, mixed)
    return ResultsResponse(
        site=site,
        entry=entry,
        folder=str(folder.resolve()),
        files=files,
        design=_render_document(folder / DESIGN_FILE),
        prompt=_render_document(folder / PROMPT_FILE),
        preview_url=_files_url(site, PREVIEW_FILE) if (folder / PREVIEW_FILE).is_file() else None,
        screenshots=screenshots,
        prompt_images=prompt_images,
        token_counts=token_counts,
        warnings=warnings,
    )


# ─── Scripted jobs ───────────────────────────────────────────────────────────

Step = tuple[EventKind, str, str | None, int | None]  # kind, message, hint, step number


def _success_script(root: Path, site: str, robots_warning: bool) -> list[Step]:
    folder = root / site
    steps: list[Step] = [("step", "Thu thập dữ liệu trang", None, 1)]
    if robots_warning:
        steps.append(
            (
                "warning",
                "robots.txt không cho phép truy cập /. Công cụ vẫn tiếp tục vì mục đích tham "
                "khảo — hãy tôn trọng điều khoản sử dụng của trang.",
                None,
                None,
            )
        )
    else:
        steps.append(("success", "robots.txt cho phép truy cập /.", None, None))
    steps += [
        ("progress", "Đang thu thập", None, None),
        ("progress", "Đang thu thập — Mở trình duyệt…", None, None),
        ("progress", "Đang thu thập — Trang 'home': tải trang…", None, None),
        (
            "info",
            "Trang 'home': 86 phần tử, 24 biến CSS, 6 trạng thái khi rê chuột (hover), 3 ảnh chụp.",
            None,
            None,
        ),
        ("warning", "[home] Phát hiện banner cookie — đã ẩn trước khi chụp.", None, None),
        ("success", f"Đã ghi {folder / RAW_FILE}", None, None),
        ("step", "Phân tích thiết kế", None, 2),
        ("progress", "Đang phân tích — Màu sắc…", None, None),
        ("success", f"Đã ghi {folder / ANALYSIS_FILE}", None, None),
        ("step", "Dựng DESIGN.md", None, 3),
        ("info", "Dựng DESIGN.md cho Acme Cloud (ngôn ngữ: en).", None, None),
        ("success", f"Đã ghi {folder / DESIGN_FILE}", None, None),
        ("step", "Xuất tokens.json và preview.html", None, 4),
        ("success", f"Đã ghi {folder / TOKENS_FILE}", None, None),
        ("success", f"Đã ghi {folder / PREVIEW_FILE}", None, None),
    ]
    return steps


@dataclass
class FakeJob:
    snapshot: JobSnapshot
    request: JobRequest
    events: list[JobEvent] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


@dataclass
class FakeState:
    """What the fake server remembers; tests read it to check what the page did."""

    settings: UiSettings
    defaults: UiSettings
    jobs: dict[str, FakeJob] = field(default_factory=dict)
    current_job: str | None = None
    heartbeats: int = 0
    shutdown_requests: list[bool] = field(default_factory=list)
    opened_folders: list[str] = field(default_factory=list)
    settings_saves: int = 0
    #: Flip to False to show the "no browser" path (status, install button, errors).
    browsers_available: bool = True
    install: InstallStatus = field(default_factory=InstallStatus)
    chromium_installed: bool = False
    requests: list[tuple[str, str]] = field(default_factory=list)


class FakeApi:
    """The fake server. Use as a context manager or call start()/stop()."""

    def __init__(
        self,
        output_root: Path,
        *,
        static_dir: Path | None = None,
        token: str = FAKE_TOKEN,
        event_delay_s: float = 0.05,
        mode: UiMode = "browser",
    ) -> None:
        self.output_root = output_root.resolve()
        self.static_dir = (static_dir or resource_path("ui", "static")).resolve()
        self.token = token
        self.event_delay_s = event_delay_s
        self._initial_delay_s = event_delay_s
        #: Site folders present at start; reset() removes the ones jobs added later.
        self._base_sites = {path.name for path in self.output_root.iterdir() if path.is_dir()}
        self.mode: UiMode = mode
        self.lock = threading.RLock()
        self.state = self._fresh_state()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # lifecycle -----------------------------------------------------------------

    def _fresh_state(self) -> FakeState:
        defaults = UiSettings(output_dir=self.output_root)
        return FakeState(settings=defaults.model_copy(), defaults=defaults)

    def reset(self) -> None:
        """Forget jobs, settings and counters, restore the event pace and remove the result
        folders that jobs created (between tests)."""
        with self.lock:
            threads = [job.thread for job in self.state.jobs.values() if job.thread is not None]
            for job in self.state.jobs.values():
                job.cancel.set()
        for thread in threads:
            thread.join(timeout=5)
        with self.lock:
            self.state = self._fresh_state()
            self.event_delay_s = self._initial_delay_s
            for path in self.output_root.iterdir():
                if path.is_dir() and path.name not in self._base_sites:
                    shutil.rmtree(path, ignore_errors=True)

    def start(self) -> FakeApi:
        handler = _handler_class(self)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        with self.lock:
            for job in self.state.jobs.values():
                job.cancel.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> FakeApi:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    @property
    def port(self) -> int:
        assert self._server is not None, "FakeApi.start() first"
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    @property
    def launch_url(self) -> str:
        return f"{self.url}#token={self.token}"

    # jobs ---------------------------------------------------------------------

    def busy_job(self) -> str | None:
        with self.lock:
            job_id = self.state.current_job
            if job_id is None:
                return None
            job = self.state.jobs[job_id]
            return None if job.snapshot.finished else job_id

    def start_job(self, request: JobRequest) -> JobSnapshot:
        normalize_url(request.url)  # InvalidUrlError -> 422
        with self.lock:
            settings = request.apply_to(self.state.settings).model_copy(
                update={"output_dir": self.output_root}
            )
            job_id = f"{len(self.state.jobs) + 1:012x}"
            snapshot = JobSnapshot(
                id=job_id,
                url=request.url,
                status="running",
                created_at=datetime.now(UTC),
                started_at=datetime.now(UTC),
                total=PIPELINE_STEPS,
                settings=settings,
                site=domain_of(request.url),
            )
            job = FakeJob(snapshot=snapshot, request=request)
            self.state.jobs[job_id] = job
            self.state.current_job = job_id
            job.thread = threading.Thread(target=self._run_job, args=(job,), daemon=True)
            job.thread.start()
            return snapshot.model_copy()

    def _emit(
        self, job: FakeJob, kind: EventKind, message: str, hint: str | None, step: int | None
    ) -> None:
        with self.lock:
            event = JobEvent(
                seq=len(job.events) + 1,
                kind=kind,
                message=message,
                hint=hint,
                step=step,
                total=PIPELINE_STEPS if step is not None else None,
                at=datetime.now(UTC),
            )
            job.events.append(event)
            update: dict[str, Any] = {"last_seq": event.seq}
            if step is not None:
                update.update(step=step, step_message=message)
            job.snapshot = job.snapshot.model_copy(update=update)

    def _finish(
        self, job: FakeJob, status: JobStatus, error: JobError | None, files: list[str]
    ) -> None:
        with self.lock:
            job.snapshot = job.snapshot.model_copy(
                update={
                    "status": status,
                    "finished_at": datetime.now(UTC),
                    "error": error,
                    "files": files,
                }
            )

    def _script_for(self, job: FakeJob) -> tuple[list[Step], GdmError | None]:
        url = job.request.url.lower()
        site = job.snapshot.site
        if "slow" in url:
            slow: list[Step] = [("step", "Thu thập dữ liệu trang", None, 1)]
            slow += [
                ("progress", f"Đang thu thập — chờ trang… {i}s", None, None) for i in range(300)
            ]
            return slow, None
        first: list[Step] = [
            ("step", "Thu thập dữ liệu trang", None, 1),
            ("success", "robots.txt cho phép truy cập /.", None, None),
            ("progress", "Đang thu thập — Mở trình duyệt…", None, None),
        ]
        if "no-browser" in url:
            return first, BrowserNotInstalledError()
        if "fail" in url:
            return first, BotBlockedError(
                f"Trang {normalize_url(job.request.url)} chặn trình duyệt tự động "
                "(trang kiểm tra bot/captcha, HTTP 403).",
                hint=(
                    "Công cụ không vượt qua cơ chế chống bot. Hãy thử lại sau, "
                    "hoặc chọn một trang công khai khác của site."
                ),
            )
        return _success_script(self.output_root, site, "robots" in url), None

    def _run_job(self, job: FakeJob) -> None:
        script, failure = self._script_for(job)
        delay = self.event_delay_s * (4 if "slow" in job.request.url.lower() else 1)
        for kind, message, hint, step in script:
            if job.cancel.wait(delay):
                cancelled = JobCancelledError()
                self._emit(job, "warning", cancelled.message, cancelled.hint, None)
                self._finish(job, "cancelled", job_error(cancelled), [])
                return
            self._emit(job, kind, message, hint, step)
        if failure is not None:
            error = job_error(failure)
            self._emit(job, "error", error.message, error.hint, None)
            self._finish(job, "failed", error, [])
            return
        folder = self.output_root / job.snapshot.site
        if not folder.exists():
            shutil.copytree(self.output_root / ACME_SITE, folder)
        self._emit(job, "success", FINAL_SUCCESS_MESSAGE, None, None)
        files = [
            name
            for name in (
                DESIGN_FILE,
                PREVIEW_FILE,
                TOKENS_FILE,
                PROMPT_FILE,
                ANALYSIS_FILE,
                SYNTHESIS_FILE,
                RAW_FILE,
            )
            if (folder / name).is_file()
        ]
        self._finish(job, "succeeded", None, files)

    def job_view(self, job_id: str, since: int) -> JobSnapshot | None:
        with self.lock:
            job = self.state.jobs.get(job_id)
            if job is None:
                return None
            events = [event for event in job.events if event.seq > since][:500]
            return job.snapshot.model_copy(update={"events": events})

    def cancel_job(self, job_id: str) -> tuple[JobSnapshot, bool] | None:
        with self.lock:
            job = self.state.jobs.get(job_id)
            if job is None:
                return None
            if job.snapshot.finished:
                return job.snapshot.model_copy(), False
            job.cancel.set()
            job.snapshot = job.snapshot.model_copy(update={"cancel_requested": True})
            return job.snapshot.model_copy(), True

    def wait_job(self, job_id: str, timeout: float = 10.0) -> JobSnapshot:
        """Block until a job finished (tests)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            view = self.job_view(job_id, 0)
            if view is not None and view.finished:
                return view
            time.sleep(0.02)
        raise TimeoutError(job_id)

    # browsers -----------------------------------------------------------------

    def browser_status(self) -> BrowserStatusResponse:
        with self.lock:
            available = {
                "chromium": self.state.chromium_installed,
                "msedge": self.state.browsers_available,
                "chrome": False,
            }
            infos = [
                BrowserInfo(
                    name=name,
                    label=BROWSER_LABELS[name],
                    available=available[name],
                    version=(FAKE_EDGE_VERSION if name == "msedge" else "141.0.7390.37")
                    if available[name]
                    else None,
                    path=f"C:/fake/{name}.exe" if available[name] else None,
                    detail=None
                    if available[name]
                    else (
                        "Chưa tải Chromium cho Playwright."
                        if name == "chromium"
                        else f"Không tìm thấy {BROWSER_LABELS[name]} trên máy."
                    ),
                )
                for name in AUTO_ORDER
            ]
            auto = next((info.name for info in infos if info.available), None)
            return BrowserStatusResponse(browsers=infos, auto=auto, install=self.state.install)

    def start_install(self) -> tuple[InstallStatus, bool]:
        with self.lock:
            if self.state.install.state == "running":
                return self.state.install, False
            self.state.install = InstallStatus(
                state="running",
                percent=0.0,
                message="Đang tải Chromium… 0%",
                started_at=datetime.now(UTC),
            )
        threading.Thread(target=self._run_install, daemon=True).start()
        return self.state.install, True

    def _run_install(self) -> None:
        for percent in (10, 25, 50, 75, 100):
            time.sleep(self.event_delay_s * 2)
            with self.lock:
                line = f"|{'■' * (percent // 10):<10}| {percent:3d}% of 150.2 MiB"
                tail = [*self.state.install.log_tail, line][-20:]
                self.state.install = self.state.install.model_copy(
                    update={
                        "percent": float(percent),
                        "message": f"Đang tải Chromium… {percent}%",
                        "log_tail": tail,
                    }
                )
        with self.lock:
            self.state.chromium_installed = True
            self.state.install = self.state.install.model_copy(
                update={
                    "state": "succeeded",
                    "message": "Đã tải xong Chromium.",
                    "finished_at": datetime.now(UTC),
                }
            )

    # settings -----------------------------------------------------------------

    def settings_view(self) -> SettingsResponse:
        with self.lock:
            return SettingsResponse(
                settings=self.state.settings,
                defaults=self.state.defaults,
                output_root=str(self.output_root),
                output_root_locked=False,
                config_path=str(self.output_root.parent / "ui-settings.json"),
                browsers=list(BROWSER_CHOICES),
                limits=SettingsLimits(
                    pages=(MIN_PAGES, MAX_PAGES),
                    max_elements=(MIN_MAX_ELEMENTS, MAX_MAX_ELEMENTS),
                    timeout_s=(MIN_TIMEOUT_S, MAX_TIMEOUT_S),
                    delta_e=(MIN_DELTA_E, MAX_DELTA_E),
                ),
            )

    def update_settings(self, changes: dict[str, Any]) -> SettingsResponse:
        """Raises ValidationError for bad values (-> 422)."""
        with self.lock:
            merged = {**self.state.settings.model_dump(), **changes}
            settings = UiSettings.model_validate(merged)
            self.state.settings = settings
            self.state.settings_saves += 1
        return self.settings_view()


# ─── HTTP handler ────────────────────────────────────────────────────────────


class _HttpError(Exception):
    def __init__(
        self,
        status: int,
        code: ApiErrorCode,
        message: str,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error = ApiError(code=code, message=message, hint=hint, details=details)


def _not_found() -> _HttpError:
    return _HttpError(404, "not_found", "Không tìm thấy địa chỉ này.")


def _safe_child(base: Path, relative: str) -> Path | None:
    """`base`/`relative` when every segment is acceptable and the real path stays inside."""
    segments = relative.split("/")
    for segment in segments:
        if (
            not segment
            or segment in (".", "..")
            or segment.startswith(".")
            or any(char in segment for char in ("\\", ":", "\x00"))
        ):
            return None
    candidate = base.joinpath(*segments)
    try:
        real = candidate.resolve(strict=True)
        real.relative_to(base.resolve(strict=True))
    except (OSError, ValueError):
        return None
    return real if real.is_file() else None


def _handler_class(api: FakeApi) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "gdm-fake-ui"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            return

        # plumbing ---------------------------------------------------------------

        def _common_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")

        def _send_json(self, status: int, model: BaseModel) -> None:
            body = model.model_dump_json().encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._common_headers()
            self.end_headers()
            self.wfile.write(body)

        def _drain_unread_body(self) -> None:
            """Read a body the handler did not consume (bounded), so closing the connection
            does not reset it before the client has read the error (Windows sends RST)."""
            if getattr(self, "_body_consumed", True):
                return
            self._body_consumed = True
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._discard_pending()
                return
            if 0 < length <= MAX_DRAIN_BYTES:
                with contextlib.suppress(OSError):
                    self.rfile.read(length)

        def _discard_pending(self) -> None:
            """Read what the client already sent of a body of unknown length (a bad
            Content-Length), waiting at most PENDING_BODY_WAIT_S, as the real server does: the
            unread bytes would otherwise make Windows reset the connection before the client
            has read the error."""
            self._body_consumed = True
            read1 = getattr(self.rfile, "read1", None)
            if read1 is None:
                return
            try:
                self.connection.settimeout(PENDING_BODY_WAIT_S)
                budget = MAX_DRAIN_BYTES
                while budget > 0:
                    chunk = read1(min(budget, 65536))
                    if not chunk:
                        break
                    budget -= len(chunk)
            except (OSError, ValueError):
                pass
            finally:
                with contextlib.suppress(OSError):
                    self.connection.settimeout(self.timeout)

        def _send_error(self, error: _HttpError) -> None:
            self._drain_unread_body()
            self._send_json(error.status, ErrorResponse(error=error.error))

        def _send_file(self, path: Path, *, csp: str | None, download: bool) -> None:
            data = path.read_bytes()
            content_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            if csp:
                self.send_header("Content-Security-Policy", csp)
            if download or content_type == "application/octet-stream":
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self._common_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _check_host(self) -> None:
            allowed = {f"127.0.0.1:{api.port}", f"localhost:{api.port}"}
            if self.headers.get("Host", "") not in allowed:
                raise _HttpError(403, "bad_host", "Địa chỉ máy chủ không hợp lệ.")

        def _check_origin(self) -> None:
            origin = self.headers.get("Origin")
            allowed = {f"http://127.0.0.1:{api.port}", f"http://localhost:{api.port}"}
            if origin is not None and origin not in allowed:
                raise _HttpError(403, "bad_origin", "Yêu cầu đến từ một trang khác.")

        def _check_token(self) -> None:
            if self.headers.get(TOKEN_HEADER) != api.token:
                raise _HttpError(
                    403,
                    "forbidden",
                    "Phiên giao diện không hợp lệ.",
                    hint="Mở lại giao diện bằng địa chỉ mà gdm ui in ra.",
                )

        def _body(self) -> dict[str, Any]:
            self._body_consumed = True
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                self._discard_pending()
                self.close_connection = True
                raise _HttpError(400, "bad_json", "Content-Length không hợp lệ.")
            if length > MAX_BODY_BYTES:
                # Drain (bounded) so the client reads the 413 instead of a connection reset.
                self.rfile.read(min(length, MAX_DRAIN_BYTES))
                self.close_connection = True
                raise _HttpError(413, "too_large", "Dữ liệu gửi lên quá lớn.")
            raw = self.rfile.read(length) if length else b""
            if not raw.strip():
                return {}
            content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if content_type != "application/json":
                raise _HttpError(415, "unsupported_media_type", "Cần dữ liệu JSON.")
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise _HttpError(400, "bad_json", "Dữ liệu JSON không hợp lệ.") from None
            if not isinstance(data, dict):
                raise _HttpError(400, "bad_json", "Cần một đối tượng JSON.")
            return data

        def _route(self, handler: Callable[[], None]) -> None:
            with api.lock:
                api.state.requests.append((self.command, self.path))
            self._body_consumed = self.command not in ("POST", "PUT")
            try:
                self._check_host()
                handler()
            except _HttpError as error:
                self._send_error(error)
            except ValidationError as error:
                first = error.errors()[0]
                field_name = ".".join(str(part) for part in first["loc"])
                self._send_error(
                    _HttpError(
                        422,
                        "invalid",
                        f"Giá trị không hợp lệ ở '{field_name}': {describe_error(first)}.",
                        details={"field": field_name},
                    )
                )
            except InvalidUrlError as error:
                self._send_error(_HttpError(422, "invalid_url", error.message, error.hint))

        # verbs ------------------------------------------------------------------

        def do_OPTIONS(self) -> None:
            self._send_error(
                _HttpError(405, "method_not_allowed", "Phương thức không được hỗ trợ.")
            )

        def do_GET(self) -> None:
            self._route(self._get)

        def do_HEAD(self) -> None:
            self._route(self._head)

        def do_POST(self) -> None:
            self._route(self._post)

        def do_PUT(self) -> None:
            self._route(self._put)

        def do_DELETE(self) -> None:
            self._route(self._method_not_allowed)

        def _method_not_allowed(self) -> None:
            raise _HttpError(405, "method_not_allowed", "Phương thức không được hỗ trợ.")

        def _head(self) -> None:
            # HEAD serves pages and files only; the JSON API answers GET.
            if urlsplit(self.path).path.startswith("/api/"):
                self._check_token()
                self._method_not_allowed()
            self._get()

        # GET --------------------------------------------------------------------

        def _get(self) -> None:
            parts = urlsplit(self.path)
            path = unquote(parts.path)
            query = parse_qs(parts.query)
            if path == "/":
                self._send_file(api.static_dir / "index.html", csp=INDEX_CSP, download=False)
                return
            if path.startswith("/static/"):
                found = _safe_child(api.static_dir, path.removeprefix("/static/"))
                if found is None:
                    raise _not_found()
                self._send_file(found, csp=None, download=False)
                return
            if path.startswith("/files/"):
                site, _, relative = path.removeprefix("/files/").partition("/")
                folder = api.output_root / site
                found = (
                    _safe_child(folder, relative)
                    if is_valid_site_name(site) and folder.is_dir()
                    else None
                )
                if found is None:
                    raise _not_found()
                csp = FILES_HTML_CSP if found.suffix.lower() == ".html" else None
                self._send_file(found, csp=csp, download=query.get("download") == ["1"])
                return
            if not path.startswith("/api/"):
                raise _not_found()
            self._check_token()
            raw_since = (query.get("since") or [""])[-1].strip()
            try:
                since = max(int(raw_since or 0), 0)
            except ValueError:
                raise _HttpError(
                    422,
                    "invalid",
                    "Giá trị không hợp lệ ở 'since': cần một số nguyên.",
                    details={"field": "since"},
                ) from None
            if path == "/api/health":
                busy = api.busy_job()
                self._send_json(
                    200,
                    HealthResponse(
                        version=__version__,
                        mode=api.mode,
                        output_root=str(api.output_root),
                        busy=busy is not None,
                        job_id=busy,
                    ),
                )
            elif path == "/api/settings":
                self._send_json(200, api.settings_view())
            elif path == "/api/history":
                entries = [
                    entry
                    for folder in sorted(api.output_root.iterdir())
                    if folder.is_dir() and (entry := history_entry(folder)) is not None
                ]
                entries.sort(
                    key=lambda entry: entry.analyzed_at or datetime.min.replace(tzinfo=UTC),
                    reverse=True,
                )
                self._send_json(
                    200, HistoryResponse(output_root=str(api.output_root), entries=entries)
                )
            elif path == "/api/jobs/current":
                with api.lock:
                    job_id = api.state.current_job
                view = api.job_view(job_id, since) if job_id else None
                self._send_json(200, CurrentJobResponse(job=view))
            elif match := _JOB_PATH_RE.fullmatch(path):
                if match.group("cancel"):
                    raise _HttpError(405, "method_not_allowed", "Phương thức không được hỗ trợ.")
                view = api.job_view(match.group("id"), since)
                if view is None:
                    raise _HttpError(404, "job_not_found", "Không tìm thấy lượt phân tích này.")
                self._send_json(200, JobResponse(job=view))
            elif match := _RESULTS_PATH_RE.fullmatch(path):
                site = match.group("site")
                results = (
                    results_for(api.output_root / site)
                    if is_valid_site_name(site) and (api.output_root / site).is_dir()
                    else None
                )
                if results is None:
                    raise _HttpError(404, "site_not_found", f"Không có kết quả cho '{site}'.")
                self._send_json(200, results)
            elif path == "/api/browser/status":
                self._send_json(200, api.browser_status())
            elif path == "/api/browser/install":
                self._send_json(200, InstallResponse(install=api.state.install))
            else:
                raise _not_found()

        # POST / PUT -------------------------------------------------------------

        def _post(self) -> None:
            path = unquote(urlsplit(self.path).path)
            if not path.startswith("/api/"):
                raise _HttpError(405, "method_not_allowed", "Phương thức không được hỗ trợ.")
            self._check_origin()
            self._check_token()
            body = self._body()
            if path == "/api/jobs":
                request = JobRequest.model_validate(body)
                busy = api.busy_job()
                if busy is not None:
                    raise _HttpError(
                        409,
                        "busy",
                        "Đang có một lượt phân tích chạy.",
                        hint="Chờ lượt hiện tại xong hoặc bấm Hủy rồi thử lại.",
                        details={"job_id": busy},
                    )
                self._send_json(202, JobResponse(job=api.start_job(request)))
            elif match := _JOB_PATH_RE.fullmatch(path):
                if not match.group("cancel"):
                    raise _HttpError(405, "method_not_allowed", "Phương thức không được hỗ trợ.")
                outcome = api.cancel_job(match.group("id"))
                if outcome is None:
                    raise _HttpError(404, "job_not_found", "Không tìm thấy lượt phân tích này.")
                snapshot, accepted = outcome
                self._send_json(202 if accepted else 200, JobResponse(job=snapshot))
            elif path == "/api/open-folder":
                request_folder = OpenFolderRequest.model_validate(body)
                if request_folder.site is None:
                    folder = api.output_root
                elif (
                    is_valid_site_name(request_folder.site)
                    and (api.output_root / request_folder.site).is_dir()
                ):
                    folder = api.output_root / request_folder.site
                else:
                    raise _HttpError(
                        404, "site_not_found", f"Không có kết quả cho '{request_folder.site}'."
                    )
                with api.lock:
                    api.state.opened_folders.append(str(folder))
                self._send_json(200, OpenFolderResponse(folder=str(folder)))
            elif path == "/api/browser/install":
                status, started = api.start_install()
                if not started:
                    raise _HttpError(
                        409,
                        "install_running",
                        "Đang tải Chromium.",
                        hint="Chờ lượt tải hiện tại xong.",
                        details={"install": status.model_dump(mode="json")},
                    )
                self._send_json(202, InstallResponse(install=status))
            elif path == "/api/heartbeat":
                with api.lock:
                    api.state.heartbeats += 1
                idle = 120.0 if api.mode == "browser" else None
                self._send_json(200, HeartbeatResponse(idle_shutdown_s=idle))
            elif path == "/api/shutdown":
                request_shutdown = ShutdownRequest.model_validate(body)
                busy = api.busy_job()
                if busy is not None and not request_shutdown.force:
                    raise _HttpError(
                        409,
                        "busy",
                        "Đang có một lượt phân tích chạy.",
                        hint="Hủy lượt phân tích trước khi tắt.",
                        details={"job_id": busy},
                    )
                if busy is not None:
                    api.cancel_job(busy)
                with api.lock:
                    api.state.shutdown_requests.append(request_shutdown.force)
                self._send_json(202, ShutdownResponse())
            else:
                raise _not_found()

        def _put(self) -> None:
            path = unquote(urlsplit(self.path).path)
            if path != "/api/settings":
                if path.startswith("/api/"):
                    self._check_token()
                raise _HttpError(405, "method_not_allowed", "Phương thức không được hỗ trợ.")
            self._check_origin()
            self._check_token()
            body = self._body()
            unknown = sorted(set(body) - set(UiSettings.model_fields))
            if unknown:
                raise _HttpError(
                    422,
                    "invalid",
                    f"Không có tùy chọn '{unknown[0]}'.",
                    details={"field": unknown[0]},
                )
            self._send_json(200, api.update_settings(body))

    return Handler


def serve_fake_api(output_root: Path, **kwargs: Any) -> Iterator[FakeApi]:
    """Generator for fixtures: build the output, start the server, stop it afterwards."""
    if not (output_root / ACME_SITE / DESIGN_FILE).is_file():
        build_fake_output(output_root)
    api = FakeApi(output_root, **kwargs).start()
    try:
        yield api
    finally:
        api.stop()
