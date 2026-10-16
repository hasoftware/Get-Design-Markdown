"""HTTP server of the local UI. This docstring is the API contract (frozen).

Frozen interface: this contract, the constants, the response models and the UiServer signature.

Transport
=========
- stdlib `http.server.ThreadingHTTPServer`, bound to HOST = 127.0.0.1 only (never 0.0.0.0 or
  "localhost"), port from run_ui (0 = a free port chosen by the OS).
- JSON in and out, UTF-8. Every JSON response has `Content-Type: application/json;
  charset=utf-8`; every response has `Cache-Control: no-store` (no-cache for static files),
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and
  `Cross-Origin-Resource-Policy: same-origin`.
- Request bodies: only for POST/PUT, at most MAX_BODY_BYTES (else 413 "too_large", after
  draining at most MAX_DRAIN_BYTES and with the connection closed),
  `Content-Type: application/json` (else 415 "unsupported_media_type"), a JSON object (else
  400 "bad_json"). An empty body counts as {}.
- Timestamps are ISO 8601 UTC strings; paths in responses are absolute OS paths (strings).

Security
========
1. Token. UiServer creates a random token per session (secrets.token_urlsafe(32)). The page is
   opened at `http://127.0.0.1:<port>/#token=<token>` (launch_url): a fragment never reaches
   the server, logs or Referer. The frontend reads it, keeps it in sessionStorage, removes it
   from the address bar (history.replaceState) and sends it as the header
   `X-GDM-Token: <token>` on EVERY /api request; a missing or wrong token -> 403 "forbidden"
   (compared with hmac.compare_digest). A custom header forces a CORS preflight, which is
   refused (point 3), so no other web page can call the API.
2. Host. Every request (also /, /static, /files) must carry `Host: 127.0.0.1:<port>` or
   `Host: localhost:<port>`, else 403 "bad_host" (DNS rebinding). For POST/PUT an `Origin`
   header, when present, must be `http://127.0.0.1:<port>` or `http://localhost:<port>`
   (else 403 "bad_origin").
3. CORS. No Access-Control-* header is ever sent. OPTIONS on any path -> 405
   "method_not_allowed" without CORS headers.
4. Files. /files/{site}/{path} only serves regular files inside <output root>/<site>/:
   `site` must pass history.is_valid_site_name; each path segment must be non-empty, not "." or
   "..", not start with ".", and contain no "\\", ":" or NUL; the resolved real path (symlinks
   followed) must stay inside the resolved site folder. Anything else -> 404 "not_found"
   (never a hint about what exists). No directory listings. Same rules for /static/{path}
   inside ui/static.
5. Content Security Policy. GET / (index.html) is sent with INDEX_CSP. HTML files under /files
   (preview.html) are sent with FILES_HTML_CSP, which sandboxes them even when opened
   directly. The frontend shows preview.html in `<iframe sandbox="allow-scripts">` (never
   allow-same-origin) and rendered Markdown in `<iframe sandbox srcdoc=...>` (no scripts), so
   site-derived content can neither run in the UI's origin nor read the token.
6. Errors never include stack traces; unexpected exceptions -> 500 "internal" with a
   Vietnamese message, the traceback goes to the log.

Error envelope (every 4xx/5xx)
==============================
    {"error": {"code": "<code>", "message": "<Vietnamese>", "hint": "<Vietnamese>" | null,
               "details": {...} | null}}
Codes: forbidden, bad_host, bad_origin, not_found, method_not_allowed, too_large,
unsupported_media_type, bad_json, invalid (422; details = {"field": "<dotted path>"} of the
first validation error, message from storage.describe_error), invalid_url (422), busy (409;
details = {"job_id": "..."}), job_not_found (404), site_not_found (404), install_running (409),
unsupported (501), internal (500).

Endpoints (all /api/* require X-GDM-Token)
==========================================
GET  /api/health
     200 HealthResponse
         {"ok": true, "app": "get-design-markdown", "version": "0.1.0", "api": 1,
          "mode": "browser" | "window" | "none", "output_root": "...", "busy": false,
          "job_id": null | "<id of the queued/running job>"}

GET  /api/settings
     200 SettingsResponse
         {"settings": UiSettings, "defaults": UiSettings, "output_root": "...",
          "output_root_locked": bool, "config_path": ".../ui-settings.json",
          "browsers": ["auto", "chromium", "msedge", "chrome"],
          "limits": {"pages": [0, 10], "max_elements": [100, 20000], "timeout_s": [5, 600],
                     "delta_e": [0.5, 20]}}
     `output_root` is where jobs write and history reads: the `--out` given to `gdm ui` when
     there was one (then output_root_locked = true and changing output_dir in the settings
     does not move it for this session), else settings.output_dir.
PUT  /api/settings
     body: a partial UiSettings object (any subset of its fields; unknown field -> 422
     "invalid"); merged over the current settings, validated, saved with save_ui_settings.
     output_dir is expanded (~) and made absolute; it must not be an existing file (422
     "invalid", field "output_dir"); the folder is created only when a job writes to it.
     200 SettingsResponse (after the change). Allowed while a job runs (the job keeps its
     settings).

POST /api/jobs
     body: JobRequest {"url": "stripe.com", "pages": 2, "dark": true, ...}; unset fields use
     the saved settings. URL checked with storage.normalize_url -> 422 "invalid_url" with its
     message/hint.
     202 JobResponse {"job": JobSnapshot}   (status "queued" or "running", no events)
     409 "busy" when a job is queued or running (details.job_id).
GET  /api/jobs/current?since=N
     200 CurrentJobResponse {"job": JobSnapshot | null} — the queued/running job, else the
     last finished one (lets a reloaded page resume), else null.
GET  /api/jobs/{id}?since=N
     200 JobResponse; job.events = events with seq > N (N defaults to 0, at most
     jobs.MAX_EVENTS_PER_RESPONSE per call, oldest first); job.last_seq = newest seq.
     The frontend polls every POLL_INTERVAL_MS with since = the last seq it received until
     job.status is succeeded / failed / cancelled. 404 "job_not_found".
POST /api/jobs/{id}/cancel
     202 JobResponse (cancel_requested = true) while queued/running; 200 JobResponse
     unchanged when the job already finished; 404 "job_not_found".

GET  /api/history
     200 HistoryResponse {"output_root": "...", "entries": [HistoryEntry, ...]} newest first.
GET  /api/results/{site}
     200 ResultsResponse:
         {"site": "stripe.com", "entry": HistoryEntry, "folder": "<absolute path>",
          "files": [ResultFile...],          # every regular file of the site folder and of
                                             # screenshots/ and prompt-images/, sorted by path
          "design": RenderedDocument | null, # DESIGN.md (render_md.render_markdown)
          "prompt": RenderedDocument | null, # prompt.md
          "preview_url": "/files/stripe.com/preview.html" | null,
          "screenshots": [ScreenshotInfo...],  # from analysis.json screenshots whose file
                                               # exists, else screenshots/*.png by name
          "prompt_images": ["/files/stripe.com/prompt-images/desktop.png", ...],
          "token_counts": {"colors": 10, ...} | null,   # synthesis.json token_counts
          "warnings": ["..."]}                           # analysis.json warnings
     URLs are root-relative and percent-encoded. 404 "site_not_found" for an invalid or
     missing site.

POST /api/open-folder
     body: {"site": "stripe.com"} or {} / {"site": null} for the output root.
     Opens the folder in the OS file manager (os.startfile / open / xdg-open), creating the
     output root if needed. 200 {"ok": true, "folder": "..."}; 404 "site_not_found";
     501 "unsupported" when no file manager can be started.

GET  /api/browser/status
     200 BrowserStatusResponse {"browsers": [BrowserInfo x3], "auto": "chromium" | "msedge" |
     "chrome" | null, "install": InstallStatus}   (browser_install.browser_status()).
POST /api/browser/install
     Starts browser_install.BrowserInstaller (Playwright's Chromium, a few minutes).
     202 InstallResponse {"install": InstallStatus} when started; 409 "install_running"
     (details.install = InstallStatus) when one already runs.
GET  /api/browser/install
     200 InstallResponse — polled by the frontend every second while state == "running".

POST /api/heartbeat
     200 HeartbeatResponse {"ok": true, "idle_shutdown_s": 120 | null}. The page sends it on
     load, every HEARTBEAT_INTERVAL_S and when it becomes visible again.
POST /api/shutdown
     body: {"force": false}. 409 "busy" while a job runs unless force (then the job is
     cancelled first). 202 {"ok": true}; the server stops right after the response.

GET  /files/{site}/{path}
     The file (see Security 4). Content types: .html text/html; .md text/markdown;
     .json application/json; .png/.jpg/.jpeg/.webp images; .txt text/plain; .css text/css;
     others application/octet-stream as an attachment. All text types carry charset=utf-8.
     `?download=1` adds `Content-Disposition: attachment; filename="<name>"`. GET and HEAD.
     No token (iframes and <img> cannot send headers); the Host check still applies.
GET  /                 ui/static/index.html (with INDEX_CSP).
GET  /static/{path}    files of ui/static (the frontend references /static/app.js,
                       /static/app.css...). GET and HEAD. No token.

Lifetime
========
- mode "browser" (`gdm ui`): the server stops after IDLE_SHUTDOWN_S without a heartbeat while
  no job and no Chromium install is running (the clock starts when the server starts).
- mode "window" (exe, `gdm ui --window`): stops when the window closes (app.py calls stop()).
- mode "none" (`gdm ui --no-open`): no idle shutdown; Ctrl+C stops it.
- POST /api/shutdown stops any mode. stop() cancels a running job and waits for it (up to
  10 s) before closing the socket.
"""

