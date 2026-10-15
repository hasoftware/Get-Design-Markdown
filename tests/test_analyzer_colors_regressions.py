"""Color analyzer regressions found on real sites: action colors of one hue, layered hero text,
four-tier dark text ramps, textless controls and CSS variable attribution."""

from __future__ import annotations

from typing import Any

from get_design_markdown.analyzer.colors import analyze_colors
from get_design_markdown.analyzer.colors_samples import is_button_like, layered_text_elements
from get_design_markdown.analyzer.common import element_index
from get_design_markdown.analyzer.css_values import delta_e_2000, parse_color
from get_design_markdown.models.analysis import ColorPalette, ColorToken
from get_design_markdown.models.raw import CssVariable, ElementSample, PageCapture
from tests.test_analyzer_colors import _button, _el, _marketing_page, _page, _text, _tokens


def _by_name(palette: ColorPalette) -> dict[str, ColorToken]:
    return {token.name: token for token in palette.tokens}


def _var(name: str, value: str) -> CssVariable:
    return CssVariable(name=name, declared=value, source="root")


def _neutral_ctas(page: PageCapture) -> PageCapture:
    """The marketing page with its two indigo CTAs turned near-black."""
    page.elements[3] = _button(3, "#111111", parent=2, y=12)
    page.elements[4] = _button(4, "#111111", y=500)
    return page


# ─── Action colors of the same hue ───────────────────────────────────────────


def _two_indigo_page(variables: list[CssVariable]) -> PageCapture:
    """Six CTAs in #635bff and three in #533afd (ΔE2000 8.2, same hue)."""
    extra = [
        _button(20 + n, "#635bff", y=900 + n * 60, box=(600, 900 + n * 60, 160, 44))
        for n in range(6)
    ]
    extra += [
        _button(30 + n, "#533afd", y=900 + n * 60, box=(900, 900 + n * 60, 160, 44))
        for n in range(3)
    ]
    return _neutral_ctas(_marketing_page(extra=extra, variables=variables))


def test_button_variant_variable_outranks_a_brand_scale_variable() -> None:
    page = _two_indigo_page(
        [
            _var("--brand-500", "#635bff"),
            _var("--hds-color-button-primary-bg", "#533afd"),
            _var("--hds-color-button-primary-text", "#ffffff"),
        ]
    )
    by_name = _by_name(analyze_colors([page]))
    # The color the variable calls "button primary bg" is the primary action color...
    assert by_name["primary"].value == "#533afd"
    assert by_name["primary"].css_variable == "--hds-color-button-primary-bg"
    # ...and the other indigo, clearly distinct and backing more buttons, still gets a token.
    assert by_name["secondary"].value == "#635bff"
    assert by_name["secondary"].css_variable == "--brand-500"
    assert "6 buttons" in by_name["secondary"].evidence
    assert by_name["on-primary"].value == "#ffffff"
    assert by_name["on-primary"].css_variable == "--hds-color-button-primary-text"
    assert by_name["on-secondary"].value == "#ffffff"


def test_second_button_color_of_the_primary_hue_is_a_token_without_hints() -> None:
    by_name = _by_name(analyze_colors([_two_indigo_page([])]))
    assert by_name["primary"].value == "#635bff"
    assert by_name["secondary"].value == "#533afd"
    assert "3 buttons" in by_name["secondary"].evidence


def test_close_or_rare_shades_of_the_primary_are_not_tokens() -> None:
    primary, close = parse_color("#4f46e5"), parse_color("#5a52e8")
    assert primary is not None and close is not None
    assert 3.0 <= delta_e_2000(primary, close) < 5.0
    many = [_button(20 + n, "#4f46e5", y=900 + n * 60) for n in range(8)]
    # A barely different shade on three buttons: same token family, no secondary.
    shade = [
        _button(30 + n, "#5a52e8", y=900 + n * 60, box=(900, 900 + n * 60, 160, 44))
        for n in range(3)
    ]
    assert "secondary" not in _tokens(analyze_colors([_marketing_page(extra=many + shade)]))
    # A distinct shade on two small buttons next to 10 primaries scores too little.
    rare = [
        _button(30 + n, "#3f37c9", y=900 + n * 60, box=(900, 900 + n * 60, 60, 24))
        for n in range(2)
    ]
    assert "secondary" not in _tokens(analyze_colors([_marketing_page(extra=many + rare)]))


# ─── Layered duplicate text ──────────────────────────────────────────────────


