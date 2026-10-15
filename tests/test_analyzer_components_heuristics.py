"""Component heuristics tuned on real sites (stripe.com, linear.app), as synthetic pages."""

from __future__ import annotations

from typing import Any

from get_design_markdown.analyzer.components_context import build_contexts
from get_design_markdown.analyzer.components_detect import decorative_elements, is_icon_only
from get_design_markdown.models.analysis import ColorPalette, ColorRole, ColorToken
from get_design_markdown.models.raw import BoundingBox, ElementSample
from tests.test_analyzer_components import (
    BRAND,
    INK,
    TRANSPARENT,
    WHITE,
    PageBuilder,
    analyze,
    border_styles,
    button_styles,
)

DARK = "rgb(8, 9, 10)"


def test_buttons_inside_role_img_illustrations_are_ignored() -> None:
    b = PageBuilder()
    graphic = b.add("div", b.main, (100, 300, 600, 400), own_text=False, role="img")
    b.add("div", graphic, (120, 320, 160, 40), "Continue", **button_styles(BRAND))
    b.add("button", graphic, (120, 380, 160, 40), "Pay now", **button_styles(BRAND))
    assert not any(name.startswith("button-") for name in analyze([b.page()]))


def test_figure_and_illustration_classes_are_decorative() -> None:
    b = PageBuilder()
    figure = b.add("figure", b.main, (100, 300, 600, 400), own_text=False)
    pill: dict[str, Any] = {
        "background_color": "rgb(236, 237, 254)",
        "font_size": "12px",
        "padding": ("2px", "8px", "2px", "8px"),
        "border_radius": ("999px", "999px", "999px", "999px"),
    }
    b.add("span", figure, (120, 320, 80, 20), "Succeeded", **pill)
    art = b.add(
        "div", b.main, (100, 800, 600, 400), own_text=False, classes=["payments-graphic__screen"]
    )
    b.add("span", art, (120, 820, 80, 20), "Refunded", **pill)
    illustration = b.add(
        "div", b.main, (100, 1300, 600, 400), own_text=False, classes=["b-30Va_illustration"]
    )
    b.add("span", illustration, (120, 1320, 80, 20), "Bug", **pill)
    assert "badge" not in analyze([b.page()])
    b.add("span", b.main, (120, 1800, 80, 20), "New", **pill)
    assert analyze([b.page()])["badge"].samples == ["New"]


def _app_window(b: PageBuilder) -> int:
    """A framed app mock-up holding its own sidebar <nav> inside <main>."""
    frame = b.add(
        "div",
        b.main,
        (60, 500, 1320, 720),
        own_text=False,
        background_color="rgb(9, 10, 11)",
        border_radius=("12px", "12px", "12px", "12px"),
    )
    sidebar = b.add("nav", frame, (68, 510, 232, 700), own_text=False, landmark="nav")
    selected = button_styles("rgba(255, 255, 255, 0.04)", "rgb(208, 214, 224)")
    b.add("button", sidebar, (76, 600, 208, 28), "Faster app launch", **selected)
    return frame


def test_app_window_mockups_are_decorative() -> None:
    b = PageBuilder(body_background=DARK)
    frame = _app_window(b)
    b.add("a", b.main, (600, 1500, 140, 44), "Get started", **button_styles(WHITE, DARK))
    ctx = build_contexts([b.page()])[0]
    assert frame in decorative_elements(ctx)
    components = analyze([b.page()])
    assert components["button-primary"].properties["backgroundColor"] == "#ffffff"
    assert "button-secondary" not in components
    assert "sidebar" not in components


def test_offscreen_carousel_items_are_ignored() -> None:
    b = PageBuilder()
    b.add("a", b.main, (1400, 300, 160, 44), "Next slide", **button_styles(BRAND))
    assert "button-primary" not in analyze([b.page()])


def test_header_cta_decides_the_primary_among_neutral_buttons() -> None:
    b = PageBuilder(body_background=DARK)
    header = b.add("header", b.body, (0, 0, 1440, 72), own_text=False, landmark="header")
    light = button_styles("rgb(229, 229, 230)", DARK)
    b.add("a", header, (1280, 20, 80, 32), "Sign up", **light)
    big = button_styles("rgba(255, 255, 255, 0.08)", "rgb(247, 248, 248)")
    b.add("a", b.main, (100, 400, 400, 60), "Contact our sales team", **big)
    components = analyze([b.page()])
    assert components["button-primary"].properties["backgroundColor"] == "#e5e5e6"
    assert components["button-secondary"].properties["backgroundColor"] == "#ffffff14"


def test_boxed_link_without_padding_is_a_button_not_a_text_link() -> None:
    b = PageBuilder()
    styles = {
        **border_styles("rgb(214, 217, 252)"),
        "color": BRAND,
        "display": "inline-flex",
        "border_radius": ("4px", "4px", "4px", "4px"),
    }
    link = b.add("a", b.main, (1170, 400, 166, 40), "Read the story", own_text=False, **styles)
    b.add("span", link, (1186, 410, 120, 20), "Read the story", color=BRAND)
    b.add("a", b.main, (100, 600, 120, 20), "View partners", display="inline-block", color=BRAND)
    components = analyze([b.page()])
    assert components["button-outline"].samples == ["Read the story"]
    assert components["text-link"].samples == ["View partners"]
    assert "borderColor" not in components["text-link"].properties