from __future__ import annotations

import contextlib
import errno
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, Literal, cast
from urllib.parse import parse_qs, quote, unquote, urlsplit

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from get_design_markdown import __version__
from get_design_markdown.errors import (
    GdmError,
    InvalidUrlError,
    NotImplementedYetError,
)
from get_design_markdown.models.base import GdmModel
from get_design_markdown.models.raw import BROWSER_CHOICES, Screenshot
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
    normalize_url,
)
from get_design_markdown.ui import browser_install
from get_design_markdown.ui.browser_install import (
    BrowserInfo,
    BrowserInstaller,
    BrowserName,
    InstallStatus,
)
from get_design_markdown.ui.history import (
    HistoryEntry,
    is_valid_site_name,
    list_history,
    read_entry,
)
from get_design_markdown.ui.jobs import (
    JOB_ID_RE,
    JobBusyError,
    JobManager,
    JobNotFoundError,
    JobRequest,
    JobSnapshot,
)
from get_design_markdown.ui.render_md import render_markdown
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
    absolute_output_dir,
    load_ui_settings,
    save_ui_settings,
)
from get_design_markdown.ui.settings import settings_path as default_settings_path

API_VERSION = 1
APP_NAME = "get-design-markdown"
HOST = "127.0.0.1"
TOKEN_HEADER = "X-GDM-Token"
#: Fragment parameter carrying the token in launch_url: "#token=<token>".
TOKEN_FRAGMENT = "token"
MAX_BODY_BYTES = 64 * 1024
#: A body over MAX_BODY_BYTES is read and discarded up to this size before the 413 is sent
#: (and the connection closed), so the client gets the answer instead of a reset.
MAX_DRAIN_BYTES = 1024 * 1024
IDLE_SHUTDOWN_S = 120.0
HEARTBEAT_INTERVAL_S = 30.0
#: How often the frontend polls a running job / a running install (milliseconds).
POLL_INTERVAL_MS = 500
INSTALL_POLL_INTERVAL_MS = 1000

INDEX_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
#: preview.html has one inline script (theme toggle) and may load Google Fonts.
FILES_HTML_CSP = (
    "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com data:; img-src 'self' data:; base-uri 'none'; "
    "form-action 'none'"
)

UiMode = Literal["browser", "window", "none"]

ApiErrorCode = Literal[
    "forbidden",
    "bad_host",
    "bad_origin",
    "not_found",
    "method_not_allowed",
    "too_large",
    "unsupported_media_type",
    "bad_json",
    "invalid",
    "invalid_url",
    "busy",
    "job_not_found",
    "site_not_found",
    "install_running",
    "unsupported",
    "internal",
]


# ─── Response models ─────────────────────────────────────────────────────────


class ApiError(GdmModel):
    code: ApiErrorCode
    message: str
    hint: str | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(GdmModel):
    error: ApiError


class HealthResponse(GdmModel):
    ok: bool = True
    app: str = APP_NAME
    version: str
    api: int = API_VERSION
    mode: UiMode
    output_root: str
    busy: bool = False
    job_id: str | None = None


class SettingsLimits(GdmModel):
    """[min, max] of the numeric settings (UiSettings bounds)."""

    pages: tuple[int, int]
    max_elements: tuple[int, int]
    timeout_s: tuple[float, float]
    delta_e: tuple[float, float]


class SettingsResponse(GdmModel):
    settings: UiSettings
    defaults: UiSettings
    output_root: str
    output_root_locked: bool = False
    config_path: str
    browsers: list[str]
    limits: SettingsLimits


class JobResponse(GdmModel):
    job: JobSnapshot


class CurrentJobResponse(GdmModel):
    job: JobSnapshot | None = None


class HistoryResponse(GdmModel):
    output_root: str
    entries: list[HistoryEntry] = Field(default_factory=list)


ResultFileKind = Literal[
    "design", "preview", "tokens", "prompt", "analysis", "synthesis", "raw", "image", "other"
]


class ResultFile(GdmModel):
    #: File name ("DESIGN.md", "home-1440.png").
    name: str
    #: Path relative to the site folder, "/" separators ("screenshots/home-1440.png").
    path: str
    size: int
    #: "/files/<site>/<path>" (percent-encoded).
    url: str
    kind: ResultFileKind


class RenderedDocument(GdmModel):
    #: The file as written (UTF-8 text).
    markdown: str
    #: YAML front matter without the "---" lines, or None.
    front_matter: str | None = None
    #: HTML fragment of the body (render_md.render_markdown).
    html: str


class ScreenshotInfo(GdmModel):
    #: Page slug ("home", "pricing").
    page: str
    viewport_width: int
    color_scheme: Literal["light", "dark"] = "light"
    #: Relative to the site folder ("screenshots/home-1440.png").
    path: str
    url: str


class ResultsResponse(GdmModel):
    site: str
    entry: HistoryEntry
    folder: str
    files: list[ResultFile] = Field(default_factory=list)
    design: RenderedDocument | None = None
    prompt: RenderedDocument | None = None
    preview_url: str | None = None
    screenshots: list[ScreenshotInfo] = Field(default_factory=list)
    prompt_images: list[str] = Field(default_factory=list)
    token_counts: dict[str, int] | None = None
    warnings: list[str] = Field(default_factory=list)


class OpenFolderRequest(GdmModel):
    site: str | None = None


