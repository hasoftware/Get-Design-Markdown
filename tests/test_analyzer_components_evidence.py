"""How much evidence a component spec rests on: groups ranked by page-weighted counts (the
requested page leads), hover properties shared by most hovered members, and corner radii kept
when they differ (a tab is not a rounded box).

Synthetic pages modelled on findings from stripe.com (round 2 of the verification).
"""

from __future__ import annotations

from typing import Any

import pytest

from get_design_markdown.analyzer.components_context import rounded_value
from get_design_markdown.models.analysis import (
    ColorPalette,
    ColorRole,
    ColorToken,
    ScaleStep,
)
from tests.test_analyzer_components import (
    BRAND,
    PageBuilder,
    analyze,
    border_styles,
    button_styles,
)

MAIN_LINK = "rgb(83, 58, 253)"
SUB_LINK = "rgb(99, 91, 255)"
TAB = ("4px", "4px", "0px", "0px")


def _filler(b: PageBuilder, count: int) -> None:
    """Plain blocks: a long sub-page (a pricing table) has many rendered elements."""
    for i in range(count):
        b.add("div", b.main, (100, 1200 + 30 * i, 600, 24), f"Row {i}")


def _inline_links(b: PageBuilder, labels: list[str], **styles: Any) -> None:
    paragraph = b.add("p", b.main, (100, 400, 900, 120), "Read more:", own_text=True)
    for i, label in enumerate(labels):
        b.add("a", paragraph, (300 + 90 * i, 400, 80, 20), label, display="inline", **styles)


# ─── Page-weighted ranking ───────────────────────────────────────────────────


def test_text_link_follows_the_main_page_over_a_longer_sub_page() -> None:
    # stripe.com: 18 small #635bff links of the pricing page outnumbered the 5 inline
    # #533afd callout links of the home page.
    home = PageBuilder()
    _inline_links(home, ["View services", "View partners"], color=MAIN_LINK, font_size="16px")
    pricing = PageBuilder()
    labels = [f"Feature {i}" for i in range(6)]
    _inline_links(
        pricing,
        labels,
        color=SUB_LINK,
        font_size="13px",
        font_weight="425",
        letter_spacing="0.2px",
    )
    _filler(pricing, 40)
    link = analyze([home.page(), pricing.page("pricing")])["text-link"]
    assert link.properties["textColor"] == "#533afd"
    assert link.properties["fontSize"] == "16px"
    assert "letterSpacing" not in link.properties
    assert link.count == 2
    assert link.samples == ["View services", "View partners"]


def test_text_input_follows_the_main_page_over_a_longer_sub_page() -> None:
    field: dict[str, Any] = {
        "padding": ("8px", "12px", "8px", "12px"),
        "border_radius": ("6px", "6px", "6px", "6px"),
    }
    home = PageBuilder()
    home.add(
        "input",
        home.main,
        (100, 300, 320, 40),
        input_type="email",
        background_color="rgb(255, 255, 255)",
        **field,
        **border_styles("rgb(203, 213, 225)"),
    )
    sub = PageBuilder()
    for i in range(3):
        sub.add(
            "input",
            sub.main,
            (100, 300 + 60 * i, 320, 40),
            input_type="text",
            background_color="rgb(241, 245, 249)",
            **field,
        )
    _filler(sub, 40)
    component = analyze([home.page(), sub.page("contact")])["text-input"]
    assert component.properties["borderColor"] == "#cbd5e1"
    assert component.samples == ["input[type=email]"]


# ─── Hover states ────────────────────────────────────────────────────────────


def _hovered_buttons(*changes: dict[str, tuple[str, str]]) -> PageBuilder:
    b = PageBuilder()
    for i, changed in enumerate(changes):
        button = b.add(
            "a", b.main, (100 + 200 * i, 200, 160, 44), f"Go {i}", **button_styles(BRAND)
        )
        b.hover(button, "button", **changed)
    return b


def test_hover_drops_a_change_seen_on_one_of_two_members() -> None:
    # stripe.com pricing: "Contact sales" fades to 0.6 on hover, "Get started" does not.
    to_navy = ("rgb(99, 91, 255)", "rgb(10, 37, 64)")
    b = _hovered_buttons(
        {"background_color": to_navy, "opacity": ("1", "0.600022")},
        {"background_color": to_navy},
    )
    primary = analyze([b.page()])["button-primary"]
    assert primary.states == {"hover": {"backgroundColor": "#0a2540"}}