def test_label_wrapper_padding_is_reported() -> None:
    b = PageBuilder()
    link = b.add(
        "a",
        b.main,
        (100, 300, 160, 44),
        "Start now",
        own_text=False,
        background_color=BRAND,
        border_radius=("6px", "6px", "6px", "6px"),
        display="inline-flex",
    )
    b.add(
        "span",
        link,
        (100, 300, 160, 44),
        "Start now",
        color=WHITE,
        padding=("12px", "20px", "12px", "20px"),
    )
    primary = analyze([b.page()])["button-primary"]
    assert primary.properties["padding"] == "12px 20px"
    assert primary.properties["textColor"] == "#ffffff"


def test_square_button_with_hidden_label_is_icon_only() -> None:
    element = ElementSample(
        index=0,
        tag="a",
        text="Read the story",
        box=BoundingBox(x=0, y=0, width=40, height=40),
    )
    assert is_icon_only(element)
    wide = element.model_copy(update={"box": BoundingBox(x=0, y=0, width=84, height=40)})
    assert not is_icon_only(wide.model_copy(update={"text": "Sign in Sign in"}))


def test_hover_ignores_stale_and_invisible_changes() -> None:
    b = PageBuilder()
    header = b.add("header", b.body, (0, 0, 1440, 64), own_text=False, landmark="header")
    nav = b.add("nav", header, (200, 12, 600, 40), own_text=False, landmark="nav")
    link = b.add("a", nav, (200, 14, 90, 36), "Products", color=INK)
    b.hover(
        link,
        "link",
        color=("rgb(80, 97, 122)", INK),
        border_color=(INK, "rgb(83, 58, 253)"),
    )
    assert analyze([b.page()])["nav-link"].states == {}


def test_borderless_field_takes_its_frame_from_wrappers() -> None:
    b = PageBuilder()
    frame = b.add(
        "div",
        b.main,
        (336, 300, 768, 131),
        own_text=False,
        background_color="rgb(212, 222, 233)",
        padding=("1px", "1px", "1px", "1px"),
        border_radius=("6px", "6px", "6px", "6px"),
    )
    label = b.add(
        "label",
        frame,
        (337, 301, 766, 129),
        own_text=False,
        background_color=WHITE,
        border_radius=("6px", "6px", "6px", "6px"),
    )
    b.add(
        "textarea",
        label,
        (337, 301, 766, 129),
        background_color=WHITE,
        padding=("11px", "17px", "11px", "17px"),
    )
    field = analyze([b.page()])["text-input"]
    assert field.properties["borderColor"] == "#d4dee9"
    assert field.properties["borderWidth"] == "1px"
    assert field.properties["rounded"] == "6px"


def test_transparent_text_fields_are_ignored() -> None:
    b = PageBuilder()
    b.add("textarea", b.main, (100, 300, 600, 200), color=TRANSPARENT)
    assert "text-input" not in analyze([b.page()])


def _layered_card(b: PageBuilder, parent: int, x: float) -> int:
    card = b.add("button", parent, (x, 1000, 400, 400), own_text=False, display="block")
    frame = b.add(
        "div",
        card,
        (x - 1, 999, 402, 402),
        own_text=False,
        background_color="rgb(229, 237, 245)",
        border_radius=("6px", "6px", "6px", "6px"),
    )
    b.add("div", frame, (x, 1000, 400, 400), own_text=False, background_color=WHITE)
    text = b.add("div", card, (x + 24, 1024, 300, 60), own_text=False)
    b.add("h3", text, (x + 24, 1024, 300, 28), f"Tile {x:g}", font_size="22px")
    b.add("p", text, (x + 24, 1056, 300, 28), "Description")
    return card


def test_layered_card_surface_and_visual_padding() -> None:
    b = PageBuilder()
    grid = b.add("div", b.main, (100, 1000, 1240, 400), own_text=False)
    _layered_card(b, grid, 100)
    _layered_card(b, grid, 520)
    card = analyze([b.page()])["card"]
    assert card.count == 2
    assert card.properties == {
        "backgroundColor": "#ffffff",
        "borderColor": "#e5edf5",
        "borderWidth": "1px",
        "rounded": "6px",
        "padding": "24px",
    }


