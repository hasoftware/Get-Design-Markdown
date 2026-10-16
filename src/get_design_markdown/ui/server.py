"""HTTP server of the local UI. This docstring is the API contract (frozen).

Frozen interface: this contract, the constants, the response models and the UiServer signature.
To implement (the stub raises NotImplementedYetError): UiServer.

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

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.base import GdmModel
from get_design_markdown.ui.browser_install import (
    BrowserInfo,
    BrowserInstaller,
    BrowserName,
    InstallStatus,
)
from get_design_markdown.ui.history import HistoryEntry
from get_design_markdown.ui.jobs import JobManager, JobSnapshot
from get_design_markdown.ui.settings import UiSettings

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


# ─── To implement ────────────────────────────────────────────────────────────


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
        self._token = token
        self._jobs = jobs
        self._installer = installer
        self._idle_timeout_s = idle_timeout_s
        self._static_dir = static_dir
        self._defaults = defaults

    @property
    def port(self) -> int:
        """The bound port (after start())."""
        raise NotImplementedYetError("Máy chủ giao diện chưa được xây dựng.")

    @property
    def token(self) -> str:
        raise NotImplementedYetError("Máy chủ giao diện chưa được xây dựng.")

    @property
    def url(self) -> str:
        """ "http://127.0.0.1:<port>/" (after start())."""
        raise NotImplementedYetError("Máy chủ giao diện chưa được xây dựng.")

    @property
    def launch_url(self) -> str:
        """url + "#token=<token>": what the browser tab or the window opens."""
        raise NotImplementedYetError("Máy chủ giao diện chưa được xây dựng.")

    @property
    def output_root(self) -> Path:
        """Current output root (absolute)."""
        raise NotImplementedYetError("Máy chủ giao diện chưa được xây dựng.")

    def start(self) -> None:
        """Bind 127.0.0.1:<port> and serve in a daemon thread; returns once listening.
        Raises GdmError (Vietnamese) when the port is taken."""
        raise NotImplementedYetError("Máy chủ giao diện chưa được xây dựng.")

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the server stopped (idle shutdown, /api/shutdown or stop());
        False when `timeout` expired first."""
        raise NotImplementedYetError("Máy chủ giao diện chưa được xây dựng.")

    def stop(self) -> None:
        """Cancel a running job, stop serving and close the socket (idempotent)."""
        raise NotImplementedYetError("Máy chủ giao diện chưa được xây dựng.")

    def __enter__(self) -> UiServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
