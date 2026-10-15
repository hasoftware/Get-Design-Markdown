"""Component detection (analyzer/components.py): fixture equality and synthetic heuristics."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

import pytest

from get_design_markdown.analyzer.components import analyze_components, sort_components
from get_design_markdown.models.analysis import (
    ColorPalette,
    ColorRole,
    ColorToken,
    ComponentKind,
    ComponentSpec,
    ScaleStep,
    SpacingSystem,
    TypeLevel,
    Typography,
)
from get_design_markdown.models.raw import (
    BoundingBox,
    ComputedStyle,
    ElementSample,
    HoverState,
    Landmark,
    PageCapture,
)
from tests.fixture_data import load_analysis_sample, load_raw_sample, load_site_contract

WHITE = "rgb(255, 255, 255)"
INK = "rgb(15, 23, 42)"
BRAND = "rgb(99, 91, 255)"
TRANSPARENT = "rgba(0, 0, 0, 0)"


def _analyze_sample(*, empty: bool = False) -> list[ComponentSpec]:
    raw = load_raw_sample()
    reference = load_analysis_sample()
    if empty:
        return analyze_components(
            raw.pages,
            palette=ColorPalette(),
            typography=Typography(),
            spacing=SpacingSystem(),
            radii=[],
            shadows=[],
        )
    return analyze_components(
        raw.pages,
        palette=reference.colors,
        typography=reference.typography,
        spacing=reference.spacing,
        radii=reference.radii,
        shadows=reference.shadows,
    )


# ─── Fixture: raw_sample.json -> analysis_sample.json ─────────────────────────


def test_fixture_component_names_match_contract() -> None:
    names = [component.name for component in _analyze_sample()]
    assert names == load_site_contract().components


@pytest.mark.parametrize("name", load_site_contract().components)
def test_fixture_component_matches_reference(name: str) -> None:
    got = {component.name: component for component in _analyze_sample()}[name]
    want = {component.name: component for component in load_analysis_sample().components}[name]
    assert got.kind == want.kind
    assert got.variant == want.variant
    assert got.properties == want.properties
    assert got.token_refs == want.token_refs
    assert got.states == want.states
    assert got.state_token_refs == want.state_token_refs
    if want.contrast_ratio is None:
        assert got.contrast_ratio is None
    else:
        assert got.contrast_ratio is not None
        assert abs(got.contrast_ratio - want.contrast_ratio) <= 0.01


def test_fixture_counts_hints_and_samples() -> None:
    components = {component.name: component for component in _analyze_sample()}
    assert components["button-primary"].count == 2
    assert components["button-primary"].samples == ["Start building free", "Get started"]
    assert components["nav-link"].count == 5
    assert components["card"].count == 3
    assert components["card"].samples[0] == "Deploy in seconds"
    assert components["text-input"].samples == ["input[type=email]"]
    assert components["text-link"].class_hints == ["text-link"]
    assert components["top-nav"].class_hints[0] == "site-header"
    assert components["footer"].class_hints[0] == "site-footer"
    # Hashed classes ("sc-bdVaJa", "css-1q2w3e4") never become hints.
    for component in components.values():
        assert "sc-bdVaJa" not in component.class_hints
        assert all("css-" not in hint for hint in component.class_hints)


def test_fixture_without_other_analyses_keeps_properties_and_drops_refs() -> None:
    reference = {component.name: component for component in load_analysis_sample().components}
    components = _analyze_sample(empty=True)
    assert [component.name for component in components] == list(reference)
    for component in components:
        assert component.properties == reference[component.name].properties
        assert component.states == reference[component.name].states
        assert component.token_refs == {}
        assert component.state_token_refs == {}


def test_fixture_refs_resolve_and_follow_property_rules() -> None:
    analysis = load_analysis_sample()
    names = {
        "colors": {token.name for token in analysis.colors.tokens},
        "typography": {level.name for level in analysis.typography.levels},
        "rounded": {step.name for step in analysis.radii},
        "spacing": {step.name for step in analysis.spacing.steps},
    }
    for component in _analyze_sample():
        for key, ref in component.token_refs.items():
            assert key == "typography" or key in component.properties
            group, _, name = ref.strip("{}").partition(".")
            assert name in names[group], (component.name, ref)
        for state, refs in component.state_token_refs.items():
            for key in refs:
                assert key in component.states[state]


def test_empty_pages_give_no_components() -> None:
    assert (
        analyze_components(
            [],
            palette=ColorPalette(),
            typography=Typography(),
            spacing=SpacingSystem(),
            radii=[],
            shadows=[],
        )
        == []
    )


def test_sort_components_puts_unknown_names_last_alphabetically() -> None:
    specs = [
        ComponentSpec(name=name, kind=ComponentKind.BUTTON)
        for name in ("zeta-widget", "footer", "alpha-widget", "button-primary")
    ]
    assert [spec.name for spec in sort_components(specs)] == [
        "button-primary",
        "footer",
        "alpha-widget",
        "zeta-widget",
    ]


# ─── Synthetic pages ─────────────────────────────────────────────────────────


class PageBuilder:
    """html > body (white) > main; add() appends elements in document order."""

    def __init__(self, *, body_background: str = WHITE) -> None:
        self.elements: list[ElementSample] = []
        self.hovers: list[HoverState] = []
        self.add("html", None, (0, 0, 1440, 3000), own_text=False)
        self.body = self.add(
            "body", 0, (0, 0, 1440, 3000), own_text=False, background_color=body_background
        )
        self.main = self.add(
            "main", self.body, (0, 80, 1440, 2500), own_text=False, landmark="main"
        )

    def add(
        self,
        tag: str,
        parent: int | None,
        box: tuple[float, float, float, float],
        text: str = "",
        *,
        own_text: bool = True,
        classes: Sequence[str] = (),
        landmark: Landmark | None = None,
        in_overlay: bool = False,
        role: str | None = None,
        input_type: str | None = None,
        placeholder_color: str | None = None,
        **styles: Any,
    ) -> int:
        index = len(self.elements)
        parent_element = None if parent is None else self.elements[parent]
        if landmark is None and parent_element is not None:
            landmark = parent_element.landmark
        if parent_element is not None:
            in_overlay = in_overlay or parent_element.in_overlay
        defaults: dict[str, Any] = {"color": INK, "font_family": "Inter, sans-serif"}
        if parent_element is not None:
            inherited = parent_element.styles
            defaults.update(
                color=inherited.color,
                font_family=inherited.font_family,
                font_size=inherited.font_size,
                font_weight=inherited.font_weight,
                line_height=inherited.line_height,
            )
        defaults.update(styles)
        x, y, width, height = box
        self.elements.append(
            ElementSample(
                index=index,
                tag=tag,
                role=role,
                classes=list(classes),
                text=text,
                has_own_text=own_text and bool(text),
                input_type=input_type,
                placeholder_color=placeholder_color,
                box=BoundingBox(x=x, y=y, width=width, height=height),
                depth=0 if parent_element is None else parent_element.depth + 1,
                parent_index=parent,
                landmark=landmark,
                in_overlay=in_overlay,
                styles=ComputedStyle(**defaults),
            )
        )
        if parent is not None and parent_element is not None:
            self.elements[parent] = parent_element.model_copy(
                update={"child_count": parent_element.child_count + 1}
            )
        return index

    def hover(
        self,
        index: int,
        kind: Literal["button", "link"] = "button",
        **changed: tuple[str, str],
    ) -> None:
        self.hovers.append(HoverState(element_index=index, kind=kind, changed=changed))

    def page(self, name: str = "home") -> PageCapture:
        url = f"https://example.test/{'' if name == 'home' else name}"
        return PageCapture(
            url=url,
            final_url=url,
            name=name,
            elements=list(self.elements),
            hover_states=list(self.hovers),
        )


def button_styles(background: str, color: str = WHITE, **extra: Any) -> dict[str, Any]:
    styles: dict[str, Any] = {
        "background_color": background,
        "color": color,
        "padding": ("10px", "18px", "10px", "18px"),
        "border_radius": ("6px", "6px", "6px", "6px"),
        "font_size": "15px",
        "font_weight": "500",
        "line_height": "20px",
        "display": "inline-flex",
        "cursor": "pointer",
    }
    styles.update(extra)
    return styles


def border_styles(color: str, width: str = "1px") -> dict[str, Any]:
    return {
        "border_width": (width, width, width, width),
        "border_style": ("solid", "solid", "solid", "solid"),
        "border_color": (color, color, color, color),
    }


def analyze(
    pages: Sequence[PageCapture],
    *,
    palette: ColorPalette | None = None,
    typography: Typography | None = None,
    radii: Sequence[ScaleStep] = (),
    spacing: SpacingSystem | None = None,
) -> dict[str, ComponentSpec]:
    components = analyze_components(
        pages,
        palette=palette or ColorPalette(),
        typography=typography or Typography(),
        spacing=spacing or SpacingSystem(),
        radii=list(radii),
        shadows=[],
    )
    return {component.name: component for component in components}


def test_anchor_styled_as_button_is_a_primary_button() -> None:
    b = PageBuilder()
    b.add("a", b.main, (100, 200, 160, 40), "Start now", **button_styles(BRAND))
    components = analyze([b.page()])
    primary = components["button-primary"]
    assert primary.kind is ComponentKind.BUTTON and primary.variant == "primary"
    assert primary.properties["backgroundColor"] == "#635bff"
    assert primary.properties["padding"] == "10px 18px"
    assert primary.properties["height"] == "40px"
    assert primary.properties["fontFamily"] == "Inter"


def test_anchor_without_button_padding_is_not_a_button() -> None:
    b = PageBuilder()
    styles = button_styles(BRAND, padding=("10px", "4px", "10px", "4px"))
    b.add("a", b.main, (100, 200, 160, 40), "Start now", **styles)
    assert "button-primary" not in analyze([b.page()])


def test_ghost_button_requires_a_background_hover() -> None:
    b = PageBuilder()
    ghost = b.add(
        "a", b.main, (100, 200, 140, 36), "Watch video", **button_styles(TRANSPARENT, BRAND)
    )
    assert "button-ghost" not in analyze([b.page()])
    b.hover(ghost, background_color=(TRANSPARENT, "rgb(238, 242, 255)"))
    components = analyze([b.page()])
    assert components["button-ghost"].variant == "ghost"
    assert components["button-ghost"].states == {"hover": {"backgroundColor": "#eef2ff"}}
    assert components["button-ghost"].properties["backgroundColor"] == "transparent"


def test_ghost_requires_rounded_corners() -> None:
    b = PageBuilder()
    styles = button_styles(TRANSPARENT, BRAND, border_radius=("0px", "0px", "0px", "0px"))
    ghost = b.add("a", b.main, (100, 200, 140, 36), "Watch video", **styles)
    b.hover(ghost, background_color=(TRANSPARENT, "rgb(238, 242, 255)"))
    assert "button-ghost" not in analyze([b.page()])


def test_nav_links_are_never_ghost_buttons() -> None:
    b = PageBuilder()
    header = b.add("header", b.body, (0, 0, 1440, 64), own_text=False, landmark="header")
    nav = b.add("nav", header, (200, 12, 600, 40), own_text=False, landmark="nav")
    links = [
        b.add("a", nav, (200 + 100 * i, 14, 90, 36), label, **button_styles(TRANSPARENT, INK))
        for i, label in enumerate(("Product", "Pricing", "Docs"))
    ]
    for index in links:
        b.hover(index, "link", background_color=(TRANSPARENT, "rgb(241, 245, 249)"))
    components = analyze([b.page()])
    assert "button-ghost" not in components
    nav_link = components["nav-link"]
    assert nav_link.count == 3 and nav_link.variant == "nav"
    assert nav_link.states == {"hover": {"backgroundColor": "#f1f5f9"}}
    assert "backgroundColor" not in nav_link.properties
    assert nav_link.properties["textDecoration"] == "none"
    assert components["top-nav"].count == 1


def test_overlay_buttons_are_ignored() -> None:
    b = PageBuilder()
    banner = b.add(
        "div",
        b.body,
        (24, 800, 480, 60),
        own_text=False,
        in_overlay=True,
        landmark="dialog",
        background_color="rgb(17, 24, 39)",
    )
    b.add("button", banner, (400, 812, 84, 36), "Accept", **button_styles("rgb(245, 158, 11)"))
    assert not any(name.startswith("button-") for name in analyze([b.page()]))


def test_icon_only_buttons_are_ignored() -> None:
    b = PageBuilder()
    b.add("button", b.main, (100, 200, 40, 40), "×", **button_styles(BRAND))
    b.add("button", b.main, (200, 200, 40, 40), "", **button_styles(BRAND))
    assert "button-primary" not in analyze([b.page()])


def test_neutral_filled_button_is_primary_without_chromatic_buttons() -> None:
    b = PageBuilder()
    b.add("a", b.main, (100, 200, 180, 44), "Get started", **button_styles(INK))
    b.add(
        "a",
        b.main,
        (300, 200, 140, 44),
        "Contact sales",
        **button_styles("rgb(241, 245, 249)", INK),
    )
    components = analyze([b.page()])
    assert components["button-primary"].properties["backgroundColor"] == "#0f172a"
    assert components["button-secondary"].properties["backgroundColor"] == "#f1f5f9"


def test_chromatic_button_takes_secondary_before_neutral_ones() -> None:
    b = PageBuilder()
    b.add("a", b.main, (100, 200, 200, 48), "Start now", **button_styles(BRAND))
    b.add("a", b.main, (320, 200, 160, 44), "Contact", **button_styles(INK))
    b.add("a", b.main, (500, 200, 120, 40), "Try it", **button_styles("rgb(16, 185, 129)"))
    components = analyze([b.page()])
    assert components["button-primary"].properties["backgroundColor"] == "#635bff"
    assert components["button-secondary"].properties["backgroundColor"] == "#10b981"
    assert components["button-tertiary"].properties["backgroundColor"] == "#0f172a"


def test_palette_primary_token_decides_the_primary_variant() -> None:
    b = PageBuilder()
    b.add("a", b.main, (100, 200, 300, 56), "Big green", **button_styles("rgb(16, 185, 129)"))
    b.add("a", b.main, (500, 200, 120, 40), "Brand", **button_styles(BRAND))
    palette = ColorPalette(
        tokens=[ColorToken(name="primary", value="#635bff", role=ColorRole.BRAND)]
    )
    components = analyze([b.page()], palette=palette)
    assert components["button-primary"].properties["backgroundColor"] == "#635bff"
    assert components["button-primary"].token_refs["backgroundColor"] == "{colors.primary}"
    assert components["button-secondary"].properties["backgroundColor"] == "#10b981"


def test_size_variants_merge_into_the_most_prominent_button() -> None:
    b = PageBuilder()
    b.add("a", b.main, (100, 200, 200, 48), "Large", **button_styles(BRAND))
    small = button_styles(BRAND, border_radius=("4px", "4px", "4px", "4px"))
    b.add("a", b.main, (320, 200, 100, 32), "Small", **small)
    components = analyze([b.page()])
    primary = components["button-primary"]
    assert primary.count == 2
    assert primary.properties["height"] == "48px"
    assert "button-secondary" not in components


def test_outline_button_and_hover_border() -> None:
    b = PageBuilder()
    outline = b.add(
        "button",
        b.main,
        (100, 200, 150, 44),
        "Book a demo",
        **button_styles(TRANSPARENT, INK, **border_styles("rgb(226, 232, 240)")),
    )
    b.hover(outline, border_color=("rgb(226, 232, 240)", "rgb(148, 163, 184)"))
    component = analyze([b.page()])["button-outline"]
    assert component.properties["borderColor"] == "#e2e8f0"
    assert component.properties["borderWidth"] == "1px"
    assert component.states == {"hover": {"borderColor": "#94a3b8"}}
    assert component.contrast_ratio == pytest.approx(17.85, abs=0.01)


def test_button_with_background_equal_to_its_section_is_not_filled() -> None:
    b = PageBuilder()
    b.add("a", b.main, (100, 200, 150, 44), "Learn more", **button_styles(WHITE, INK))
    assert not any(name.startswith("button-") for name in analyze([b.page()]))


def test_styled_child_of_a_plain_anchor_is_the_button() -> None:
    b = PageBuilder()
    link = b.add(
        "a", b.main, (100, 200, 160, 44), "Get started", own_text=False, display="inline-block"
    )
    b.add("span", link, (100, 200, 160, 44), "Get started", **button_styles(BRAND))
    components = analyze([b.page()])
    assert components["button-primary"].properties["backgroundColor"] == "#635bff"
    assert "text-link" not in components and "badge" not in components


def test_label_in_a_child_span_gives_text_style() -> None:
    b = PageBuilder()
    button = b.add(
        "a", b.main, (100, 200, 160, 44), "Go on", own_text=False, **button_styles(BRAND)
    )
    b.add("span", button, (118, 212, 60, 20), "Go on", color=WHITE, font_weight="600")
    primary = analyze([b.page()])["button-primary"]
    assert primary.properties["textColor"] == "#ffffff"
    assert primary.properties["fontWeight"] == "600"


def test_sub_page_members_are_counted() -> None:
    home = PageBuilder()
    home.add("a", home.main, (100, 200, 160, 44), "Start now", **button_styles(BRAND))
    pricing = PageBuilder()
    pricing.add("a", pricing.main, (100, 300, 160, 44), "Buy now", **button_styles(BRAND))
    primary = analyze([home.page(), pricing.page("pricing")])["button-primary"]
    assert primary.count == 2
    assert primary.samples == ["Start now", "Buy now"]


def test_hover_state_uses_the_most_common_change_set() -> None:
    b = PageBuilder()
    buttons = [
        b.add("a", b.main, (100 + 200 * i, 200, 160, 44), f"Go {i}", **button_styles(BRAND))
        for i in range(3)
    ]
    b.hover(buttons[0], background_color=(BRAND, "rgb(10, 37, 64)"))
    b.hover(buttons[1], background_color=(BRAND, "rgb(67, 56, 202)"))
    b.hover(buttons[2], background_color=(BRAND, "rgb(67, 56, 202)"))
    primary = analyze([b.page()])["button-primary"]
    assert primary.states == {"hover": {"backgroundColor": "#4338ca"}}


def _card(b: PageBuilder, parent: int, x: float, **styles: Any) -> int:
    card = b.add(
        "div",
        parent,
        (x, 600, 320, 240),
        own_text=False,
        padding=("24px", "24px", "24px", "24px"),
        border_radius=("12px", "12px", "12px", "12px"),
        **styles,
    )
    b.add("h3", card, (x + 24, 624, 272, 28), f"Card {x:g}", font_size="20px")
    b.add("p", card, (x + 24, 664, 272, 48), "Body copy")
    return card


def test_card_needs_a_sibling_or_a_shadow() -> None:
    surface = {"background_color": "rgb(241, 245, 249)"}
    single = PageBuilder()
    grid = single.add("div", single.main, (100, 600, 1200, 240), own_text=False)
    _card(single, grid, 100, **surface)
    assert "card" not in analyze([single.page()])

    pair = PageBuilder()
    grid = pair.add("div", pair.main, (100, 600, 1200, 240), own_text=False)
    _card(pair, grid, 100, **surface)
    _card(pair, grid, 460, **surface)
    card = analyze([pair.page()])["card"]
    assert card.count == 2 and card.kind is ComponentKind.CARD
    assert card.properties["padding"] == "24px"
    assert card.contrast_ratio is None
    assert card.samples == ["Card 100", "Card 460"]

    shadowed = PageBuilder()
    grid = shadowed.add("div", shadowed.main, (100, 600, 1200, 240), own_text=False)
    _card(shadowed, grid, 100, box_shadow="rgba(0, 0, 0, 0.1) 0px 4px 12px 0px")
    assert analyze([shadowed.page()])["card"].properties["shadow"] == (
        "0px 4px 12px 0px rgba(0, 0, 0, 0.1)"
    )


def test_cards_with_a_stronger_shadow_become_card_elevated() -> None:
    b = PageBuilder()
    grid = b.add("div", b.main, (100, 600, 1200, 240), own_text=False)
    flat = {**border_styles("rgb(226, 232, 240)"), "background_color": WHITE}
    for x in (100, 460, 820):
        _card(b, grid, x, **flat)
    lifted = b.add("div", b.main, (100, 900, 1200, 240), own_text=False)
    raised = {"background_color": WHITE, "box_shadow": "rgba(0, 0, 0, 0.2) 0px 12px 32px 0px"}
    for x in (100, 460):
        _card(b, lifted, x, **raised)
    components = analyze([b.page()])
    assert components["card"].count == 3
    assert "shadow" not in components["card"].properties
    assert components["card-elevated"].variant == "elevated"
    assert components["card-elevated"].count == 2


def test_badge_pill_detection() -> None:
    b = PageBuilder()
    b.add(
        "span",
        b.main,
        (600, 150, 120, 24),
        "New",
        background_color="rgb(238, 242, 255)",
        color=BRAND,
        font_size="12px",
        font_weight="600",
        line_height="16px",
        padding=("4px", "10px", "4px", "10px"),
        border_radius=("999px", "999px", "999px", "999px"),
    )
    badge = analyze([b.page()])["badge"]
    assert badge.variant == "pill" and badge.kind is ComponentKind.BADGE
    assert badge.properties["rounded"] == "9999px"
    assert badge.properties["height"] == "24px"


def test_large_text_is_not_a_badge() -> None:
    b = PageBuilder()
    b.add(
        "span",
        b.main,
        (600, 150, 120, 30),
        "Large",
        background_color="rgb(238, 242, 255)",
        font_size="18px",
        padding=("4px", "10px", "4px", "10px"),
        border_radius=("6px", "6px", "6px", "6px"),
    )
    assert "badge" not in analyze([b.page()])


def test_text_link_must_be_chromatic_or_underlined() -> None:
    b = PageBuilder()
    paragraph = b.add("p", b.main, (100, 400, 600, 48), "Read the docs or", own_text=True)
    b.add("a", paragraph, (300, 400, 80, 24), "the docs", display="inline", color=INK)
    assert "text-link" not in analyze([b.page()])
    b.add(
        "a",
        paragraph,
        (400, 400, 80, 24),
        "our blog",
        display="inline",
        color=INK,
        text_decoration_line="underline",
    )
    link = analyze([b.page()])["text-link"]
    assert link.properties["textDecoration"] == "underline"
    assert link.count == 1


def test_text_input_properties() -> None:
    b = PageBuilder()
    b.add(
        "input",
        b.main,
        (100, 300, 320, 40),
        input_type="search",
        placeholder_color="rgb(148, 163, 184)",
        background_color=WHITE,
        padding=("8px", "12px", "8px", "12px"),
        border_radius=("8px", "8px", "8px", "8px"),
        **border_styles("rgb(203, 213, 225)"),
    )
    b.add("input", b.main, (100, 360, 20, 20), input_type="checkbox")
    component = analyze([b.page()])["text-input"]
    assert component.count == 1
    assert component.samples == ["input[type=search]"]
    assert component.properties["placeholderColor"] == "#94a3b8"
    assert component.properties["borderColor"] == "#cbd5e1"


def test_token_refs_need_exact_weight_and_matching_steps() -> None:
    b = PageBuilder()
    b.add("a", b.main, (100, 200, 160, 40), "Start now", **button_styles(BRAND))
    typography = Typography(
        levels=[TypeLevel(name="ui", size_px=15, font_family="Inter", weight=600)]
    )
    radii = [ScaleStep(name="sm", value_px=6)]
    primary = analyze([b.page()], typography=typography, radii=radii)["button-primary"]
    assert primary.token_refs == {"rounded": "{rounded.sm}"}
    exact = Typography(levels=[TypeLevel(name="ui", size_px=15, font_family="Inter", weight=500)])
    primary = analyze([b.page()], typography=exact, radii=radii)["button-primary"]
    assert primary.token_refs["typography"] == "{typography.ui}"


def test_pill_radius_refers_to_the_full_step() -> None:
    b = PageBuilder()
    styles = button_styles(BRAND, border_radius=("22px", "22px", "22px", "22px"))
    b.add("a", b.main, (100, 200, 160, 44), "Start now", **styles)
    radii = [ScaleStep(name="md", value_px=8), ScaleStep(name="full", value_px=9999)]
    primary = analyze([b.page()], radii=radii)["button-primary"]
    assert primary.properties["rounded"] == "22px"
    assert primary.token_refs["rounded"] == "{rounded.full}"


def test_top_nav_footer_and_sidebar() -> None:
    b = PageBuilder()
    bottom_border: dict[str, Any] = {
        "border_width": ("0px", "0px", "1px", "0px"),
        "border_style": ("none", "none", "solid", "none"),
        "border_color": (INK, INK, "rgb(226, 232, 240)", INK),
    }
    header = b.add(
        "header",
        b.body,
        (0, 0, 1440, 72),
        own_text=False,
        landmark="header",
        background_color=WHITE,
        position="fixed",
        **bottom_border,
    )
    inner = b.add(
        "div", header, (0, 0, 1440, 72), own_text=False, padding=("0px", "32px", "0px", "32px")
    )
    b.add("a", inner, (32, 20, 80, 32), "Home")
    aside = b.add("aside", b.main, (0, 72, 280, 1200), own_text=False, landmark="aside")
    b.add("a", aside, (16, 100, 200, 32), "Getting started")
    footer = b.add(
        "footer",
        b.body,
        (0, 2600, 1440, 400),
        own_text=False,
        landmark="footer",
        background_color="rgb(11, 16, 32)",
        color="rgb(148, 163, 184)",
        font_size="14px",
        padding=("48px", "0px", "48px", "0px"),
    )
    b.add("footer", footer, (0, 2900, 1440, 40), "© Example", own_text=True)
    b.add("a", footer, (100, 2650, 80, 20), "Privacy policy")
    b.add("a", footer, (100, 2680, 80, 20), "Terms of service")
    components = analyze([b.page()])
    top = components["top-nav"]
    assert top.properties["position"] == "fixed"
    assert top.properties["padding"] == "0px 32px"
    assert top.properties["borderWidth"] == "0px 0px 1px"
    assert top.properties["height"] == "72px"
    assert top.contrast_ratio is None
    assert components["sidebar"].properties["width"] == "280px"
    bottom = components["footer"]
    assert bottom.properties["padding"] == "48px 0px"
    assert bottom.properties["textColor"] == "#94a3b8"
    assert bottom.contrast_ratio == pytest.approx(7.38, abs=0.01)
    assert "fontSize" not in bottom.properties
