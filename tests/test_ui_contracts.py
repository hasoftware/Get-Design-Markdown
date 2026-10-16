"""Shared, already-implemented parts of the local UI package (models, folders, job glue, CLI)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from get_design_markdown import cli, pipeline
from get_design_markdown.config import Settings
from get_design_markdown.console import ConsoleEvent, print_info
from get_design_markdown.errors import (
    BotBlockedError,
    BrowserNotInstalledError,
    CollectError,
    GdmError,
    InvalidDataError,
    JobCancelledError,
    PageTimeoutError,
    SynthesisValidationError,
)
from get_design_markdown.models.raw import CaptureOptions
from get_design_markdown.resources import resource_path
from get_design_markdown.ui import app as ui_app
from get_design_markdown.ui import settings as ui_settings
from get_design_markdown.ui.history import is_valid_site_name
from get_design_markdown.ui.jobs import (
    JOB_ID_RE,
    JobBusyError,
    JobEvent,
    JobNotFoundError,
    JobRequest,
    capture_options_for,
    error_code,
    job_error,
    run_pipeline,
)
from get_design_markdown.ui.server import FILES_HTML_CSP, INDEX_CSP, ErrorResponse
from get_design_markdown.ui.settings import UiSettings
from tests.conftest import FIXED_TIME

runner = CliRunner()


# ─── Settings & folders ──────────────────────────────────────────────────────


def test_tests_never_use_the_real_config_folder(isolated_ui_folders: Path) -> None:
    assert ui_settings.config_dir() == isolated_ui_folders.resolve()
    assert ui_settings.settings_path() == isolated_ui_folders.resolve() / "ui-settings.json"
    assert os.environ[ui_settings.LOG_DIR_ENV]
    assert ui_settings.log_dir() == Path(os.environ[ui_settings.LOG_DIR_ENV]).resolve()


@pytest.mark.parametrize(
    ("platform", "env", "expected_config", "expected_logs"),
    [
        (
            "win32",
            {"APPDATA": "C:/Users/u/AppData/Roaming", "LOCALAPPDATA": "C:/Users/u/AppData/Local"},
            "C:/Users/u/AppData/Roaming/get-design-markdown",
            "C:/Users/u/AppData/Local/get-design-markdown/logs",
        ),
        (
            "darwin",
            {},
            "HOME/Library/Application Support/get-design-markdown",
            "HOME/Library/Logs/get-design-markdown",
        ),
        (
            "linux",
            {"XDG_CONFIG_HOME": "/xdg/config", "XDG_STATE_HOME": "/xdg/state"},
            "/xdg/config/get-design-markdown",
            "/xdg/state/get-design-markdown/logs",
        ),
        (
            "linux",
            {},
            "HOME/.config/get-design-markdown",
            "HOME/.local/state/get-design-markdown/logs",
        ),
    ],
)
def test_config_and_log_folders_per_platform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    platform: str,
    env: dict[str, str],
    expected_config: str,
    expected_logs: str,
) -> None:
    for name in (
        ui_settings.CONFIG_DIR_ENV,
        ui_settings.LOG_DIR_ENV,
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(ui_settings, "_platform", lambda: platform)
    monkeypatch.setattr(ui_settings, "_home", lambda: tmp_path)

    def expand(text: str) -> Path:
        return Path(text.replace("HOME", str(tmp_path))).resolve()

    assert ui_settings.config_dir() == expand(expected_config)
    assert ui_settings.log_dir() == expand(expected_logs)


def test_default_output_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    documents = tmp_path / "Docs"
    monkeypatch.setattr(ui_settings, "documents_dir", lambda: documents)
    exe = ui_settings.default_output_dir(Settings(), frozen=True)
    assert exe == (documents / "Get-Design-Markdown").resolve()
    monkeypatch.chdir(tmp_path)
    source = ui_settings.default_output_dir(Settings(output_dir=Path("out")), frozen=False)
    assert source == (tmp_path / "out").resolve()
    assert source.is_absolute()


def test_documents_dir_exists() -> None:
    assert ui_settings.documents_dir().is_dir()


def test_ui_settings_defaults_follow_the_cli_settings(tmp_path: Path) -> None:
    config = Settings(output_dir=tmp_path, max_elements=999_999, timeout_s=1.0, browser="msedge")
    defaults = UiSettings.defaults(config, frozen=False)
    assert defaults.output_dir == tmp_path.resolve()
    assert defaults.max_elements == ui_settings.MAX_MAX_ELEMENTS  # clamped
    assert defaults.timeout_s == ui_settings.MIN_TIMEOUT_S  # clamped
    assert defaults.browser == "msedge"
    plain = UiSettings.defaults(Settings(output_dir=tmp_path), frozen=False)
    assert (plain.pages, plain.dark, plain.lang, plain.prompt_only, plain.examples) == (
        0,
        False,
        "en",
        False,
        False,
    )
    assert (plain.max_elements, plain.timeout_s, plain.delta_e) == (3000, 45.0, 3.0)
    assert plain.browser == "auto" and plain.open_after


@pytest.mark.parametrize(
    "change",
    [{"pages": 11}, {"pages": -1}, {"lang": "fr"}, {"browser": "firefox"}, {"delta_e": 0.1}],
)
def test_ui_settings_bounds(tmp_path: Path, change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        UiSettings.model_validate({"output_dir": str(tmp_path), **change})


def test_ui_settings_json_round_trip(tmp_path: Path) -> None:
    settings = UiSettings(output_dir=tmp_path, pages=4, lang="vi", browser="chrome")
    assert UiSettings.model_validate_json(settings.model_dump_json()) == settings


# ─── Jobs ────────────────────────────────────────────────────────────────────


def test_job_request_overrides_only_what_it_sets(tmp_path: Path) -> None:
    saved = UiSettings(output_dir=tmp_path, pages=2, dark=True, lang="vi")
    request = JobRequest(url="stripe.com", pages=0, browser="msedge")
    resolved = request.apply_to(saved)
    assert (resolved.pages, resolved.dark, resolved.lang, resolved.browser) == (
        0,
        True,
        "vi",
        "msedge",
    )
    assert resolved.output_dir == saved.output_dir
    with pytest.raises(ValidationError):
        JobRequest(url="")
    with pytest.raises(ValidationError):
        JobRequest.model_validate({"url": "x.com", "output_dir": "/tmp"})


def test_capture_options_match_the_cli(tmp_path: Path) -> None:
    settings = UiSettings(
        output_dir=tmp_path, pages=3, dark=True, max_elements=500, timeout_s=30, browser="chrome"
    )
    assert capture_options_for(settings) == CaptureOptions(
        max_elements=500, dark=True, pages=3, timeout_s=30, browser="chrome"
    )


def test_default_runner_calls_run_get_inside_the_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_get(url: str, **kwargs: Any) -> pipeline.GetResult:
        calls.append({"url": url, **kwargs})
        print_info("đang chạy")
        return pipeline.GetResult(folder=tmp_path / "stripe.com")

    monkeypatch.setattr(pipeline, "run_get", fake_run_get)
    events: list[ConsoleEvent] = []
    settings = UiSettings(output_dir=tmp_path, lang="vi", prompt_only=True, delta_e=4.5)
    folder = run_pipeline(JobRequest(url="stripe.com"), settings, events.append)
    assert folder == tmp_path / "stripe.com"
    assert events == [ConsoleEvent("info", "đang chạy")]
    assert calls == [
        {
            "url": "stripe.com",
            "out_root": tmp_path,
            "options": capture_options_for(settings),
            "lang": "vi",
            "prompt_only": True,
            "examples": False,
            "open_browser": False,
            "threshold": 4.5,
        }
    ]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (JobCancelledError(), "cancelled"),
        (BrowserNotInstalledError(), "browser_missing"),
        (PageTimeoutError("x"), "timeout"),
        (BotBlockedError("x"), "bot_blocked"),
        (CollectError("x"), "collect"),
        (SynthesisValidationError("x"), "synthesis"),
        (InvalidDataError("x"), "data"),
        (GdmError("x"), "error"),
        (RuntimeError("boom"), "unexpected"),
    ],
)
def test_error_codes(error: BaseException, code: str) -> None:
    assert error_code(error) == code
    assert job_error(error).code == code


def test_job_error_keeps_vietnamese_messages() -> None:
    known = job_error(BotBlockedError("Trang chặn bot.", hint="Thử lại sau."))
    assert (known.message, known.hint) == ("Trang chặn bot.", "Thử lại sau.")
    unexpected = job_error(ValueError("first line\nsecond line"))
    assert unexpected.message == "Lỗi không mong muốn: ValueError: first line"
    assert unexpected.hint and "ui.log" in unexpected.hint
    busy = JobBusyError("0123456789ab")
    assert busy.job_id == "0123456789ab" and busy.hint
    assert JobNotFoundError("x").message


@pytest.mark.parametrize(
    "error",
    [
        BrowserNotInstalledError(),
        PageTimeoutError("Trang https://example.com/ không tải xong trong 45 giây."),
    ],
)
def test_job_error_hints_speak_of_the_interface(error: GdmError) -> None:
    """Command-line hints (playwright install, --browser, GDM_TIMEOUT) become UI wording."""
    failure = job_error(error)
    assert failure.message == error.message
    assert failure.hint and "Tùy chọn nâng cao" in failure.hint
    for jargon in ("playwright", "--browser", "GDM_", "uv run"):
        assert jargon not in failure.hint


def test_job_event_from_console_event() -> None:
    event = JobEvent.from_console(
        ConsoleEvent("step", "Phân tích", step=2, total=4), seq=7, at=FIXED_TIME
    )
    assert (event.seq, event.kind, event.step, event.total) == (7, "step", 2, 4)
    assert JOB_ID_RE.fullmatch("0123456789ab")


# ─── History, server constants, static files ────────────────────────────────


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("stripe.com", True),
        ("localhost_8000", True),
        ("__1_8080", True),
        ("xn--bcher-kva.example", True),
        ("..", False),
        (".", False),
        (".hidden", False),
        ("a/b", False),
        ("a\\b", False),
        ("c:", False),
        ("", False),
    ],
)
def test_site_names(name: str, valid: bool) -> None:
    assert is_valid_site_name(name) is valid


def test_content_security_policies() -> None:
    assert "script-src 'self'" in INDEX_CSP and "frame-ancestors 'none'" in INDEX_CSP
    assert "'unsafe-eval'" not in INDEX_CSP
    assert FILES_HTML_CSP.startswith("sandbox allow-scripts;")
    assert "allow-same-origin" not in FILES_HTML_CSP


def test_error_envelope_shape() -> None:
    parsed = ErrorResponse.model_validate(
        {"error": {"code": "busy", "message": "Đang chạy.", "details": {"job_id": "x"}}}
    )
    assert parsed.error.hint is None
    with pytest.raises(ValidationError):
        ErrorResponse.model_validate({"error": {"code": "nope", "message": "x"}})


def test_static_folder_is_packaged() -> None:
    index = resource_path("ui", "static", "index.html")
    text = index.read_text(encoding="utf-8")
    assert '<html lang="vi">' in text
    assert "<script>" not in text  # INDEX_CSP forbids inline scripts


# ─── CLI ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def ui_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_run_ui(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(ui_app, "run_ui", fake_run_ui)
    return calls


def test_cli_ui_modes(ui_calls: list[dict[str, Any]], tmp_path: Path) -> None:
    assert runner.invoke(cli.app, ["ui"]).exit_code == 0
    assert runner.invoke(cli.app, ["ui", "--window", "--port", "8765"]).exit_code == 0
    result = runner.invoke(cli.app, ["ui", "--no-open", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert ui_calls == [
        {"port": 0, "mode": "browser", "out_root": None},
        {"port": 8765, "mode": "window", "out_root": None},
        {"port": 0, "mode": "none", "out_root": tmp_path},
    ]


def test_cli_ui_rejects_window_with_no_open(ui_calls: list[dict[str, Any]]) -> None:
    result = runner.invoke(cli.app, ["ui", "--window", "--no-open"])
    assert result.exit_code == 1
    assert "--window" in result.output and "Gợi ý:" in result.output
    assert ui_calls == []


def test_cli_ui_port_range() -> None:
    assert runner.invoke(cli.app, ["ui", "--port", "70000"]).exit_code == 2


def test_cli_ui_reports_errors_in_vietnamese(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_run_ui(**kwargs: Any) -> None:
        raise GdmError("Cổng 8765 đang được dùng.", hint="Chọn cổng khác bằng --port.")

    monkeypatch.setattr(ui_app, "run_ui", failing_run_ui)
    result = runner.invoke(cli.app, ["ui", "--port", "8765"])
    assert result.exit_code == 1
    assert "Lỗi:" in result.output and "Cổng 8765" in result.output