def test_card_padding_on_an_inner_body_wrapper() -> None:
    b = PageBuilder(body_background="rgb(241, 245, 249)")
    grid = b.add("div", b.main, (100, 600, 1200, 300), own_text=False)
    for x in (100, 500):
        card = b.add(
            "div",
            grid,
            (x, 600, 360, 300),
            own_text=False,
            background_color=WHITE,
            border_radius=("8px", "8px", "8px", "8px"),
        )
        b.add("img", card, (x, 600, 360, 160), own_text=False)
        body: dict[str, Any] = {"padding": ("16px", "16px", "16px", "16px")}
        inner = b.add("div", card, (x, 760, 360, 140), own_text=False, **body)
        b.add("h5", inner, (x + 16, 776, 328, 24), "Card title")
        b.add("p", inner, (x + 16, 808, 328, 48), "Some quick example text.")
    found = analyze([b.page()])["card"]
    assert found.count == 2
    assert found.properties["padding"] == "16px"
    assert found.properties["backgroundColor"] == "#ffffff"


def test_inner_layer_of_a_card_is_not_a_second_card() -> None:
    b = PageBuilder()
    grid = b.add("div", b.main, (100, 600, 1200, 300), own_text=False)
    for x in (100, 500):
        outer = b.add(
            "article",
            grid,
            (x, 600, 360, 300),
            own_text=False,
            box_shadow="rgba(0, 0, 0, 0.1) 0px 4px 12px 0px",
            border_radius=("12px", "12px", "12px", "12px"),
            background_color=WHITE,
            padding=("24px", "24px", "24px", "24px"),
        )
        inner = b.add(
            "div",
            outer,
            (x + 2, 602, 356, 296),
            own_text=False,
            **border_styles("rgb(226, 232, 240)"),
            border_radius=("10px", "10px", "10px", "10px"),
            padding=("22px", "22px", "22px", "22px"),
        )
        b.add("h3", inner, (x + 24, 624, 300, 28), "Title")
        b.add("p", inner, (x + 24, 660, 300, 48), "Body")
    components = analyze([b.page()])
    assert components["card"].count == 2
    assert "card-elevated" not in components


def test_strongest_neutral_fill_is_primary_when_both_are_in_the_header() -> None:
    b = PageBuilder()
    header = b.add("header", b.body, (0, 0, 1440, 64), own_text=False, landmark="header")
    ringed = button_styles(WHITE, INK, box_shadow="rgb(235, 235, 235) 0px 0px 0px 1px")
    b.add("a", header, (1100, 16, 110, 32), "Get a Demo", **ringed)
    b.add("a", header, (1220, 16, 80, 32), "Sign Up", **button_styles("rgb(23, 23, 23)"))
    b.add("a", b.main, (100, 400, 300, 48), "Talk to our sales team", **ringed)
    components = analyze([b.page()])
    assert components["button-primary"].properties["backgroundColor"] == "#171717"
    assert components["button-secondary"].properties["backgroundColor"] == "#ffffff"


def test_badge_named_links_are_badges_not_buttons() -> None:
    b = PageBuilder()
    b.add(
        "a",
        b.main,
        (500, 150, 340, 38),
        "Security updates for Bootstrap 3 & 4",
        classes=["badge", "badge-subtle", "badge-lg"],
        background_color="rgb(255, 247, 226)",
        color="rgb(158, 122, 0)",
        padding=("8px", "16px", "8px", "16px"),
        border_radius=("999px", "999px", "999px", "999px"),
    )
    components = analyze([b.page()])
    assert not any(name.startswith("button-") for name in components)
    assert components["badge"].variant == "pill"
    assert components["badge"].class_hints[0] == "badge"


def test_pinned_header_is_the_top_nav_whatever_its_measured_y() -> None:
    b = PageBuilder()
    header = b.add(
        "header",
        b.body,
        (0, 735, 1440, 72),
        own_text=False,
        role="banner",
        landmark="header",
        position="fixed",
    )
    b.add("a", header, (100, 751, 80, 40), "Product")
    assert analyze([b.page()])["top-nav"].properties["position"] == "fixed"


def test_utility_classes_are_not_hints() -> None:
    b = PageBuilder()
    classes = ["btn", "btn-primary", "px-4", "md:px-6", "bg-blue-500", "text-copy-14", "!px-2"]
    b.add("a", b.main, (100, 200, 160, 40), "Start", classes=classes, **button_styles(BRAND))
    assert analyze([b.page()])["button-primary"].class_hints == ["btn", "btn-primary"]


def test_border_colors_prefer_border_tokens_of_the_same_value() -> None:
    b = PageBuilder()
    styles = button_styles(TRANSPARENT, INK, **border_styles("rgb(229, 237, 245)"))
    b.add("a", b.main, (100, 300, 150, 44), "Learn more", **styles)
    b.add("a", b.main, (300, 300, 150, 44), "Panel", **button_styles("rgb(229, 237, 245)", INK))
    palette = ColorPalette(
        tokens=[
            ColorToken(name="surface-raised", value="#e5edf5", role=ColorRole.SURFACE),
            ColorToken(name="hairline", value="#e5edf5", role=ColorRole.BORDER),
        ]
    )
    components = analyze([b.page()], palette=palette)
    assert components["button-outline"].token_refs["borderColor"] == "{colors.hairline}"
    filled = components["button-primary"].token_refs["backgroundColor"]
    assert filled == "{colors.surface-raised}"
