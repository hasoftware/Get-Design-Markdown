"""Color analyzer regressions, round 2: float-exact hint gates, text tiers below muted, main-page
evidence (representatives, variable attribution, contrast ranking) and neutral action colors."""

from __future__ import annotations

import pytest

from get_design_markdown.analyzer.colors import analyze_colors
from get_design_markdown.analyzer.colors_roles import reaches_share
from get_design_markdown.models.analysis import ColorPalette, ColorToken
from get_design_markdown.models.raw import CssVariable, ElementSample, PageCapture
from tests.test_analyzer_colors import _button, _el, _marketing_page, _page, _text, _tokens
from tests.test_analyzer_colors_regressions import _neutral_ctas, _toggle


def _by_name(palette: ColorPalette) -> dict[str, ColorToken]:
    return {token.name: token for token in palette.tokens}


def _var(name: str, value: str) -> CssVariable:
    return CssVariable(name=name, declared=value, source="root")


# ─── Hint gates are exact at the boundary ────────────────────────────────────


def test_share_boundary_is_inclusive_whatever_the_float_sum() -> None:
    # Python 3.11's plain sum() over six equal CTAs, and the half that three of them score:
    # half of the best came out one ulp above the score, which flipped the primary on 3.11 only.
    assert 0.5 * 503.42824712167277 > 251.71412356083636
    assert reaches_share(251.71412356083636, 503.42824712167277, 0.5)
    assert reaches_share(251.71412356083636, 503.4282471216727, 0.5)
    assert not reaches_share(250.0, 503.42824712167277, 0.5)
    assert reaches_share(0.0, 0.0, 0.5)


def _indigo_ctas(brand_ctas: int) -> PageCapture:
    """`brand_ctas` CTAs in #635bff (--brand-500) and three in #533afd, the color a
    --button-primary-bg variable names."""
    extra = [
        _button(20 + n, "#635bff", y=900 + n * 60, box=(600, 900 + n * 60, 160, 44))
        for n in range(brand_ctas)
    ]
    extra += [
        _button(40 + n, "#533afd", y=900 + n * 60, box=(900, 900 + n * 60, 160, 44))
        for n in range(3)
    ]
    variables = [
        _var("--brand-500", "#635bff"),
        _var("--button-primary-bg", "#533afd"),
    ]
    return _neutral_ctas(_marketing_page(extra=extra, variables=variables))


@pytest.mark.parametrize(
    ("brand_ctas", "primary"),
    [
        (5, "#533afd"),
        # Exactly half of the best button score still lets the variant variable win.
        (6, "#533afd"),
        (7, "#635bff"),
    ],
)
def test_button_variant_hint_needs_half_of_the_best_score(brand_ctas: int, primary: str) -> None:
    assert _tokens(analyze_colors([_indigo_ctas(brand_ctas)]))["primary"] == primary


# ─── Text tiers below muted ──────────────────────────────────────────────────


def _four_tier_page(
    *, quiet_lines: int = 2, subdued_lines: int = 4, named: bool = True
) -> PageCapture:
    """White page with a solid / soft / subdued / quiet text ramp (#061b31 / #50617a / #64748d /
    #7d8ba4): headings pair ink with the subdued tone, the quiet gray is rare and doubles as the
    input placeholder."""
    elements: list[ElementSample] = [
        _text(2, "#061b31", tag="h1", size="56px", text="Financial infrastructure", y=150),
        _text(3, "#061b31", tag="h2", size="32px", text="Grow fast", y=260),
    ]
    for n in range(4):
        elements.append(_text(4 + n, "#061b31", text="Ink body copy for the page " * 2, y=320))
    for n in range(10):
        y = 400 + n * 40
        elements.append(_text(10 + n, "#50617a", text="Soft description copy " * 2, y=y))
    for n in range(subdued_lines):
        y = 900 + n * 60
        text = "accept payments and grow your revenue"
        elements.append(_text(30 + n, "#64748d", tag="p", size="20px", text=text, y=y))
    for n in range(quiet_lines):
        elements.append(
            _text(40 + n, "#7d8ba4", text="Footnote line", y=1300 + n * 30, size="13px")
        )
    elements.append(
        _el(
            90,
            "input",
            parent=1,
            box=(100, 1800, 320, 40),
            placeholder_color="#7d8ba4",
            input_type="email",
            border_width=("1px",) * 4,
            border_style=("solid",) * 4,
            border_color=("#e5edf5",) * 4,
        )
    )
    variables = (
        [
            _var("--text-solid", "#061b31"),
            _var("--text-soft", "#50617a"),
            _var("--text-subdued", "#64748d"),
            _var("--text-quiet", "#7d8ba4"),
            _var("--input-text-placeholder", "#7d8ba4"),
        ]
        if named
        else []
    )
    return _page(elements, height=2000, variables=variables)