def test_hover_keeps_a_change_seen_on_most_members() -> None:
    to_navy = ("rgb(99, 91, 255)", "rgb(10, 37, 64)")
    fade = ("1", "0.6")
    b = _hovered_buttons(
        {"background_color": to_navy, "opacity": fade},
        {"background_color": to_navy, "opacity": fade},
        {"background_color": to_navy},
    )
    primary = analyze([b.page()])["button-primary"]
    assert primary.states == {"hover": {"backgroundColor": "#0a2540", "opacity": "0.6"}}


def test_shared_hover_color_still_gets_its_token_reference() -> None:
    # No member's full change set equals the merged state: the reference is still resolved.
    to_navy = ("rgb(99, 91, 255)", "rgb(10, 37, 64)")
    b = _hovered_buttons(
        {"background_color": to_navy, "opacity": ("1", "0.6")},
        {"background_color": to_navy, "color": ("rgb(255, 255, 255)", "rgb(238, 242, 255)")},
    )
    palette = ColorPalette(
        tokens=[
            ColorToken(name="canvas", value="#ffffff", role=ColorRole.BACKGROUND),
            ColorToken(name="primary", value="#635bff", role=ColorRole.BRAND),
            ColorToken(name="ink", value="#0a2540", role=ColorRole.TEXT),
        ]
    )
    primary = analyze([b.page()], palette=palette)["button-primary"]
    assert primary.states == {"hover": {"backgroundColor": "#0a2540"}}
    assert primary.state_token_refs == {"hover": {"backgroundColor": "{colors.ink}"}}


# ─── Corner radii ────────────────────────────────────────────────────────────


def _badge(radius: tuple[str, str, str, str], height: float = 30) -> PageBuilder:
    b = PageBuilder()
    b.add(
        "span",
        b.main,
        (474, 600, 114, height),
        "Choose a plan",
        background_color="rgb(246, 249, 251)",
        color="rgb(10, 37, 64)",
        font_size="14px",
        padding=("3px", "10px", "3px", "10px"),
        border_radius=radius,
    )
    return b


def test_tab_badge_keeps_its_four_corners_without_a_radius_reference() -> None:
    # stripe.com pricing: "Choose a plan" is a tab on top of a card (4px 4px 0px 0px).
    radii = [ScaleStep(name="md", value_px=4), ScaleStep(name="full", value_px=9999)]
    badge = analyze([_badge(TAB).page()], radii=radii)["badge"]
    assert badge.properties["rounded"] == "4px 4px 0px 0px"
    assert "rounded" not in badge.token_refs
    assert badge.variant is None


def test_even_corners_stay_one_radius_with_its_reference() -> None:
    radii = [ScaleStep(name="md", value_px=4)]
    badge = analyze([_badge(("4px", "4px", "4px", "4px")).page()], radii=radii)["badge"]
    assert badge.properties["rounded"] == "4px"
    assert badge.token_refs["rounded"] == "{rounded.md}"


def test_tab_rounded_on_top_only_is_not_a_pill() -> None:
    badge = analyze([_badge(("999px", "999px", "0px", "0px"), height=24).page()])["badge"]
    assert badge.variant is None
    assert badge.properties["rounded"] == "9999px 9999px 0px 0px"


def test_card_keeps_uneven_corners_of_its_surface() -> None:
    b = PageBuilder()
    grid = b.add("div", b.main, (100, 600, 1200, 240), own_text=False)
    for x in (100, 460):
        card = b.add(
            "div",
            grid,
            (x, 600, 320, 240),
            own_text=False,
            background_color="rgb(241, 245, 249)",
            padding=("24px", "24px", "24px", "24px"),
            border_radius=("12px", "12px", "0px", "0px"),
        )
        b.add("h3", card, (x + 24, 624, 272, 28), f"Card {x}", font_size="20px")
        b.add("p", card, (x + 24, 664, 272, 48), "Body copy")
    card_spec = analyze([b.page()], radii=[ScaleStep(name="lg", value_px=12)])["card"]
    assert card_spec.properties["rounded"] == "12px 12px 0px 0px"
    assert "rounded" not in card_spec.token_refs


@pytest.mark.parametrize(
    ("corners", "expected"),
    [
        ((4.0, 4.0, 0.0, 0.0), "4px 4px 0px 0px"),
        ((8.0, 0.0, 8.0, 0.0), "8px 0px"),
        ((6.0, 6.0, 6.0, 6.0), "6px"),
        ((4.0, 4.2, 4.0, 4.0), "4.2px"),
        ((0.0, 0.0, 0.0, 0.0), None),
    ],
)
def test_rounded_value(corners: tuple[float, float, float, float], expected: str | None) -> None:
    assert rounded_value(corners) == expected
