"""Starting the local UI: `gdm ui` (run_ui) and `gdm-ui` / the Windows exe (main).

Frozen interface: the signatures, constants and self-test report models below.
Implemented here: run_ui, main, run_self_test, redirect_std_streams, open_window.

run_ui(port, mode, out_root)
----------------------------
Starts server.UiServer(mode=mode, out_root=out_root, port=port), prints (Vietnamese) the
address and how to stop it, then:
- mode "browser": opens server.launch_url in the default browser (webbrowser.open) and waits
  until the server stops (idle shutdown after 2 minutes without heartbeat, /api/shutdown);
- mode "window": open_window(server.launch_url) — a pywebview window titled WINDOW_TITLE,
  WINDOW_SIZE, min size WINDOW_MIN_SIZE; closing it stops the server. When pywebview or
  its engine is missing (no `gui` extra, no WebView2 runtime on Windows) it prints a Vietnamese
  warning and falls back to mode "browser";
- mode "none": prints launch_url only and serves until Ctrl+C.
Ctrl+C stops the server cleanly (a running job is cancelled). Errors are GdmError.

main(argv) — entry point of `gdm-ui` and of the exe (Get-Design-Markdown.exe)
-----------------------------------------------------------------------------
argparse, Vietnamese help:
    Get-Design-Markdown.exe                      window (falls back to a browser tab)
    Get-Design-Markdown.exe --browser-tab        browser tab instead of the window
    Get-Design-Markdown.exe --port N             fixed port (default 0 = free port)
    Get-Design-Markdown.exe --no-open            serve only (mode "none"), print launch_url
    Get-Design-Markdown.exe --self-test [--self-test-report PATH]
    Get-Design-Markdown.exe install-browser      (hidden) browser_install.run_install_cli()
- First thing, before anything can print: redirect_std_streams() — in the windowed exe
  sys.stdout / sys.stderr are None; they are replaced by log_dir()/ui.log (append, UTF-8,
  line-buffered) and the `logging` root logger writes there too. With a real console nothing
  changes.
- `--self-test` runs run_self_test(), writes the report (JSON, SelfTestReport) to PATH or
  log_dir()/self-test.json, and exits 0 when every step passed, else 1. It never opens a window
  or a browser tab, so CI can run it with `Start-Process -Wait -PassThru`.
- Unexpected errors in the windowed exe are logged and shown in a native message box when
  possible (they cannot be printed), exit code 1.
- The exe's output root defaults to <Documents>/Get-Design-Markdown (settings.UiSettings.
  defaults(); frozen exe), `gdm-ui` from a checkout to GDM_OUTPUT_DIR or ./output.

Self-test steps (SELF_TEST_STEPS, in order; each one timed, a failing step does not stop the
others):
1. "imports":   import every module of the package (pkgutil.walk_packages), playwright,
                markdown_it, and webview when the exe was built with it.
2. "resources": read every packaged resource: collector/js/*.js, synthesizer/phrases/*.yaml,
                templates/*.j2, ui/static/* (resources.resource_path / resource_text).
3. "server":    start UiServer(mode="none", port=0) on a temporary output root and settings
                file, GET /api/health with the token (expects 200 and ok=true), stop it.
4. "browser":   open_browser("auto") from the collector, load about:blank, record the session
                label (e.g. "msedge 141.0.3537.71") in the step detail.
5. "markdown":  render_md.render_markdown() on a small DESIGN.md with front matter and a
                table; the front matter is split off and raw HTML is escaped.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import faulthandler
import importlib
import importlib.util
import io
import json
import logging
import os
import pkgutil
import platform
import re
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn, TextIO

from pydantic import Field

from get_design_markdown import __version__
from get_design_markdown.console import console
from get_design_markdown.errors import GdmError
from get_design_markdown.models.base import GdmModel
from get_design_markdown.resources import is_frozen, resource_path, template_environment
from get_design_markdown.ui.browser_install import ensure_user_browsers_path, run_install_cli
from get_design_markdown.ui.server import TOKEN_HEADER, UiMode, UiServer
from get_design_markdown.ui.settings import LOG_FILE, config_dir, log_dir

logger = logging.getLogger(__name__)

WINDOW_TITLE = "Get Design Markdown"
WINDOW_SIZE = (1280, 860)
WINDOW_MIN_SIZE = (1024, 680)
SELF_TEST_STEPS = ("imports", "resources", "server", "browser", "markdown")
SELF_TEST_REPORT_FILE = "self-test.json"
INSTALL_BROWSER_COMMAND = "install-browser"


class SelfTestStep(GdmModel):
    name: str
    ok: bool
    seconds: float = Field(ge=0)
    #: Short English or Vietnamese detail: what was checked, or the error (first line).
    detail: str = ""


class SelfTestReport(GdmModel):
    ok: bool
    version: str
    frozen: bool
    python: str
    platform: str
    started_at: datetime
    steps: list[SelfTestStep] = Field(default_factory=list)


# ─── Messages ────────────────────────────────────────────────────────────────

#: Program name shown in the help of the exe and of `gdm-ui`.
EXE_PROG = "Get-Design-Markdown.exe"
SCRIPT_PROG = "gdm-ui"
#: Title of the native message boxes of the windowed exe.
MESSAGE_BOX_TITLE = "Get Design Markdown"
WEBVIEW2_DOWNLOAD_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
PYWEBVIEW_MISSING = (
    "chưa cài gói pywebview (từ mã nguồn: uv sync --extra gui; "
    'từ file wheel: pip install "<file wheel>[gui]")'
)
WEBVIEW2_MISSING = (
    f"máy chưa có Microsoft Edge WebView2 Runtime (tải miễn phí tại {WEBVIEW2_DOWNLOAD_URL})"
)
WINDOW_FALLBACK = "Không mở được cửa sổ riêng: {reason}. Giao diện sẽ mở trong tab trình duyệt."
ENGINE_FAILED = "trình hiển thị của cửa sổ không khởi động được"
#: Polling step of the waits, so Ctrl+C is handled promptly on Windows.
WAIT_STEP_S = 0.5
#: The log file is started afresh (previous one kept as ui.log.1) above this size.
LOG_ROTATE_BYTES = 5 * 1024 * 1024
#: Upper bound of the self-test's browser step (seconds).
SELF_TEST_BROWSER_TIMEOUT_S = 120.0
SELF_TEST_HTTP_TIMEOUT_S = 15.0
#: Minimum WebView2 runtime pywebview's Edge Chromium renderer accepts.
MIN_WEBVIEW2_VERSION = (86, 0, 622, 0)
#: EdgeUpdate client ids of the WebView2 runtime (stable, beta, dev, canary).
WEBVIEW2_CLIENT_IDS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",
)

_LOG_HANDLER_NAME = "gdm-ui-log"


def _say(message: str) -> None:
    """A plain (non-markup) line on the console: the terminal, or ui.log in the windowed exe."""
    console.print(message, markup=False, highlight=False, soft_wrap=True)


def _error_text(error: BaseException) -> str:
    """Vietnamese message (and hint) of a GdmError, else '<Type>: <first line>'."""
    if isinstance(error, GdmError):
        return error.message + (f"\nGợi ý: {error.hint}" if error.hint else "")
    text = str(error).strip()
    first = text.splitlines()[0] if text else ""
    return f"{type(error).__name__}: {first}" if first else type(error).__name__


# ─── Native window ───────────────────────────────────────────────────────────


def _platform() -> str:
    """sys.platform (a function so tests can simulate other systems)."""
    return sys.platform


def _import_webview() -> ModuleType | None:
    """The pywebview module, or None when the `gui` extra is not installed or cannot load."""
    try:
        import webview  # a plain import statement: PyInstaller then bundles pywebview
    except Exception as error:  # ImportError, or a broken install
        logger.info("pywebview is not available: %s", error)
        return None
    # Typed the same with and without the `gui` extra installed (mypy sees Any without it).
    module: ModuleType = webview
    return module


def _parse_version(text: str) -> tuple[int, ...] | None:
    try:
        parts = tuple(int(part) for part in text.strip().split("."))
    except ValueError:
        return None
    return parts if parts and any(parts) else None


def webview2_version() -> str | None:
    """Version of the Microsoft Edge WebView2 runtime (registry, as pywebview checks it), or
    None when it is missing, too old, or this is not Windows."""
    if sys.platform != "win32":
        return None
    import winreg

    machine_path = (
        r"SOFTWARE\Microsoft\EdgeUpdate\Clients"
        if platform.machine().lower() == "x86"
        else r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
    )
    locations = (
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, machine_path),
    )
    best: tuple[tuple[int, ...], str] | None = None
    for client in WEBVIEW2_CLIENT_IDS:
        for hive, base in locations:
            try:
                with winreg.OpenKey(hive, f"{base}\\{client}") as key:
                    value, _ = winreg.QueryValueEx(key, "pv")
            except OSError:
                continue
            parsed = _parse_version(str(value))
            if (
                parsed is not None
                and parsed >= MIN_WEBVIEW2_VERSION
                and (best is None or parsed > best[0])
            ):
                best = (parsed, str(value))
    return best[1] if best is not None else None


def window_problem() -> str | None:
    """Why a native window cannot be shown (Vietnamese, lower-case clause), None when it can.

    Cheap: imports pywebview and, on Windows, reads the WebView2 runtime version from the
    registry; no window or engine is started.
    """
    if _import_webview() is None:
        return PYWEBVIEW_MISSING
    if _platform() == "win32" and webview2_version() is None:
        return WEBVIEW2_MISSING
    return None


def _windows_engine_problem() -> str | None:
    """pywebview's own renderer choice on Windows: it silently uses the obsolete Internet
    Explorer engine when it cannot use WebView2 (the UI needs a modern engine)."""
    try:
        winforms = importlib.import_module("webview.platforms.winforms")
    except Exception as error:  # pythonnet / .NET could not load
        return f"không nạp được thành phần cửa sổ của Windows ({_error_text(error)})"
    renderer = getattr(winforms, "renderer", "edgechromium")
    if renderer != "edgechromium":
        return WEBVIEW2_MISSING
    return None


def _prefers_dark() -> bool:
    """Windows "dark mode for apps" (used for the window background before the page paints)."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except OSError:
        return False
    return bool(value == 0)


