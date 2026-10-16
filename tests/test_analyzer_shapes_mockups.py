"""Regression tests of analyzer/shapes.py: illustration and mock-up shapes are not the site's
elevation, and a lone component surface keeps its shadow as a level."""

from __future__ import annotations

from typing import Any

from get_design_markdown.analyzer.shapes import (
    analyze_radii,
    analyze_shadows,
    animation_indexes,
    is_component_surface,
    iter_foundation_elements,
    mock_frame_indexes,
)
from get_design_markdown.analyzer.spacing import analyze_spacing
from get_design_markdown.analyzer.typography import collect_samples
from get_design_markdown.models.raw import BoundingBox, ComputedStyle, ElementSample, PageCapture

WHITE = "rgb(255, 255, 255)"
DARK = "rgb(22, 23, 24)"
SOFT = "rgba(50, 50, 93, 0.25) 0px 2px 5px -1px"
CARD = "rgba(50, 50, 93, 0.25) 0px 13px 27px -5px"
TILE = "rgba(3, 3, 39, 0.25) 0px 14px 21px -14px"
FORM = "rgba(0, 55, 112, 0.14) 0px 20px 80px -16px, rgba(0, 59, 137, 0.06) 0px 10px 60px -16px"


def _el(
    index: int,
    tag: str = "div",
    *,
    parent: int | None = None,
    box: tuple[float, float, float, float] = (100, 100, 300, 200),
    classes: tuple[str, ...] = (),
    role: str | None = None,
    children: int = 0,
    text: str = "",
    **styles: Any,
) -> ElementSample:
    x, y, width, height = box
    return ElementSample(
        index=index,
        tag=tag,
        role=role,
        parent_index=parent,
        child_count=children,
        text=text,
        has_own_text=bool(text),
        box=BoundingBox(x=x, y=y, width=width, height=height),
        classes=list(classes),
        styles=ComputedStyle(**styles),
    )


def _page(elements: list[ElementSample], name: str = "home") -> PageCapture:
    return PageCapture(
        url=f"https://x.test/{name}",
        final_url=f"https://x.test/{name}",
        name=name,
        elements=elements,
    )


def _radius(value: str) -> tuple[str, str, str, str]:
    return (value, value, value, value)


# ─── Animated illustrations ──────────────────────────────────────────────────


def _animation_page() -> PageCapture:
    """stripe.com: "developer-systems-animation" tiles inside a "lazy-animation" wrapper."""
    return _page(
        [
            _el(0, "main", box=(0, 0, 1440, 3000), children=2),
            _el(1, classes=("lazy-animation", "lazy-animation--loaded"), parent=0, children=1),
            _el(2, "figure", classes=("developer-systems-animation",), parent=1, children=2),
            *[
                _el(
                    3 + i,
                    classes=("developer-systems-animation__system",),
                    parent=2,
                    box=(100 + 70 * i, 100, 58, 32),
                    text="ERP",
                    font_size="14px",
                    font_family="Inter",
                    background_color=WHITE,
                    border_radius=_radius("4px"),
                    box_shadow=TILE,
                )
                for i in range(2)
            ],
            # A real card wrapped by a "fade-animation" utility (no BEM parts): it counts.
            _el(10, classes=("fade-animation",), parent=0, children=1),
            _el(
                11,
                "article",
                classes=("card",),
                parent=10,
                children=2,
                background_color=WHITE,
                border_radius=_radius("8px"),
                box_shadow=CARD,
            ),
        ]
    )


def test_animation_indexes_flag_bem_animation_blocks_only() -> None:
    page = _animation_page()
    assert animation_indexes(page) == frozenset({2, 3, 4})
    assert animation_indexes(_page([_el(0, classes=("scroll-animation",))])) == frozenset()


def test_animation_tiles_are_not_shadow_levels_or_radii() -> None:
    page = _animation_page()
    shadows = analyze_shadows([page])
    assert [(level.css, level.used_on) for level in shadows] == [
        ("0px 13px 27px -5px rgba(50, 50, 93, 0.25)", ["article.card"])
    ]
    assert [(step.value_px, step.count) for step in analyze_radii([page])] == [(8.0, 1)]


def test_typography_and_spacing_skip_animation_tiles() -> None:
    page = _animation_page()
    assert collect_samples([page]) == []
    kept = {ref.element.index for ref in iter_foundation_elements([page])}
    assert kept.isdisjoint({2, 3, 4}) and {0, 1, 10, 11} <= kept

    def tiles(block: str) -> PageCapture:
        padding = ("12px", "16px", "12px", "16px")
        return _page(
            [
                _el(0, classes=(block,), children=3),
                *[
                    _el(1 + i, classes=(f"{block}__system",), parent=0, padding=padding)
                    for i in range(3)
                ],
            ]
        )

    assert [step.value_px for step in analyze_spacing([tiles("systems-grid")]).steps] == [12, 16]
    assert analyze_spacing([tiles("developer-systems-animation")]).steps == []


