"""Start-up of the local UI: run_ui modes, the native window, logging and main() (no real window,
browser tab or message box is ever opened)."""

from __future__ import annotations

import contextlib
import faulthandler
import io
import logging
import sys
import tempfile
import threading
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar, TextIO

import pytest

from get_design_markdown.console import console as ui_console
from get_design_markdown.errors import GdmError
from get_design_markdown.ui import app
from get_design_markdown.ui.app import (
    INSTALL_BROWSER_COMMAND,
    PYWEBVIEW_MISSING,
    WEBVIEW2_MISSING,
    WINDOW_MIN_SIZE,
    WINDOW_SIZE,
    WINDOW_TITLE,
    main,
    open_window,
    redirect_std_streams,
    run_ui,
    translate_argparse_error,
    window_problem,
)
from get_design_markdown.ui.server import UiMode

# ─── A fake UiServer ─────────────────────────────────────────────────────────


class FakeServer:
    """Records how run_ui drives the server; wait() returns True after `waits` calls."""

    instances: ClassVar[list[FakeServer]] = []
    waits: ClassVar[int] = 1
    interrupt: ClassVar[bool] = False

    def __init__(self, *, mode: UiMode, out_root: Path | None, port: int, **_: Any) -> None:
        self.mode = mode
        self.out_root = out_root
        self.requested_port = port
        self.started = 0
        self.stopped = 0
        self.wait_calls: list[float | None] = []
        FakeServer.instances.append(self)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{5000 + len(FakeServer.instances)}/"

    @property
    def launch_url(self) -> str:
        return f"{self.url}#token=secret-{len(FakeServer.instances)}"

    def start(self) -> None:
        self.started += 1

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_calls.append(timeout)
        if FakeServer.interrupt:
            raise KeyboardInterrupt
        return len(self.wait_calls) >= FakeServer.waits

    def stop(self) -> None:
        self.stopped += 1


@pytest.fixture
def servers(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[FakeServer]]:
    FakeServer.instances = []
    FakeServer.waits = 1
    FakeServer.interrupt = False
    monkeypatch.setattr(app, "UiServer", FakeServer)
    yield FakeServer.instances


@pytest.fixture
def tabs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(app, "_open_tab", fake_open)
    return opened


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """open_window() replaced: records the URL and reports the window as shown."""
    shown: list[str] = []

    def fake_window(url: str) -> bool:
        shown.append(url)
        return True

    monkeypatch.setattr(app, "open_window", fake_window)
    monkeypatch.setattr(app, "window_problem", lambda: None)
    return shown


# ─── run_ui ──────────────────────────────────────────────────────────────────


