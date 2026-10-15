"""Tests of analyzer/spacing.py: base unit, spacing scale, container, section rhythm."""

from __future__ import annotations

from typing import Any

import pytest

from get_design_markdown.analyzer.common import (
    decorative_indexes,
    element_index,
    is_decorative_root,
    is_off_canvas,
    iter_design_elements,
)
from get_design_markdown.analyzer.naming import SPACING_STEP_NAMES
from get_design_markdown.analyzer.spacing import (
    analyze_spacing,
    collect_values,
    common_paddings,
    container_max_width,
    detect_base_unit,
    is_horizontally_centered,
    on_grid,
    section_gap,
)
from get_design_markdown.models.analysis import SpacingSystem
from get_design_markdown.models.raw import (
    BoundingBox,
    ComputedStyle,
    ElementSample,
    LayoutSnapshot,
    PageCapture,
)
from tests.fixture_data import load_analysis_sample, load_raw_sample, load_site_contract


def _sides(top: str, right: str | None = None) -> tuple[str, str, str, str]:
    right = top if right is None else right
    return (top, right, top, right)


def _el(
    index: int,
    tag: str = "div",
    *,
    parent: int | None = None,
    box: tuple[float, float, float, float] = (0, 0, 200, 40),
    classes: tuple[str, ...] = (),
    role: str | None = None,
    landmark: str | None = None,
    child_count: int = 0,
    **styles: Any,
) -> ElementSample:
    x, y, width, height = box
    return ElementSample(
        index=index,
        tag=tag,
        parent_index=parent,
        box=BoundingBox(x=x, y=y, width=width, height=height),
        classes=list(classes),
        role=role,
        landmark=landmark,
        child_count=child_count,
        styles=ComputedStyle(**styles),
    )


def _page(elements: list[ElementSample], **kwargs: Any) -> PageCapture:
    return PageCapture(
        url="https://x.test/", final_url="https://x.test/", elements=elements, **kwargs
    )


def _padded(start: int, values: list[int], count: int = 2) -> list[ElementSample]:
    """`count` buttons per padding value (padding: v)."""
    elements = []
    for position, value in enumerate(values):
        for copy in range(count):
            elements.append(_el(start + position * count + copy, "a", padding=_sides(f"{value}px")))
    return elements


# ─── Fixture ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fixture_spacing() -> SpacingSystem:
    return analyze_spacing(load_raw_sample().pages)


def test_fixture_spacing_contract(fixture_spacing: SpacingSystem) -> None:
    contract = load_site_contract().spacing
    assert fixture_spacing.base_unit_px == contract["base_unit_px"] == 4
    assert fixture_spacing.container_max_width_px == contract["container_max_width_px"]
    assert fixture_spacing.section_gap_px == contract["section_gap_px"]
    values = [step.value_px for step in fixture_spacing.steps]
    assert set(values) >= set(contract["required_steps_px"])
    assert values == sorted(values)
    assert {step.name: step.value_px for step in fixture_spacing.steps}["md"] == 16


def test_fixture_spacing_matches_reference(fixture_spacing: SpacingSystem) -> None:
    reference = load_analysis_sample().spacing
    got = {step.name: step.value_px for step in fixture_spacing.steps}
    for step in reference.steps:
        if step.name in got:
            assert got[step.name] == step.value_px, step.name
    names = [step.name for step in fixture_spacing.steps]
    assert names == list(SPACING_STEP_NAMES[: len(names)])
    assert all(step.count >= 2 and step.used_on for step in fixture_spacing.steps)


def test_fixture_ignores_auto_margins_overlays_and_sections(fixture_spacing: SpacingSystem) -> None:
    values = {step.value_px for step in fixture_spacing.steps}
    assert 276 not in values  # code sample centered with margin 0 276px
    assert 96 not in values  # section padding -> section_gap_px
    assert max(values) <= 128
    collected = collect_values(load_raw_sample().pages)
    assert 276 not in collected
    assert 14 in collected and collected[14].count == 1  # input only; cookie button skipped


def test_fixture_common_paddings(fixture_spacing: SpacingSystem) -> None:
    paddings = fixture_spacing.common_paddings
    assert len(paddings) == 4
    assert set(paddings[:3]) == {"8px 12px", "32px", "12px 20px"}


# ─── Unit tests ──────────────────────────────────────────────────────────────


def test_empty_inputs() -> None:
    assert analyze_spacing([]) == SpacingSystem()
    assert analyze_spacing([_page([_el(0)])]) == SpacingSystem()


def test_on_grid_counts_border_compensation() -> None:
    assert on_grid(12, 4) and on_grid(11, 4) and on_grid(19, 4) and on_grid(7, 8)
    assert not on_grid(6, 4) and not on_grid(10, 4) and not on_grid(2, 4)


def test_eight_pixel_grid() -> None:
    page = _page(_padded(0, [8, 16, 24, 32, 48, 64]) + _padded(100, [4]))
    result = analyze_spacing([page])
    assert result.base_unit_px == 8
    assert [step.value_px for step in result.steps] == [4, 8, 16, 24, 32, 48, 64]
    assert {step.name: step.value_px for step in result.steps}["md"] == 16