class OpenFolderResponse(GdmModel):
    ok: bool = True
    folder: str


class BrowserStatusResponse(GdmModel):
    browsers: list[BrowserInfo]
    auto: BrowserName | None = None
    install: InstallStatus


class InstallResponse(GdmModel):
    install: InstallStatus


class HeartbeatResponse(GdmModel):
    ok: bool = True
    #: Seconds without heartbeat before the server stops (None: never, window/none modes).
    idle_shutdown_s: float | None = None


class ShutdownRequest(GdmModel):
    force: bool = False


class ShutdownResponse(GdmModel):
    ok: bool = True


# ─── Server ──────────────────────────────────────────────────────────────────


class UiServer:
    """The local UI server (see the module docstring). Usable as a context manager:

        with UiServer(mode="none") as server:
            print(server.launch_url)
            server.wait()

    - `out_root`: output root for this session (the `--out` of `gdm ui`; locks it), None to use
      the saved settings' output_dir.
    - `settings_path`: ui-settings.json (None: settings.settings_path()).
    - `port`: 0 picks a free port; `token`: None generates one (tests may pass their own).
    - `jobs` / `installer`: injectable (tests pass a JobManager with a fake runner).
    - `idle_timeout_s`: seconds without heartbeat before the idle shutdown; only used in mode
      "browser"; None disables it (tests pass a small value).
    - `static_dir`: the frontend folder (None: resources.resource_path("ui", "static")).
    - `defaults`: the UiSettings defaults (None: UiSettings.defaults(), from the environment);
      the saved file is read with settings.load_ui_settings(settings_path, defaults).
    """

    def __init__(
        self,
        *,
        mode: UiMode = "browser",
        out_root: Path | None = None,
        settings_path: Path | None = None,
        port: int = 0,
        token: str | None = None,
        jobs: JobManager | None = None,
        installer: BrowserInstaller | None = None,
        idle_timeout_s: float | None = IDLE_SHUTDOWN_S,
        static_dir: Path | None = None,
        defaults: UiSettings | None = None,
    ) -> None:
        self.mode = mode
        self.requested_port = port
        self._out_root = out_root
        self._settings_path = settings_path
        self._token = token if token is not None else secrets.token_urlsafe(32)
        self._jobs = jobs if jobs is not None else JobManager()
        self._installer = installer if installer is not None else BrowserInstaller()
        self._idle_timeout_s = idle_timeout_s
        self._static_dir = static_dir
        self._defaults = defaults
        #: Guards the settings, the heartbeat clock and the lifecycle flags.
        self._lock = threading.RLock()
        #: One Chromium install request at a time (status check + start).
        self._install_lock = threading.Lock()
        self._settings: UiSettings | None = None
        self._locked_root = Path(out_root).expanduser().resolve() if out_root is not None else None
        self._httpd: _HttpServer | None = None
        self._port: int | None = None
        self._serve_thread: threading.Thread | None = None
        self._idle_thread: threading.Thread | None = None
        self._last_heartbeat = time.monotonic()
        self._started = False
        self._stop_started = False
        #: Set when stop() begins (ends the idle watcher).
        self._stopping = threading.Event()
        #: Set once the server is fully stopped (also before start(): nothing to wait for).
        self._stopped = threading.Event()
        self._stopped.set()

    # ─── properties ─────────────────────────────────────────────────────────

    @property
    def port(self) -> int:
        """The bound port (after start())."""
        if self._port is None:
            raise RuntimeError("UiServer.start() has not been called.")
        return self._port

    @property
    def token(self) -> str:
        return self._token

    @property
    def url(self) -> str:
        """ "http://127.0.0.1:<port>/" (after start())."""
        return f"http://{HOST}:{self.port}/"

    @property
    def launch_url(self) -> str:
        """url + "#token=<token>": what the browser tab or the window opens."""
        return f"{self.url}#{TOKEN_FRAGMENT}={self._token}"

    @property
    def output_root(self) -> Path:
        """Current output root (absolute)."""
        if self._locked_root is not None:
            return self._locked_root
        return absolute_output_dir(self.settings.output_dir)

    @property
    def output_root_locked(self) -> bool:
        return self._locked_root is not None

    @property
    def jobs(self) -> JobManager:
        return self._jobs

    @property
    def installer(self) -> BrowserInstaller:
        return self._installer

    @property
    def config_path(self) -> Path:
        """ui-settings.json of this session."""
        return self._settings_path if self._settings_path is not None else default_settings_path()

    @property
    def defaults(self) -> UiSettings:
        with self._lock:
            if self._defaults is None:
                self._defaults = UiSettings.defaults()
            return self._defaults

    @property
    def settings(self) -> UiSettings:
        """The saved settings (read from config_path on first use)."""
        with self._lock:
            if self._settings is None:
                self._settings = load_ui_settings(self.config_path, self.defaults)
            return self._settings

    @property
    def idle_shutdown_s(self) -> float | None:
        """Seconds without heartbeat before the server stops (None: never)."""
        return self._idle_timeout_s if self.mode == "browser" else None

    # ─── settings, heartbeat ────────────────────────────────────────────────

    def update_settings(self, changes: Mapping[str, Any]) -> UiSettings:
        """Merge `changes` over the saved settings, validate and save them; ValidationError for
        a bad value, ConfigError when the file cannot be written (nothing changes then)."""
        with self._lock:
            merged = UiSettings.model_validate({**self.settings.model_dump(), **changes})
            save_ui_settings(merged, self.config_path)
            self._settings = merged
            return merged

    def job_settings(self, request: JobRequest) -> UiSettings:
        """The settings a job runs with: saved settings, the request's overrides, and the
        session's output root (results always go where history and /files look)."""
        resolved = request.apply_to(self.settings)
        return resolved.model_copy(update={"output_dir": self.output_root})

    def heartbeat(self) -> None:
        with self._lock:
            self._last_heartbeat = time.monotonic()

    def start_install(self) -> tuple[InstallStatus, bool]:
        """Start the Chromium download unless one runs: (status, True when started)."""
        with self._install_lock:
            current = self._installer.status()
            if current.state == "running":
                return current, False
            return self._installer.start(), True

    # ─── lifecycle ──────────────────────────────────────────────────────────

    def static_dir(self) -> Path:
        """Folder of the frontend (index.html, /static files)."""
        if self._static_dir is not None:
            return self._static_dir
        try:
            return resource_path("ui", "static")
        except FileNotFoundError:
            raise GdmError(
                "Không tìm thấy các tệp của giao diện (thư mục ui/static).",
                hint="Bản cài đặt có thể bị thiếu dữ liệu; hãy cài lại get-design-markdown.",
            ) from None

    def start(self) -> None:
        """Bind 127.0.0.1:<port> and serve in a daemon thread; returns once listening.
        Raises GdmError (Vietnamese) when the port is taken."""
        with self._lock:
            if self._started and not self._stop_started:
                return
            # Settings, defaults and the frontend folder are checked before binding, so a
            # configuration problem is reported as such (GdmError) and not as a dead page.
            self._settings = self.settings
            self._static_dir = self.static_dir()
            httpd = _bind(self, self.requested_port)
            self._httpd = httpd
            self._port = httpd.bound_port
            self._started = True
            self._stop_started = False
            self._stopping.clear()
            self._stopped.clear()
            self._last_heartbeat = time.monotonic()
            self._serve_thread = threading.Thread(
                target=httpd.serve_forever,
                kwargs={"poll_interval": SERVE_POLL_INTERVAL_S},
                name="gdm-ui-server",
                daemon=True,
            )
            self._serve_thread.start()
            timeout = self.idle_shutdown_s
            if timeout is not None:
                self._idle_thread = threading.Thread(
                    target=self._watch_idle, args=(timeout,), name="gdm-ui-idle", daemon=True
                )
                self._idle_thread.start()
        logger.info("Giao diện chạy ở %s (chế độ %s).", self.url, self.mode)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the server stopped (idle shutdown, /api/shutdown or stop());
        False when `timeout` expired first."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            # Short slices keep Ctrl+C responsive on Windows, where an endless wait is not
            # interrupted.
            slice_s = WAIT_SLICE_S
            if deadline is not None:
                slice_s = min(slice_s, deadline - time.monotonic())
                if slice_s <= 0:
                    return self._stopped.is_set()
            if self._stopped.wait(slice_s):
                return True

    def stop(self) -> None:
        """Cancel a running job, stop serving and close the socket (idempotent)."""
        with self._lock:
            if not self._started or self._stop_started:
                already_stopping = self._started
                first = False
            else:
                self._stop_started = True
                first = True
        if not first:
            if already_stopping:
                self._stopped.wait(STOP_JOB_TIMEOUT_S + 5)
            return
        self._stopping.set()
        try:
            self._jobs.shutdown(timeout=STOP_JOB_TIMEOUT_S)
        except Exception:
            logger.exception("Không dừng được lượt phân tích khi tắt giao diện.")
        httpd, serve_thread = self._httpd, self._serve_thread
        if httpd is not None:
            if serve_thread is not None and serve_thread.is_alive():
                httpd.shutdown()
            httpd.server_close()
        current = threading.current_thread()
        for thread in (serve_thread, self._idle_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=5)
        self._stopped.set()
        logger.info("Đã tắt giao diện ở cổng %s.", self._port)

    def stop_soon(self) -> None:
        """stop() from another thread (a request handler answers first, then the server
        stops)."""
        threading.Thread(target=self.stop, name="gdm-ui-stop", daemon=True).start()

    def _idle_expired(self, timeout: float) -> bool:
        with self._lock:
            idle_for = time.monotonic() - self._last_heartbeat
        if idle_for < timeout or self._jobs.busy:
            return False
        return not _install_running(self._installer)

    def _watch_idle(self, timeout: float) -> None:
        interval = min(1.0, max(timeout / 4, 0.01))
        while not self._stopping.wait(interval):
            if self._idle_expired(timeout):
                logger.info(
                    "Không còn trang giao diện nào mở sau %.0f giây; tự tắt máy chủ.", timeout
                )
                self.stop()
                return

    def __enter__(self) -> UiServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


