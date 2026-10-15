"""Narrative rules tuned on the real stripe.com and linear.app captures."""

from __future__ import annotations

import re

from get_design_markdown.models.analysis import (
    ColorCluster,
    ColorPalette,
    ColorRole,
    ColorToken,
    ComponentKind,
    ComponentSpec,
    ContrastPair,
    DesignAnalysis,
    ShadowLevel,
    ThemeAnalysis,
)
from get_design_markdown.synthesizer.facts import DesignFacts
from get_design_markdown.synthesizer.render import render_design_md
from get_design_markdown.synthesizer.traits import compute_traits
from get_design_markdown.synthesizer.validate import validate_design_md
from tests.fixture_data import load_analysis_sample


def _section(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match is not None, title
    return match.group(1)


def _button(name: str, background: str, count: int, ref: str | None = None) -> ComponentSpec:
    return ComponentSpec(
        name=name,
        kind=ComponentKind.BUTTON,
        variant=name.removeprefix("button-"),
        count=count,
        properties={"backgroundColor": background, "textColor": "#ffffff"},
        token_refs={"backgroundColor": ref} if ref else {},
    )


def _stripe_like_buttons(analysis: DesignAnalysis) -> None:
    analysis.colors.tokens[0].value = "#635bff"
    analysis.components = [
        _button("button-primary", "#635bff", 27, "{colors.primary}"),
        _button("button-secondary", "#533afd", 8),
        _button("button-tertiary", "#00d4ff", 1),
    ]


def test_same_hue_buttons_form_one_accent_and_rare_ones_are_minor() -> None:
    analysis = load_analysis_sample()
    _stripe_like_buttons(analysis)
    traits = compute_traits(DesignFacts(analysis))
    assert traits.accent == "single"
    assert [a.hex for a in traits.actions] == ["#635bff"]
    assert traits.actions[0].variants == ("#533afd",)
    assert traits.actions[0].count == 35
    assert [a.hex for a in traits.minor_actions] == ["#00d4ff"]
    text = render_design_md(analysis)
    assert "a single indigo accent (#635bff / #533afd)" in text
    assert "Rarely used extra action colors: cyan #00d4ff on button-tertiary." in text
    assert "Don't add a second accent" not in text


def test_two_frequent_hue_families_make_a_two_signal_system() -> None:
    analysis = load_analysis_sample()
    analysis.components = [
        _button("button-primary", "#4f46e5", 10, "{colors.primary}"),
        _button("button-secondary", "#f97316", 6),
    ]
    traits = compute_traits(DesignFacts(analysis))
    assert traits.accent == "two" and not traits.minor_actions
    assert "two-signal action system (indigo #4f46e5 and orange #f97316)" in render_design_md(
        analysis
    )


def test_rings_and_dark_canvas_do_not_count_as_strong_elevation() -> None:
    analysis = load_analysis_sample()
    for token in analysis.colors.tokens:
        if token.name == "canvas":
            token.value = "#08090a"
    analysis.colors.scheme = "dark"
    analysis.shadows = [
        ShadowLevel(
            name="lg",
            css="inset 0px 1px 0px 0px rgba(255, 255, 255, 0.04), "
            "0px 0px 0px 1px rgba(0, 0, 0, 0.6), 0px 4px 4px 0px rgba(0, 0, 0, 0.1)",
            layers=3,
        ),
        ShadowLevel(name="xl", css="0px 4px 24px 0px rgba(0, 0, 0, 0.2)"),
    ]
    facts = DesignFacts(analysis)
    profile = facts.shadow_profile()
    assert profile.has_rings and profile.has_drop and profile.drop_alpha == 0.2
    assert compute_traits(facts).depth == "subtle"
    text = render_design_md(analysis)
    assert "strongest drop-shadow alpha 0.2" in text
    assert "act as borders rather than elevation" in text


def test_ring_only_shadows() -> None:
    analysis = load_analysis_sample()
    analysis.shadows = [ShadowLevel(name="sm", css="0px 0px 0px 1px rgba(0, 0, 0, 0.08)")]
    assert compute_traits(DesignFacts(analysis)).depth == "rings"
    text = render_design_md(analysis)
    assert "Shadows are only 1px rings or inset highlights" in text


def test_stripe_like_shadows_are_medium_on_a_light_canvas() -> None:
    analysis = load_analysis_sample()
    analysis.shadows = [
        ShadowLevel(
            name="lg",
            css="0px 13px 27px -5px rgba(50, 50, 93, 0.25), 0px 8px 16px -8px rgba(0, 0, 0, 0.3)",
            layers=2,
        )
    ]
    assert compute_traits(DesignFacts(analysis)).depth == "moderate"


def _same_scheme_dark(analysis: DesignAnalysis) -> None:
    tokens = [token.model_copy() for token in analysis.colors.tokens]
    pairs = [pair.model_copy() for pair in analysis.colors.contrast_pairs]
    pairs.append(
        ContrastPair(
            foreground="#999999",
            background="#ffffff",
            ratio=2.85,
            passes_aa=False,
            passes_aa_large=False,
            passes_aaa=False,
            context="only in the dark capture",
        )
    )
    analysis.dark = ThemeAnalysis(
        colors=ColorPalette(scheme="light", tokens=tokens, contrast_pairs=pairs)
    )


def test_dark_capture_that_stays_light_is_not_a_dark_theme() -> None:
    analysis = load_analysis_sample()
    _same_scheme_dark(analysis)
    facts = DesignFacts(analysis)
    assert not facts.has_dark_theme()
    assert not [issue for issue in facts.contrast_issues() if issue.dark]
    text = render_design_md(analysis)
    colors = _section(text, "Colors")
    assert "there is no separate dark theme" in colors
    assert "| Token | Dark |" not in colors
    assert "Dark theme:" not in text
    assert "2.85:1" not in text


def test_real_dark_theme_keeps_table_and_dedupes_identical_pairs() -> None:
    analysis = load_analysis_sample()
    facts = DesignFacts(analysis)
    assert facts.has_dark_theme()
    dark = [issue for issue in facts.contrast_issues() if issue.dark]
    assert [issue.ratio for issue in dark] == [4.23]
    text = render_design_md(analysis)
    assert "The front-matter tokens describe the light theme" in text
    assert "| Token | Dark | Front matter |" in text


def test_surface_ladder_starts_at_the_canvas() -> None:
    analysis = load_analysis_sample()
    for token in analysis.colors.tokens:
        if token.name == "canvas":
            token.value = "#f6f9fc"
        if token.name == "surface":
            token.value = "#ffffff"
    names = [token.name for token in DesignFacts(analysis).surfaces_by_lightness()]
    assert names == ["canvas", "surface", "surface-inverse"]


def test_transparent_backdrop_background_is_named() -> None:
    analysis = load_analysis_sample()
    nav = next(spec for spec in analysis.components if spec.name == "top-nav")
    nav.properties["backgroundColor"] = "transparent"
    nav.properties["position"] = "fixed"
    text = _section(render_design_md(analysis), "Elevation & Depth")
    assert "`top-nav` is fixed; its background is transparent (shows the surface behind it)" in text


def test_navigation_prompt_replaces_the_form_without_an_input() -> None:
    analysis = load_analysis_sample()
    analysis.components = [s for s in analysis.components if s.name != "text-input"]
    guide = _section(render_design_md(analysis), "Agent Prompt Guide")
    assert "Design a sign-up block" not in guide
    assert "“Build the top navigation: a top-nav bar (" in guide
    assert "links styled as nav-link (" in guide
    assert "collapse the navigation into a hamburger below 1024px" in guide


def test_one_off_failing_colors_are_grouped() -> None:
    analysis = load_analysis_sample()
    for ratio, fg in ((2.4, "#81b81a"), (2.41, "#ff6118")):
        analysis.colors.contrast_pairs.append(
            ContrastPair(
                foreground=fg,
                background="#ffe0d1",
                ratio=ratio,
                passes_aa=False,
                passes_aa_large=False,
                passes_aaa=False,
                context="illustration",
            )
        )
    donts = [
        line
        for line in _section(render_design_md(analysis), "Do's and Don'ts").splitlines()
        if line.startswith("- Don't")
    ]
    grouped = [line for line in donts if "one-off text colors" in line]
    assert len(grouped) == 1
    assert "#81b81a on #ffe0d1 (2.40:1, illustration)" in grouped[0]
    assert "#64748b on `{colors.surface-inverse}` — #0b1020 (3.98:1" in grouped[0]
    assert any(line.startswith("- Don't set `{colors.faint}`") for line in donts)


def test_translucent_component_background_validates_against_the_canvas() -> None:
    analysis = load_analysis_sample()
    analysis.colors.tokens.append(
        ColorToken(name="hairline-strong", value="#ffffff14", role=ColorRole.BORDER)
    )
    for token in analysis.colors.tokens:
        if token.name == "canvas":
            token.value = "#08090a"
        if token.name == "on-primary":
            token.value = "#f7f8f8"
    analysis.colors.scheme = "dark"
    analysis.components[1].token_refs["backgroundColor"] = "{colors.hairline-strong}"
    analysis.components[1].properties["backgroundColor"] = "#ffffff14"
    text = render_design_md(analysis)
    assert 'backgroundColor: "{colors.hairline-strong}"' in text
    warnings = [i.message for i in validate_design_md(text) if i.rule == "contrast-ratio"]
    assert not [message for message in warnings if "button-secondary" in message]


def test_decorative_colors_matching_tokens_are_not_listed() -> None:
    analysis = load_analysis_sample()
    analysis.colors.clusters.append(
        ColorCluster(
            id="gradient-9", channel="gradient", hex="#ffffff", weight=9e9, decorative=True
        )
    )
    hexes = [cluster.hex for cluster in DesignFacts(analysis).decorative_clusters()]
    assert hexes == ["#6366f12e", "#ec48991f"]
    text = render_design_md(analysis)
    assert "(#6366f12e and #ec48991f) as text, button or surface colors" in text


def test_skipped_analyzers_are_not_reported_as_absent() -> None:
    analysis = load_analysis_sample()
    analysis.shadows = []
    analysis.breakpoints = []
    analysis.warnings.append(
        "Chưa phân tích (để trống trong analysis.json): Đổ bóng (GĐ4), Breakpoint (GĐ4)."
    )
    facts = DesignFacts(analysis)
    assert facts.skipped_parts() == {"shadows", "breakpoints"}
    assert compute_traits(facts).depth is None
    text = render_design_md(analysis)
    assert "Shadows were not analyzed in this capture" in text
    assert "depth comes from tonal surface steps" not in text
    assert "Don't add drop shadows" not in text
    assert "No width breakpoints were found" not in text
    assert "Not measured in this capture: shadows and breakpoints." in text
    assert not [i for i in validate_design_md(text) if i.severity == "error"]


def test_color_names_follow_oklch_hue_families() -> None:
    from get_design_markdown.synthesizer.catalog import load_phrases
    from get_design_markdown.synthesizer.colornames import color_name

    en = load_phrases("en")
    vi = load_phrases("vi")
    expected = {
        "#4f46e5": "indigo",
        "#635bff": "indigo",
        "#00d4ff": "cyan",
        "#16a34a": "green",
        "#ef4444": "red",
        "#f97316": "orange",
        "#ffb451": "orange",
        "#eab308": "yellow",
        "#ec4899": "pink",
        "#a855f7": "purple",
        "#0a2540": "deep blue",
        "#92400e": "brown",
        "#ffffff": "white",
        "#0f172a": "cool near-black",
        "#f8fafc": "off-white",
    }
    assert {hex_value: color_name(hex_value, en) for hex_value in expected} == expected
    assert color_name("#0a2540", vi) == "xanh dương đậm"
    assert color_name("not a color", en) == ""
