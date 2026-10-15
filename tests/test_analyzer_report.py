"""Rich analysis report (analyzer/report.py)."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Console

from get_design_markdown.analyzer.report import build_report
from get_design_markdown.models.analysis import (
    ColorCluster,
    ColorPalette,
    ContrastPair,
    DesignAnalysis,
    LayoutInfo,
    SiteInfo,
    ThemeAnalysis,
)
from tests.fixture_data import load_analysis_sample


def _render(analysis: DesignAnalysis, *, width: int = 120) -> str:
    console = Console(record=True, width=width, color_system=None, force_terminal=False)
    console.print(build_report(analysis))
    return console.export_text()


def _empty_analysis() -> DesignAnalysis:
    return DesignAnalysis(
        tool_version="0.1.0",
        analyzed_at=datetime(2026, 10, 15, tzinfo=UTC),
        site=SiteInfo(url="https://example.test/", name="Example"),
    )


def test_report_on_sample_contains_key_values() -> None:
    text = _render(load_analysis_sample())
    for expected in (
        "Bảng màu",
        "#4f46e5",
        "thương hiệu",
        "Tương phản chưa đạt AA",
        "2.56",
        "Typography",
        "Space Grotesk",
        "Spacing / Bo góc / Đổ bóng",
        "xxs 4px",
        "Breakpoint",
        "1024px",
        "Responsive",
        "Nav thu thành nút hamburger",
        "Component",
        "button-primary",
        "nền → #4338ca",
        "17.85",
        "Dark mode",
        "#818cf8",
        "Cảnh báo",
        "banner cookie",
    ):
        assert expected in text, expected


def test_report_lists_failing_pairs_only() -> None:
    text = _render(load_analysis_sample())
    contrast = text.split("Tương phản chưa đạt AA", 1)[1].split("Typography", 1)[0]
    assert "2.56" in contrast and "3.30" in contrast and "3.98" in contrast
    assert "17.85" not in contrast


def test_report_fits_100_columns() -> None:
    text = _render(load_analysis_sample(), width=100)
    assert max(len(line) for line in text.splitlines()) <= 100


def test_empty_analysis_renders() -> None:
    text = _render(_empty_analysis())
    assert "Example" in text
    assert "Bảng màu: chưa có dữ liệu." in text
    assert "Component: không nhận diện được component nào." in text
    assert "Dark mode" not in text
    assert "Cảnh báo" not in text


def test_palette_falls_back_to_clusters_and_contrast_all_pass() -> None:
    analysis = _empty_analysis()
    analysis.colors = ColorPalette(
        clusters=[
            ColorCluster(id="bg-1", channel="background", hex="#ffffff", share=0.8, count=4),
            ColorCluster(id="tx-1", channel="text", hex="#111111", share=0.9, count=9),
        ],
        contrast_pairs=[
            ContrastPair(
                foreground="#111111",
                background="#ffffff",
                ratio=18.88,
                passes_aa=True,
                passes_aa_large=True,
                passes_aaa=True,
            )
        ],
    )
    text = _render(analysis)
    assert "chưa đặt token" in text and "bg-1" in text and "80.0%" in text
    assert "mọi cặp chữ/nền đo được đều đạt AA" in text


def test_dark_mode_without_tokens_and_layout_without_changes() -> None:
    analysis = _empty_analysis()
    analysis.dark = ThemeAnalysis(colors=ColorPalette(scheme="dark"))
    analysis.layout = LayoutInfo(shell="top-nav", container_max_width_px=1200, gutter_px=24)
    analysis.warnings = ["Trang có thể bị chặn bot."]
    text = _render(analysis)
    assert "Dark mode: có bản tối nhưng chưa đặt token màu." in text
    assert "không thấy thay đổi bố cục" in text
    assert "Trang có thể bị chặn bot." in text
