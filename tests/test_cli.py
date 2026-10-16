from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from get_design_markdown import __version__, cli, pipeline
from get_design_markdown.collector import collect
from get_design_markdown.errors import BotBlockedError
from get_design_markdown.models.raw import RawCapture, RobotsCheck

runner = CliRunner()


def test_help_lists_all_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("collect", "analyze", "synthesize", "export", "get"):
        assert command in result.output


def test_version() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_invalid_url_prints_vietnamese_error() -> None:
    result = runner.invoke(cli.app, ["collect", "ftp://stripe.com"])
    assert result.exit_code == 1
    assert "Lỗi:" in result.output
    assert "http/https" in result.output


def test_collect_reports_robots_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_check(url: str, **_: object) -> RobotsCheck:
        return RobotsCheck(
            robots_url="https://stripe.com/robots.txt", allowed=False, note="robots.txt chặn."
        )

    async def fake_collect_site(url: str, **_: object) -> RawCapture:
        raise BotBlockedError("Trang trả về trang kiểm tra chống bot.")

    monkeypatch.setattr(pipeline, "check_robots", fake_check)
    monkeypatch.setattr(collect, "collect_site", fake_collect_site)
    result = runner.invoke(cli.app, ["collect", "stripe.com", "--out", str(tmp_path)])
    output = " ".join(result.output.split())
    assert "robots.txt chặn." in output
    assert "Cảnh báo" in output
    assert "chống bot" in output
    assert result.exit_code == 1
    assert not (tmp_path / "stripe.com" / "raw.json").exists()


def test_analyze_missing_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GDM_OUTPUT_DIR", str(tmp_path))
    result = runner.invoke(cli.app, ["analyze", "linear.app"])
    assert result.exit_code == 1
    assert "Không tìm thấy thư mục dữ liệu" in result.output


def test_get_validates_pages_range() -> None:
    result = runner.invoke(cli.app, ["get", "stripe.com", "--pages", "99"])
    assert result.exit_code == 2


# ─── Vietnamese usage errors and help (cli.run) ──────────────────────────────


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = cli.run(list(args))
    captured = capsys.readouterr()
    return code, " ".join((captured.out + captured.err).split())


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("analyze",), "Lỗi: Thiếu tham số bắt buộc 'THƯ_MỤC'."),
        (("bogus",), "Lỗi: Không có lệnh 'bogus'."),
        (("anlyze",), "Không có lệnh 'anlyze'. Có phải ý bạn là 'analyze'?"),
        (("analyze", "x", "--bogus"), "Lỗi: Không có tùy chọn --bogus."),
        (
            ("collect", "--pages", "11", "https://example.com"),
            "Giá trị không hợp lệ cho '--pages': 11 nằm ngoài khoảng 0<=x<=10.",
        ),
        (("analyze", "x", "--delta-e", "abc"), "'abc' không phải số thực."),
        (("synthesize", "x", "--lang", "fr"), "'fr' không thuộc các giá trị 'en', 'vi'."),
        (("analyze", "a", "b"), "Thừa tham số không dùng tới: b."),
        (("analyze", "--delta-e"), "Tùy chọn '--delta-e' cần một giá trị."),
    ],
)
def test_usage_errors_are_vietnamese(
    capsys: pytest.CaptureFixture[str], args: tuple[str, ...], expected: str
) -> None:
    code, output = _run(capsys, *args)
    assert code == 2
    assert expected in output
    assert "Gợi ý:" in output and "--help" in output
    for english in ("Missing", "No such", "Invalid value", "Try '", "Usage:", "Error"):
        assert english not in output, english


def test_unknown_usage_messages_are_kept() -> None:
    assert cli.translate_usage_error("Something new.") == "Something new."


def test_help_is_vietnamese(capsys: pytest.CaptureFixture[str]) -> None:
    for args in (["-h"], ["analyze", "--help"], ["get", "-h"]):
        code, output = _run(capsys, *args)
        assert code == 0
        assert "Cách dùng:" in output
        assert "Hiện hướng dẫn này rồi" in output
        assert "Show this message" not in output and "Usage:" not in output
    _, output = _run(capsys, "analyze", "--help")
    assert "Tham số" in output and "Tùy chọn" in output and "[bắt buộc]" in output
    # Placeholders are Vietnamese too: no Typer type labels or Click's [OPTIONS].
    assert "gdm analyze [TÙY_CHỌN] THƯ_MỤC" in " ".join(output.split()).replace("pytest", "gdm")
    for english in ("<str>", "<float", "<int", "<path>", "[OPTIONS]", "COMMAND", "{"):
        assert english not in output, english
    _, output = _run(capsys, "collect", "--help")
    assert "SỐ [0<=x<=10]" in output and "THƯ_MỤC" in output
    for english in ("<str>", "<int", "<path>", "[OPTIONS]"):
        assert english not in output, english
    _, output = _run(capsys, "-h")
    assert "[TÙY_CHỌN] LỆNH [THAM_SỐ]..." in output


def test_run_returns_the_command_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(capsys, "--version")[0] == 0
    code, output = _run(capsys, "collect", "ftp://stripe.com")
    assert code == 1 and "Lỗi:" in output