def _layered_hero_page() -> PageCapture:
    """Two stacked copies of the hero title (green under half-transparent blue), as a blend."""
    title = "Financial infrastructure to grow your revenue."
    box = (208, 258, 1018, 220)
    elements: list[ElementSample] = []
    for offset, color in ((0, "rgb(129, 184, 26)"), (10, "rgba(0, 14, 255, 0.5)")):
        layer = 20 + offset
        elements.append(
            _el(layer, "h1", parent=1, box=box, text=title, own_text=False, child_count=1)
        )
        elements.append(
            _text(layer + 1, color, parent=layer, tag="span", size="56px", text=title, box=box)
        )
    # The same words elsewhere on the page are ordinary text.
    elements.append(_text(40, "#81b81a", text=title, y=1200, size="32px", tag="h2"))
    return _marketing_page(extra=elements)


def test_layered_copies_of_the_same_text_are_detected() -> None:
    page = _layered_hero_page()
    index = element_index(page)
    assert layered_text_elements(page.elements, index) == {21, 31}


def test_layered_text_colors_are_decorative_and_never_contrast_pairs() -> None:
    palette = analyze_colors([_layered_hero_page()])
    texts = {cluster.hex: cluster for cluster in palette.clusters if cluster.channel == "text"}
    assert texts["#000eff80"].decorative
    # #81b81a is also used by a real heading in its own box: not decorative.
    assert not texts["#81b81a"].decorative
    foregrounds = {pair.foreground for pair in palette.contrast_pairs}
    assert "#000eff80" not in foregrounds
    green = [pair for pair in palette.contrast_pairs if pair.foreground == "#81b81a"]
    assert [pair.occurrences for pair in green] == [1]
    assert all(token.value not in ("#000eff80",) for token in palette.tokens)


def test_text_inside_its_own_ancestor_box_is_not_a_layer() -> None:
    page = _page(
        [
            _el(2, "a", parent=1, box=(100, 100, 200, 40), text="Go", own_text=True, href="/x"),
            _text(3, "#111111", parent=2, tag="span", text="Go", box=(100, 100, 200, 40)),
        ]
    )
    assert layered_text_elements(page.elements, element_index(page)) == set()


# ─── Dark text ramps ─────────────────────────────────────────────────────────


def _ramp_page(
    *,
    secondary_lines: int = 6,
    ink_lines: int = 0,
    variables: list[CssVariable] | None = None,
) -> PageCapture:
    """Near-black page: ink #f7f8f8, tertiary #8a8f98 (heaviest gray), secondary #d0d6e0
    (lighter, about 0.1 OKLCH below ink) and a small quaternary #62666d."""
    elements: list[ElementSample] = [
        _text(2, "#f7f8f8", tag="h1", size="56px", text="Plan and build products", y=150),
        _text(3, "#f7f8f8", tag="h2", size="32px", text="Made for modern teams", y=230),
    ]
    for n in range(ink_lines):
        elements.append(
            _text(50 + n, "#f7f8f8", text="Body copy in the main ink " * 2, y=900 + n * 30)
        )
    for n in range(6):
        y = 300 + n * 40
        elements.append(_text(4 + n, "#8a8f98", text="Description copy " * 3, y=y))
        elements.append(_text(16 + n, "#62666d", text="Timestamp 2m", y=y + 10))
    for n in range(secondary_lines):
        elements.append(_text(30 + n, "#d0d6e0", text="Mock UI label text " * 2, y=600 + n * 40))
    return _page(
        elements,
        body_bg="#08090a",
        text_color="#f7f8f8",
        variables=variables or [],
    )


def test_text_tiers_follow_lightness_without_hints() -> None:
    by_name = _by_name(analyze_colors([_ramp_page()]))
    assert by_name["ink"].value == "#f7f8f8"
    # The lighter secondary gray is muted, the heavier but dimmer one faint.
    assert by_name["muted"].value == "#d0d6e0"
    assert by_name["faint"].value == "#8a8f98"


def test_rare_gray_close_to_ink_is_not_muted() -> None:
    by_name = _by_name(analyze_colors([_ramp_page(secondary_lines=1)]))
    assert by_name["muted"].value == "#8a8f98"
    assert by_name["faint"].value == "#62666d"


def test_named_secondary_text_close_to_ink_is_muted() -> None:
    # Ink dominates: the secondary gray is under 10% of the text, too light to be muted unnamed.
    unnamed = _by_name(analyze_colors([_ramp_page(secondary_lines=5, ink_lines=20)]))
    assert unnamed["muted"].value == "#8a8f98"
    variables = [
        _var("--color-fg-primary", "#f7f8f8"),
        _var("--color-fg-secondary", "#d0d6e0"),
        _var("--color-fg-tertiary", "#8a8f98"),
        _var("--color-fg-quaternary", "#62666d"),
    ]
    page = _ramp_page(secondary_lines=5, ink_lines=20, variables=variables)
    by_name = _by_name(analyze_colors([page]))
    assert by_name["ink"].css_variable == "--color-fg-primary"
    assert by_name["muted"].value == "#d0d6e0"
    assert by_name["muted"].css_variable == "--color-fg-secondary"
    assert by_name["faint"].value == "#8a8f98"
    assert by_name["faint"].css_variable == "--color-fg-tertiary"
    assert "on-primary" not in by_name