def webview_storage_dir() -> Path:
    """Profile folder of the window's web engine: <config folder>/webview (GDM_UI_CONFIG_DIR
    moves it with the settings). A persistent profile keeps the page's own cookies, so the
    dismissed first-run guide and a manual light / dark choice survive a new launch; the page
    only ever loads the local UI, and its session token lives in sessionStorage."""
    return config_dir() / "webview"


def open_window(url: str) -> bool:
    """Show `url` in a pywebview window and block until it is closed; False (without opening
    anything) when pywebview or its engine is unavailable."""
    problem = window_problem()
    webview = _import_webview() if problem is None else None
    if problem is None and webview is not None and _platform() == "win32":
        problem = _windows_engine_problem()
    if problem is not None or webview is None:
        logger.warning("Native window unavailable: %s", problem)
        return False
    window: Any = None
    try:
        # Lets the "Tải về" buttons save DESIGN.md (a Save dialog); a pywebview without this
        # setting simply keeps downloads disabled.
        with contextlib.suppress(KeyError, TypeError, AttributeError):
            webview.settings["ALLOW_DOWNLOADS"] = True
        window = webview.create_window(
            WINDOW_TITLE,
            url,
            width=WINDOW_SIZE[0],
            height=WINDOW_SIZE[1],
            min_size=WINDOW_MIN_SIZE,
            background_color="#111318" if _prefers_dark() else "#FFFFFF",
            text_select=True,
        )
        webview.start(private_mode=False, storage_path=str(webview_storage_dir()))
    except Exception as error:
        shown = window is not None and _event_is_set(getattr(window, "events", None), "shown")
        logger.warning("The native window failed (shown=%s): %s", shown, error, exc_info=True)
        return shown
    return True


