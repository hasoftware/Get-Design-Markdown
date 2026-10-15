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
