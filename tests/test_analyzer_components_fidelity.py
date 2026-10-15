"""Component specs that stay faithful to what was measured: hover references keep the
direction of the change, representatives follow the most seen instance and the primary's
size, chrome gradients are not "transparent", type references need the same tracking.

Synthetic pages modelled on findings from stripe.com and linear.app.
"""

from __future__ import annotations

from typing import Any

import pytest

from get_design_markdown.analyzer.components_props import keeps_change
from get_design_markdown.analyzer.css_values import parse_color
from get_design_markdown.models.analysis import (
    ColorPalette,
    ColorRole,
    ColorToken,
    TypeLevel,
    Typography,
)
from tests.test_analyzer_components import (
    BRAND,
    WHITE,
    PageBuilder,
    analyze,
    border_styles,
    button_styles,
)

DARK = "rgb(8, 9, 10)"
LIGHT_TEXT = "rgb(247, 248, 248)"
GHOST_FILL = "rgba(255, 255, 255, 0.05)"
HOVER_FILL = "rgb(25, 26, 27)"
PILL = ("9999px", "9999px", "9999px", "9999px")


def _dark_palette(*extra: ColorToken) -> ColorPalette:
    return ColorPalette(
        tokens=[ColorToken(name="canvas", value="#08090a", role=ColorRole.BACKGROUND), *extra]
    )


def _hovered_pill(hover_to: str = HOVER_FILL) -> PageBuilder:
    """linear.app: a #ffffff0d pill on #08090a that lightens to #191a1b on hover."""
    b = PageBuilder(body_background=DARK)
    button = b.add(
        "a", b.main, (100, 300, 150, 44), "Contact sales", **button_styles(GHOST_FILL, LIGHT_TEXT)
    )
    b.hover(button, background_color=(GHOST_FILL, hover_to))
    return b


# ─── Hover token references ──────────────────────────────────────────────────


def test_hover_never_snaps_to_a_token_on_the_other_side_of_the_rest() -> None:
    # #0f1011 is within ΔE2000 3 of #191a1b, but darker than the resting #141516.
    surface = ColorToken(name="surface", value="#0f1011", role=ColorRole.SURFACE)
    spec = analyze([_hovered_pill().page()], palette=_dark_palette(surface))["button-primary"]
    assert spec.states == {"hover": {"backgroundColor": "#191a1b"}}
    assert spec.state_token_refs == {}


def test_dark_hover_needs_a_close_token() -> None:
    # #202122 keeps the direction (lighter, nearer the hover) but is ΔE 2.2 away near black.
    raised = ColorToken(name="surface-raised", value="#202122", role=ColorRole.SURFACE)
    spec = analyze([_hovered_pill().page()], palette=_dark_palette(raised))["button-primary"]
    assert spec.state_token_refs == {}


def test_hover_keeps_a_token_that_reads_as_the_hover() -> None:
    raised = ColorToken(name="surface-raised", value="#1c1d1e", role=ColorRole.SURFACE)
    spec = analyze([_hovered_pill().page()], palette=_dark_palette(raised))["button-primary"]
    assert spec.state_token_refs == {"hover": {"backgroundColor": "{colors.surface-raised}"}}


def test_light_hover_tokens_still_resolve() -> None:
    b = PageBuilder()
    styles = button_styles("rgba(0, 0, 0, 0)", "rgb(15, 23, 42)", **border_styles("#cbd5e1"))
    button = b.add("button", b.main, (100, 300, 150, 44), "Book a demo", **styles)
    b.hover(button, background_color=("rgba(0, 0, 0, 0)", "rgb(248, 250, 252)"))
    palette = ColorPalette(
        tokens=[
            ColorToken(name="canvas", value="#ffffff", role=ColorRole.BACKGROUND),
            ColorToken(name="surface", value="#f8fafc", role=ColorRole.SURFACE),
        ]
    )
    spec = analyze([b.page()], palette=palette)["button-outline"]
    assert spec.state_token_refs == {"hover": {"backgroundColor": "{colors.surface}"}}


@pytest.mark.parametrize(
    ("token", "expected"),
    [("#0f1011", False), ("#141516", False), ("#191a1b", True), ("#1c1d1e", True)],
)
def test_keeps_change(token: str, expected: bool) -> None:
    rest, hover, behind = (parse_color(v) for v in (GHOST_FILL, HOVER_FILL, DARK))
    color = parse_color(token)
    assert rest is not None and hover is not None and behind is not None and color is not None
    assert keeps_change(color, rest, hover, behind) is expected


# ─── Representatives ─────────────────────────────────────────────────────────


def test_outline_background_follows_the_most_seen_instance() -> None:
    # stripe.com: the hero "Sign up with Google" has a #ffffffa6 fill, two plain ones further
    # down are transparent; the hero outweighs them.
    b = PageBuilder()
    border = border_styles("rgb(185, 185, 249)")
    hero = button_styles("rgba(255, 255, 255, 0.65)", BRAND, **border)
    b.add("a", b.main, (357, 519, 212, 48), "Sign up with Google", **hero)
    plain = button_styles("rgba(255, 255, 255, 0)", BRAND, **border)
    b.add("a", b.main, (1168, 2600, 168, 48), "Read the letter", **plain)
    b.add("a", b.main, (241, 2900, 146, 48), "Contact sales", **plain)
    outline = analyze([b.page()])["button-outline"]
    assert outline.count == 3
    assert outline.properties["backgroundColor"] == "#ffffffa6"