def _event_is_set(events: Any, name: str) -> bool:
    event = getattr(events, name, None)
    is_set = getattr(event, "is_set", None)
    try:
        return bool(is_set()) if callable(is_set) else False
    except Exception:
        return False


# ─── run_ui ──────────────────────────────────────────────────────────────────


def _open_tab(url: str) -> bool:
    """Open `url` in the default browser; False when no browser could be started."""
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception as error:
        logger.warning("Cannot open the browser: %s", error)
        return False


def _wait_until_stopped(server: UiServer) -> None:
    """Block until the server stops, in short steps so Ctrl+C is handled on Windows."""
    while not server.wait(timeout=WAIT_STEP_S):
        pass


def _announce(server: UiServer, mode: UiMode) -> None:
    _say(f"Giao diện Get Design Markdown đang chạy tại {server.url}")
    if mode == "window":
        _say("Đang mở cửa sổ giao diện… Đóng cửa sổ (hoặc nhấn Ctrl+C) để dừng.")
    elif mode == "browser":
        _say(
            "Đang mở giao diện trong trình duyệt. Nếu trình duyệt không tự mở, hãy dán địa chỉ "
            "sau (gồm mã truy cập của phiên này) vào thanh địa chỉ:"
        )
        _say(f"  {server.launch_url}")
        _say("Giao diện tự dừng khoảng 2 phút sau khi bạn đóng tab. Nhấn Ctrl+C để dừng ngay.")
    else:
        _say("Mở địa chỉ sau trong trình duyệt (gồm mã truy cập của phiên này):")
        _say(f"  {server.launch_url}")
        _say("Nhấn Ctrl+C để dừng.")


