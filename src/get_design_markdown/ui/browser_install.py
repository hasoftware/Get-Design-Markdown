"""Which browsers the collector can use, and installing Playwright's Chromium from the UI.

Frozen interface: the models, constants and signatures below.
Implemented here: browser_status, install_command, run_install_cli and BrowserInstaller.

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

Headless launches (`chromium.launch(headless=True)`, what the collector does) run Playwright's
"chromium-headless-shell" build, so that is the executable looked for; `playwright install
chromium` downloads it together with the full Chromium.

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

import json
import logging
import os
import platform
import plistlib
import re
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Literal

from pydantic import Field

from get_design_markdown.models.base import GdmModel
from get_design_markdown.resources import is_frozen

logger = logging.getLogger(__name__)

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


# ─── Where things are ────────────────────────────────────────────────────────

#: Environment variable Playwright reads for its browsers folder ("0": inside the package).
BROWSERS_PATH_ENV = "PLAYWRIGHT_BROWSERS_PATH"
#: browsers.json entry of the build a headless launch runs.
HEADLESS_SHELL = "chromium-headless-shell"

CHROMIUM_MISSING = "Chưa tải Chromium cho Playwright."
CHROMIUM_UNKNOWN = "Không đọc được phiên bản Chromium mà Playwright cần."
NOT_INSTALLED: dict[BrowserName, str] = {
    "chromium": CHROMIUM_MISSING,
    "msedge": "Chưa cài Microsoft Edge.",
    "chrome": "Chưa cài Google Chrome.",
}

#: Install locations of the stable channels, as Playwright looks for them.
_WINDOWS_SUFFIX: dict[BrowserName, tuple[str, ...]] = {
    "msedge": ("Microsoft", "Edge", "Application", "msedge.exe"),
    "chrome": ("Google", "Chrome", "Application", "chrome.exe"),
}
_MAC_APP: dict[BrowserName, str] = {
    "msedge": "/Applications/Microsoft Edge.app",
    "chrome": "/Applications/Google Chrome.app",
}
_MAC_BINARY: dict[BrowserName, str] = {
    "msedge": "Microsoft Edge",
    "chrome": "Google Chrome",
}
_LINUX_PATH: dict[BrowserName, str] = {
    "msedge": "/opt/microsoft/msedge/msedge",
    "chrome": "/opt/google/chrome/chrome",
}
_VERSION_DIR_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")


def _platform() -> str:
    """sys.platform (a function so tests can simulate other systems)."""
    return sys.platform


def _machine() -> str:
    return platform.machine().lower()


def _home() -> Path:
    return Path.home()


def driver_executable() -> tuple[Path, Path]:
    """(node executable, cli.js) of the Playwright driver shipped with the playwright package
    (inside the bundle for the frozen exe)."""
    from playwright._impl._driver import compute_driver_executable

    node, cli = compute_driver_executable()
    return Path(node), Path(cli)


def browsers_json_path() -> Path:
    """The driver's browsers.json (browser revisions this playwright version expects)."""
    return driver_executable()[1].parent / "browsers.json"


def playwright_browsers_dir() -> Path:
    """Folder where Playwright keeps its downloaded browsers (same rules as the driver).

    Inside the frozen exe an unset PLAYWRIGHT_BROWSERS_PATH means the per-user folder too:
    ensure_user_browsers_path() sets it at start-up, because Playwright would otherwise look
    inside the bundle (it defaults to "0" when frozen).
    """
    configured = os.environ.get(BROWSERS_PATH_ENV, "").strip()
    if configured == "0":
        return browsers_json_path().parent / ".local-browsers"
    if configured:
        return Path(configured).expanduser().absolute()
    return default_browsers_dir()


def default_browsers_dir() -> Path:
    """Playwright's per-user browsers folder (LOCALAPPDATA/ms-playwright on Windows)."""
    system = _platform()
    if system == "win32":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else _home() / "AppData" / "Local"
    elif system == "darwin":
        base = _home() / "Library" / "Caches"
    else:
        xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
        base = Path(xdg) if xdg else _home() / ".cache"
    return base / "ms-playwright"


