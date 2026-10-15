"""scripts/run_e2e.py without a browser: site detection, error parsing, a faked `gdm get`."""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from scripts import run_e2e

from tests.test_verify_output import build_valid_folder

Builder = Callable[[Path, str], None]


@pytest.fixture(autouse=True)
def github_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never append to the real $GITHUB_OUTPUT of a CI step."""
    target = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(target))
    return target


@pytest.fixture
def site(tmp_path: Path) -> Path:
    directory = tmp_path / "site"
    directory.mkdir()
    (directory / "index.html").write_text(
        "<!doctype html><title>Acme</title><h1>Acme</h1>", encoding="utf-8"
    )
    return directory


def fake_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    build: Builder | None = None,
) -> list[str]:
    calls: list[str] = []

    def run(url: str, out: Path, *, timeout_s: float) -> subprocess.CompletedProcess[str]:
        calls.append(url)
        if build is not None:
            build(out, f"127.0.0.1_{urlsplit(url).port}")
        return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(run_e2e, "run_pipeline", run)
    return calls


def build_complete(out: Path, name: str) -> None:
    build_valid_folder(out, name)


def build_until_analysis(out: Path, name: str) -> None:
    folder = build_valid_folder(out, name)
    for filename in ("DESIGN.md", "synthesis.json", "tokens.json", "preview.html"):
        (folder / filename).unlink()


def build_broken(out: Path, name: str) -> None:
    folder = build_valid_folder(out, name)
    shutil.rmtree(folder / "screenshots")


def run_main(site: Path, out: Path) -> int:
    return run_e2e.main(["--site", str(site), "--out", str(out)])


def test_without_fixture_site_it_is_a_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = fake_pipeline(monkeypatch)
    assert run_main(tmp_path / "missing", tmp_path / "out") == 0
    assert capsys.readouterr().out.startswith("::notice::Chưa có trang fixture")
    assert calls == []


def test_successful_run_is_verified(
    site: Path,
    tmp_path: Path,
    github_output: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = fake_pipeline(monkeypatch, stdout="✓ Đã ghi DESIGN.md\n", build=build_complete)
    assert run_main(site, tmp_path / "out") == 0
    output = capsys.readouterr().out
    assert len(calls) == 1 and calls[0].startswith("http://127.0.0.1:")
    folder = tmp_path / "out" / f"127.0.0.1_{urlsplit(calls[0]).port}"
    assert f"DESIGN.md: {folder / 'DESIGN.md'}" in output
    assert "Kết quả: 0 lỗi" in output
    # raw.json of the fixture says "acme.example": a warning, surfaced as an annotation.
    assert "::warning::[raw.domain]" in output
    lines = github_output.read_text(encoding="utf-8").splitlines()
    assert f"site_dir={folder}" in lines
    assert f"design_md={folder / 'DESIGN.md'}" in lines


def test_broken_output_fails(
    site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pipeline(monkeypatch, build=build_broken)
    assert run_main(site, tmp_path / "out") == 1
    assert "::error::[raw.screenshots]" in capsys.readouterr().out


def test_missing_stage_without_output_is_a_notice(
    site: Path,
    tmp_path: Path,
    github_output: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = (
        "Lỗi: Phần thu thập bằng trình duyệt chưa được xây dựng (giai đoạn 1–2).\n"
        "Gợi ý: Hiện mới có bước kiểm tra robots.txt.\n"
    )
    fake_pipeline(monkeypatch, returncode=1, stderr=stderr)
    assert run_main(site, tmp_path / "out") == 0
    output = capsys.readouterr().out
    assert (
        "::notice::Stage chưa xong: Phần thu thập bằng trình duyệt chưa được xây dựng "
        "(giai đoạn 1–2)." in output
    )
    assert not github_output.exists()


def test_missing_stage_still_verifies_existing_files(
    site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stderr = "Lỗi: Dựng DESIGN.md chưa được xây dựng (giai đoạn 6).\n"
    fake_pipeline(monkeypatch, returncode=1, stderr=stderr, build=build_until_analysis)
    assert run_main(site, tmp_path / "out") == 0
    output = capsys.readouterr().out
    assert "::notice::Stage chưa xong: Dựng DESIGN.md chưa được xây dựng" in output
    assert "Kết quả: 0 lỗi" in output
    assert "design.missing" in output  # reported as a note in partial mode


def test_missing_stage_with_broken_files_fails(
    site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def build(out: Path, name: str) -> None:
        build_until_analysis(out, name)
        shutil.rmtree(out / name / "screenshots")

    stderr = "Lỗi: Dựng DESIGN.md chưa được xây dựng (giai đoạn 6).\n"
    fake_pipeline(monkeypatch, returncode=1, stderr=stderr, build=build)
    assert run_main(site, tmp_path / "out") == 1


def test_other_failure_keeps_the_exit_code(
    site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pipeline(monkeypatch, returncode=3, stderr="Lỗi: Không kết nối được tới trang.\n")
    assert run_main(site, tmp_path / "out") == 3
    assert "::error::'gdm get' thất bại (mã thoát 3)." in capsys.readouterr().out


def test_success_without_output_folder_fails(
    site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pipeline(monkeypatch)
    assert run_main(site, tmp_path / "out") == 1


def test_timeout_fails(
    site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(url: str, out: Path, *, timeout_s: float) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="gdm get", timeout=timeout_s)

    monkeypatch.setattr(run_e2e, "run_pipeline", run)
    assert (
        run_e2e.main(["--site", str(site), "--out", str(tmp_path / "out"), "--timeout", "5"]) == 1
    )
    assert "::error::'gdm get' chạy quá 5 giây." in capsys.readouterr().out


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            "Lỗi: Phần thu thập bằng trình duyệt chưa được xây dựng (giai đoạn 1–2).\n"
            "Gợi ý: Hiện mới có bước kiểm tra robots.txt.",
            "Phần thu thập bằng trình duyệt chưa được xây dựng (giai đoạn 1–2).",
        ),
        (
            "• Đang phân tích\nLỗi: Chưa có phần phân tích nào được\nxây dựng (giai đoạn 3–5).",
            "Chưa có phần phân tích nào được xây dựng (giai đoạn 3–5).",
        ),
        ("Cảnh báo: Xuất tokens.json chưa được xây dựng (giai đoạn 8).\nLỗi: Hết giờ.", None),
        ("Lỗi: Trang yêu cầu đăng nhập.\nGợi ý: chưa được xây dựng", None),
        ("✓ Đã ghi DESIGN.md", None),
        ("", None),
    ],
)
def test_stage_missing_message(output: str, expected: str | None) -> None:
    assert run_e2e.stage_missing_message(output) == expected


def test_annotation_is_one_line() -> None:
    assert run_e2e.annotation("error", "50% xong\nrồi\r") == "::error::50%25 xong%0Arồi%0D"


def test_write_github_output(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    run_e2e.write_github_output({"a": "1", "b": "x y"}, env={"GITHUB_OUTPUT": str(target)})
    run_e2e.write_github_output({"c": "2"}, env={"GITHUB_OUTPUT": str(target)})
    assert target.read_text(encoding="utf-8") == "a=1\nb=x y\nc=2\n"
    run_e2e.write_github_output({"d": "3"}, env={})
    assert "d=3" not in target.read_text(encoding="utf-8")


def test_pipeline_command() -> None:
    command = run_e2e.pipeline_command("http://127.0.0.1:8000/", Path("e2e-output"))
    assert command[:4] == [sys.executable, "-m", "get_design_markdown", "get"]
    assert command[4:] == [
        "http://127.0.0.1:8000/",
        "--out",
        "e2e-output",
        "--dark",
        "--pages",
        "2",
    ]


def test_serve_site_serves_the_directory(site: Path) -> None:
    with run_e2e.serve_site(site) as url:
        assert url.startswith("http://127.0.0.1:") and url.endswith("/")
        with urllib.request.urlopen(url, timeout=10) as response:
            assert "<title>Acme</title>" in response.read().decode("utf-8")