def run_ui(*, port: int = 0, mode: UiMode = "browser", out_root: Path | None = None) -> None:
    """`gdm ui`: start the server and show it (see the module docstring)."""
    if mode == "window":
        problem = window_problem()
        if problem is not None:
            _say(WINDOW_FALLBACK.format(reason=problem))
            mode = "browser"
    server = UiServer(mode=mode, out_root=out_root, port=port)
    server.start()
    try:
        if mode == "window":
            _announce(server, mode)
            if not open_window(server.launch_url):
                # The engine failed before anything was shown: serve a browser tab instead
                # (restarted in mode "browser", so it stops by itself once the tab is closed).
                server.stop()
                _say(WINDOW_FALLBACK.format(reason=ENGINE_FAILED))
                mode = "browser"
                replacement = UiServer(mode=mode, out_root=out_root, port=port)
                replacement.start()
                server = replacement
        if mode != "window":
            _announce(server, mode)
            if mode == "browser" and not _open_tab(server.launch_url):
                _say("Không tự mở được trình duyệt — hãy tự mở địa chỉ ở trên.")
            _wait_until_stopped(server)
    except KeyboardInterrupt:
        _say("Đang dừng giao diện…")
    finally:
        server.stop()
    _say("Đã dừng giao diện Get Design Markdown.")


# ─── Logging for the windowed exe ────────────────────────────────────────────


def _open_log(path: Path) -> TextIO:
    """ui.log opened for appending (UTF-8, line-buffered); an oversized log is rotated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_ROTATE_BYTES:
            path.replace(path.with_name(path.name + ".1"))
    except OSError:
        pass
    return open(path, "a", encoding="utf-8", errors="backslashreplace", buffering=1)


def _log_to(stream: TextIO) -> None:
    """Route the `logging` root logger (INFO and above) to `stream`, once."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.get_name() == _LOG_HANDLER_NAME:
            root.removeHandler(handler)
            handler.close()
    handler = logging.StreamHandler(stream)
    handler.set_name(_LOG_HANDLER_NAME)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)


def _log_thread_exception(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    logger.error(
        "Unhandled error in thread %s",
        args.thread.name if args.thread is not None else "?",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),  # type: ignore[arg-type]
    )


def redirect_std_streams() -> Path | None:
    """Point None sys.stdout / sys.stderr at log_dir()/ui.log; returns the log path, or None
    when both streams were usable and nothing changed."""
    if sys.stdout is not None and sys.stderr is not None:
        return None
    stream: TextIO
    path: Path | None = None
    for candidate in (log_dir() / LOG_FILE, Path(tempfile.gettempdir()) / "gdm-ui.log"):
        try:
            stream = _open_log(candidate)
        except OSError:
            continue
        path = candidate
        break
    else:  # nowhere to write: discard the output rather than crash on print()
        stream = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    _log_to(stream)
    threading.excepthook = _log_thread_exception
    if path is not None:
        with contextlib.suppress(OSError, ValueError, io.UnsupportedOperation):
            faulthandler.enable(stream)
        stream.write(
            f"\n===== Get Design Markdown {__version__} — "
            f"{datetime.now().astimezone().isoformat(timespec='seconds')} =====\n"
        )
    return path


