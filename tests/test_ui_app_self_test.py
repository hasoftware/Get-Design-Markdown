"""The exe's --self-test: report, independent steps, and each check (fakes where the real part
belongs to another module of the UI)."""

from __future__ import annotations

import importlib
import io
import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from get_design_markdown import __version__
from get_design_markdown.errors import GdmError, NotImplementedYetError
from get_design_markdown.ui import app
from get_design_markdown.ui.app import (
    SELF_TEST_STEPS,
    SelfTestReport,
    check_imports,
    check_markdown,
    check_resources,
    check_server,
    run_self_test,
)
from get_design_markdown.ui.server import TOKEN_HEADER, UiMode

# ─── run_self_test with fake checks ──────────────────────────────────────────


def _install_checks(
    monkeypatch: pytest.MonkeyPatch, checks: dict[str, Callable[[], str]]
) -> list[str]:
    calls: list[str] = []

    def wrap(name: str, check: Callable[[], str]) -> Callable[[], str]:
        def run() -> str:
            calls.append(name)
            return check()

        return run

    monkeypatch.setattr(
        app, "_self_test_checks", lambda: {name: wrap(name, c) for name, c in checks.items()}
    )
    return calls


def _ok(detail: str) -> Callable[[], str]:
    return lambda: detail


def _raise(error: BaseException) -> Callable[[], str]:
    def check() -> str:
        raise error

    return check


def test_all_steps_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _install_checks(monkeypatch, {name: _ok(f"{name} ok") for name in SELF_TEST_STEPS})
    report_path = tmp_path / "nested" / "report.json"
    stream = io.StringIO()
    assert run_self_test(report_path, stream=stream) == 0
    assert calls == list(SELF_TEST_STEPS)
    report = SelfTestReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.ok and report.version == __version__ and report.frozen is False
    assert [step.name for step in report.steps] == list(SELF_TEST_STEPS)
    assert all(step.ok and step.seconds >= 0 for step in report.steps)
    assert report.steps[3].detail == "browser ok"
    summary = stream.getvalue()
    assert "[ĐẠT] imports" in summary and "Kết quả: đạt 5/5 bước." in summary
    assert str(report_path) in summary


def test_a_failing_step_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_checks(
        monkeypatch,
        {
            "imports": _ok("76 module"),
            "resources": _raise(FileNotFoundError("Packaged resource not found: ui/static")),
            "server": _raise(GdmError("Cổng đang bận.", hint="Thử lại.")),
            "browser": _raise(SystemExit(3)),
            "markdown": _ok("ok"),
        },
    )
    stream = io.StringIO()
    assert run_self_test(tmp_path / "report.json", stream=stream) == 1
    assert calls == list(SELF_TEST_STEPS)
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["ok"] is False
    steps = {step["name"]: step for step in data["steps"]}
    assert steps["imports"]["ok"] and steps["markdown"]["ok"]
    assert steps["resources"]["detail"] == (
        "FileNotFoundError: Packaged resource not found: ui/static"
    )
    assert steps["server"]["detail"] == "Cổng đang bận. Gợi ý: Thử lại."
    assert not steps["browser"]["ok"]
    assert "[LỖI] server" in stream.getvalue() and "đạt 2/5 bước" in stream.getvalue()


def test_default_report_goes_to_the_log_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GDM_UI_LOG_DIR", str(tmp_path / "logs"))
    _install_checks(monkeypatch, {name: _ok("ok") for name in SELF_TEST_STEPS})
    assert run_self_test() == 0
    assert SelfTestReport.model_validate_json(
        (tmp_path / "logs" / app.SELF_TEST_REPORT_FILE).read_text(encoding="utf-8")
    ).ok


def test_unwritable_report_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_checks(monkeypatch, {name: _ok("ok") for name in SELF_TEST_STEPS})
    stream = io.StringIO()
    assert run_self_test(tmp_path, stream=stream) == 1  # a folder, not a file
    assert "không ghi được báo cáo" in stream.getvalue()


def test_long_details_are_cut(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    checks = {name: _ok("ok") for name in SELF_TEST_STEPS}
    checks["server"] = _raise(RuntimeError("x" * 2000 + "\nsecond line"))
    _install_checks(monkeypatch, checks)
    run_self_test(tmp_path / "r.json")
    report = SelfTestReport.model_validate_json((tmp_path / "r.json").read_text("utf-8"))
    assert len(report.steps[2].detail) == 500 and "second line" not in report.steps[2].detail


# ─── The real checks ─────────────────────────────────────────────────────────


def test_check_imports_loads_every_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "_webview_bundled", lambda: False)
    detail = check_imports()
    count = int(detail.split(" module", 1)[0])
    assert count > 50
    assert "playwright " in detail and "markdown-it-py " in detail
    assert "pywebview: không có" in detail


def test_check_imports_reports_a_broken_module(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = importlib.import_module

    def flaky(name: str, package: str | None = None) -> Any:
        if name == "get_design_markdown.exporters.tokens":
            raise ImportError("missing piece")
        return real_import(name, package)

    monkeypatch.setattr(app, "_webview_bundled", lambda: False)
    monkeypatch.setattr(importlib, "import_module", flaky)
    with pytest.raises(RuntimeError, match=r"exporters\.tokens \(ImportError: missing piece\)"):
        check_imports()


def test_check_resources_reads_every_packaged_file() -> None:
    detail = check_resources()
    for part in ("collector/js", "synthesizer/phrases", "templates", "ui/static", "driver"):
        assert part in detail


def test_check_resources_fails_on_a_missing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "REQUIRED_RESOURCES", (("ui", "static", "does-not-exist.js"),))
    with pytest.raises(FileNotFoundError):
        check_resources()