def test_browser_mode_opens_a_tab_and_waits(
    servers: list[FakeServer], tabs: list[str], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    FakeServer.waits = 3
    run_ui(port=8765, mode="browser", out_root=tmp_path)
    [server] = servers
    assert (server.mode, server.requested_port, server.out_root) == ("browser", 8765, tmp_path)
    assert server.started == 1 and server.stopped == 1
    assert tabs == [server.launch_url]
    assert len(server.wait_calls) == 3 and all(t == app.WAIT_STEP_S for t in server.wait_calls)
    out = capsys.readouterr().out
    assert f"đang chạy tại {server.url}" in out
    assert server.launch_url in out and "Ctrl+C" in out
    assert "Đã dừng giao diện" in out


def test_browser_mode_without_a_browser_says_so(
    servers: list[FakeServer], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(app, "_open_tab", lambda url: False)
    run_ui(mode="browser")
    assert "Không tự mở được trình duyệt" in capsys.readouterr().out


def test_none_mode_only_prints_the_address(
    servers: list[FakeServer], tabs: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    run_ui(mode="none")
    [server] = servers
    assert server.mode == "none" and tabs == []
    out = capsys.readouterr().out
    assert server.launch_url in out and "Nhấn Ctrl+C để dừng." in out


def test_window_mode_shows_the_window_and_stops_after_it_closes(
    servers: list[FakeServer], tabs: list[str], windows: list[str]
) -> None:
    run_ui(mode="window")
    [server] = servers
    assert server.mode == "window"
    assert windows == [server.launch_url] and tabs == []
    assert server.wait_calls == []  # the window blocks; its closing ends the session
    assert server.stopped == 1


def test_window_mode_says_when_it_stopped(
    servers: list[FakeServer], windows: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    run_ui(mode="window")
    out = capsys.readouterr().out
    assert "Đang mở cửa sổ giao diện" in out and "Đã dừng giao diện" in out


def test_window_mode_falls_back_to_a_tab_without_pywebview(
    servers: list[FakeServer],
    tabs: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(app, "window_problem", lambda: PYWEBVIEW_MISSING)
    monkeypatch.setattr(app, "open_window", lambda url: pytest.fail("no window expected"))
    run_ui(mode="window")
    [server] = servers
    assert server.mode == "browser"  # so the idle shutdown works once the tab is closed
    assert tabs == [server.launch_url]
    out = capsys.readouterr().out
    assert "Không mở được cửa sổ riêng" in out and "pywebview" in out


def test_window_mode_restarts_in_browser_mode_when_the_engine_fails(
    servers: list[FakeServer],
    tabs: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(app, "window_problem", lambda: None)
    monkeypatch.setattr(app, "open_window", lambda url: False)
    run_ui(port=0, mode="window")
    first, second = servers
    assert (first.mode, first.stopped) == ("window", 1)
    assert (second.mode, second.started, second.stopped) == ("browser", 1, 1)
    assert tabs == [second.launch_url]
    assert "tab trình duyệt" in capsys.readouterr().out


def test_ctrl_c_stops_the_server(
    servers: list[FakeServer], tabs: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    FakeServer.interrupt = True
    run_ui(mode="browser")
    assert servers[0].stopped == 1
    out = capsys.readouterr().out
    assert "Đang dừng giao diện…" in out and "Đã dừng giao diện" in out


def test_start_errors_propagate(monkeypatch: pytest.MonkeyPatch, tabs: list[str]) -> None:
    class BusyPort(FakeServer):
        def start(self) -> None:
            raise GdmError("Cổng 8765 đang được dùng.", hint="Chọn cổng khác bằng --port.")

    monkeypatch.setattr(app, "UiServer", BusyPort)
    with pytest.raises(GdmError, match="Cổng 8765"):
        run_ui(port=8765, mode="browser")
    assert tabs == []


# ─── Native window ───────────────────────────────────────────────────────────


class FakeEvent:
    def __init__(self) -> None:
        self.flag = False

    def is_set(self) -> bool:
        return self.flag


class FakeWebview(types.ModuleType):
    """The parts of pywebview open_window() uses."""

    def __init__(self, *, fail: str | None = None) -> None:
        super().__init__("webview")
        self.settings: dict[str, Any] = {"ALLOW_DOWNLOADS": False}
        self.created: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.started: list[dict[str, Any]] = []
        self.fail = fail
        self.window = types.SimpleNamespace(events=types.SimpleNamespace(shown=FakeEvent()))

    def create_window(self, *args: Any, **kwargs: Any) -> Any:
        self.created.append((args, kwargs))
        return self.window

    def start(self, **kwargs: Any) -> None:
        self.started.append(kwargs)
        if self.fail == "after_shown":
            self.window.events.shown.flag = True
        if self.fail:
            raise RuntimeError("engine crashed")


@pytest.fixture
def fake_webview(monkeypatch: pytest.MonkeyPatch) -> FakeWebview:
    module = FakeWebview()
    monkeypatch.setattr(app, "_import_webview", lambda: module)
    monkeypatch.setattr(app, "_platform", lambda: "linux")
    return module


def test_window_problem_without_pywebview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "_import_webview", lambda: None)
    assert window_problem() == PYWEBVIEW_MISSING
    assert open_window("http://127.0.0.1:1/") is False


def test_import_webview_when_it_is_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "webview", None)  # import raises ImportError
    assert app._import_webview() is None


def test_window_problem_without_webview2(
    fake_webview: FakeWebview, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_platform", lambda: "win32")
    monkeypatch.setattr(app, "webview2_version", lambda: None)
    assert window_problem() == WEBVIEW2_MISSING
    assert open_window("http://127.0.0.1:1/") is False
    assert fake_webview.created == []
    monkeypatch.setattr(app, "webview2_version", lambda: "141.0.3537.71")
    assert window_problem() is None


def test_open_window_creates_the_window(fake_webview: FakeWebview) -> None:
    assert open_window("http://127.0.0.1:5000/#token=t") is True
    [(args, kwargs)] = fake_webview.created
    assert args == (WINDOW_TITLE, "http://127.0.0.1:5000/#token=t")
    assert (kwargs["width"], kwargs["height"]) == WINDOW_SIZE
    assert kwargs["min_size"] == WINDOW_MIN_SIZE
    assert fake_webview.started == [{"private_mode": True}]
    assert fake_webview.settings["ALLOW_DOWNLOADS"] is True


def test_open_window_engine_failure_before_showing(fake_webview: FakeWebview) -> None:
    fake_webview.fail = "before_shown"
    assert open_window("http://127.0.0.1:5000/") is False


def test_open_window_engine_failure_after_showing(fake_webview: FakeWebview) -> None:
    fake_webview.fail = "after_shown"
    assert open_window("http://127.0.0.1:5000/") is True


def test_open_window_refuses_the_internet_explorer_engine(
    fake_webview: FakeWebview, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_platform", lambda: "win32")
    monkeypatch.setattr(app, "webview2_version", lambda: "141.0.3537.71")
    winforms = types.ModuleType("webview.platforms.winforms")
    winforms.renderer = "mshtml"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview.platforms.winforms", winforms)
    assert open_window("http://127.0.0.1:5000/") is False
    assert fake_webview.created == []
    winforms.renderer = "edgechromium"  # type: ignore[attr-defined]
    assert open_window("http://127.0.0.1:5000/") is True


@pytest.mark.skipif(sys.platform != "win32", reason="WebView2 only exists on Windows")
def test_real_webview2_version_reads_the_registry() -> None:
    version = app.webview2_version()
    assert version is None or version.split(".")[0].isdigit()


def test_parse_version() -> None:
    assert app._parse_version("141.0.3537.71") == (141, 0, 3537, 71)
    assert app._parse_version("0.0.0.0") is None
    assert app._parse_version("abc") is None


# ─── Logs of the windowed exe ────────────────────────────────────────────────


@pytest.fixture
def no_streams(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Logs go to tmp_path; the logging handler, excepthook and faulthandler are restored.
    Yields the log folder (use `without_console()` in the test body)."""
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("GDM_UI_LOG_DIR", str(log_dir))
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    enabled: list[object] = []
    monkeypatch.setattr(faulthandler, "enable", lambda stream: enabled.append(stream))
    root = logging.getLogger()
    level = root.level
    try:
        yield log_dir
    finally:
        for handler in list(root.handlers):
            if handler.get_name() == app._LOG_HANDLER_NAME:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(level)


@contextlib.contextmanager
def without_console(*, keep_stderr: TextIO | None = None) -> Iterator[None]:
    """sys.stdout / sys.stderr set to None like in the windowed exe, for the block only (pytest
    re-installs its capture streams between test phases, so this cannot be a fixture)."""
    saved = sys.stdout, sys.stderr
    sys.stdout = None
    sys.stderr = keep_stderr
    try:
        yield
    finally:
        replaced = {sys.stdout, sys.stderr} - {None, keep_stderr, *saved}
        sys.stdout, sys.stderr = saved
        for stream in replaced:
            assert stream is not None
            stream.close()


def test_redirect_does_nothing_with_a_console(capsys: pytest.CaptureFixture[str]) -> None:
    before = (sys.stdout, sys.stderr)
    assert redirect_std_streams() is None
    assert (sys.stdout, sys.stderr) == before


def test_redirect_writes_output_and_logging_to_ui_log(no_streams: Path) -> None:
    with without_console():
        path = redirect_std_streams()
        assert sys.stdout is not None and sys.stdout is sys.stderr
        print("Xin chào từ bản exe")
        ui_console.print("Rich cũng ghi vào nhật ký", markup=False)
        logging.getLogger("gdm.test").info("thông tin nhật ký")
        assert threading.excepthook is app._log_thread_exception
        sys.stdout.flush()
    assert path == no_streams / "ui.log"
    text = path.read_text(encoding="utf-8")
    assert "===== Get Design Markdown" in text
    assert "Xin chào từ bản exe" in text and "Rich cũng ghi vào nhật ký" in text
    assert "INFO gdm.test: thông tin nhật ký" in text


def test_redirect_only_replaces_the_missing_stream(no_streams: Path) -> None:
    console_err = io.StringIO()
    with without_console(keep_stderr=console_err):
        assert redirect_std_streams() == no_streams / "ui.log"
        assert sys.stderr is console_err and sys.stdout is not None


def test_redirect_rotates_a_large_log(no_streams: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    no_streams.mkdir(parents=True)
    (no_streams / "ui.log").write_text("x" * 100, encoding="utf-8")
    monkeypatch.setattr(app, "LOG_ROTATE_BYTES", 10)
    with without_console():
        path = redirect_std_streams()
    assert path is not None and (no_streams / "ui.log.1").read_text(encoding="utf-8") == "x" * 100


def test_redirect_falls_back_to_the_temp_folder(
    no_streams: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a folder", encoding="utf-8")
    monkeypatch.setattr(app, "log_dir", lambda: blocker / "logs")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    with without_console():
        assert redirect_std_streams() == tmp_path / "tmp" / "gdm-ui.log"


def test_thread_errors_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    def boom() -> None:
        raise ValueError("lỗi trong luồng")

    thread = threading.Thread(target=boom, name="gdm-test-thread")
    try:
        boom()
    except ValueError:
        exc_info = sys.exc_info()
    args = threading.ExceptHookArgs((exc_info[0], exc_info[1], exc_info[2], thread))
    with caplog.at_level(logging.ERROR):
        app._log_thread_exception(args)
    assert "gdm-test-thread" in caplog.text and "lỗi trong luồng" in caplog.text


# ─── main() ──────────────────────────────────────────────────────────────────


@pytest.fixture
def ui_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(app, "run_ui", lambda **kwargs: calls.append(kwargs))
    return calls


@pytest.fixture
def boxes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    shown: list[tuple[str, bool]] = []

    def fake_box(text: str, *, error: bool = True) -> bool:
        shown.append((text, error))
        return True

    monkeypatch.setattr(app, "show_message_box", fake_box)
    return shown


def _exit_code(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as stop:
        main(argv)
    code = stop.value.code
    assert isinstance(code, int)
    return code


def test_main_defaults_to_the_window(ui_calls: list[dict[str, Any]]) -> None:
    assert _exit_code([]) == 0
    assert _exit_code(["--browser-tab", "--port", "8765"]) == 0
    assert _exit_code(["--no-open"]) == 0
    assert ui_calls == [
        {"port": 0, "mode": "window", "out_root": None},
        {"port": 8765, "mode": "browser", "out_root": None},
        {"port": 0, "mode": "none", "out_root": None},
    ]


def test_main_help_is_vietnamese(
    ui_calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _exit_code(["--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Cách dùng: gdm-ui")
    for text in ("Tùy chọn:", "--browser-tab", "--self-test-report", "Ví dụ:", "tham khảo"):
        assert text in out
    assert INSTALL_BROWSER_COMMAND not in out  # hidden sub-command
    assert ui_calls == []


def test_main_version(capsys: pytest.CaptureFixture[str]) -> None:
    from get_design_markdown import __version__

    assert _exit_code(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"Get Design Markdown {__version__}"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--port", "abc"], "--port: cổng phải là số nguyên từ 0 đến 65535"),
        (["--port", "70000"], "--port: cổng phải là số nguyên từ 0 đến 65535"),
        (["--port"], "Thiếu giá trị cho --port."),
        (["--bogus"], "Tham số không hợp lệ: --bogus."),
        (["--self-test-report", "x.json"], "--self-test-report chỉ dùng cùng với --self-test"),
        (["--no-open", "--browser-tab"], "không dùng --no-open cùng với --browser-tab"),
    ],
)
def test_main_usage_errors(
    argv: list[str],
    expected: str,
    ui_calls: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _exit_code(argv) == 2
    err = capsys.readouterr().err
    assert "Cách dùng:" in err and f"Lỗi: {expected}" in err and "Gợi ý:" in err
    assert ui_calls == []


def test_main_self_test(
    monkeypatch: pytest.MonkeyPatch, ui_calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    calls: list[tuple[Path | None, object]] = []

    def fake_self_test(report_path: Path | None = None, *, stream: object = None) -> int:
        calls.append((report_path, stream))
        return 1

    monkeypatch.setattr(app, "run_self_test", fake_self_test)
    report = tmp_path / "report.json"
    assert _exit_code(["--self-test", "--self-test-report", str(report)]) == 1
    assert _exit_code(["--self-test"]) == 1
    assert calls[0][0] == report and calls[1][0] is None
    assert calls[0][1] is sys.stdout
    assert ui_calls == []


def test_main_install_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_install(argv: list[str]) -> int:
        calls.append(list(argv))
        return 3

    monkeypatch.setattr(app, "run_install_cli", fake_install)
    assert _exit_code([INSTALL_BROWSER_COMMAND, "--dry-run"]) == 3
    assert calls == [["--dry-run"]]


def test_main_reports_ui_errors_on_the_console(
    monkeypatch: pytest.MonkeyPatch,
    boxes: list[tuple[str, bool]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    def busy(**kwargs: Any) -> None:
        raise GdmError("Cổng 8765 đang được dùng.", hint="Chọn cổng khác bằng --port.")

    monkeypatch.setattr(app, "run_ui", busy)
    assert _exit_code(["--port", "8765"]) == 1
    err = capsys.readouterr().err
    assert "Lỗi: Cổng 8765 đang được dùng." in err and "Gợi ý: Chọn cổng khác" in err
    assert boxes == []  # a console exists: no message box


@pytest.fixture
def windowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """main() behaves as in the windowed exe (its streams were redirected to a log)."""
    log = tmp_path / "ui.log"
    monkeypatch.setattr(app, "redirect_std_streams", lambda: log)
    return log


def test_windowed_exe_shows_errors_in_a_message_box(
    windowed: Path, monkeypatch: pytest.MonkeyPatch, boxes: list[tuple[str, bool]]
) -> None:
    def busy(**kwargs: Any) -> None:
        raise GdmError("Không mở được máy chủ giao diện.", hint="Thử lại.")

    monkeypatch.setattr(app, "run_ui", busy)
    assert _exit_code([]) == 1
    [(text, error)] = boxes
    assert error and "Không mở được máy chủ giao diện." in text and "Gợi ý: Thử lại." in text


def test_windowed_exe_unexpected_error(
    windowed: Path, monkeypatch: pytest.MonkeyPatch, boxes: list[tuple[str, bool]]
) -> None:
    def crash(**kwargs: Any) -> None:
        raise RuntimeError("something broke")

    monkeypatch.setattr(app, "run_ui", crash)
    assert _exit_code([]) == 1
    [(text, error)] = boxes
    assert error and "lỗi không mong muốn" in text
    assert "RuntimeError: something broke" in text and str(windowed) in text


def test_windowed_exe_shows_help_and_usage_errors(
    windowed: Path, ui_calls: list[dict[str, Any]], boxes: list[tuple[str, bool]]
) -> None:
    assert _exit_code(["--help"]) == 0
    assert _exit_code(["--bogus"]) == 2
    (help_text, help_error), (usage_text, usage_error) = boxes
    assert not help_error and "Cách dùng:" in help_text
    assert usage_error and "Tham số không hợp lệ: --bogus." in usage_text


def test_windowed_self_test_never_shows_a_message_box(
    windowed: Path, monkeypatch: pytest.MonkeyPatch, boxes: list[tuple[str, bool]]
) -> None:
    monkeypatch.setattr(app, "run_self_test", lambda *args, **kwargs: 1)
    assert _exit_code(["--self-test"]) == 1
    assert boxes == []


def test_ctrl_c_exits_with_130(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(**kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(app, "run_ui", interrupted)
    assert _exit_code([]) == 130


def test_main_points_the_frozen_exe_at_the_user_browsers(
    monkeypatch: pytest.MonkeyPatch, ui_calls: list[dict[str, Any]]
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(app, "ensure_user_browsers_path", lambda: calls.append(True))
    assert _exit_code([]) == 0
    assert calls == [True]


def test_exe_program_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "is_frozen", lambda: True)
    assert app.build_parser().prog == "Get-Design-Markdown.exe"
    monkeypatch.setattr(app, "is_frozen", lambda: False)
    assert app.build_parser().prog == "gdm-ui"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("unrecognized arguments: --x --y", "Tham số không hợp lệ: --x --y."),
        ("argument --port: expected one argument", "Thiếu giá trị cho --port."),
        ("argument --port: invalid int value: 'x'", "Giá trị 'x' không hợp lệ cho --port."),
        ("argument --a: not allowed with argument --b", "Không dùng --a cùng với --b."),
        ("the following arguments are required: url", "Thiếu tham số bắt buộc: url."),
        ("argument --port: cổng sai", "--port: cổng sai"),
        ("something new", "something new"),
    ],
)
def test_translate_argparse_error(message: str, expected: str) -> None:
    assert translate_argparse_error(message) == expected


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows branch")
def test_message_box_is_windows_only() -> None:
    assert app.show_message_box("x") is False