def _ensure_utf8_stdio() -> None:
    """Consoles and pipes on Windows default to a legacy code page; Vietnamese needs UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower() not in (
            "utf-8",
            "utf8",
        ):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def show_message_box(text: str, *, error: bool = True) -> bool:
    """A native Windows message box (the windowed exe cannot print); False elsewhere."""
    if sys.platform != "win32":
        return False
    import ctypes

    flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
    flags |= 0x10000 | 0x40000  # MB_SETFOREGROUND | MB_TOPMOST
    try:
        ctypes.windll.user32.MessageBoxW(None, text, MESSAGE_BOX_TITLE, flags)
    except (AttributeError, OSError):
        return False
    return True


# ─── Self-test ───────────────────────────────────────────────────────────────

#: Packaged files the exe must carry (a missing one means a broken build).
REQUIRED_RESOURCES: tuple[tuple[str, ...], ...] = (
    ("collector", "js", "extract.js"),
    ("synthesizer", "phrases", "en.yaml"),
    ("synthesizer", "phrases", "vi.yaml"),
    ("templates", "design.md.j2"),
    ("templates", "preview.html.j2"),
    ("templates", "prompt.md.j2"),
    ("ui", "static", "index.html"),
)
#: (folder, glob) of every packaged resource group the self-test reads.
RESOURCE_GROUPS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("collector", "js"), "*.js"),
    (("synthesizer", "phrases"), "*.yaml"),
    (("templates",), "*.j2"),
    (("ui", "static"), "**/*"),
)
_TEXT_SUFFIXES = frozenset({".js", ".yaml", ".j2", ".html", ".css", ".json", ".svg", ".txt", ".md"})

SELF_TEST_MARKDOWN = """---
version: alpha
name: Self-test
colors:
  primary: "#635bff"
---
# Kiểm tra Markdown

| Token | Giá trị |
|---|---|
| primary | #635bff |

<script>alert("x")</script>
"""


def _module_version(module: ModuleType, *attributes: str) -> str:
    for attribute in attributes:
        value = getattr(module, attribute, None)
        if isinstance(value, str) and value:
            return value
    return "?"


def _webview_bundled() -> bool:
    """pywebview is part of this install (the exe is built with it; `gdm-ui` needs the `gui`
    extra): then its import is checked too."""
    return importlib.util.find_spec("webview") is not None


def check_imports() -> str:
    """Self-test step "imports"."""
    import get_design_markdown

    count = 0
    failures: list[str] = []

    def failed(name: str) -> None:
        failures.append(name)

    for module in pkgutil.walk_packages(
        get_design_markdown.__path__, prefix="get_design_markdown.", onerror=failed
    ):
        if module.name.rsplit(".", 1)[-1] == "__main__":
            continue  # running it would start the CLI
        try:
            importlib.import_module(module.name)
        except Exception as error:
            failures.append(f"{module.name} ({_error_text(error)})")
            continue
        count += 1
    playwright_version = _module_version(
        importlib.import_module("playwright._repo_version"), "version", "__version__"
    )
    importlib.import_module("playwright.async_api")
    markdown_it = importlib.import_module("markdown_it")
    parts = [
        f"{count} module",
        f"playwright {playwright_version}",
        f"markdown-it-py {_module_version(markdown_it, '__version__')}",
    ]
    if _webview_bundled():
        webview = importlib.import_module("webview")
        version = _module_version(webview, "__version__")
        if version == "?":
            with contextlib.suppress(ImportError):
                version = _module_version(
                    importlib.import_module("webview._version"), "__version__", "version"
                )
        engine = ""
        if sys.platform == "win32":
            winforms = importlib.import_module("webview.platforms.winforms")
            engine = f" ({getattr(winforms, 'renderer', '?')})"
        parts.append(f"pywebview {version}{engine}")
    else:
        parts.append("pywebview: không có")
    if failures:
        raise RuntimeError("Không import được: " + ", ".join(failures))
    if count == 0:
        raise RuntimeError("Không tìm thấy module nào của get_design_markdown.")
    return ", ".join(parts)


def check_resources() -> str:
    """Self-test step "resources"."""
    for parts in REQUIRED_RESOURCES:
        resource_path(*parts)  # FileNotFoundError names the missing file
    counts: list[str] = []
    for folder_parts, pattern in RESOURCE_GROUPS:
        folder = resource_path(*folder_parts)
        files = sorted(path for path in folder.glob(pattern) if path.is_file())
        if not files:
            raise FileNotFoundError(f"Thư mục tài nguyên trống: {'/'.join(folder_parts)}")
        for path in files:
            if path.suffix.lower() in _TEXT_SUFFIXES:
                path.read_text(encoding="utf-8")
            else:
                path.read_bytes()
        counts.append(f"{len(files)} {'/'.join(folder_parts)}")
    environment = template_environment()
    templates = environment.list_templates(extensions=["j2"])
    for name in templates:
        environment.get_template(name)
    from get_design_markdown.ui.browser_install import browsers_json_path, driver_executable

    node, cli = driver_executable()
    for path in (node, cli, browsers_json_path()):
        if not path.is_file():
            raise FileNotFoundError(f"Thiếu trình điều khiển Playwright: {path}")
    json.loads(browsers_json_path().read_text(encoding="utf-8"))
    return ", ".join([*counts, f"{len(templates)} template biên dịch được", "Playwright driver"])


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    """HTTP client for 127.0.0.1 that ignores HTTP(S)_PROXY settings."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def check_server() -> str:
    """Self-test step "server"."""
    opener = _no_proxy_opener()
    with tempfile.TemporaryDirectory(prefix="gdm-self-test-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        server = UiServer(
            mode="none",
            out_root=root / "output",
            settings_path=root / "ui-settings.json",
            port=0,
            idle_timeout_s=None,
        )
        server.start()
        try:
            request = urllib.request.Request(
                f"{server.url}api/health", headers={TOKEN_HEADER: server.token}
            )
            with opener.open(request, timeout=SELF_TEST_HTTP_TIMEOUT_S) as response:
                status = response.status
                payload = json.loads(response.read().decode("utf-8"))
            if status != 200 or not isinstance(payload, dict) or payload.get("ok") is not True:
                raise RuntimeError(f"/api/health trả về HTTP {status}: {payload!r}")
            with opener.open(server.url, timeout=SELF_TEST_HTTP_TIMEOUT_S) as response:
                index_status = response.status
                index = response.read().decode("utf-8", errors="replace")
            if index_status != 200 or "<html" not in index.lower():
                raise RuntimeError(f"Trang chính trả về HTTP {index_status}.")
            url = server.url
        finally:
            server.stop()
    return (
        f"{url} — /api/health ok (phiên bản {payload.get('version')}, api "
        f"{payload.get('api')}), trang chính ok"
    )