# ─── Buttons vs. other controls ──────────────────────────────────────────────


def _toggle(index: int, y: float, **kwargs: Any) -> ElementSample:
    return _el(
        index,
        "label",
        parent=1,
        box=(900, y, 32, 20),
        background_color="#5e6ad2",
        padding=("2px", "2px", "2px", "2px"),
        cursor="pointer",
        classes=["container", "active"],
        **kwargs,
    )


def test_textless_clickable_boxes_are_not_buttons() -> None:
    assert not is_button_like(_toggle(2, 100))
    assert is_button_like(_toggle(2, 100, text="Yearly"))
    icon_link = _el(
        2,
        "a",
        href="/x",
        background_color="#5e6ad2",
        padding=("8px", "8px", "8px", "8px"),
        cursor="pointer",
    )
    assert is_button_like(icon_link)


def test_brand_color_on_toggles_is_primary_with_honest_evidence() -> None:
    page = _neutral_ctas(
        _marketing_page(
            extra=[_toggle(20, 900), _toggle(21, 960)],
            variables=[_var("--color-brand-bg", "#5e6ad2")],
        )
    )
    primary = _by_name(analyze_colors([page]))["primary"]
    assert primary.value == "#5e6ad2"
    assert primary.css_variable == "--color-brand-bg"
    assert "2 controls" in primary.evidence
    assert "button" not in primary.evidence
    # Without the brand variable two toggles do not make a brand color.
    unnamed = _neutral_ctas(_marketing_page(extra=[_toggle(20, 900), _toggle(21, 960)]))
    assert "primary" not in _tokens(analyze_colors([unnamed]))


# ─── CSS variable attribution ────────────────────────────────────────────────


def _hairline_page(variables: list[CssVariable]) -> PageCapture:
    sides = ("1px",) * 4
    elements = [
        _el(
            2 + n,
            "div",
            parent=1,
            box=(120 + n * 200, 700, 180, 300),
            background_color="#0f1011",
            border_width=sides,
            border_style=("solid",) * 4,
            border_color=("rgba(255, 255, 255, 0.08)",) * 4,
            border_radius=("8px",) * 4,
            child_count=1,
            text="Card",
        )
        for n in range(6)
    ]
    elements.append(_text(10, "#f7f8f8", tag="h1", size="48px", text="Title", y=100))
    return _page(elements, body_bg="#08090a", text_color="#f7f8f8", variables=variables)


def test_token_variable_must_hold_the_token_value() -> None:
    translucent = _var("--color-border-translucent", "rgba(255, 255, 255, 0.05)")
    exact = _var("--header-border", "rgba(255, 255, 255, 0.08)")
    hairline = _by_name(analyze_colors([_hairline_page([translucent, exact])]))["hairline"]
    assert hairline.value == "#ffffff14"
    assert hairline.css_variable == "--header-border"
    # Only a variable with another alpha: no attribution, and no mention in the evidence.
    alone = _by_name(analyze_colors([_hairline_page([translucent])]))["hairline"]
    assert alone.css_variable is None
    assert "--color-border-translucent" not in alone.evidence
    # The cluster still lists it as a nearby variable, after exact values.
    palette = analyze_colors([_hairline_page([translucent, exact])])
    border = next(cluster for cluster in palette.clusters if cluster.hex == "#ffffff14")
    assert border.css_variables[0] == "--header-border"


# ─── Main page first ─────────────────────────────────────────────────────────


def test_a_large_sub_page_does_not_choose_the_primary() -> None:
    # stripe.com: the pricing page (2 657 elements, 27 pill CTAs in #635bff) outweighed the
    # home page (1 624 elements, 8 CTAs in #533afd) at a flat sub-page weight of 0.5.
    home = _marketing_page()
    rows = [
        _text(100 + n, "#111827", text="Feature row", y=900 + n * 30, parent=1) for n in range(200)
    ]
    ctas = [
        _button(400 + n, "#0ea5e9", y=900 + n * 60, box=(600, 900 + n * 60, 160, 44))
        for n in range(8)
    ]
    pricing = _page([*rows, *ctas], name="pricing", height=8000)
    by_name = _by_name(analyze_colors([home, pricing]))
    assert by_name["primary"].value == "#4f46e5"
    # The sub-page CTA color may still be documented, but only as a further action color.
    assert all(token.value != "#0ea5e9" for token in by_name.values() if token.name != "secondary")