def _fake_markdown(front: str | None, html: str) -> Callable[[str], tuple[str | None, str]]:
    return lambda text: (front, html)


def test_check_markdown_accepts_safe_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from get_design_markdown.ui import render_md

    good = "<h1>Kiểm tra</h1><table><tr><td>x</td></tr></table><p>&lt;script&gt;</p>"
    monkeypatch.setattr(render_md, "render_markdown", _fake_markdown("version: alpha", good))
    assert "HTML thô đã escape" in check_markdown()


@pytest.mark.parametrize(
    ("front", "html", "problem"),
    [
        (None, "<table></table>&lt;script&gt;", "front matter chưa được tách"),
        ("version: alpha", "<p>version: alpha</p><table>&lt;script&gt;", "render vào thân"),
        ("version: alpha", "<p>no table</p>&lt;script&gt;", "bảng không được render"),
        ("version: alpha", "<table></table><script>alert(1)</script>", "không được escape"),
    ],
)
def test_check_markdown_rejects_unsafe_output(
    monkeypatch: pytest.MonkeyPatch, front: str | None, html: str, problem: str
) -> None:
    from get_design_markdown.ui import render_md

    monkeypatch.setattr(render_md, "render_markdown", _fake_markdown(front, html))
    with pytest.raises(RuntimeError, match=problem):
        check_markdown()


def test_check_markdown_with_the_real_renderer() -> None:
    try:
        detail = check_markdown()
    except NotImplementedYetError:
        pytest.skip("render_md chưa được xây dựng trong bản này.")
    assert "HTML thô đã escape" in detail


# ─── check_server against a fake UiServer speaking HTTP ──────────────────────


class _HealthHandler(BaseHTTPRequestHandler):
    token: ClassVar[str] = "secret"
    health: ClassVar[dict[str, Any]] = {}
    requests: ClassVar[list[tuple[str, str | None, str | None]]] = []

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        type(self).requests.append(
            (self.path, self.headers.get(TOKEN_HEADER), self.headers.get("Host"))
        )
        if self.path == "/api/health":
            if self.headers.get(TOKEN_HEADER) != self.token:
                self._send(403, b'{"error": {"code": "forbidden", "message": "x"}}', "json")
                return
            self._send(200, json.dumps(type(self).health).encode(), "json")
        elif self.path == "/":
            self._send(200, b'<!doctype html><html lang="vi"></html>', "html")
        else:
            self._send(404, b"{}", "json")

    def _send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        content_type = "application/json" if kind == "json" else "text/html; charset=utf-8"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeHttpUiServer:
    """UiServer stand-in serving /api/health and / over real HTTP on 127.0.0.1."""

    instances: ClassVar[list[FakeHttpUiServer]] = []

    def __init__(self, *, mode: UiMode, out_root: Path | None, **kwargs: Any) -> None:
        self.mode = mode
        self.out_root = out_root
        self.kwargs = kwargs
        self.stopped = False
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
        FakeHttpUiServer.instances.append(self)

    @property
    def token(self) -> str:
        return _HealthHandler.token

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/"

    def start(self) -> None:
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.stopped = True
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def http_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeHttpUiServer]]:
    FakeHttpUiServer.instances = []
    _HealthHandler.requests = []
    _HealthHandler.health = {"ok": True, "version": __version__, "api": 1, "mode": "none"}
    monkeypatch.setattr(app, "UiServer", FakeHttpUiServer)
    # A proxy in the environment must not be used for 127.0.0.1.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    yield FakeHttpUiServer


def test_check_server_calls_health_with_the_token(
    http_server: type[FakeHttpUiServer],
) -> None:
    detail = check_server()
    [server] = http_server.instances
    assert server.mode == "none" and server.stopped
    assert server.out_root is not None and server.out_root.name == "output"
    assert server.kwargs["port"] == 0 and server.kwargs["idle_timeout_s"] is None
    assert server.kwargs["settings_path"].name == "ui-settings.json"
    assert not server.out_root.parent.exists()  # temporary folder removed
    assert ("/api/health", "secret", server.url[len("http://") : -1]) in _HealthHandler.requests
    assert f"phiên bản {__version__}" in detail and "trang chính ok" in detail


def test_check_server_rejects_a_bad_health(http_server: type[FakeHttpUiServer]) -> None:
    _HealthHandler.health = {"ok": False}
    with pytest.raises(RuntimeError, match="/api/health"):
        check_server()
    assert http_server.instances[0].stopped


def test_check_server_with_the_real_server() -> None:
    try:
        detail = check_server()
    except NotImplementedYetError:
        pytest.skip("Máy chủ giao diện chưa được xây dựng trong bản này.")
    assert "/api/health ok" in detail


@pytest.mark.browser
def test_check_browser_launches_auto() -> None:
    detail = app.check_browser()
    assert detail.startswith("auto -> chromium ")