async def _probe_browser() -> str:
    from get_design_markdown.collector.browser import open_browser

    async with open_browser("auto") as session:
        page = await session.browser.new_page()
        try:
            await page.goto("about:blank")
            await page.evaluate("() => document.readyState")
        finally:
            await page.close()
        return session.label


def check_browser() -> str:
    """Self-test step "browser"."""

    async def bounded() -> str:
        return await asyncio.wait_for(_probe_browser(), timeout=SELF_TEST_BROWSER_TIMEOUT_S)

    label = asyncio.run(bounded())
    return f"auto -> {label}"


def check_markdown() -> str:
    """Self-test step "markdown"."""
    from get_design_markdown.ui.render_md import render_markdown

    front_matter, html = render_markdown(SELF_TEST_MARKDOWN)
    problems: list[str] = []
    if front_matter is None or "version: alpha" not in front_matter:
        problems.append("front matter chưa được tách")
    if "version: alpha" in html:
        problems.append("front matter bị render vào thân")
    if "<table" not in html:
        problems.append("bảng không được render")
    if "<script" in html.lower() or "&lt;script&gt;" not in html:
        problems.append("HTML thô không được escape")
    if problems:
        raise RuntimeError("; ".join(problems))
    return f"front matter + bảng + HTML thô đã escape ({len(html)} ký tự HTML)"


def _self_test_checks() -> dict[str, Callable[[], str]]:
    """The check of each SELF_TEST_STEPS name (looked up at call time; tests replace them)."""
    return {
        "imports": check_imports,
        "resources": check_resources,
        "server": check_server,
        "browser": check_browser,
        "markdown": check_markdown,
    }


def _run_step(name: str, check: Callable[[], str]) -> SelfTestStep:
    started = time.perf_counter()
    try:
        detail = check()
        ok = True
    except (Exception, SystemExit) as error:
        # INFO: the traceback lands in ui.log for the exe without cluttering a console run.
        logger.info("Self-test step %s failed", name, exc_info=True)
        detail = " ".join(_error_text(error).split())
        ok = False
    seconds = round(max(time.perf_counter() - started, 0.0), 3)
    return SelfTestStep(name=name, ok=ok, seconds=seconds, detail=detail[:500])


