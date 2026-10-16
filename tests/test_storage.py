from __future__ import annotations

from pathlib import Path

import pytest

from get_design_markdown.errors import InvalidDataError, InvalidUrlError, OutputNotFoundError
from get_design_markdown.models.raw import RawCapture
from get_design_markdown.storage import (
    RAW_FILE,
    domain_of,
    load_model,
    normalize_url,
    page_slug,
    resolve_site_dir,
    save_json,
    save_model,
    save_text,
    screenshot_filename,
    site_dir,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("stripe.com", "https://stripe.com/"),
        ("https://Linear.App", "https://linear.app/"),
        ("http://example.com/pricing?plan=pro#faq", "http://example.com/pricing?plan=pro"),
        ("  https://stripe.com/docs  ", "https://stripe.com/docs"),
        ("http://localhost:8000", "http://localhost:8000/"),
    ],
)
def test_normalize_url(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize("url", ["", "   ", "ftp://stripe.com", "https://", "notadomain"])
def test_normalize_url_rejects_invalid(url: str) -> None:
    with pytest.raises(InvalidUrlError):
        normalize_url(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.stripe.com/payments", "stripe.com"),
        ("linear.app", "linear.app"),
        ("http://localhost:8000/x", "localhost_8000"),
    ],
)
def test_domain_of(url: str, expected: str) -> None:
    assert domain_of(url) == expected


def test_site_dir(tmp_path: Path) -> None:
    assert site_dir(tmp_path, "https://www.stripe.com") == tmp_path / "stripe.com"


def test_save_and_load_round_trip(tmp_path: Path, raw_capture: RawCapture) -> None:
    path = save_model(raw_capture, tmp_path / "stripe.com" / RAW_FILE)
    assert not path.with_name(RAW_FILE + ".tmp").exists()
    assert load_model(RawCapture, path) == raw_capture


def test_load_missing_file_mentions_producing_command(tmp_path: Path) -> None:
    with pytest.raises(OutputNotFoundError) as info:
        load_model(RawCapture, tmp_path / RAW_FILE)
    assert info.value.hint is not None
    assert "gdm collect" in info.value.hint


def test_load_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / RAW_FILE
    path.write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(InvalidDataError) as info:
        load_model(RawCapture, path)
    assert "không đúng định dạng" in info.value.message


def test_resolve_site_dir_accepts_folder_or_domain(tmp_path: Path) -> None:
    folder = tmp_path / "stripe.com"
    folder.mkdir()
    assert resolve_site_dir(str(folder), tmp_path) == folder
    assert resolve_site_dir("https://stripe.com", tmp_path) == folder
    with pytest.raises(OutputNotFoundError):
        resolve_site_dir("linear.app", tmp_path)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://stripe.com/", "home"),
        ("https://stripe.com", "home"),
        ("https://stripe.com/docs/api/", "docs-api"),
        ("http://127.0.0.1:8000/pricing.html", "pricing"),
        ("https://linear.app/Customers?x=1", "customers"),
        ("https://x.test/%C3%A9t%C3%A9/", "t"),
    ],
)
def test_page_slug(url: str, expected: str) -> None:
    assert page_slug(url) == expected


def test_screenshot_filename() -> None:
    assert screenshot_filename("home", 1440) == "screenshots/home-1440.png"
    assert screenshot_filename("pricing", 390, "dark") == "screenshots/pricing-dark-390.png"


def test_save_text_and_json_use_utf8_and_lf(tmp_path: Path) -> None:
    text = "Xin chào\nthế giới\n"
    text_path = save_text(text, tmp_path / "a" / "DESIGN.md")
    assert text_path.read_bytes() == text.encode()
    json_path = save_json({"tên": "màu"}, tmp_path / "tokens.json")
    assert json_path.read_bytes() == '{\n  "tên": "màu"\n}\n'.encode()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("{\n", "(gốc) — JSON không hợp lệ (dòng 2, cột 0)"),
        ('{"schema_version": 1}', "— thiếu trường bắt buộc"),
        ('{"schema_version": "x"}', "schema_version — cần một số nguyên"),
    ],
)
def test_load_invalid_file_explains_in_vietnamese(
    tmp_path: Path, content: str, expected: str
) -> None:
    path = tmp_path / RAW_FILE
    path.write_text(content, encoding="utf-8")
    with pytest.raises(InvalidDataError) as info:
        load_model(RawCapture, path)
    message = info.value.message
    assert expected in message
    for english in ("Invalid JSON", "Field required", "EOF while parsing", "Input should be"):
        assert english not in message