def test_many_plain_instances_keep_the_outline_transparent() -> None:
    b = PageBuilder()
    border = border_styles("rgb(185, 185, 249)")
    hero = button_styles("rgba(255, 255, 255, 0.65)", BRAND, **border)
    b.add("a", b.main, (100, 2600, 160, 44), "Sign up", **hero)
    plain = button_styles("rgba(255, 255, 255, 0)", BRAND, **border)
    for i in range(3):
        b.add("a", b.main, (100 + 220 * i, 400, 200, 48), f"Learn more {i}", **plain)
    outline = analyze([b.page()])["button-outline"]
    assert outline.properties["backgroundColor"] == "transparent"


def _size(padding_x: str, font: str) -> dict[str, Any]:
    return {
        "padding": ("0px", padding_x, "0px", padding_x),
        "font_size": font,
        "line_height": "normal",
        "border_radius": PILL,
    }


def test_variants_are_documented_at_the_primary_size() -> None:
    # linear.app: "Get started" / "Contact sales" are both 44px / 16px; the secondary's most
    # prominent instances are wide 40px / 13px buttons elsewhere.
    b = PageBuilder(body_background=DARK)
    large, medium = _size("20px", "16px"), _size("14px", "13px")
    primary = button_styles("rgb(229, 229, 230)", DARK, **large)
    b.add("a", b.main, (579, 400, 127, 44), "Get started", **primary)
    b.add("a", b.main, (718, 400, 144, 44), "Contact sales", **button_styles(GHOST_FILL, **large))
    for x in (80, 417):
        b.add("a", b.main, (x, 770, 272, 40), "Get started", **button_styles(GHOST_FILL, **medium))
    components = analyze([b.page()])
    assert components["button-primary"].properties["height"] == "44px"
    secondary = components["button-secondary"]
    assert secondary.count == 3
    assert secondary.properties["height"] == "44px"
    assert secondary.properties["fontSize"] == "16px"
    assert secondary.properties["padding"] == "0px 20px"


def test_variants_without_the_primary_size_keep_their_own() -> None:
    b = PageBuilder(body_background=DARK)
    primary = button_styles("rgb(229, 229, 230)", DARK, **_size("20px", "16px"))
    b.add("a", b.main, (579, 400, 127, 44), "Get started", **primary)
    medium = button_styles(GHOST_FILL, **_size("14px", "13px"))
    b.add("a", b.main, (80, 770, 272, 40), "Contact sales", **medium)
    assert analyze([b.page()])["button-secondary"].properties["height"] == "40px"


# ─── Chrome & typography ─────────────────────────────────────────────────────


def test_transparent_header_shows_its_gradient_tint() -> None:
    # linear.app: transparent header painting a translucent dark gradient under blur(20px).
    b = PageBuilder(body_background=DARK)
    gradient = "linear-gradient(rgba(11, 11, 11, 0.8) 0%, rgba(10, 10, 10, 0.76) 100%)"
    header = b.add(
        "header",
        b.body,
        (0, 0, 1440, 73),
        own_text=False,
        landmark="header",
        background_image=gradient,
        backdrop_filter="blur(20px)",
        position="fixed",
    )
    b.add("a", header, (100, 20, 80, 32), "Product", color=LIGHT_TEXT)
    top = analyze([b.page()])["top-nav"]
    assert top.properties["backgroundColor"] == "#0b0b0bcc"
    assert top.properties["backdropFilter"] == "blur(20px)"


def test_covering_fill_still_beats_the_gradient() -> None:
    b = PageBuilder()
    header = b.add(
        "header",
        b.body,
        (0, 0, 1440, 72),
        own_text=False,
        landmark="header",
        background_image="linear-gradient(rgb(0, 0, 0), rgb(20, 20, 20))",
    )
    bar = b.add("div", header, (0, 0, 1440, 72), own_text=False, background_color=WHITE)
    b.add("a", bar, (100, 20, 80, 32), "Product")
    assert analyze([b.page()])["top-nav"].properties["backgroundColor"] == "#ffffff"


@pytest.mark.parametrize(("tracking", "referenced"), [(-0.13, False), (0.0, True)])
def test_typography_ref_needs_the_same_letter_spacing(tracking: float, referenced: bool) -> None:
    # linear.app: nav links have letter-spacing normal; body-sm is tracked -0.13px.
    b = PageBuilder()
    header = b.add("header", b.body, (0, 0, 1440, 72), own_text=False, landmark="header")
    nav = b.add("nav", header, (600, 0, 600, 72), own_text=False, landmark="nav")
    for i, label in enumerate(("Product", "Pricing", "Contact")):
        b.add(
            "a",
            nav,
            (600 + 100 * i, 20, 80, 32),
            label,
            font_size="13px",
            font_weight="400",
            letter_spacing="normal",
        )
    level = TypeLevel(
        name="body-sm", size_px=13, font_family="Inter", weight=400, letter_spacing_px=tracking
    )
    refs = analyze([b.page()], typography=Typography(levels=[level]))["nav-link"].token_refs
    assert (refs.get("typography") == "{typography.body-sm}") is referenced