def test_used_subdued_tier_is_faint_over_a_rare_placeholder_gray() -> None:
    by_name = _by_name(analyze_colors([_four_tier_page()]))
    assert by_name["ink"].value == "#061b31"
    assert by_name["muted"].value == "#50617a"
    # #64748d sits only 0.067 OKLCH below muted, but it is a named tier carrying far more text
    # than the placeholder/quiet gray.
    assert by_name["faint"].value == "#64748d"
    assert by_name["faint"].css_variable == "--text-subdued"
    assert all(token.value != "#7d8ba4" for token in by_name.values())


def test_faint_named_tier_used_half_as_much_wins() -> None:
    by_name = _by_name(analyze_colors([_four_tier_page(quiet_lines=24)]))
    assert by_name["muted"].value == "#50617a"
    assert by_name["faint"].value == "#7d8ba4"
    assert by_name["faint"].css_variable == "--text-quiet"


def test_unnamed_short_step_below_muted_needs_a_visible_share() -> None:
    # Without names, a tier only 0.067 below muted is a tier when it carries >= 10% of the
    # text (four 20px lines here)...
    by_name = _by_name(analyze_colors([_four_tier_page(named=False)]))
    assert by_name["muted"].value == "#50617a"
    assert by_name["faint"].value == "#64748d"
    # ...but not as a single line: the clearly dimmer gray is the faint tier then.
    single = _by_name(analyze_colors([_four_tier_page(named=False, subdued_lines=1)]))
    assert single["faint"].value == "#7d8ba4"
    # With names, even that single line is the subdued tier and outweighs the rare quiet gray.
    named = _by_name(analyze_colors([_four_tier_page(subdued_lines=1)]))
    assert named["faint"].value == "#64748d"


# ─── Main-page evidence ──────────────────────────────────────────────────────


def _quiet_sections_home() -> PageCapture:
    elements: list[ElementSample] = [
        _text(2, "#111827", tag="h1", size="56px", text="Ship faster with less", y=150),
        _text(3, "#4b5563", text="Body copy that explains the product.", y=240),
        _text(4, "#4b5563", text="More body copy that explains it.", y=280),
    ]
    for n in range(2):
        elements.append(
            _el(
                10 + n,
                "section",
                parent=1,
                box=(0, 1000 + n * 1500, 1440, 300),
                background_color="#f8fafd",
                child_count=1,
            )
        )
        elements.append(_text(20 + n, "#111827", parent=10 + n, text="Quiet band", y=1100))
    variables = [_var("--surface-bg-subdued", "#f8fafd"), _var("--util-bg-quiet", "#f8fafd")]
    return _page(elements, height=4000, variables=variables)


