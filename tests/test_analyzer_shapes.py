"""Tests of analyzer/shapes.py: radius scale and shadow levels."""

from __future__ import annotations

from typing import Any

import pytest

from get_design_markdown.analyzer.css_values import parse_box_shadow
from get_design_markdown.analyzer.naming import FULL_RADIUS_PX
from get_design_markdown.analyzer.shapes import (
    analyze_radii,
    analyze_shadows,
    element_radius,
    elevation_score,
    shows_shape,
)
from get_design_markdown.models.raw import BoundingBox, ComputedStyle, ElementSample, PageCapture
from tests.fixture_data import load_analysis_sample, load_raw_sample, load_site_contract

WHITE = "rgb(255, 255, 255)"


def _radius(value: str) -> tuple[str, str, str, str]:
    return (value, value, value, value)


def _el(
    index: int,
    tag: str = "div",
    *,
    box: tuple[float, float, float, float] = (0, 0, 200, 40),
    classes: tuple[str, ...] = (),
    input_type: str | None = None,
    in_overlay: bool = False,
    **styles: Any,
) -> ElementSample:
    x, y, width, height = box
    return ElementSample(
        index=index,
        tag=tag,
        box=BoundingBox(x=x, y=y, width=width, height=height),
        classes=list(classes),
        input_type=input_type,
        in_overlay=in_overlay,
        styles=ComputedStyle(**styles),
    )


def _card(index: int, radius: str, **kwargs: Any) -> ElementSample:
    return _el(index, background_color=WHITE, border_radius=_radius(radius), **kwargs)


def _page(elements: list[ElementSample]) -> PageCapture:
    return PageCapture(url="https://x.test/", final_url="https://x.test/", elements=elements)


# ─── Fixture ─────────────────────────────────────────────────────────────────


def test_fixture_radii() -> None:
    radii = analyze_radii(load_raw_sample().pages)
    assert [(step.name, step.value_px) for step in radii] == [
        ("sm", 6.0),
        ("md", 8.0),
        ("lg", 12.0),
        ("xl", 16.0),
        ("full", 9999.0),
    ]
    assert [step.value_px for step in radii] == load_site_contract().radii_px
    reference = {step.name: step for step in load_analysis_sample().radii}
    for step in radii:
        assert step.used_on
        if step.name != "sm":  # the reference counts the overlay's cookie button too
            assert step.count == reference[step.name].count


def test_fixture_shadows() -> None:
    shadows = analyze_shadows(load_raw_sample().pages)
    expected = load_analysis_sample().shadows
    assert [(s.name, s.css, s.layers, s.count) for s in shadows] == [
        (s.name, s.css, s.layers, s.count) for s in expected
    ]
    assert len(shadows) == load_site_contract().shadow_levels
    assert all("rgba(0, 0, 0, 0.3)" not in s.css for s in shadows)  # cookie banner (overlay)


# ─── Radii ───────────────────────────────────────────────────────────────────


def test_empty_inputs() -> None:
    assert analyze_radii([]) == []
    assert analyze_shadows([]) == []
    assert analyze_radii([_page([_el(0)])]) == []


@pytest.mark.parametrize(
    ("box", "radius", "expected"),
    [
        ((0, 0, 40, 40), "50%", (20.0, True)),  # avatar circle
        ((0, 0, 24, 24), "50%", (12.0, True)),  # small icon circle
        ((0, 0, 120, 48), "24px", (24.0, True)),  # pill button
        ((0, 0, 80, 24), "12px", (12.0, False)),  # small tag: below the 16px pill floor
        ((0, 0, 300, 200), "9999px", (9999.0, True)),
        ((0, 0, 300, 200), "8px 4px", (8.0, False)),  # elliptical corner
        ((0, 0, 300, 200), "10%", (20.0, False)),
        ((0, 0, 300, 200), "0px", None),
    ],
)
def test_element_radius(
    box: tuple[float, float, float, float], radius: str, expected: tuple[float, bool] | None
) -> None:
    assert element_radius(_el(0, box=box, border_radius=_radius(radius))) == expected


def test_largest_corner_wins() -> None:
    element = _el(0, border_radius=("12px", "12px", "0px", "0px"))
    assert element_radius(element) == (12.0, False)


def test_shows_shape() -> None:
    assert shows_shape(_el(0, background_color=WHITE))
    assert shows_shape(_el(0, "a"))  # interactive: hover fill reveals the corners
    assert shows_shape(_el(0, "img"))
    assert shows_shape(_el(0, overflow_x="hidden"))
    assert shows_shape(_el(0, box_shadow="rgba(0, 0, 0, 0.1) 0px 1px 2px 0px"))
    assert not shows_shape(_el(0))


def test_radii_scale_names_and_full() -> None:
    elements = [
        *[_card(i, "16px") for i in range(3)],
        *[_el(10 + i, "button", border_radius=_radius("6px")) for i in range(4)],
        *[_card(20 + i, "4px", box=(0, 0, 60, 30)) for i in range(2)],
        _el(30, "span", box=(0, 0, 80, 28), background_color=WHITE, border_radius=_radius("999px")),
        _el(31, "div", border_radius=_radius("30px")),  # invisible: no paint, not interactive
        _card(32, "20px", in_overlay=True),
    ]
    radii = analyze_radii([_page(elements)])
    assert [(s.name, s.value_px, s.count) for s in radii] == [
        ("sm", 4.0, 2),
        ("md", 6.0, 4),
        ("lg", 16.0, 3),
        ("full", FULL_RADIUS_PX, 1),
    ]