def run_self_test(report_path: Path | None = None, *, stream: TextIO | None = None) -> int:
    """Run SELF_TEST_STEPS, write the SelfTestReport JSON to `report_path` (default
    log_dir()/self-test.json), print a short summary to `stream` when given; 0 if all passed."""
    started_at = datetime.now(UTC)
    checks = _self_test_checks()
    steps = [_run_step(name, checks[name]) for name in SELF_TEST_STEPS]
    report = SelfTestReport(
        ok=all(step.ok for step in steps),
        version=__version__,
        frozen=is_frozen(),
        python=platform.python_version(),
        platform=platform.platform(),
        started_at=started_at,
        steps=steps,
    )
    target = (report_path or log_dir() / SELF_TEST_REPORT_FILE).expanduser().absolute()
    written = True
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        written = False
        logger.error("Cannot write the self-test report %s: %s", target, error)
    if stream is not None:
        kind = "bản exe" if report.frozen else "bản Python"
        lines = [f"Tự kiểm tra Get Design Markdown {__version__} ({kind}, Python {report.python})"]
        for step in steps:
            mark = "ĐẠT" if step.ok else "LỖI"
            lines.append(f"  [{mark}] {step.name} ({step.seconds:.2f} s): {step.detail}")
        passed = sum(step.ok for step in steps)
        lines.append(f"Kết quả: đạt {passed}/{len(steps)} bước.")
        lines.append(
            f"Báo cáo: {target}" if written else f"Lỗi: không ghi được báo cáo vào {target}."
        )
        try:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass
    logger.info("Self-test %s", "passed" if report.ok and written else "failed")
    return 0 if report.ok and written else 1


# ─── main ────────────────────────────────────────────────────────────────────


class _ParserExit(Exception):
    def __init__(self, status: int, message: str | None, *, usage_error: bool) -> None:
        super().__init__(message or "")
        self.status = status
        self.message = message
        self.usage_error = usage_error


#: argparse error messages (English, hard-coded) -> Vietnamese; full matches, in order.
_ARGPARSE_MESSAGES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"unrecognized arguments: (?P<args>.+)"), "Tham số không hợp lệ: {args}."),
    (
        re.compile(r"argument (?P<name>[^:]+): expected one argument"),
        "Thiếu giá trị cho {name}.",
    ),
    (
        re.compile(r"argument (?P<name>[^:]+): invalid \S+ value: (?P<value>.+)"),
        "Giá trị {value} không hợp lệ cho {name}.",
    ),
    (
        re.compile(r"argument (?P<name>[^:]+): not allowed with argument (?P<other>.+)"),
        "Không dùng {name} cùng với {other}.",
    ),
    (
        re.compile(r"argument (?P<name>[^:]+): ignored explicit argument (?P<value>.+)"),
        "{name} không nhận giá trị ({value}).",
    ),
    (
        re.compile(r"the following arguments are required: (?P<names>.+)"),
        "Thiếu tham số bắt buộc: {names}.",
    ),
    (re.compile(r"argument (?P<name>[^:]+): (?P<detail>.+)"), "{name}: {detail}"),
)


def translate_argparse_error(message: str) -> str:
    """Vietnamese text of an argparse error message; unknown ones are kept as they are."""
    for pattern, template in _ARGPARSE_MESSAGES:
        match = pattern.fullmatch(message.strip())
        if match is not None:
            return template.format(**match.groupdict())
    return message


