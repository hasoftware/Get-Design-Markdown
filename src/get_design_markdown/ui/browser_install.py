"""Which browsers the collector can use, and installing Playwright's Chromium from the UI.

Frozen interface: the models, constants and signatures below.
To implement (the stubs raise NotImplementedYetError): browser_status, install_command,
run_install_cli and BrowserInstaller.

Browser status (GET /api/browser/status)
---------------------------------------
browser_status() must be fast (well under a second) and must not launch a browser or start the
Playwright driver: it only looks for executables on disk.
- "chromium": Playwright's Chromium for the installed playwright package, i.e.
  <browsers path>/chromium-<revision>/... where <browsers path> is $PLAYWRIGHT_BROWSERS_PATH or
  Playwright's default (%LOCALAPPDATA%\\ms-playwright, ~/Library/Caches/ms-playwright,
  ~/.cache/ms-playwright) and the revision comes from the driver's browsers.json; a frozen exe
  never bundles Chromium, so it uses the same per-user folder.
- "msedge" / "chrome": the stable channel's executable at the locations Playwright uses for
  `channel="msedge"` / `channel="chrome"` (Windows: Program Files / Program Files (x86) /
  %LOCALAPPDATA%; macOS: /Applications; Linux: /opt/microsoft/msedge, /opt/google/chrome).
- `auto` is the first available of chromium -> msedge -> chrome (the order open_browser("auto")
  tries), None when none is available.
- `version` is filled only when it can be read without starting the browser (Chromium's
  revision folder, the Windows file version...), else None.

Installing Chromium (POST /api/browser/install, polled with GET /api/browser/install)
------------------------------------------------------------------------------------
- install_command(): the command that installs Playwright's Chromium:
    from source / a wheel: [sys.executable, "-m", "playwright", "install", "chromium"]
    frozen exe:            [sys.executable, "install-browser"]   (hidden sub-command of
                           ui/app.py main(), which runs run_install_cli())
- run_install_cli(): body of the exe's `install-browser` sub-command: runs the bundled Playwright
  driver with `install chromium`, streams its output to stdout and returns its exit code.
- BrowserInstaller runs install_command() in a subprocess (no console window on Windows:
  CREATE_NO_WINDOW), reads its output line by line, keeps the last LOG_TAIL_LINES lines and turns
  Playwright's progress lines ("|■■■■    |  40% of 150.2 MiB") into `percent`. One install at a
  time: start() while running returns the running status. Messages are Vietnamese.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import Field

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.base import GdmModel

BrowserName = Literal["chromium", "msedge", "chrome"]
#: Order in which open_browser("auto") tries the browsers.
AUTO_ORDER: tuple[BrowserName, ...] = ("chromium", "msedge", "chrome")
#: Vietnamese display names.
BROWSER_LABELS: dict[BrowserName, str] = {
    "chromium": "Chromium (Playwright)",
    "msedge": "Microsoft Edge",
    "chrome": "Google Chrome",
}
LOG_TAIL_LINES = 20

InstallState = Literal["idle", "running", "succeeded", "failed"]


class BrowserInfo(GdmModel):
    name: BrowserName
    #: BROWSER_LABELS[name].
    label: str
    available: bool
    version: str | None = None
    #: Executable found on disk (None when unavailable).
    path: str | None = None
    #: Vietnamese reason when unavailable ("Chưa tải Chromium cho Playwright.").
    detail: str | None = None


class BrowserStatus(GdmModel):
    #: Always the three browsers, in AUTO_ORDER.
    browsers: list[BrowserInfo]
    #: What `auto` would use now; None when no browser is available.
    auto: BrowserName | None = None


class InstallError(GdmModel):
    message: str
    hint: str | None = None


class InstallStatus(GdmModel):
    """Progress of the Chromium download, polled by the UI."""

    state: InstallState = "idle"
    #: 0-100 while the download reports progress, else None.
    percent: float | None = Field(default=None, ge=0, le=100)
    #: Short Vietnamese status line ("Đang tải Chromium… 40%").
    message: str = ""
    #: Last LOG_TAIL_LINES lines of the installer output.
    log_tail: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    #: Set when state is "failed".
    error: InstallError | None = None


# ─── To implement ────────────────────────────────────────────────────────────


def browser_status() -> BrowserStatus:
    """Availability of chromium / msedge / chrome (see the module docstring)."""
    raise NotImplementedYetError("Chưa kiểm tra được trình duyệt.")


def install_command() -> list[str]:
    """Command line that installs Playwright's Chromium (see the module docstring)."""
    raise NotImplementedYetError("Chưa cài được Chromium từ giao diện.")


def run_install_cli(argv: Sequence[str] = ()) -> int:
    """The exe's hidden `install-browser` sub-command; returns the installer's exit code."""
    raise NotImplementedYetError("Chưa cài được Chromium từ giao diện.")


class BrowserInstaller:
    """One background Chromium install at a time; thread-safe."""

    def __init__(self, command: Sequence[str] | None = None) -> None:
        #: None: install_command() when start() runs (tests inject a fake command).
        self.command = list(command) if command is not None else None

    def start(self) -> InstallStatus:
        """Start the install in the background (no-op while one is running)."""
        raise NotImplementedYetError("Chưa cài được Chromium từ giao diện.")

    def status(self) -> InstallStatus:
        """Current progress ("idle" before the first start())."""
        raise NotImplementedYetError("Chưa cài được Chromium từ giao diện.")

    def wait(self, timeout: float | None = None) -> InstallStatus:
        """Block until the running install ends (or `timeout` seconds passed)."""
        raise NotImplementedYetError("Chưa cài được Chromium từ giao diện.")