def ensure_user_browsers_path() -> None:
    """Frozen exe: point PLAYWRIGHT_BROWSERS_PATH at the per-user folder unless it is set.

    Playwright's Python package sets it to "0" (browsers inside the bundle) for frozen apps,
    but the exe ships no browser: Chromium is downloaded once per user (`install-browser`) to
    the folder a regular Playwright install uses, and found there by every later launch.
    """
    if is_frozen() and not os.environ.get(BROWSERS_PATH_ENV, "").strip():
        os.environ[BROWSERS_PATH_ENV] = str(default_browsers_dir())


def _headless_shell_files() -> tuple[str, ...]:
    """Path of the headless shell inside its revision folder, for this OS and CPU."""
    system = _platform()
    arm = _machine() in ("arm64", "aarch64")
    if system == "win32":
        return ("chrome-headless-shell-win64", "chrome-headless-shell.exe")
    if system == "darwin":
        folder = "chrome-headless-shell-mac-arm64" if arm else "chrome-headless-shell-mac-x64"
        return (folder, "chrome-headless-shell")
    folder = "chrome-headless-shell-linux-arm64" if arm else "chrome-headless-shell-linux64"
    return (folder, "chrome-headless-shell")


def _browsers_json_entry(name: str) -> dict[str, Any] | None:
    try:
        data = json.loads(browsers_json_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("Cannot read Playwright's browsers.json: %s", error)
        return None
    for entry in data.get("browsers", []) if isinstance(data, dict) else []:
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    return None


def chromium_info() -> BrowserInfo:
    """Playwright's headless Chromium for the installed playwright version."""
    label = BROWSER_LABELS["chromium"]
    entry = _browsers_json_entry(HEADLESS_SHELL) or _browsers_json_entry("chromium")
    revision = str(entry.get("revision") or "") if entry else ""
    if not revision:
        return BrowserInfo(name="chromium", label=label, available=False, detail=CHROMIUM_UNKNOWN)
    folder = f"{HEADLESS_SHELL.replace('-', '_')}-{revision}"
    executable = playwright_browsers_dir().joinpath(folder, *_headless_shell_files())
    if not executable.is_file():
        return BrowserInfo(name="chromium", label=label, available=False, detail=CHROMIUM_MISSING)
    version = entry.get("browserVersion") if entry else None
    return BrowserInfo(
        name="chromium",
        label=label,
        available=True,
        version=str(version) if version else None,
        path=str(executable),
    )


def channel_candidates(name: BrowserName) -> list[Path]:
    """Executables Playwright tries for channel `name` ("msedge" / "chrome"), in order."""
    system = _platform()
    if system == "win32":
        drive = os.environ.get("HOMEDRIVE", "").strip()
        prefixes = [
            os.environ.get("LOCALAPPDATA", "").strip(),
            os.environ.get("PROGRAMFILES", "").strip(),
            os.environ.get("PROGRAMFILES(X86)", "").strip(),
            f"{drive}\\Program Files" if drive else "",
            f"{drive}\\Program Files (x86)" if drive else "",
        ]
        unique = list(dict.fromkeys(prefix for prefix in prefixes if prefix))
        return [Path(prefix).joinpath(*_WINDOWS_SUFFIX[name]) for prefix in unique]
    if system == "darwin":
        return [Path(_MAC_APP[name]) / "Contents" / "MacOS" / _MAC_BINARY[name]]
    return [Path(_LINUX_PATH[name])]


def _windows_file_version(path: Path) -> str | None:
    """FileVersion of a Windows executable from its version resource (no process started)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    try:
        version_dll = ctypes.WinDLL("version", use_last_error=True)
        size = version_dll.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(str(path), 0, size, buffer):
            return None
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version_dll.VerQueryValueW(
            buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)
        ):
            return None
        if not pointer.value or length.value < 16:
            return None
        # VS_FIXEDFILEINFO: signature, struct version, then FileVersionMS / FileVersionLS.
        words = (ctypes.c_uint32 * 4).from_address(pointer.value)
        if words[0] != 0xFEEF04BD:
            return None
        most, least = words[2], words[3]
        return f"{most >> 16}.{most & 0xFFFF}.{least >> 16}.{least & 0xFFFF}"
    except (AttributeError, OSError, ValueError):
        return None


def _version_from_folders(executable: Path) -> str | None:
    """Highest version-named folder next to a Chromium-based executable (Windows layout)."""
    try:
        names = [
            child.name
            for child in executable.parent.iterdir()
            if child.is_dir() and _VERSION_DIR_RE.match(child.name)
        ]
    except OSError:
        return None
    if not names:
        return None
    return max(names, key=lambda name: tuple(int(part) for part in name.split(".")))


def _mac_bundle_version(executable: Path) -> str | None:
    plist = executable.parent.parent / "Info.plist"
    try:
        with plist.open("rb") as handle:
            data = plistlib.load(handle)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    version = data.get("CFBundleShortVersionString") if isinstance(data, dict) else None
    return str(version) if version else None


def executable_version(executable: Path) -> str | None:
    """Version of an installed Edge / Chrome when it can be read from disk, else None."""
    system = _platform()
    if system == "win32":
        return _windows_file_version(executable) or _version_from_folders(executable)
    if system == "darwin":
        return _mac_bundle_version(executable)
    return None


def channel_info(name: BrowserName) -> BrowserInfo:
    """The installed stable Microsoft Edge / Google Chrome."""
    for candidate in channel_candidates(name):
        if candidate.is_file():
            return BrowserInfo(
                name=name,
                label=BROWSER_LABELS[name],
                available=True,
                version=executable_version(candidate),
                path=str(candidate),
            )
    return BrowserInfo(
        name=name, label=BROWSER_LABELS[name], available=False, detail=NOT_INSTALLED[name]
    )


def browser_status() -> BrowserStatus:
    """Availability of chromium / msedge / chrome (see the module docstring)."""
    browsers: list[BrowserInfo] = []
    for name in AUTO_ORDER:
        try:
            info = chromium_info() if name == "chromium" else channel_info(name)
        except Exception as error:  # never break the status endpoint
            logger.warning("Cannot check browser %s: %s", name, error)
            info = BrowserInfo(
                name=name,
                label=BROWSER_LABELS[name],
                available=False,
                detail="Không kiểm tra được trình duyệt này.",
            )
        browsers.append(info)
    auto = next((info.name for info in browsers if info.available), None)
    return BrowserStatus(browsers=browsers, auto=auto)


# ─── Installing Chromium ─────────────────────────────────────────────────────


def install_command() -> list[str]:
    """Command line that installs Playwright's Chromium (see the module docstring)."""
    if is_frozen():
        from get_design_markdown.ui.app import INSTALL_BROWSER_COMMAND

        return [sys.executable, INSTALL_BROWSER_COMMAND]
    return [sys.executable, "-m", "playwright", "install", "chromium"]


def _creation_flags() -> int:
    """CREATE_NO_WINDOW on Windows: no console window flashes up for a child process."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _write_line(stream: IO[str] | None, text: str) -> None:
    if stream is None:
        return
    try:
        stream.write(text + "\n")
        stream.flush()
    except (OSError, ValueError):
        pass


def run_install_cli(argv: Sequence[str] = ()) -> int:
    """The exe's hidden `install-browser` sub-command; returns the installer's exit code.

    Runs `<driver node> <driver cli.js> install [argv...] chromium` with the driver's
    environment. The driver writes straight to this process's standard output (the pipe of
    BrowserInstaller, or the console), so its progress lines reach the caller unchanged.
    """
    ensure_user_browsers_path()
    try:
        from playwright._impl._driver import get_driver_env

        node, cli = driver_executable()
    except Exception as error:  # a bundle built without playwright
        _write_line(sys.stderr, f"Lỗi: không tìm thấy Playwright trong bản cài này ({error}).")
        return 1
    if not node.is_file() or not cli.is_file():
        _write_line(
            sys.stderr,
            f"Lỗi: thiếu trình điều khiển Playwright ({node.name}, {cli.name}) trong bản cài này.",
        )
        return 1
    _write_line(sys.stdout, f"Đang tải Chromium cho Playwright vào {playwright_browsers_dir()}…")
    command = [str(node), str(cli), "install", *argv, "chromium"]
    try:
        completed = subprocess.run(
            command,
            env=get_driver_env(),
            stdin=subprocess.DEVNULL,
            creationflags=_creation_flags(),
            check=False,
        )
    except OSError as error:
        _write_line(sys.stderr, f"Lỗi: không chạy được trình cài Chromium ({error}).")
        return 1
    except KeyboardInterrupt:
        return 130
    return completed.returncode


#: A progress figure such as " 40%" in Playwright's download lines.
_PERCENT_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s?%")
#: "Downloading Chrome Headless Shell 156.0.8078.4 (playwright chromium-headless-shell v1248)
#: from https://..."
_DOWNLOADING_RE = re.compile(r"^Downloading\s+(?P<title>.+?)(?:\s+\(playwright\b.*)?$", re.I)
_DOWNLOADED_RE = re.compile(r"\bdownloaded to\b", re.I)
_VERSION_SUFFIX_RE = re.compile(r"\s+v?\d+(?:\.\d+)+\s*$")
_NETWORK_RE = re.compile(
    r"ENOTFOUND|ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN|getaddrinfo|socket hang up|"
    r"network|proxy|certificate|self.signed|unable to (?:get|verify)",
    re.I,
)
_DISK_RE = re.compile(r"ENOSPC|no space left", re.I)
_PERMISSION_RE = re.compile(r"EPERM|EACCES|EBUSY|permission denied|access is denied", re.I)

INSTALL_STARTING = "Đang chuẩn bị tải Chromium…"
INSTALL_SUCCEEDED = "Đã tải xong Chromium. Từ giờ công cụ có thể dùng Chromium của Playwright."
INSTALL_RETRY_HINT = (
    'Bấm "Tải Chromium" để thử lại. Bạn cũng có thể cài Microsoft Edge hoặc Google Chrome: '
    "công cụ tự dùng trình duyệt có sẵn."
)


def parse_progress(line: str) -> float | None:
    """The last percentage in an installer output line (0-100), or None; pure."""
    matches = _PERCENT_RE.findall(line)
    if not matches:
        return None
    value = float(matches[-1])
    return value if 0 <= value <= 100 else None


def download_title(line: str) -> str | None:
    """The download title of a "Downloading Chrome Headless Shell 156.0... from ..." line
    ("Chrome Headless Shell"), None for any other line; pure."""
    match = _DOWNLOADING_RE.match(line.strip())
    if match is None:
        return None
    title = re.split(r"\s+from\s+https?://", match.group("title"), maxsplit=1)[0]
    title = _VERSION_SUFFIX_RE.sub("", title).strip()
    return title or "Chromium"


def failure_hint(lines: Iterable[str]) -> str:
    """Vietnamese hint for a failed install, guessed from the installer's last lines; pure."""
    text = "\n".join(lines)
    if _DISK_RE.search(text):
        return (
            "Ổ đĩa không đủ chỗ trống (Chromium cần khoảng 300 MB). Giải phóng dung lượng rồi "
            "thử lại. " + INSTALL_RETRY_HINT
        )
    if _PERMISSION_RE.search(text):
        return (
            f"Không ghi được vào thư mục {playwright_browsers_dir()} (thiếu quyền hoặc tệp đang "
            "bị khóa). Đóng các cửa sổ Chromium đang mở rồi thử lại. " + INSTALL_RETRY_HINT
        )
    if _NETWORK_RE.search(text):
        return (
            "Kiểm tra kết nối Internet (hoặc proxy, tường lửa của công ty) rồi thử lại. "
            + INSTALL_RETRY_HINT
        )
    return INSTALL_RETRY_HINT