# ─── Implementation ──────────────────────────────────────────────────────────

logger = logging.getLogger("get_design_markdown.ui")

#: Seconds stop() waits for a cancelled job before closing the socket.
STOP_JOB_TIMEOUT_S = 10.0
#: Socket timeout of one connection: a stalled client cannot hold a thread for ever.
REQUEST_TIMEOUT_S = 30.0
#: How long an error answer waits for the rest of a body of unknown length.
PENDING_BODY_WAIT_S = 0.1
#: How often serve_forever() checks for a shutdown request (bounds how long stop() takes).
SERVE_POLL_INTERVAL_S = 0.05
WAIT_SLICE_S = 0.25
#: Files up to this size are read at once (and closed) before they are sent; larger ones
#: (raw.json of a big site) are streamed.
IN_MEMORY_FILE_BYTES = 16 * 1024 * 1024
_COPY_CHUNK = 64 * 1024

#: Content types of /files (anything else is an attachment).
FILE_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".txt": "text/plain; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
#: /static also serves the frontend's scripts, icons and fonts.
STATIC_CONTENT_TYPES: dict[str, str] = {
    **FILE_CONTENT_TYPES,
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".gif": "image/gif",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".map": "application/json; charset=utf-8",
}
OCTET_STREAM = "application/octet-stream"
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

_KIND_BY_NAME: dict[str, ResultFileKind] = {
    DESIGN_FILE: "design",
    PREVIEW_FILE: "preview",
    TOKENS_FILE: "tokens",
    PROMPT_FILE: "prompt",
    ANALYSIS_FILE: "analysis",
    SYNTHESIS_FILE: "synthesis",
    RAW_FILE: "raw",
}
#: "home-1440", "home-dark-1440" (storage.screenshot_filename without the folder and suffix).
_SCREENSHOT_STEM_RE = re.compile(r"^(?P<page>.+?)(?P<dark>-dark)?-(?P<width>\d{2,5})$")
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ ()+-]")
_WINDOWS_PORT_ERRORS = frozenset({10013, 10048})  # WSAEACCES, WSAEADDRINUSE

_TOKEN_COUNTS = TypeAdapter(dict[str, int])
_WARNINGS = TypeAdapter(list[str])

UNEXPECTED_MESSAGE = "Máy chủ giao diện gặp lỗi không mong muốn."
UNEXPECTED_HINT = (
    "Hãy thử lại. Nếu lỗi lặp lại, gửi kèm file nhật ký ui.log (hoặc nội dung cửa sổ dòng lệnh "
    "đang chạy gdm ui) khi báo lỗi."
)


# ─── Opening a folder ────────────────────────────────────────────────────────


def open_folder(folder: Path) -> None:
    """Show `folder` in the system file manager (Explorer, Finder, xdg-open).

    Raises OSError (or subprocess.SubprocessError) when no file manager can be started.
    Tests replace this function: it must never run during the test suite.
    """
    target = str(folder)
    if sys.platform == "win32":
        os.startfile(target)
        return
    command = ["open", target] if sys.platform == "darwin" else ["xdg-open", target]
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _install_running(installer: BrowserInstaller) -> bool:
    try:
        return installer.status().state == "running"
    except Exception:
        logger.debug("Install status unavailable", exc_info=True)
        return False


# ─── Paths ───────────────────────────────────────────────────────────────────


def is_safe_segment(segment: str) -> bool:
    """A path segment /files and /static accept: non-empty, not "." or "..", no leading dot,
    no separator, drive colon or NUL."""
    return (
        bool(segment)
        and segment not in (".", "..")
        and not segment.startswith(".")
        and not any(char in segment for char in ("/", "\\", ":", "\x00"))
    )


def safe_child(base: Path, segments: Sequence[str]) -> Path | None:
    """The regular file `base`/segments when every segment is acceptable and its real path
    (symlinks followed) stays inside the real `base`; else None."""
    if not segments or not all(is_safe_segment(segment) for segment in segments):
        return None
    try:
        root = base.resolve(strict=True)
        real = root.joinpath(*segments).resolve(strict=True)
        if real == root or not real.is_relative_to(root):
            return None
        mode = real.stat().st_mode
    except (OSError, RuntimeError, ValueError):
        return None
    return real if stat.S_ISREG(mode) else None


def site_folder(output_root: Path, site: str) -> Path | None:
    """<output root>/<site> when `site` is a valid name of an existing folder, else None."""
    if not is_valid_site_name(site):
        return None
    folder = output_root / site
    try:
        return folder if folder.is_dir() else None
    except OSError:
        return None


def files_url(site: str, relative: str) -> str:
    """Root-relative, percent-encoded URL of a file under /files."""
    encoded = "/".join(quote(part, safe="") for part in relative.split("/"))
    return f"/files/{quote(site, safe='')}/{encoded}"


