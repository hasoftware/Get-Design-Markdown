"""Tests of analyzer/breakpoints.py: real @media breakpoints only."""

from __future__ import annotations

import pytest

from get_design_markdown.analyzer.breakpoints import (
    analyze_breakpoints,
    boundary_counts,
    max_width_boundary,
    min_width_boundary,
    query_boundaries,
)
from get_design_markdown.models.analysis import Breakpoint
from get_design_markdown.models.raw import MediaQuery, StylesheetInfo
from tests.fixture_data import load_analysis_sample, load_raw_sample, load_site_contract


def _sheet(*queries: tuple[str, int], url: str = "https://x.test/app.css") -> StylesheetInfo:
    return StylesheetInfo(
        url=url,
        media_queries=[MediaQuery(query=query, occurrences=count) for query, count in queries],
    )


def _widths(breakpoints: list[Breakpoint]) -> list[tuple[str, float | None, int]]:
    return [(bp.name, bp.min_width_px, bp.occurrences) for bp in breakpoints]


def test_fixture_breakpoints() -> None:
    raw = load_raw_sample()
    result = analyze_breakpoints(raw.stylesheets)
    assert result == load_analysis_sample().breakpoints
    assert [bp.min_width_px for bp in result] == load_site_contract().breakpoints_px
    assert _widths(result) == [
        ("sm", 640.0, 14),
        ("md", 768.0, 40),
        ("lg", 1024.0, 44),
        ("xl", 1280.0, 9),
    ]


def test_no_media_queries_means_no_breakpoints() -> None:
    assert analyze_breakpoints([]) == []
    assert analyze_breakpoints([StylesheetInfo(url="https://x.test/a.css")]) == []
    only_other = _sheet(
        ("print", 3),
        ("(prefers-color-scheme: dark)", 2),
        ("(prefers-reduced-motion: reduce)", 4),
        ("(hover: hover)", 9),
    )
    assert analyze_breakpoints([only_other]) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (767.98, 768.0),
        (767.5, 768.0),
        (767.0, 768.0),
        (768.0, 768.0),
        (1199.0, 1200.0),
        (639.9999, 640.0),
        (1023.2, 1023.0),
        (1024.0, 1024.0),
    ],
)
def test_max_width_boundary(value: float, expected: float) -> None:
    assert max_width_boundary(value) == expected


def test_min_width_boundary() -> None:
    assert min_width_boundary(768.4) == 768.0
    assert min_width_boundary(768.6) == 769.0


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("(min-width: 768px)", [768.0]),
        ("screen and (max-width: 47.9375em)", [768.0]),
        ("(width >= 1024px)", [1024.0]),
        ("(640px <= width < 1024px)", [640.0, 1024.0]),
        ("(min-width: 640px) and (max-width: 939px)", [640.0, 940.0]),
        ("print and (min-width: 768px)", []),
        ("only print", []),
        ("not all and (min-width: 768px)", []),
    ],
)
def test_query_boundaries_from_text(query: str, expected: list[float]) -> None:
    assert list(query_boundaries(MediaQuery(query=query))) == expected


def test_query_boundaries_prefer_collector_numbers() -> None:
    media = MediaQuery(query="(max-width: var(--bp))", max_width_px=767)
    assert list(query_boundaries(media)) == [768.0]


def test_occurrences_sum_and_close_boundaries_merge() -> None:
    sheet = _sheet(
        ("(min-width: 768px)", 30),
        ("(max-width: 767px)", 6),
        ("(min-width: 769px)", 1),
        ("(min-width: 1024px)", 10),
        ("(max-width: 1023.98px)", 2),
    )
    assert _widths(analyze_breakpoints([sheet])) == [("md", 768.0, 37), ("lg", 1024.0, 12)]


def test_out_of_range_boundaries_are_dropped() -> None:
    sheet = _sheet(("(min-width: 200px)", 5), ("(min-width: 768px)", 5), ("(min-width: 3000px)", 5))
    assert _widths(analyze_breakpoints([sheet])) == [("md", 768.0, 5)]


def test_rare_boundaries_dropped_when_many_exist() -> None:
    # stripe.com: two real breakpoints and a tail of component one-offs.
    sheet = _sheet(
        ("(min-width:640px)", 206),
        ("(max-width:639px)", 40),
        ("(min-width:940px)", 117),
        ("(max-width:939px)", 11),
        ("(min-width:1264px)", 4),
        ("(min-width:1300px)", 3),
        ("(min-width:600px)", 3),
        ("(min-width:1115px)", 1),
        ("(min-width:400px)", 1),
    )
    assert _widths(analyze_breakpoints([sheet])) == [("md", 640.0, 246), ("lg", 940.0, 128)]


def test_few_boundaries_keep_single_occurrences() -> None:
    sheet = _sheet(("(min-width: 600px)", 1), ("(min-width: 900px)", 1), ("(min-width: 1200px)", 1))
    assert [bp.min_width_px for bp in analyze_breakpoints([sheet])] == [600.0, 900.0, 1200.0]


def test_compact_scale_keeps_a_breakpoint_written_once() -> None:
    # The fixture site: Tailwind-like scale where 1280 appears in a single rule.
    sheet = _sheet(
        ("(min-width: 640px)", 2),
        ("(max-width: 639.98px)", 2),
        ("(min-width: 768px)", 5),
        ("(min-width: 1024px)", 4),
        ("(min-width: 1280px)", 1),
    )
    assert [bp.min_width_px for bp in analyze_breakpoints([sheet])] == [
        640.0,
        768.0,
        1024.0,
        1280.0,
    ]


def test_at_most_seven_breakpoints_named_around_md() -> None:
    queries = [(f"(min-width: {width}px)", 10) for width in range(400, 2400, 200)]
    result = analyze_breakpoints([_sheet(*queries)])
    assert len(result) == 7
    names = {bp.name: bp.min_width_px for bp in result}
    assert names["md"] == 800.0
    widths = [bp.min_width_px or 0.0 for bp in result]
    assert widths == sorted(widths)


def test_duplicate_stylesheet_urls_count_once() -> None:
    sheet = _sheet(("(min-width: 768px)", 4))
    assert boundary_counts([sheet, sheet]) == {768.0: 4}
    other = _sheet(("(min-width: 768px)", 4), url="inline:home#1")
    assert boundary_counts([sheet, other]) == {768.0: 8}