class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    def add_usage(
        self,
        usage: str | None,
        actions: Any,
        groups: Any,
        prefix: str | None = None,
    ) -> None:
        super().add_usage(
            usage, actions, groups, prefix="Cách dùng: " if prefix is None else prefix
        )


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that raises instead of exiting, with Vietnamese error texts."""

    def error(self, message: str) -> NoReturn:
        text = f"{self.format_usage()}Lỗi: {translate_argparse_error(message)}\n"
        text += f"Gợi ý: chạy '{self.prog} --help' để xem hướng dẫn.\n"
        raise _ParserExit(2, text, usage_error=True)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise _ParserExit(status, message, usage_error=False)

    def print_help(self, file: Any = None) -> None:
        raise _ParserExit(0, self.format_help(), usage_error=False)


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(
            f"cổng phải là số nguyên từ 0 đến 65535 (nhận được {value!r})"
        )
    return port


def build_parser() -> argparse.ArgumentParser:
    """Command-line options of the exe and of `gdm-ui` (Vietnamese help)."""
    prog = EXE_PROG if is_frozen() else SCRIPT_PROG
    parser = _Parser(
        prog=prog,
        add_help=False,
        formatter_class=_HelpFormatter,
        description=(
            "Giao diện đồ họa của Get Design Markdown: nhập URL một trang web, chỉnh tùy\n"
            "chọn và xem DESIGN.md, bảng màu, kiểu chữ, ảnh chụp.\n"
            "Mặc định mở cửa sổ riêng; nếu máy thiếu WebView2 thì tự mở trong tab trình\n"
            "duyệt."
        ),
        epilog=(
            "Ví dụ:\n"
            f"  {prog}\n"
            f"  {prog} --browser-tab\n"
            f"  {prog} --self-test --self-test-report self-test.json\n\n"
            "Kết quả được lưu trong thư mục chọn ở giao diện (bản exe mặc định:\n"
            "Documents\\Get-Design-Markdown). Chỉ dùng để tham khảo phong cách, không sao\n"
            "chép logo hay tài sản có bản quyền."
        ),
    )
    parser._optionals.title = "Tùy chọn"
    parser._positionals.title = "Tham số"
    parser.add_argument("-h", "--help", action="help", help="Hiện hướng dẫn này rồi thoát.")
    parser.add_argument("--version", action="store_true", help="Hiện phiên bản rồi thoát.")
    parser.add_argument(
        "--browser-tab",
        action="store_true",
        help="Mở giao diện trong tab trình duyệt thay vì cửa sổ riêng.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help=(
            "Chỉ chạy máy chủ cục bộ và in địa chỉ (kèm mã truy cập), không mở cửa sổ hay tab; "
            "dùng cho tự động hóa. Dừng bằng Ctrl+C hoặc POST /api/shutdown."
        ),
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=0,
        metavar="CỔNG",
        help="Cổng của máy chủ cục bộ 127.0.0.1 (mặc định 0 = tự chọn một cổng trống).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help=(
            "Tự kiểm tra bản cài (thư viện, tài nguyên, máy chủ, trình duyệt, Markdown), ghi "
            "báo cáo JSON rồi thoát: mã thoát 0 khi mọi bước đều đạt. Không mở cửa sổ nào."
        ),
    )
    parser.add_argument(
        "--self-test-report",
        type=Path,
        metavar="ĐƯỜNG_DẪN",
        help=f"Nơi ghi báo cáo tự kiểm tra (mặc định: thư mục nhật ký/{SELF_TEST_REPORT_FILE}).",
    )
    return parser


def _report_fatal(text: str, *, windowed: bool, error: bool = True) -> None:
    """Show a message the user must see: message box in the windowed exe, else stderr."""
    if windowed:
        if error:
            logger.error("%s", text)
        if show_message_box(text, error=error):
            return
    stream = sys.stderr if error else sys.stdout
    try:
        if stream is not None:
            stream.write(text.rstrip("\n") + "\n")
            stream.flush()
    except (OSError, ValueError):
        pass


def _main(args: list[str], *, windowed: bool) -> int:
    if args and args[0] == INSTALL_BROWSER_COMMAND:
        return run_install_cli(args[1:])
    parser = build_parser()
    try:
        options = parser.parse_args(args)
        if options.self_test_report is not None and not options.self_test:
            parser.error("--self-test-report chỉ dùng cùng với --self-test")
        if options.no_open and options.browser_tab:
            parser.error("không dùng --no-open cùng với --browser-tab")
    except _ParserExit as stop:
        if stop.message:
            _report_fatal(stop.message, windowed=windowed, error=stop.usage_error)
        return stop.status
    if options.version:
        _report_fatal(f"Get Design Markdown {__version__}", windowed=windowed, error=False)
        return 0
    if options.self_test:
        return run_self_test(options.self_test_report, stream=sys.stdout)
    mode: UiMode = "none" if options.no_open else "browser" if options.browser_tab else "window"
    try:
        run_ui(port=options.port, mode=mode, out_root=None)
    except GdmError as error:
        logger.warning("The UI could not start: %s", error.message)
        _report_fatal(f"Lỗi: {_error_text(error)}", windowed=windowed)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point of `gdm-ui` and of the exe; exits with sys.exit(code)."""
    log_path = redirect_std_streams()
    _ensure_utf8_stdio()
    ensure_user_browsers_path()
    windowed = log_path is not None
    args = list(sys.argv[1:] if argv is None else argv)
    logger.info("Starting (version %s, frozen=%s, args=%s)", __version__, is_frozen(), args)
    try:
        code = _main(args, windowed=windowed)
    except KeyboardInterrupt:
        code = 130
    except Exception as error:
        logger.exception("Unexpected error")
        where = f"\n\nNhật ký: {log_path}" if log_path is not None else ""
        _report_fatal(
            "Get Design Markdown gặp lỗi không mong muốn và phải đóng.\n\n"
            f"{_error_text(error)}{where}",
            windowed=windowed,
        )
        code = 1
    logger.info("Exit code %s", code)
    sys.exit(code)