def attachment_header(name: str) -> str:
    """Content-Disposition for a download; non-ASCII names also get an RFC 5987 filename*."""
    plain = _UNSAFE_FILENAME_RE.sub("_", name) or "download"
    header = f'attachment; filename="{plain}"'
    if plain != name:
        header += f"; filename*=UTF-8''{quote(name, safe='')}"
    return header


# ─── Results ─────────────────────────────────────────────────────────────────


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _rendered(path: Path) -> RenderedDocument | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    front_matter, html = render_markdown(text)
    return RenderedDocument(markdown=text, front_matter=front_matter, html=html)


def _file_kind(relative: str) -> ResultFileKind:
    kind = _KIND_BY_NAME.get(relative)
    if kind is not None:
        return kind
    return "image" if relative.lower().endswith(IMAGE_SUFFIXES) else "other"


def _result_files(folder: Path, site: str) -> list[ResultFile]:
    files: list[ResultFile] = []
    for prefix in ("", f"{SCREENSHOTS_DIR}/", f"{PROMPT_IMAGES_DIR}/"):
        directory = folder / prefix if prefix else folder
        try:
            names = [entry.name for entry in os.scandir(directory)]
        except OSError:
            continue
        for name in names:
            relative = prefix + name
            real = safe_child(folder, relative.split("/"))
            if real is None:
                continue
            try:
                size = real.stat().st_size
            except OSError:
                continue
            files.append(
                ResultFile(
                    name=name,
                    path=relative,
                    size=size,
                    url=files_url(site, relative),
                    kind=_file_kind(relative),
                )
            )
    files.sort(key=lambda file: (file.path.casefold(), file.path))
    return files