def _now() -> datetime:
    return datetime.now(UTC)


class BrowserInstaller:
    """One background Chromium install at a time; thread-safe."""

    def __init__(self, command: Sequence[str] | None = None) -> None:
        #: None: install_command() when start() runs (tests inject a fake command).
        self.command = list(command) if command is not None else None
        self._lock = threading.Lock()
        self._status = InstallStatus()
        self._tail: deque[str] = deque(maxlen=LOG_TAIL_LINES)
        self._finished = threading.Event()
        self._finished.set()
        self._part = 0
        self._title = "Chromium"

    def start(self) -> InstallStatus:
        """Start the install in the background (no-op while one is running)."""
        with self._lock:
            if self._status.state == "running":
                return self._snapshot()
            command = list(self.command) if self.command is not None else install_command()
            self._tail.clear()
            self._part = 0
            self._title = "Chromium"
            self._status = InstallStatus(
                state="running", message=INSTALL_STARTING, started_at=_now()
            )
            self._finished.clear()
            logger.info("Installing Chromium: %s", command)
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=self._environment(),
                    creationflags=_creation_flags(),
                )
            except (OSError, ValueError) as error:
                logger.warning("Cannot start the Chromium installer: %s", error)
                self._fail(
                    f"Không chạy được trình cài Chromium: {error}",
                    hint=INSTALL_RETRY_HINT,
                )
                return self._snapshot()
            thread = threading.Thread(
                target=self._pump, args=(process,), name="gdm-chromium-install", daemon=True
            )
            thread.start()
            return self._snapshot()

    def status(self) -> InstallStatus:
        """Current progress ("idle" before the first start())."""
        with self._lock:
            return self._snapshot()

    def wait(self, timeout: float | None = None) -> InstallStatus:
        """Block until the running install ends (or `timeout` seconds passed)."""
        self._finished.wait(timeout)
        return self.status()

    # ── internals (called with or without the lock as noted) ──

    @staticmethod
    def _environment() -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def _snapshot(self) -> InstallStatus:
        """Copy of the status with the current log tail (lock held)."""
        return self._status.model_copy(update={"log_tail": list(self._tail)}, deep=True)

    def _fail(self, message: str, *, hint: str | None) -> None:
        """Mark the install failed (lock held)."""
        self._status = self._status.model_copy(
            update={
                "state": "failed",
                "message": message,
                "finished_at": _now(),
                "error": InstallError(message=message, hint=hint),
            }
        )
        self._finished.set()

    def _pump(self, process: subprocess.Popen[bytes]) -> None:
        """Read the installer output until it exits, then record the outcome."""
        try:
            assert process.stdout is not None
            for raw in iter(process.stdout.readline, b""):
                for piece in raw.replace(b"\r", b"\n").split(b"\n"):
                    line = piece.decode("utf-8", errors="replace").strip()
                    if line:
                        self._on_line(line)
            code = process.wait()
        except Exception as error:  # keep the UI informed whatever happens
            logger.exception("Chromium installer output could not be read")
            with self._lock:
                self._fail(f"Lỗi khi theo dõi trình cài Chromium: {error}", hint=INSTALL_RETRY_HINT)
            return
        finally:
            if process.stdout is not None:
                process.stdout.close()
        with self._lock:
            if code == 0:
                self._status = self._status.model_copy(
                    update={
                        "state": "succeeded",
                        "percent": 100.0,
                        "message": INSTALL_SUCCEEDED,
                        "finished_at": _now(),
                        "error": None,
                    }
                )
                self._finished.set()
            else:
                self._fail(
                    f"Không tải được Chromium (trình cài kết thúc với mã lỗi {code}).",
                    hint=failure_hint(self._tail),
                )
        logger.info("Chromium installer exited with code %s", code)

    def _on_line(self, line: str) -> None:
        with self._lock:
            self._tail.append(line)
            title = download_title(line)
            if title is not None:
                self._part += 1
                self._title = title
                self._status = self._status.model_copy(
                    update={"percent": 0.0, "message": self._progress_message(0.0)}
                )
                return
            if _DOWNLOADED_RE.search(line):
                self._status = self._status.model_copy(
                    update={"percent": 100.0, "message": f"Đã tải xong {self._title}."}
                )
                return
            percent = parse_progress(line)
            if percent is not None:
                self._status = self._status.model_copy(
                    update={"percent": percent, "message": self._progress_message(percent)}
                )

    def _progress_message(self, percent: float) -> str:
        part = f" (phần {self._part})" if self._part > 1 else ""
        return f"Đang tải {self._title}{part}… {percent:.0f}%"