def test_main_page_member_represents_a_cluster_over_a_long_sub_page() -> None:
    # The pricing body (#f6f9fc, ΔE 0.54) paints more weighted area than the home page's quiet
    # bands (#f8fafd): the token takes the home value, which is also what its variable holds.
    pricing = _page(
        [_text(2, "#111827", text="Plans and prices", y=100)],
        name="pricing",
        height=4000,
        body_bg="#f6f9fc",
    )
    palette = analyze_colors([_quiet_sections_home(), pricing])
    surface = _by_name(palette)["surface"]
    assert surface.value == "#f8fafd"
    assert surface.css_variable == "--surface-bg-subdued"
    cluster = next(item for item in palette.clusters if item.id == surface.cluster_id)
    assert cluster.members[0] == "#f6f9fc"
    assert set(cluster.members) == {"#f6f9fc", "#f8fafd"}
    # A cluster only sub-pages use keeps its heaviest member.
    alone = analyze_colors([pricing])
    assert _tokens(alone)["canvas"] == "#f6f9fc"


def _shade_ctas(variables: list[CssVariable]) -> PageCapture:
    page = _marketing_page(variables=variables)
    page.elements[3] = _button(3, "#635bff", parent=2, y=12)
    page.elements[4] = _button(4, "#635bff", y=500)
    return page


def test_token_cites_only_a_variable_holding_its_value() -> None:
    # --brand-500 resolves to #665efd, ΔE2000 0.88 from the measured #635bff fill.
    near = _by_name(analyze_colors([_shade_ctas([_var("--brand-500", "#665efd")])]))["primary"]
    assert near.value == "#635bff"
    assert near.css_variable is None
    assert "--brand-500" not in near.evidence
    exact = _by_name(analyze_colors([_shade_ctas([_var("--brand-500", "#635bff")])]))["primary"]
    assert exact.css_variable == "--brand-500"
    # The nearby variable is still listed on the cluster.
    palette = analyze_colors([_shade_ctas([_var("--brand-500", "#665efd")])])
    cluster = next(item for item in palette.clusters if item.hex == "#635bff")
    assert "--brand-500" in cluster.css_variables


def test_state_variables_are_not_token_evidence() -> None:
    primary = _by_name(analyze_colors([_shade_ctas([_var("--border-focus", "#635bff")])]))[
        "primary"
    ]
    assert primary.css_variable is None
    assert "--border-focus" not in primary.evidence


def test_icon_variable_ranks_after_the_text_variable() -> None:
    variables = [
        _var("--button-primary-icon", "#ffffff"),
        _var("--button-primary-text", "#ffffff"),
    ]
    on_primary = _by_name(analyze_colors([_shade_ctas(variables)]))["on-primary"]
    assert on_primary.css_variable == "--button-primary-text"


def test_sibling_token_variable_is_not_cited_for_the_same_value() -> None:
    # White text on indigo and on teal CTAs: --button-primary-text holds the value of both
    # on-primary and on-secondary, but it names on-primary only.
    teal = [
        _button(20 + n, "#0d9488", y=900 + n * 60, box=(900, 900 + n * 60, 120, 36))
        for n in range(2)
    ]
    variables = [_var("--button-primary-text", "#ffffff")]
    by_name = _by_name(analyze_colors([_marketing_page(extra=teal, variables=variables)]))
    assert by_name["secondary"].value == "#0d9488"
    assert by_name["on-primary"].css_variable == "--button-primary-text"
    assert by_name["on-secondary"].value == "#ffffff"
    assert by_name["on-secondary"].css_variable is None
    assert "--button-primary-text" not in by_name["on-secondary"].evidence


def test_contrast_pairs_rank_by_page_weight() -> None:
    home = _page(
        [_text(2 + n, "#50617a", text="Home description", y=100 + n * 40) for n in range(3)],
        height=2000,
    )
    pricing = _page(
        [_text(2 + n, "#425466", text="Pricing row", y=100 + n * 40) for n in range(5)],
        name="pricing",
        height=2000,
    )
    pairs = analyze_colors([home, pricing]).contrast_pairs
    order = [(pair.foreground, pair.occurrences) for pair in pairs]
    # Five sub-page rows weigh 2.5 against three main-page lines; occurrences stay raw counts.
    assert order[:2] == [("#50617a", 3), ("#425466", 5)]