# ─── App-window mock-ups ─────────────────────────────────────────────────────


def _mockup_page() -> PageCapture:
    """linear.app: a framed app window in <main> with its own sidebar and a shadowed panel."""
    return _page(
        [
            _el(0, "main", box=(0, 0, 1440, 3000), children=2),
            _el(
                1,
                classes=("frame",),
                parent=0,
                box=(48, 200, 1344, 700),
                children=2,
                background_color=DARK,
                border_radius=_radius("12px"),
            ),
            _el(2, "aside", parent=1, box=(48, 200, 220, 700), children=1),
            _el(3, "button", classes=("navitem",), parent=2, border_radius=_radius("8px")),
            _el(
                4,
                classes=("panel",),
                parent=1,
                box=(900, 240, 400, 520),
                children=2,
                background_color=DARK,
                border_radius=_radius("12px"),
                box_shadow="rgba(0, 0, 0, 0.25) 0px 2px 32px 0px",
            ),
            # A real card next to the mock-up.
            _el(
                10,
                "article",
                classes=("card",),
                parent=0,
                box=(48, 1000, 400, 300),
                children=2,
                background_color=WHITE,
                border_radius=_radius("6px"),
                box_shadow=CARD,
            ),
        ]
    )


def test_mock_frame_indexes_flag_framed_windows_with_their_own_navigation() -> None:
    page = _mockup_page()
    assert mock_frame_indexes(page) == frozenset({1, 2, 3, 4})
    # Navigation outside <main> (the site header) never marks a frame.
    header = _page(
        [
            _el(0, "header", children=1, background_color=WHITE, border_radius=_radius("12px")),
            _el(1, "nav", parent=0),
        ]
    )
    assert mock_frame_indexes(header) == frozenset()


def test_mock_up_panels_are_not_shadow_levels_or_radii() -> None:
    page = _mockup_page()
    assert [level.used_on for level in analyze_shadows([page])] == [["article.card"]]
    assert [step.value_px for step in analyze_radii([page])] == [6.0]


def test_mock_up_text_and_spacing_are_skipped_by_every_foundation() -> None:
    # linear.app: the 12px labels of the hero app window ("2min ago") made a caption level of
    # their own; typography and spacing now skip the window like shapes and components do.
    page = _mockup_page()
    kept = {ref.element.index for ref in iter_foundation_elements([page])}
    assert kept.isdisjoint({1, 2, 3, 4})
    assert {0, 10} <= kept


# ─── Lone component surfaces ─────────────────────────────────────────────────


def test_is_component_surface() -> None:
    assert is_component_surface(_el(0, children=2, background_color=WHITE))
    assert is_component_surface(_el(0, "a"))
    assert not is_component_surface(_el(0, children=2))  # unpainted wrapper
    assert not is_component_surface(_el(0, background_color=WHITE))  # empty shape
    assert not is_component_surface(_el(0, "img", background_color=WHITE, children=1))


def test_a_lone_card_shadow_stays_a_level_among_many() -> None:
    # stripe.com: the contact-sales form card is the only element with its shadow; with more
    # than four levels the one-off shadows of an image and a bare shape go, the card's stays.
    pricing = _page(
        [
            *[_el(i, children=3, background_color=WHITE, box_shadow=SOFT) for i in range(5)],
            *[_el(10 + i, children=3, background_color=WHITE, box_shadow=CARD) for i in range(3)],
            _el(20, "img", box_shadow="rgba(55, 39, 37, 0.15) 0px 10px 50px -7.8px"),
            _el(21, box_shadow="rgba(0, 0, 0, 0.2) 0px 40px 80px 0px"),
            _el(
                22,
                classes=("csf-form-card",),
                children=2,
                background_color=WHITE,
                box_shadow=FORM,
            ),
        ],
        "contact-sales",
    )
    shadows = analyze_shadows([pricing])
    assert [(level.name, level.count) for level in shadows] == [("sm", 5), ("md", 3), ("lg", 1)]
    assert shadows[-1].used_on == ["div.csf-form-card"]
    assert shadows[-1].css == (
        "0px 20px 80px -16px rgba(0, 55, 112, 0.14), 0px 10px 60px -16px rgba(0, 59, 137, 0.06)"
    )