def _image_width(path: Path) -> int:
    """Pixel width of an image file (0 when it cannot be read); only reads the header."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            return int(image.width)
    except Exception:
        return 0


def _analysis_screenshots(
    folder: Path, site: str, analysis: dict[str, Any] | None
) -> list[ScreenshotInfo]:
    """analysis.json screenshots whose file exists (paths checked like /files)."""
    shots: list[ScreenshotInfo] = []
    raw_shots = analysis.get("screenshots") if analysis is not None else None
    if not isinstance(raw_shots, list):
        return shots
    for item in raw_shots:
        if not isinstance(item, dict):
            continue
        known = {key: value for key, value in item.items() if key in Screenshot.model_fields}
        try:
            shot = Screenshot.model_validate(known)
        except ValidationError:
            continue
        relative = shot.path.replace("\\", "/")
        if safe_child(folder, relative.split("/")) is None:
            continue
        # storage.screenshot_filename(): "<page>-1440.png" / "<page>-dark-1440.png".
        stem = Path(relative).stem
        scheme = "-dark" if shot.color_scheme == "dark" else ""
        suffix = f"{scheme}-{shot.viewport_width}"
        page = stem.removesuffix(suffix) if stem.endswith(suffix) else shot.name
        shots.append(
            ScreenshotInfo(
                page=page or stem,
                viewport_width=shot.viewport_width,
                color_scheme=shot.color_scheme,
                path=relative,
                url=files_url(site, relative),
            )
        )
    return shots


def _folder_screenshots(folder: Path, site: str) -> list[ScreenshotInfo]:
    """screenshots/*.png by name (when analysis.json lists none)."""
    try:
        names = sorted(
            entry.name
            for entry in os.scandir(folder / SCREENSHOTS_DIR)
            if entry.name.lower().endswith(".png")
        )
    except OSError:
        return []
    shots: list[ScreenshotInfo] = []
    for name in names:
        relative = f"{SCREENSHOTS_DIR}/{name}"
        real = safe_child(folder, [SCREENSHOTS_DIR, name])
        if real is None:
            continue
        stem = Path(name).stem
        match = _SCREENSHOT_STEM_RE.fullmatch(stem)
        shots.append(
            ScreenshotInfo(
                page=match.group("page") if match else stem,
                viewport_width=int(match.group("width")) if match else _image_width(real),
                color_scheme="dark" if match and match.group("dark") else "light",
                path=relative,
                url=files_url(site, relative),
            )
        )
    return shots


def _prompt_images(folder: Path, site: str) -> list[str]:
    try:
        names = sorted(
            entry.name
            for entry in os.scandir(folder / PROMPT_IMAGES_DIR)
            if entry.name.lower().endswith(IMAGE_SUFFIXES)
        )
    except OSError:
        return []
    return [
        files_url(site, f"{PROMPT_IMAGES_DIR}/{name}")
        for name in names
        if safe_child(folder, [PROMPT_IMAGES_DIR, name]) is not None
    ]


def _token_counts(folder: Path) -> dict[str, int] | None:
    synthesis = _read_json_object(folder / SYNTHESIS_FILE)
    if synthesis is None:
        return None
    try:
        return _TOKEN_COUNTS.validate_python(synthesis.get("token_counts"))
    except ValidationError:
        return None


def _analysis_warnings(analysis: dict[str, Any] | None) -> list[str]:
    if analysis is None:
        return []
    try:
        return _WARNINGS.validate_python(analysis.get("warnings", []))
    except ValidationError:
        return []


def build_results(output_root: Path, site: str) -> ResultsResponse | None:
    """GET /api/results/{site}; None for an invalid or missing site (404 "site_not_found")."""
    folder = site_folder(output_root, site)
    entry = read_entry(folder) if folder is not None else None
    if folder is None or entry is None:
        return None
    analysis = _read_json_object(folder / ANALYSIS_FILE) if entry.has_analysis else None
    screenshots = _analysis_screenshots(folder, site, analysis) or _folder_screenshots(folder, site)
    preview = safe_child(folder, [PREVIEW_FILE])
    design = safe_child(folder, [DESIGN_FILE])
    prompt = safe_child(folder, [PROMPT_FILE])
    try:
        absolute = folder.resolve()
    except OSError:
        absolute = folder.absolute()
    return ResultsResponse(
        site=site,
        entry=entry,
        folder=str(absolute),
        files=_result_files(folder, site),
        design=_rendered(design) if design is not None else None,
        prompt=_rendered(prompt) if prompt is not None else None,
        preview_url=files_url(site, PREVIEW_FILE) if preview is not None else None,
        screenshots=screenshots,
        prompt_images=_prompt_images(folder, site),
        token_counts=_token_counts(folder),
        warnings=_analysis_warnings(analysis),
    )


def settings_limits() -> SettingsLimits:
    return SettingsLimits(
        pages=(MIN_PAGES, MAX_PAGES),
        max_elements=(MIN_MAX_ELEMENTS, MAX_MAX_ELEMENTS),
        timeout_s=(MIN_TIMEOUT_S, MAX_TIMEOUT_S),
        delta_e=(MIN_DELTA_E, MAX_DELTA_E),
    )


# ─── HTTP plumbing ───────────────────────────────────────────────────────────


class HttpError(Exception):
    """An API error answered with the error envelope."""

    def __init__(
        self,
        status: int,
        code: ApiErrorCode,
        message: str,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
        *,
        allow: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error = ApiError(code=code, message=message, hint=hint, details=details)
        self.allow = tuple(allow)


def not_found() -> HttpError:
    return HttpError(404, "not_found", "Không tìm thấy địa chỉ này.")


def method_not_allowed(allow: Sequence[str] = ()) -> HttpError:
    return HttpError(
        405, "method_not_allowed", "Phương thức HTTP này không được hỗ trợ.", allow=allow
    )


def site_not_found(site: str) -> HttpError:
    # Only a well-formed folder name is echoed back.
    shown = f"'{site}'" if is_valid_site_name(site) else "trang này"
    return HttpError(
        404,
        "site_not_found",
        f"Không có kết quả phân tích cho {shown}.",
        hint="Thư mục kết quả có thể đã bị xóa, đổi tên hoặc nằm ở thư mục lưu khác.",
    )


def validation_error(error: ValidationError) -> HttpError:
    """422 "invalid" for the first error of a request body (details.field = its dotted path)."""
    first = error.errors()[0]
    field = ".".join(str(part) for part in first["loc"])
    shown = field or "(gốc)"
    return HttpError(
        422,
        "invalid",
        f"Giá trị không hợp lệ ở '{shown}': {describe_error(first)}.",
        details={"field": field},
    )


def invalid_field(field: str, message: str) -> HttpError:
    return HttpError(422, "invalid", message, details={"field": field})


def internal_error(
    message: str = UNEXPECTED_MESSAGE, hint: str | None = UNEXPECTED_HINT
) -> HttpError:
    return HttpError(500, "internal", message, hint)


def gdm_error(error: GdmError) -> HttpError:
    """A GdmError that is not part of an endpoint's contract (keeps its Vietnamese text)."""
    if isinstance(error, NotImplementedYetError):
        return HttpError(501, "unsupported", error.message, error.hint)
    return HttpError(500, "internal", error.message, error.hint)


def _port_error(port: int, error: OSError) -> GdmError:
    in_use = error.errno in (errno.EADDRINUSE, errno.EACCES) or (
        getattr(error, "winerror", None) in _WINDOWS_PORT_ERRORS
    )
    hint = (
        "Chọn cổng khác bằng --port, hoặc bỏ --port để tự chọn một cổng trống."
        if port
        else "Kiểm tra tường lửa hoặc phần mềm bảo mật đang chặn kết nối cục bộ."
    )
    if in_use:
        return GdmError(f"Cổng {port} đang được chương trình khác dùng.", hint=hint)
    reason = error.strerror or type(error).__name__
    return GdmError(f"Không mở được máy chủ giao diện ở {HOST}:{port} ({reason}).", hint=hint)


class _HttpServer(ThreadingHTTPServer):
    """ThreadingHTTPServer bound to 127.0.0.1 without reverse DNS lookups; on Windows the port
    is taken exclusively (SO_EXCLUSIVEADDRUSE) so no other program can share it."""

    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"
    request_queue_size = 64

    def __init__(self, address: tuple[str, int], app: UiServer) -> None:
        self.app = app
        super().__init__(address, _RequestHandler)

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if sys.platform == "win32" and exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        socketserver.TCPServer.server_bind(self)
        self.server_name = HOST
        self.server_port = self.bound_port

    @property
    def bound_port(self) -> int:
        return int(self.socket.getsockname()[1])

    def handle_error(self, request: Any, client_address: Any) -> None:
        logger.debug("Lỗi kết nối với %s", client_address, exc_info=True)


def _bind(app: UiServer, port: int) -> _HttpServer:
    try:
        return _HttpServer((HOST, port), app)
    except OSError as error:
        raise _port_error(port, error) from None


class _RequestHandler(BaseHTTPRequestHandler):
    """One request of the UI server (see the module docstring for the rules)."""

    protocol_version = "HTTP/1.0"
    server_version = "gdm-ui"
    sys_version = ""
    timeout = REQUEST_TIMEOUT_S

    _response_started = False
    _body_remaining = 0
    _body_unknown = False

    # ─── plumbing ───────────────────────────────────────────────────────────

    @property
    def app(self) -> UiServer:
        return cast(_HttpServer, self.server).app

    def version_string(self) -> str:
        """Server header without the Python version."""
        return self.server_version

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s %s", self.address_string(), format % args)

    def _common_headers(self, cache: str) -> None:
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")

    def _begin(self, status: int) -> None:
        self._drain()
        self._response_started = True
        self.send_response(status)

    def _send_json(self, status: int, model: BaseModel, *, allow: Sequence[str] = ()) -> None:
        body = model.model_dump_json().encode("utf-8")
        self._begin(status)
        self.send_header("Content-Type", JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        if allow:
            self.send_header("Allow", ", ".join(allow))
        self._common_headers("no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error(self, error: HttpError) -> None:
        if self._response_started:  # headers already went out: only closing is left
            self.close_connection = True
            return
        self._send_json(error.status, ErrorResponse(error=error.error), allow=error.allow)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Protocol-level errors of http.server (bad request line, unknown method, headers too
        long...) answered with the JSON envelope instead of an HTML page."""
        self.close_connection = True
        # http.server leaves the version at HTTP/0.9 for a request line it could not parse,
        # which would send the body without status line and headers.
        if getattr(self, "request_version", "HTTP/0.9") == "HTTP/0.9":
            self.request_version = "HTTP/1.0"
        # The rest of the request (a too long line, headers...) was not read: discard what
        # already arrived so closing the socket does not reset the connection before the
        # client has read the answer.
        self._body_remaining = 0
        self._body_unknown = True
        if code == HTTPStatus.NOT_IMPLEMENTED:
            error = method_not_allowed()
        elif code in (HTTPStatus.REQUEST_URI_TOO_LONG, HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE):
            error = HttpError(code, "too_large", "Yêu cầu quá lớn.")
        else:
            error = HttpError(code, "invalid", "Yêu cầu HTTP không hợp lệ.")
        with contextlib.suppress(OSError):
            self._send_json(error.status, ErrorResponse(error=error.error))

    def _send_file(
        self,
        path: Path,
        content_type: str,
        *,
        csp: str | None = None,
        attachment: bool = False,
    ) -> None:
        try:
            handle: BinaryIO = path.open("rb")
        except OSError:
            raise not_found() from None
        with handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise not_found()
            size = info.st_size
            # Small files are read at once and closed before sending: on Windows an open
            # handle makes the pipeline's atomic replace (os.replace) of that file fail.
            data = (
                handle.read() if size <= IN_MEMORY_FILE_BYTES and self.command != "HEAD" else None
            )
            if data is not None:
                size = len(data)
                handle.close()
            self._begin(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            if csp is not None:
                self.send_header("Content-Security-Policy", csp)
            if attachment:
                self.send_header("Content-Disposition", attachment_header(path.name))
            self._common_headers("no-cache")
            self.end_headers()
            if self.command == "HEAD":
                return
            if data is not None:
                self.wfile.write(data)
            else:
                shutil.copyfileobj(handle, self.wfile, _COPY_CHUNK)

    # ─── request body ───────────────────────────────────────────────────────

    def _read_body_length(self) -> None:
        """Content-Length of the request (0 when absent); chunked bodies are refused."""
        self._body_remaining = 0
        self._body_unknown = False
        if self.headers.get("Transfer-Encoding", "").strip().lower() not in ("", "identity"):
            self._body_unknown = True
            self.close_connection = True
            raise HttpError(
                400, "bad_json", "Cần gửi dữ liệu kèm Content-Length (không hỗ trợ chunked)."
            )
        values = self.headers.get_all("Content-Length") or []
        if not values:
            return
        if len(set(values)) != 1 or not values[0].strip().isdigit():
            self._body_unknown = True
            self.close_connection = True
            raise HttpError(400, "bad_json", "Content-Length không hợp lệ.")
        self._body_remaining = int(values[0].strip())

    def _discard_pending(self) -> None:
        """Read what the client already sent of a body of unknown length (chunked or a bad
        Content-Length), waiting at most PENDING_BODY_WAIT_S, so the error answer is not lost
        to a connection reset."""
        self._body_unknown = False
        read1 = getattr(self.rfile, "read1", None)
        if read1 is None:
            return
        try:
            self.connection.settimeout(PENDING_BODY_WAIT_S)
            budget = MAX_DRAIN_BYTES
            while budget > 0:
                chunk = read1(min(budget, _COPY_CHUNK))
                if not chunk:
                    break
                budget -= len(chunk)
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError):
                self.connection.settimeout(self.timeout)

    def _drain(self) -> None:
        """Read and discard the unread body (at most MAX_DRAIN_BYTES) so the client gets the
        answer instead of a connection reset; a larger rest closes the connection."""
        if self._body_unknown:
            self._discard_pending()
            return
        remaining = self._body_remaining
        if remaining <= 0:
            return
        budget = min(remaining, MAX_DRAIN_BYTES)
        try:
            while budget > 0:
                chunk = self.rfile.read(min(budget, _COPY_CHUNK))
                if not chunk:
                    break
                budget -= len(chunk)
                remaining -= len(chunk)
        except OSError:
            remaining = 1
        self._body_remaining = 0
        if remaining > 0:
            self.close_connection = True

    def _json_body(self) -> dict[str, Any]:
        length = self._body_remaining
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise HttpError(
                413,
                "too_large",
                f"Dữ liệu gửi lên quá lớn (tối đa {MAX_BODY_BYTES // 1024} KB).",
            )
        raw = self.rfile.read(length) if length else b""
        self._body_remaining = 0
        if len(raw) < length:
            self.close_connection = True
            raise HttpError(400, "bad_json", "Dữ liệu gửi lên bị thiếu.")
        if not raw.strip():
            return {}
        media_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if media_type != "application/json":
            raise HttpError(
                415,
                "unsupported_media_type",
                "Cần gửi dữ liệu JSON (Content-Type: application/json).",
            )
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise HttpError(400, "bad_json", "Dữ liệu JSON không hợp lệ.") from None
        if not isinstance(data, dict):
            raise HttpError(400, "bad_json", "Cần gửi một đối tượng JSON ({...}).")
        return data

    # ─── checks ─────────────────────────────────────────────────────────────

    def _allowed_hosts(self) -> set[str]:
        port = self.app.port
        return {f"{HOST}:{port}", f"localhost:{port}"}

    def _check_host(self) -> None:
        values = self.headers.get_all("Host") or []
        if len(values) != 1 or values[0].strip().lower() not in self._allowed_hosts():
            raise HttpError(
                403,
                "bad_host",
                "Địa chỉ máy chủ không hợp lệ.",
                hint=f"Mở giao diện bằng địa chỉ {self.app.url} mà gdm ui in ra.",
            )

    def _check_origin(self) -> None:
        values = self.headers.get_all("Origin") or []
        if not values:
            return
        allowed = {f"http://{host}" for host in self._allowed_hosts()}
        if len(values) != 1 or values[0].strip().lower() not in allowed:
            raise HttpError(403, "bad_origin", "Yêu cầu đến từ một trang web khác nên bị từ chối.")

    def _check_token(self) -> None:
        values = self.headers.get_all(TOKEN_HEADER) or []
        expected = self.app.token.encode("utf-8")
        if len(values) != 1 or not hmac.compare_digest(
            values[0].strip().encode("utf-8", "replace"), expected
        ):
            raise HttpError(
                403,
                "forbidden",
                "Phiên giao diện không hợp lệ hoặc đã hết hạn.",
                hint="Mở lại giao diện bằng địa chỉ mà gdm ui in ra (hoặc khởi động lại ứng dụng).",
            )

    # ─── verbs ──────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        self._handle("GET")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_OPTIONS(self) -> None:
        self._handle("OPTIONS")

    def do_TRACE(self) -> None:
        self._handle("TRACE")

    def do_CONNECT(self) -> None:
        self._handle("CONNECT")

    def _handle(self, method: str) -> None:
        self._response_started = False
        try:
            self._read_body_length()
            self._dispatch(method)
        except HttpError as error:
            self._send_error(error)
        except (ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception:
            logger.exception("Lỗi không mong muốn khi xử lý %s %s", method, self.path)
            self._send_error(internal_error())

    def _dispatch(self, method: str) -> None:
        self._check_host()
        if method not in ("GET", "HEAD", "POST", "PUT"):  # OPTIONS: no CORS preflight, ever
            raise method_not_allowed(("GET", "HEAD", "POST", "PUT"))
        if not self.path.startswith("/"):
            raise not_found()
        parts = urlsplit(self.path)
        segments = [unquote(segment) for segment in parts.path.split("/")[1:]]
        query = parse_qs(parts.query, keep_blank_values=True)
        if segments[0] == "api":
            self._api(method, segments[1:], query)
            return
        if method not in ("GET", "HEAD"):
            raise method_not_allowed(("GET", "HEAD"))
        if segments == [""]:
            index = safe_child(self.app.static_dir(), ["index.html"])
            if index is None:
                raise not_found()
            self._send_file(index, FILE_CONTENT_TYPES[".html"], csp=INDEX_CSP)
        elif segments[0] == "static":
            self._static(segments[1:])
        elif segments[0] == "files" and len(segments) >= 3:
            self._files(segments[1], segments[2:], query)
        else:
            raise not_found()

    def _static(self, segments: list[str]) -> None:
        found = safe_child(self.app.static_dir(), segments)
        if found is None:
            raise not_found()
        content_type = STATIC_CONTENT_TYPES.get(found.suffix.lower(), OCTET_STREAM)
        self._send_file(found, content_type, attachment=content_type == OCTET_STREAM)

    def _files(self, site: str, segments: list[str], query: dict[str, list[str]]) -> None:
        folder = site_folder(self.app.output_root, site)
        found = safe_child(folder, segments) if folder is not None else None
        if found is None:
            raise not_found()
        suffix = found.suffix.lower()
        content_type = FILE_CONTENT_TYPES.get(suffix, OCTET_STREAM)
        download = query.get("download", [""])[-1] == "1"
        self._send_file(
            found,
            content_type,
            csp=FILES_HTML_CSP if suffix == ".html" else None,
            attachment=download or content_type == OCTET_STREAM,
        )

    # ─── API ────────────────────────────────────────────────────────────────

    def _api(self, method: str, segments: list[str], query: dict[str, list[str]]) -> None:
        if method in ("POST", "PUT"):
            self._check_origin()
        self._check_token()
        routes = self._routes(segments, query)
        if routes is None:
            raise not_found()
        handler = routes.get(method)
        if handler is None:
            raise method_not_allowed(tuple(routes))
        handler()

    def _routes(
        self, segments: list[str], query: dict[str, list[str]]
    ) -> dict[str, Callable[[], None]] | None:
        match segments:
            case ["health"]:
                return {"GET": self._get_health}
            case ["settings"]:
                return {"GET": self._get_settings, "PUT": self._put_settings}
            case ["jobs"]:
                return {"POST": self._post_job}
            case ["jobs", "current"]:
                return {"GET": lambda: self._get_current_job(query)}
            case ["jobs", job_id] if JOB_ID_RE.fullmatch(job_id):
                return {"GET": lambda: self._get_job(job_id, query)}
            case ["jobs", job_id, "cancel"] if JOB_ID_RE.fullmatch(job_id):
                return {"POST": lambda: self._cancel_job(job_id)}
            case ["history"]:
                return {"GET": self._get_history}
            case ["results", site]:
                return {"GET": lambda: self._get_results(site)}
            case ["open-folder"]:
                return {"POST": self._open_folder}
            case ["browser", "status"]:
                return {"GET": self._browser_status}
            case ["browser", "install"]:
                return {"GET": self._get_install, "POST": self._post_install}
            case ["heartbeat"]:
                return {"POST": self._heartbeat}
            case ["shutdown"]:
                return {"POST": self._shutdown}
        return None

    @staticmethod
    def _since(query: dict[str, list[str]]) -> int:
        raw = (query.get("since") or [""])[-1].strip()
        if not raw:
            return 0
        try:
            return max(int(raw), 0)
        except ValueError:
            raise invalid_field(
                "since", "Giá trị không hợp lệ ở 'since': cần một số nguyên."
            ) from None

    def _get_health(self) -> None:
        app = self.app
        job_id = app.jobs.active_job_id
        self._send_json(
            200,
            HealthResponse(
                version=__version__,
                mode=app.mode,
                output_root=str(app.output_root),
                busy=job_id is not None,
                job_id=job_id,
            ),
        )

    def _settings_response(self) -> SettingsResponse:
        app = self.app
        return SettingsResponse(
            settings=app.settings,
            defaults=app.defaults,
            output_root=str(app.output_root),
            output_root_locked=app.output_root_locked,
            config_path=str(app.config_path),
            browsers=list(BROWSER_CHOICES),
            limits=settings_limits(),
        )

    def _get_settings(self) -> None:
        self._send_json(200, self._settings_response())

    def _put_settings(self) -> None:
        body = self._json_body()
        unknown = sorted(set(body) - set(UiSettings.model_fields))
        if unknown:
            raise invalid_field(unknown[0], f"Không có tùy chọn '{unknown[0]}'.")
        changes = {key: value for key, value in body.items() if key != "schema_version"}
        if "output_dir" in changes:
            changes["output_dir"] = _checked_output_dir(changes["output_dir"])
        try:
            self.app.update_settings(changes)
        except ValidationError as error:
            raise validation_error(error) from None
        except GdmError as error:  # ConfigError: the file could not be written
            raise gdm_error(error) from None
        self._send_json(200, self._settings_response())

    def _post_job(self) -> None:
        app = self.app
        body = self._json_body()
        try:
            request = JobRequest.model_validate(body)
        except ValidationError as error:
            raise validation_error(error) from None
        try:
            normalize_url(request.url)
            settings = app.job_settings(request)
            snapshot = app.jobs.start(request, settings)
        except InvalidUrlError as error:
            raise HttpError(422, "invalid_url", error.message, error.hint) from None
        except ValidationError as error:
            raise validation_error(error) from None
        except JobBusyError as error:
            raise HttpError(
                409, "busy", error.message, error.hint, details={"job_id": error.job_id}
            ) from None
        self._send_json(202, JobResponse(job=snapshot))

    def _get_current_job(self, query: dict[str, list[str]]) -> None:
        self._send_json(200, CurrentJobResponse(job=self.app.jobs.current(self._since(query))))

    def _get_job(self, job_id: str, query: dict[str, list[str]]) -> None:
        since = self._since(query)
        try:
            snapshot = self.app.jobs.get(job_id, since)
        except JobNotFoundError as error:
            raise HttpError(404, "job_not_found", error.message, error.hint) from None
        self._send_json(200, JobResponse(job=snapshot))

    def _cancel_job(self, job_id: str) -> None:
        self._json_body()
        try:
            snapshot = self.app.jobs.cancel(job_id)
        except JobNotFoundError as error:
            raise HttpError(404, "job_not_found", error.message, error.hint) from None
        self._send_json(200 if snapshot.finished else 202, JobResponse(job=snapshot))

    def _get_history(self) -> None:
        root = self.app.output_root
        self._send_json(200, HistoryResponse(output_root=str(root), entries=list_history(root)))

    def _get_results(self, site: str) -> None:
        results = build_results(self.app.output_root, site)
        if results is None:
            raise site_not_found(site)
        self._send_json(200, results)

    def _open_folder(self) -> None:
        body = self._json_body()
        try:
            request = OpenFolderRequest.model_validate(body)
        except ValidationError as error:
            raise validation_error(error) from None
        root = self.app.output_root
        if request.site is None:
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise internal_error(
                    f"Không tạo được thư mục kết quả {root} "
                    f"({error.strerror or type(error).__name__}).",
                    "Chọn một thư mục lưu khác trong phần Tùy chọn nâng cao.",
                ) from None
            folder = root
        else:
            found = site_folder(root, request.site)
            if found is None:
                raise site_not_found(request.site)
            folder = found
        if not folder.is_dir():
            raise site_not_found(request.site or "")
        try:
            open_folder(folder)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            logger.warning("Không mở được thư mục %s: %s", folder, error)
            raise HttpError(
                501,
                "unsupported",
                "Không mở được trình quản lý tệp trên máy này.",
                hint=f"Hãy tự mở thư mục: {folder}",
            ) from None
        self._send_json(200, OpenFolderResponse(folder=str(folder)))

    def _browser_status(self) -> None:
        try:
            status = browser_install.browser_status()
            install = self.app.installer.status()
        except GdmError as error:
            raise gdm_error(error) from None
        self._send_json(
            200, BrowserStatusResponse(browsers=status.browsers, auto=status.auto, install=install)
        )

    def _get_install(self) -> None:
        try:
            install = self.app.installer.status()
        except GdmError as error:
            raise gdm_error(error) from None
        self._send_json(200, InstallResponse(install=install))

    def _post_install(self) -> None:
        self._json_body()
        try:
            install, started = self.app.start_install()
        except GdmError as error:
            raise gdm_error(error) from None
        if not started:
            raise HttpError(
                409,
                "install_running",
                "Đang tải Chromium.",
                hint="Chờ lượt tải hiện tại xong.",
                details={"install": install.model_dump(mode="json")},
            )
        self._send_json(202, InstallResponse(install=install))

    def _heartbeat(self) -> None:
        self._json_body()
        self.app.heartbeat()
        self._send_json(200, HeartbeatResponse(idle_shutdown_s=self.app.idle_shutdown_s))

    def _shutdown(self) -> None:
        body = self._json_body()
        try:
            request = ShutdownRequest.model_validate(body)
        except ValidationError as error:
            raise validation_error(error) from None
        app = self.app
        job_id = app.jobs.active_job_id
        if job_id is not None:
            if not request.force:
                raise HttpError(
                    409,
                    "busy",
                    "Đang có một lượt phân tích chạy.",
                    hint="Hủy lượt phân tích trước khi tắt (hoặc gửi force: true).",
                    details={"job_id": job_id},
                )
            with contextlib.suppress(JobNotFoundError):
                app.jobs.cancel(job_id)
        self._send_json(202, ShutdownResponse())
        app.stop_soon()


def _checked_output_dir(value: Any) -> Path:
    """output_dir of PUT /api/settings: "~" expanded, absolute, not an existing file."""
    if not isinstance(value, str) or not value.strip():
        raise invalid_field("output_dir", "Thư mục lưu kết quả đang để trống.")
    if "\x00" in value:
        raise invalid_field("output_dir", "Đường dẫn thư mục lưu kết quả không hợp lệ.")
    path = absolute_output_dir(value.strip())
    try:
        is_file = path.exists() and not path.is_dir()
    except (OSError, ValueError):
        raise invalid_field(
            "output_dir", f"Không dùng được '{path}' làm thư mục lưu kết quả."
        ) from None
    if is_file:
        raise invalid_field(
            "output_dir", f"'{path}' là một tệp, không phải thư mục; hãy chọn một thư mục."
        )
    return path