def test_four_pixel_grid_and_no_grid() -> None:
    four = _page(_padded(0, [4, 8, 12, 20, 28]) + _padded(100, [10]))
    assert detect_base_unit(collect_values([four])) == 4
    loose = _page(_padded(0, [5, 6, 10, 13, 14, 18, 22]))
    assert detect_base_unit(collect_values([loose])) is None
    assert analyze_spacing([loose]).base_unit_px is None


def test_values_within_one_pixel_merge_into_grid_value() -> None:
    page = _page(_padded(0, [12, 12, 11, 20, 19]))
    steps = analyze_spacing([page]).steps
    assert [step.value_px for step in steps] == [12, 20]
    assert steps[0].count == 6


def test_md_stays_on_sixteen_with_many_small_values() -> None:
    page = _page(_padded(0, [2, 4, 6, 8, 12, 16, 24, 32], count=3))
    steps = analyze_spacing([page]).steps
    names = {step.name: step.value_px for step in steps}
    assert names["md"] == 16
    assert len([step for step in steps if step.value_px < 16]) == 3
    assert 6 not in names.values()  # off the 4px grid, pruned first


def test_at_most_ten_steps() -> None:
    page = _page(_padded(0, [4, 8, 12, 16, 20, 24, 32, 40, 48, 56, 64, 72, 80, 96]))
    steps = analyze_spacing([page]).steps
    assert len(steps) == 10


def test_one_off_values_need_gap_usage() -> None:
    elements = [
        *_padded(0, [8, 16]),
        _el(10, "a", padding=_sides("40px")),
        _el(11, "div", column_gap="28px", row_gap="normal"),
    ]
    values = [step.value_px for step in analyze_spacing([_page(elements)]).steps]
    assert values == [8, 16, 28]


def test_auto_centering_margins_are_ignored() -> None:
    elements = [
        _el(0, "div", box=(0, 0, 1440, 400), child_count=2),
        _el(1, "div", parent=0, box=(420, 0, 600, 100), margin=("24px", "420px", "0px", "420px")),
        _el(2, "div", parent=0, box=(0, 0, 1000, 100), margin=("0px", "440px", "0px", "0px")),
    ]
    values = collect_values([_page(elements)])
    assert set(values) == {24}


def test_section_only_padding_feeds_section_gap() -> None:
    elements = [
        _el(0, "main", box=(0, 0, 1440, 3000), landmark="main", child_count=3),
        *[
            _el(
                i,
                "section",
                parent=0,
                box=(0, 1000 * (i - 1), 1440, 1000),
                landmark="main",
                padding=("120px", "0px", "120px", "0px"),
            )
            for i in (1, 2, 3)
        ],
        *_padded(10, [8, 16]),
        _el(20, "div", row_gap="96px", column_gap="96px"),
    ]
    result = analyze_spacing([_page(elements)])
    assert result.section_gap_px == 120
    values = [step.value_px for step in result.steps]
    assert 120 not in values and 96 in values


def test_section_gap_from_centered_section_container() -> None:
    # stripe.com: <section> (full width, no padding) > centered container with 96px padding.
    elements = [
        _el(0, "main", box=(0, 0, 1440, 4000), landmark="main", child_count=2),
        _el(1, "section", parent=0, box=(0, 0, 1440, 1000), landmark="main", child_count=1),
        _el(
            2,
            "div",
            parent=1,
            box=(87, 0, 1266, 1000),
            landmark="main",
            padding=("96px", "16px", "96px", "16px"),
            max_width="1266px",
        ),
        _el(3, "section", parent=0, box=(0, 1000, 1440, 800), landmark="main", child_count=1),
        _el(
            4,
            "div",
            parent=3,
            box=(87, 1000, 1266, 800),
            landmark="main",
            padding=("80px", "16px", "96px", "16px"),
            max_width="1266px",
        ),
    ]
    assert section_gap([_page(elements)]) == 96


def test_section_gap_falls_back_to_gaps_between_sections() -> None:
    elements = [
        _el(0, "main", box=(0, 0, 1440, 3000), landmark="main", child_count=3),
        _el(1, "section", parent=0, box=(0, 0, 1440, 500)),
        _el(2, "section", parent=0, box=(0, 580, 1440, 500)),
        _el(3, "section", parent=0, box=(0, 1160, 1440, 500)),
    ]
    assert section_gap([_page(elements)]) == 80
    assert section_gap([_page([_el(0)])]) is None