# ─── Neutral action colors ───────────────────────────────────────────────────


def _invert_cta_page(variables: list[CssVariable]) -> PageCapture:
    """Near-black page whose CTAs are light gray with dark text (an "invert" button) and whose
    brand indigo only fills two textless toggles."""
    elements: list[ElementSample] = [
        _text(2, "#f7f8f8", tag="h1", size="56px", text="Plan and build products", y=150),
        _text(3, "#f7f8f8", tag="h2", size="32px", text="Made for modern teams", y=230),
        _button(4, "#e5e5e6", color="#08090a", y=12, box=(1240, 12, 100, 32)),
        _button(5, "#e5e5e6", color="#08090a", y=320),
        _button(6, "#e5e5e6", color="#08090a", y=1600),
        _toggle(7, 900),
        _toggle(8, 960),
    ]
    for n in range(6):
        elements.append(_text(10 + n, "#8a8f98", text="Description copy " * 3, y=400 + n * 40))
    # Code sample text a shade away from the button gray.
    elements.append(_text(20, "#e2e4e7", tag="span", text="const issue = await create()", y=700))
    return _page(elements, body_bg="#08090a", text_color="#f7f8f8", variables=variables)


def test_named_neutral_button_fill_is_the_primary_action() -> None:
    variables = [
        _var("--color-button-invert-bg", "#e5e5e6"),
        _var("--color-brand-bg", "#5e6ad2"),
    ]
    by_name = _by_name(analyze_colors([_invert_cta_page(variables)]))
    primary = by_name["primary"]
    assert primary.value == "#e5e5e6"
    assert primary.css_variable == "--color-button-invert-bg"
    assert "3 buttons" in primary.evidence
    assert by_name["on-primary"].value == "#08090a"
    # The brand hue that only colors toggles is the accent, not the primary action.
    assert by_name["secondary"].value == "#5e6ad2"
    assert by_name["secondary"].css_variable == "--color-brand-bg"
    assert "button" not in by_name["secondary"].evidence
    # A neutral action fill names backgrounds, never nearby gray text.
    pairs = {
        pair.foreground: pair
        for pair in analyze_colors([_invert_cta_page(variables)]).contrast_pairs
    }
    assert pairs["#e2e4e7"].foreground_token is None
    assert pairs["#08090a"].foreground_token == "on-primary"
    assert pairs["#08090a"].background_token == "primary"


def test_unnamed_neutral_buttons_are_not_an_action_token() -> None:
    by_name = _by_name(analyze_colors([_invert_cta_page([_var("--color-brand-bg", "#5e6ad2")])]))
    assert by_name["primary"].value == "#5e6ad2"
    assert all(token.value != "#e5e5e6" for token in by_name.values())


def test_chromatic_ctas_keep_primary_over_a_named_neutral_fill() -> None:
    grays = [
        _button(
            20 + n, "#f3f4f6", color="#111827", y=900 + n * 60, box=(900, 900 + n * 60, 120, 36)
        )
        for n in range(4)
    ]
    page = _marketing_page(extra=grays, variables=[_var("--button-ui-bg", "#f3f4f6")])
    tokens = _tokens(analyze_colors([page]))
    assert tokens["primary"] == "#4f46e5"
    assert "#f3f4f6" not in {tokens.get("secondary"), tokens.get("tertiary")}


def test_neutral_fill_named_primary_variant_is_the_primary() -> None:
    page = _neutral_ctas(_marketing_page(variables=[_var("--button-primary-bg", "#111111")]))
    by_name = _by_name(analyze_colors([page]))
    assert by_name["primary"].value == "#111111"
    assert by_name["primary"].css_variable == "--button-primary-bg"
    assert by_name["on-primary"].value == "#ffffff"
    assert "primary" not in _tokens(analyze_colors([_neutral_ctas(_marketing_page())]))
