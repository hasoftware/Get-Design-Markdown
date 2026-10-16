"""Starting the local UI: `gdm ui` (run_ui) and `gdm-ui` / the Windows exe (main).

Frozen interface: the signatures, constants and self-test report models below.
To implement (the stubs raise NotImplementedYetError): run_ui, main, run_self_test,
redirect_std_streams, open_window.

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

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TextIO

from pydantic import Field

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.base import GdmModel
from get_design_markdown.ui.server import UiMode

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


# ─── To implement ────────────────────────────────────────────────────────────


def run_ui(*, port: int = 0, mode: UiMode = "browser", out_root: Path | None = None) -> None:
    """`gdm ui`: start the server and show it (see the module docstring)."""
    raise NotImplementedYetError(
        "Giao diện đồ họa chưa sẵn sàng trong phiên bản này.",
        hint="Dùng các lệnh dòng lệnh: gdm get <url>.",
    )


def open_window(url: str) -> bool:
    """Show `url` in a pywebview window and block until it is closed; False (without opening
    anything) when pywebview or its engine is unavailable."""
    raise NotImplementedYetError("Chưa mở được cửa sổ giao diện.")


def redirect_std_streams() -> Path | None:
    """Point None sys.stdout / sys.stderr at log_dir()/ui.log; returns the log path, or None
    when both streams were usable and nothing changed."""
    raise NotImplementedYetError("Chưa ghi được nhật ký giao diện.")


def run_self_test(report_path: Path | None = None, *, stream: TextIO | None = None) -> int:
    """Run SELF_TEST_STEPS, write the SelfTestReport JSON to `report_path` (default
    log_dir()/self-test.json), print a short summary to `stream` when given; 0 if all passed."""
    raise NotImplementedYetError("Chưa chạy được bài tự kiểm tra.")


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point of `gdm-ui` and of the exe; exits with sys.exit(code)."""
    raise NotImplementedYetError("Giao diện đồ họa chưa sẵn sàng trong phiên bản này.")