def test_container_max_width_rules() -> None:
    centered = [
        _el(0, "body", box=(0, 0, 1440, 3000), child_count=4),
        *[
            _el(i, "div", parent=0, box=(120, 100 * i, 1200, 80), max_width="1200px", child_count=3)
            for i in (1, 2, 3)
        ],
        _el(4, "p", parent=0, box=(400, 900, 640, 80), max_width="640px"),
        _el(5, "div", parent=0, box=(0, 900, 900, 80), max_width="900px"),  # not centered
    ]
    assert container_max_width([_page(centered)]) == 1200
    clamp = [_el(0, "div", box=(120, 0, 1200, 80), max_width="min(1200px, 100% - 48px)")]
    assert container_max_width([_page(clamp)]) == 1200
    repeated = [
        _el(0, "body", box=(0, 0, 1440, 3000)),
        _el(1, "div", parent=0, box=(160, 0, 1120, 80)),
        _el(2, "div", parent=0, box=(160, 100, 1120, 80)),
    ]
    assert container_max_width([_page(repeated)]) == 1120
    snapshot = LayoutSnapshot(viewport_width=1440, container_width_px=1180)
    assert container_max_width([_page([_el(0)], layouts=[snapshot])]) == 1180
    assert container_max_width([]) is None


def test_is_horizontally_centered() -> None:
    parent = _el(0, "div", box=(100, 0, 1000, 500))
    child = _el(1, "div", parent=0, box=(200, 0, 800, 100))
    off = _el(2, "div", parent=0, box=(100, 0, 800, 100))
    index = element_index(_page([parent, child, off]))
    assert is_horizontally_centered(child, index)
    assert not is_horizontally_centered(off, index)
    assert is_horizontally_centered(_el(3, box=(120, 0, 1200, 10)), {})


def test_common_paddings_of_interactive_and_card_elements() -> None:
    elements = [
        *[_el(i, "a", padding=_sides("8px", "12px")) for i in range(3)],
        *[
            _el(
                10 + i,
                "article",
                child_count=2,
                background_color="rgb(255, 255, 255)",
                padding=_sides("32px"),
            )
            for i in range(2)
        ],
        _el(20, "button", padding=_sides("12px", "20px")),
        _el(21, "div", padding=_sides("48px")),  # neither interactive nor painted
        _el(22, "a"),  # no padding
    ]
    assert common_paddings([_page(elements)]) == ["8px 12px", "32px", "12px 20px"]


def test_sub_pages_weigh_half_for_the_base_unit() -> None:
    main = _page(_padded(0, [8, 16, 24], count=2))
    sub = _page(_padded(0, [6, 10, 14], count=2))
    # main: 6 elements on the 8px grid; sub: 6 x 0.5 = 3 off-grid -> 67% (< 75%) on 8px,
    # 67% (>= 60%) on 4px; swapped, only 33% of the weight is on any grid.
    assert detect_base_unit(collect_values([main, sub])) == 4
    assert detect_base_unit(collect_values([main])) == 8
    assert detect_base_unit(collect_values([sub, main])) is None


# ─── Decorative subtrees ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("classes", "role", "expected"),
    [
        (("payments-graphic",), None, True),
        (("payments-graphic__checkout",), None, True),
        (("GJ9TEa_illustration",), None, True),
        (("hero-mockup",), None, True),
        ((), "img", True),
        (("graphical",), None, False),
        (("card",), None, False),
        ((), "presentation", False),
    ],
)
def test_is_decorative_root(classes: tuple[str, ...], role: str | None, expected: bool) -> None:
    assert is_decorative_root(_el(0, classes=classes, role=role)) is expected


def test_decorative_subtrees_are_skipped() -> None:
    elements = [
        _el(0, "body", box=(0, 0, 1440, 2000)),
        _el(1, "div", parent=0, classes=("browser-graphic",)),
        _el(2, "div", parent=1, padding=_sides("7px")),
        _el(3, "span", parent=2, padding=_sides("3px")),
        _el(4, "a", parent=0, padding=_sides("12px")),
    ]
    page = _page(elements)
    assert decorative_indexes(page) == frozenset({1, 2, 3})
    assert [ref.element.index for ref in iter_design_elements([page])] == [0, 4]
    assert set(collect_values([page])) == {12}


def test_off_canvas_elements_are_skipped() -> None:
    elements = [
        _el(0, "a", box=(100, 0, 200, 40), padding=_sides("12px")),
        _el(1, "aside", box=(1440, 0, 400, 900), padding=_sides("24px")),
        _el(2, "nav", box=(-320, 0, 320, 900), padding=_sides("20px")),
    ]
    page = _page(elements)
    assert is_off_canvas(elements[1]) and is_off_canvas(elements[2])
    assert not is_off_canvas(elements[0])
    assert [ref.element.index for ref in iter_design_elements([page])] == [0]


def test_section_gap_fallback_needs_adjacent_sections() -> None:
    elements = [
        _el(0, "main", box=(0, 0, 1440, 3000), landmark="main", child_count=3),
        _el(1, "section", parent=0, box=(0, 0, 1440, 500)),
        _el(2, "div", parent=0, box=(200, 500, 1000, 300)),  # narrow block between sections
        _el(3, "section", parent=0, box=(0, 900, 1440, 500)),
    ]
    assert section_gap([_page(elements)]) is None