def test_anchor_falls_back_to_most_used_radius() -> None:
    elements = [*[_card(i, "12px") for i in range(4)], _card(10, "4px"), _card(11, "4px")]
    radii = analyze_radii([_page(elements)])
    assert {s.name: s.value_px for s in radii} == {"sm": 4.0, "md": 12.0}


def test_pill_buttons_do_not_anchor_md() -> None:
    elements = [
        *[_el(i, "button", box=(0, 0, 120, 40), border_radius=_radius("20px")) for i in range(5)],
        *[_card(10 + i, "8px") for i in range(3)],
        *[_card(20 + i, "12px") for i in range(2)],
    ]
    radii = analyze_radii([_page(elements)])
    assert [(s.name, s.value_px) for s in radii] == [("md", 8.0), ("lg", 12.0), ("full", 9999.0)]


def test_close_values_merge_and_rare_values_are_noise() -> None:
    elements = [
        *[_el(i, "button", border_radius=_radius("4px")) for i in range(40)],
        *[_card(100 + i, "6px") for i in range(30)],
        *[_card(200 + i, "6.4px") for i in range(3)],
        _card(300, "2px"),
        _card(301, "3px"),
        _card(302, "10px"),
        *[_card(400 + i, "12px") for i in range(5)],
    ]
    radii = analyze_radii([_page(elements)])
    assert [(s.name, s.value_px) for s in radii] == [("md", 4.0), ("lg", 6.0), ("xl", 12.0)]
    assert radii[1].count == 33


def test_at_most_seven_radius_steps() -> None:
    elements: list[ElementSample] = []
    for position, value in enumerate([2, 3, 4, 5, 6, 8, 10, 12, 16, 20]):
        elements.extend(
            _card(position * 10 + copy, f"{value}px", box=(0, 0, 300, 200)) for copy in range(5)
        )
    radii = analyze_radii([_page(elements)])
    assert len(radii) == 7
    assert all(step.name != "full" for step in radii)


# ─── Shadows ─────────────────────────────────────────────────────────────────


def test_elevation_score() -> None:
    assert elevation_score(parse_box_shadow("rgba(0, 0, 0, 0.5) 0px 4px 8px 0px")) == 4.0
    assert elevation_score(parse_box_shadow("0px 0px 0px 1px rgba(0, 0, 0, 0.1)")) == 0.0
    assert elevation_score(parse_box_shadow("inset 0px 2px 4px rgba(0, 0, 0, 0.5)")) == 0.0
    assert elevation_score(parse_box_shadow("0px 2px 4px black")) == 4.0


def test_shadow_filters_inset_rings_and_transparent() -> None:
    elements = [
        _el(0, box_shadow="inset 0px 1px 2px 0px rgba(0, 0, 0, 0.2)"),
        _el(1, box_shadow="rgba(0, 0, 0, 0.1) 0px 0px 0px 1px"),
        _el(2, box_shadow="rgba(0, 0, 0, 0) 0px 4px 8px 0px"),
        _el(3, box_shadow="none"),
        _el(4, box_shadow="rgba(0, 0, 0, 0.1) 0px 0px 0px 1px, rgba(0, 0, 0, 0.2) 0px 4px 8px 0px"),
    ]
    shadows = analyze_shadows([_page(elements)])
    assert [(s.name, s.layers) for s in shadows] == [("sm", 2)]
    assert (
        shadows[0].css == "0px 0px 0px 1px rgba(0, 0, 0, 0.1), 0px 4px 8px 0px rgba(0, 0, 0, 0.2)"
    )


def test_shadows_merge_similar_scores_and_order_by_elevation() -> None:
    strong = "rgba(0, 0, 0, 0.25) 0px 20px 40px 0px"
    soft = "rgba(0, 0, 0, 0.06) 0px 1px 2px 0px"
    near = "rgba(0, 0, 0, 0.0625) 0px 1px 2px 0px"
    elements = [
        _el(0, classes=("modal",), box_shadow=strong),
        *[_el(10 + i, "button", box_shadow=soft) for i in range(3)],
        _el(20, "input", box_shadow=near),
    ]
    shadows = analyze_shadows([_page(elements)])
    assert [(s.name, s.count) for s in shadows] == [("sm", 4), ("md", 1)]
    assert shadows[0].css == "0px 1px 2px 0px rgba(0, 0, 0, 0.06)"
    assert shadows[1].used_on == ["div.modal"]


def test_six_shadow_levels_start_at_xs() -> None:
    elements = [
        _el(i, box_shadow=f"rgba(0, 0, 0, 0.2) 0px {2**i}px {2 ** (i + 1)}px 0px") for i in range(7)
    ]
    shadows = analyze_shadows([_page(elements)])
    assert [s.name for s in shadows] == ["xs", "sm", "md", "lg", "xl", "xxl"]


def test_one_off_shadows_dropped_when_many_levels() -> None:
    def shadow(y: int) -> str:
        return f"rgba(0, 0, 0, 0.2) 0px {y}px {y * 2}px 0px"

    elements = [
        *[_el(i, "article", box_shadow=shadow(2)) for i in range(5)],
        *[_el(10 + i, "button", box_shadow=shadow(8)) for i in range(2)],
        _el(20, box_shadow=shadow(16)),
        _el(21, box_shadow=shadow(32)),
        _el(22, box_shadow=shadow(64)),
    ]
    shadows = analyze_shadows([_page(elements)])
    assert [(s.name, s.count) for s in shadows] == [("sm", 5), ("md", 2)]
    few = analyze_shadows([_page(elements[:7] + elements[-1:])])
    assert [(s.name, s.count) for s in few] == [("sm", 5), ("md", 2), ("lg", 1)]
