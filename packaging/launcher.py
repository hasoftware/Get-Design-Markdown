"""Entry script of the Windows exe (Get-Design-Markdown.exe): the local UI's main().

Importing the app loads native modules (greenlet, pydantic-core, pythonnet...). When that fails,
typically with "DLL load failed ... The filename or extension is too long" because the folder was
extracted somewhere with a very long path, the windowed exe has no console, and PyInstaller would
show an English "Unhandled exception in script" dialog that blocks even `--self-test`. Instead:
- the traceback is appended to ui.log (same folder as the app's log: $GDM_UI_LOG_DIR, else
  %LOCALAPPDATA%\\get-design-markdown\\logs, else the temp folder);
- with `--self-test`, a failed self-test report (same JSON shape as the app's) is written to
  `--self-test-report PATH` or <log folder>/self-test.json, and no dialog is shown;
- otherwise a short Vietnamese message box explains it (shorter folder, log path);
- the exe exits with code 1.
Only the standard library is used here: nothing of the app can be assumed importable.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import traceback
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

APP_DIR_NAME = "get-design-markdown"
LOG_DIR_ENV = "GDM_UI_LOG_DIR"
LOG_FILE = "ui.log"
SELF_TEST_FLAG = "--self-test"
SELF_TEST_REPORT_FLAG = "--self-test-report"
SELF_TEST_REPORT_FILE = "self-test.json"
SELF_TEST_STEPS = ("imports", "resources", "server", "browser", "markdown")
MESSAGE_BOX_TITLE = "Get Design Markdown"
#: Windows error 206 (ERROR_FILENAME_EXCED_RANGE): "The filename or extension is too long".
ERROR_FILENAME_EXCED_RANGE = 206
#: A folder longer than this leaves too little room for the bundled files under MAX_PATH.
LONG_FOLDER_CHARS = 150
SHORT_FOLDER_EXAMPLE = "C:\\Apps hoặc Documents\\Apps"
SKIPPED_STEP = "Không chạy: ứng dụng không khởi động được (xem bước imports)."

MainFunction = Callable[[], None]


def load_main() -> MainFunction:
    """The UI's main(); importing it is what fails when a native module cannot load."""
    from get_design_markdown.ui.app import main

    return main


def app_folder() -> Path:
    """Folder of the exe (of this script when not frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def log_dir() -> Path:
    """Folder of ui.log: the rules of get_design_markdown.ui.settings.log_dir() on Windows."""
    override = os.environ.get(LOG_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return (base / APP_DIR_NAME / "logs").resolve()


def path_too_long(error: BaseException, text: str, folder: Path) -> bool:
    """The failure looks caused by a long installation path."""
    if getattr(error, "winerror", None) == ERROR_FILENAME_EXCED_RANGE:
        return True
    lowered = text.lower()
    return "too long" in lowered or "quá dài" in lowered or len(str(folder)) > LONG_FOLDER_CHARS


def first_line(error: BaseException) -> str:
    text = " ".join(str(error).split())
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


def self_test_report_path(args: Sequence[str]) -> Path | None:
    """`--self-test-report PATH` (or `--self-test-report=PATH`) from the command line."""
    for index, arg in enumerate(args):
        if arg == SELF_TEST_REPORT_FLAG and index + 1 < len(args):
            return Path(args[index + 1])
        if arg.startswith(SELF_TEST_REPORT_FLAG + "="):
            return Path(arg.split("=", 1)[1])
    return None


def package_version() -> str:
    try:
        import get_design_markdown

        return str(get_design_markdown.__version__)
    except Exception:
        return "?"


def append_log(text: str) -> Path | None:
    """Append `text` to ui.log (else to gdm-ui.log in the temp folder, as the app does); the
    file written, None when nothing could be written."""
    for target in (log_dir() / LOG_FILE, Path(tempfile.gettempdir()) / "gdm-ui.log"):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8", errors="backslashreplace") as handle:
                handle.write(text)
        except OSError:
            continue
        return target
    return None


def write_failed_report(target: Path, detail: str) -> bool:
    """A failed self-test report (the JSON shape of get_design_markdown.ui.app.SelfTestReport)."""
    steps = [
        {
            "name": name,
            "ok": False,
            "seconds": 0.0,
            "detail": (detail if name == "imports" else SKIPPED_STEP)[:500],
        }
        for name in SELF_TEST_STEPS
    ]
    report = {
        "ok": False,
        "version": package_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "started_at": datetime.now(UTC).isoformat(),
        "steps": steps,
    }
    try:
        target = target.expanduser().absolute()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    except OSError:
        return False
    return True


def show_message_box(text: str) -> bool:
    """A native error message box (the windowed exe cannot print); False elsewhere."""
    if sys.platform != "win32":
        return False
    import ctypes

    flags = 0x10 | 0x10000 | 0x40000  # MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST
    try:
        ctypes.windll.user32.MessageBoxW(None, text, MESSAGE_BOX_TITLE, flags)
    except (AttributeError, OSError):
        return False
    return True


def startup_hint(error: BaseException, folder: Path) -> str:
    """What the user can do about it (Vietnamese)."""
    if path_too_long(error, first_line(error), folder):
        return (
            f"Thư mục của công cụ có đường dẫn quá dài ({len(str(folder))} ký tự), nên Windows "
            "không nạp được các thư viện bên trong. Hãy chép cả thư mục Get-Design-Markdown "
            f"sang chỗ có đường dẫn ngắn hơn, ví dụ {SHORT_FOLDER_EXAMPLE}, rồi mở lại."
        )
    return (
        "Hãy giải nén lại cả thư mục từ file .zip vào chỗ có đường dẫn ngắn, ví dụ "
        f"{SHORT_FOLDER_EXAMPLE}, rồi mở lại."
    )


def startup_message(error: BaseException, *, folder: Path, log: Path | None) -> str:
    """Vietnamese text shown when the app could not start."""
    lines = [
        "Get Design Markdown không khởi động được.",
        "",
        first_line(error),
        "",
        startup_hint(error, folder),
    ]
    if log is not None:
        lines += ["", f"Nhật ký: {log}"]
    return "\n".join(lines)


def report_startup_failure(error: BaseException, args: Sequence[str]) -> int:
    """Log the failure, report it (message box, or a self-test report) and return 1."""
    folder = app_folder()
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    log = append_log(
        f"\n===== Get Design Markdown {package_version()} — {stamp} =====\n"
        f"The app could not start (folder: {folder}, args: {list(args)}):\n{trace}"
    )
    message = startup_message(error, folder=folder, log=log)
    if SELF_TEST_FLAG in args:
        target = self_test_report_path(args) or log_dir() / SELF_TEST_REPORT_FILE
        detail = f"{first_line(error)} — {startup_hint(error, folder)}"
        written = write_failed_report(target, detail)
        summary = f"{message}\nBáo cáo: {target}" if written else message
    else:
        summary = message
        if show_message_box(message):
            return 1
    stream = sys.stderr or sys.stdout
    if stream is not None:
        try:
            stream.write(summary + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass
    return 1


def run(argv: Sequence[str] | None = None, *, load: Callable[[], MainFunction] = load_main) -> None:
    """Start the UI, or report why it cannot start (exit code 1)."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        main = load()
    except Exception as error:
        sys.exit(report_startup_failure(error, args))
    main()


if __name__ == "__main__":
    run()
