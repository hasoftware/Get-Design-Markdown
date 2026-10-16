"""Prose drawn from what was measured, not from token names or one-sided readings: the hero
lead color, the Quick Reference text tiers, family weights, typography references, the nav call
to action, translucent-fill notes, decorative colors a component uses, and tokens no component
references. Inputs are the Acme sample with small targeted edits."""

from __future__ import annotations

import re

from get_design_markdown.models.analysis import (
    ColorCluster,
    ComponentSpec,
    ContrastPair,
    DesignAnalysis,
    FontFamilyInfo,
    FontRole,
)
from get_design_markdown.synthesizer.facts import DesignFacts
from get_design_markdown.synthesizer.render import render_design_md
from get_design_markdown.synthesizer.sections_style import MAX_CONTRAST_ROWS
from get_design_markdown.synthesizer.validate import validate_design_md
from tests.fixture_data import load_analysis_sample


def _flat(text: str) -> str:
    return " ".join(text.split())


def _errors(text: str) -> list[str]:
    return [issue.message for issue in validate_design_md(text) if issue.severity == "error"]


def _section(text: str, title: str) -> str:
    match = re.search(rf"^##+ {re.escape(title)}\n(.*?)(?=^##+ |\Z)", text, re.M | re.S)
    assert match is not None, title
    return match.group(1)


def _hero(text: str) -> str:
    line = next(line for line in text.splitlines() if "Build a hero section" in line)
    return line


def _pair(fg: str, token: str, occurrences: int, context: str) -> ContrastPair:
    return ContrastPair(
        foreground=fg,
        background="#ffffff",
        foreground_token=token,
        background_token="canvas",
        ratio=4.6,
        passes_aa=True,
        passes_aa_large=True,
        passes_aaa=False,
        occurrences=occurrences,
        context=context,
    )


def _spec(analysis: DesignAnalysis, name: str) -> ComponentSpec:
    return next(spec for spec in analysis.components if spec.name == name)


# ─── Hero lead color ─────────────────────────────────────────────────────────


#: A faint tier that passes AA on white (4.76:1), like linear.app's #8a8f98 on its canvas.
PASSING_FAINT = "#64748b"


def _passing_faint(analysis: DesignAnalysis) -> None:
    next(t for t in analysis.colors.tokens if t.name == "faint").value = PASSING_FAINT


def _faint_heavy_analysis() -> DesignAnalysis:
    """linear.app-like: faint is the running-text tier, muted only sets large leads."""
    analysis = load_analysis_sample()
    _passing_faint(analysis)
    analysis.colors.contrast_pairs = [
        _pair(PASSING_FAINT, "faint", 257, "body text"),
        _pair("#475569", "muted", 40, "body text (large)"),
        _pair("#0f172a", "ink", 70, "body text"),
    ]
    return analysis


def test_hero_lead_color_is_the_measured_running_text_tier_not_muted_by_name() -> None:
    analysis = _faint_heavy_analysis()
    facts = DesignFacts(analysis)
    lead = facts.lead_level()
    assert lead is not None and lead.size_px < 24
    assert facts.lead_color(lead) == "faint"
    hero = _hero(render_design_md(analysis))
    assert f"color `{{colors.faint}}` — {PASSING_FAINT}" in hero
    assert "`{colors.muted}`" not in hero


def test_lead_color_never_picks_a_tier_that_fails_aa_on_the_canvas() -> None:
    # The Acme faint #94a3b8 measures 2.56:1 on white: more measured, still not a lead color.
    analysis = load_analysis_sample()
    analysis.colors.contrast_pairs = [
        _pair("#94a3b8", "faint", 257, "body text"),
        _pair("#475569", "muted", 3, "body text"),
    ]
    facts = DesignFacts(analysis)
    lead = facts.lead_level()
    assert lead is not None
    assert facts.lead_color(lead) == "muted"


def test_large_lead_prefers_the_tier_measured_on_large_text() -> None:
    analysis = _faint_heavy_analysis()
    facts = DesignFacts(analysis)
    large = facts.levels["display"].model_copy(update={"size_px": 28.0, "weight": 400})
    assert facts.lead_color(large) == "muted"


def test_lead_color_ignores_pairs_on_an_inverse_band() -> None:
    analysis = load_analysis_sample()
    _passing_faint(analysis)
    inverse = ContrastPair(
        foreground=PASSING_FAINT,
        background="#0b1020",
        foreground_token="faint",
        background_token="surface-inverse",
        ratio=7.38,
        passes_aa=True,
        passes_aa_large=True,
        passes_aaa=True,
        occurrences=500,
        context="body text",
    )
    analysis.colors.contrast_pairs = [inverse, _pair("#475569", "muted", 3, "body text")]
    facts = DesignFacts(analysis)
    lead = facts.lead_level()
    assert lead is not None
    assert facts.lead_color(lead) == "muted"


def test_lead_color_falls_back_to_the_first_secondary_tier_without_pairs() -> None:
    analysis = load_analysis_sample()
    analysis.colors.contrast_pairs = []
    facts = DesignFacts(analysis)
    lead = facts.lead_level()
    assert lead is not None
    assert facts.lead_color(lead) == "muted"
    analysis.colors.tokens = [t for t in analysis.colors.tokens if t.role.value != "text_muted"]
    facts = DesignFacts(analysis)
    assert facts.lead_color(lead) is None
    hero = _hero(render_design_md(analysis))
    assert "supporting copy in `{typography.body-lg}` — Inter 20px / 400 / 1.6;" in hero


# ─── Quick Reference text tiers ──────────────────────────────────────────────


def _set_share(analysis: DesignAnalysis, cluster_id: str, share: float) -> None:
    cluster = next(c for c in analysis.colors.clusters if c.id == cluster_id)
    cluster.share = share


def test_quick_reference_lists_a_heavily_used_faint_tier() -> None:
    analysis = load_analysis_sample()
    _set_share(analysis, "text-4", 0.256)
    quick = _section(render_design_md(analysis), "Quick Reference")
    assert "`{colors.muted}` — #475569, `{colors.faint}` — #94a3b8" in quick
    _set_share(analysis, "text-4", 0.05)
    quick = _section(render_design_md(analysis), "Quick Reference")
    assert "{colors.faint}" not in quick


# ─── Family weights ──────────────────────────────────────────────────────────


def test_single_family_lists_every_weight_in_use() -> None:
    # stripe.com: Söhne measured 300/400/425 per family, 500 only in weights_used.
    analysis = load_analysis_sample()
    analysis.typography.families = [
        FontFamilyInfo(name="Inter", role=FontRole.PRIMARY, share=1.0, weights=[400, 500])
    ]
    analysis.typography.weights_used = [400, 500, 600, 700, 800]
    for level in analysis.typography.levels:
        level.font_family = "Inter"
    facts = DesignFacts(analysis)
    assert facts.family_weights(facts.families["Inter"]) == facts.weights()
    text = _flat(render_design_md(analysis))
    assert "weights 400, 500, 600, 700 and 800;" in text
    assert "Don't use font weights outside 400, 500, 600, 700 and 800." in text


def test_unlisted_weights_go_to_the_main_family_and_family_weights_stay_allowed() -> None:
    # linear.app: Inter 400/510, Berkeley Mono 400, weights_used adds 590.
    analysis = load_analysis_sample()
    analysis.typography.weights_used = [400, 500, 600, 700, 590]
    mono = next(f for f in analysis.typography.families if f.role == FontRole.MONO)
    mono.weights = [400, 300]
    facts = DesignFacts(analysis)
    families = {name: facts.family_weights(info) for name, info in facts.families.items()}
    assert 590 in families["Inter"]
    assert families[mono.name] == [300, 400]
    listed = {weight for weights in families.values() for weight in weights}
    assert listed == set(facts.weights())
    assert 300 in facts.weights()


# ─── Typography references ───────────────────────────────────────────────────


def test_typography_ref_needs_the_measured_line_height() -> None:
    # stripe.com: a 14px / 14px nav link is not {typography.caption} 14px / 1.2 (16.8px).
    analysis = load_analysis_sample()
    nav = _spec(analysis, "nav-link")
    facts = DesignFacts(analysis)
    assert facts.typography_ref(nav) == "{typography.ui}"
    nav.properties["lineHeight"] = "20.5px"
    assert DesignFacts(analysis).typography_ref(nav) == "{typography.ui}"
    nav.properties["lineHeight"] = "15px"
    facts = DesignFacts(analysis)
    assert facts.typography_ref(nav) is None
    assert "typography" not in facts.component_props(nav)
    text = render_design_md(analysis)
    block = _section(text, "nav-link")
    assert "Typography: Inter 15px / 500 / 15px" in block
    assert "{typography.ui}" not in block
    assert not _errors(text)


def test_typography_ref_needs_the_measured_size_and_weight() -> None:
    analysis = load_analysis_sample()
    nav = _spec(analysis, "nav-link")
    nav.properties["fontWeight"] = "600"
    assert DesignFacts(analysis).typography_ref(nav) is None
    nav.properties["fontWeight"] = "500"
    nav.properties["fontSize"] = "16px"
    assert DesignFacts(analysis).typography_ref(nav) is None


def test_typography_ref_without_measured_values_is_kept() -> None:
    analysis = load_analysis_sample()
    footer = _spec(analysis, "footer")
    assert DesignFacts(analysis).typography_ref(footer) == "{typography.body-sm}"


# ─── Nav call to action ──────────────────────────────────────────────────────


def test_nav_cta_keeps_colors_and_radius_but_not_the_measured_size() -> None:
    # linear.app: button-primary is measured at 44px on the hero, the header "Sign up" is 32px.
    analysis = load_analysis_sample()
    analysis.components = [s for s in analysis.components if s.name != "text-input"]
    lines = render_design_md(analysis).splitlines()
    nav = next(line for line in lines if "Build the top navigation" in line)
    cta = nav.split("call to action on the right (", 1)[1].split(")", 1)[0]
    assert "background `{colors.primary}` — #4f46e5" in cta
    assert "radius `{rounded.md}` — 8px" in cta
    for size in ("padding", "height", "typography"):
        assert size not in cta
    hero = _hero("\n".join(lines))
    assert "padding 12px 20px, height 44px" in hero


# ─── Translucent fills ───────────────────────────────────────────────────────


def test_alpha_note_only_when_the_two_readings_differ() -> None:
    # stripe.com: #ffffffa6 over a white canvas reads 6.19:1 either way.
    analysis = load_analysis_sample()
    outline = _spec(analysis, "button-outline")
    outline.properties["backgroundColor"] = "#ffffffa6"
    outline.properties["textColor"] = "#4f46e5"
    outline.token_refs["textColor"] = "{colors.primary}"
    facts = DesignFacts(analysis)
    assert all(pair.component != "button-outline" for pair in facts.alpha_blind_pairs())
    assert "Translucent fill: `button-outline`" not in render_design_md(analysis)
    # The same fill on a near-black canvas reads differently once alpha is ignored.
    for token in analysis.colors.tokens:
        if token.name == "canvas":
            token.value = "#08090a"
    outline.properties["textColor"] = "#f7f8f8"
    outline.token_refs.pop("textColor")
    pairs = [p for p in DesignFacts(analysis).alpha_blind_pairs() if p.entry == "button-outline"]
    assert len(pairs) == 1 and f"{pairs[0].ratio:.2f}" != f"{pairs[0].alpha_blind_ratio:.2f}"


# ─── Decorative colors ───────────────────────────────────────────────────────


def _header_gradient(analysis: DesignAnalysis) -> None:
    """linear.app: the top-nav fill #0b0b0bcc is the first stop of the header gradient whose
    cluster representative is #0b0b0bc2."""
    analysis.colors.clusters.append(
        ColorCluster(
            id="gradient-9",
            channel="gradient",
            hex="#0b0b0bc2",
            alpha=0.76,
            weight=900.0,
            share=0.9,
            count=3,
            decorative=True,
            samples=["header.header"],
        )
    )


def test_gradient_color_of_a_component_fill_is_not_decorative() -> None:
    analysis = load_analysis_sample()
    _header_gradient(analysis)
    assert "#0b0b0bc2" in [c.hex for c in DesignFacts(analysis).decorative_clusters()]
    _spec(analysis, "top-nav").properties["backgroundColor"] = "#0b0b0bcc"
    assert "#0b0b0bc2" not in [c.hex for c in DesignFacts(analysis).decorative_clusters()]
    text = render_design_md(analysis)
    assert "#0b0b0bc2" not in text


def test_gradient_color_of_a_hover_state_is_not_decorative() -> None:
    analysis = load_analysis_sample()
    _header_gradient(analysis)
    _spec(analysis, "button-ghost").states["hover"]["backgroundColor"] = "#0b0b0bc8"
    assert "#0b0b0bc2" not in [c.hex for c in DesignFacts(analysis).decorative_clusters()]


def test_alpha_far_from_a_component_fill_stays_decorative() -> None:
    analysis = load_analysis_sample()
    _header_gradient(analysis)
    _spec(analysis, "top-nav").properties["backgroundColor"] = "#0b0b0b"
    assert "#0b0b0bc2" in [c.hex for c in DesignFacts(analysis).decorative_clusters()]


# ─── Tokens no component references ──────────────────────────────────────────


def test_unreferenced_tokens_are_documented_with_their_reason() -> None:
    analysis = load_analysis_sample()
    text = render_design_md(analysis)
    block = _section(text, "Tokens Without a Component Reference")
    assert "`hairline`: used only through component properties" in block
    assert "`success`: measured on page elements outside the detected components" in block
    assert "`primary`" not in block and "`muted`" not in block
    vi = _section(render_design_md(analysis, lang="vi"), "Token chưa được component tham chiếu")
    assert "`hairline`" in vi and "`success`" in vi


def test_no_unreferenced_section_when_every_token_is_referenced_or_no_components() -> None:
    analysis = load_analysis_sample()
    # hairline is only a border color and success is used by no component.
    analysis.colors.tokens = [
        t for t in analysis.colors.tokens if t.name not in ("hairline", "success")
    ]
    text = render_design_md(analysis)
    assert "Tokens Without a Component Reference" not in text
    analysis.components = []
    assert "Tokens Without a Component Reference" not in render_design_md(analysis)


# ─── Change requests of fix round 2 ──────────────────────────────────────────


def test_neutral_primary_is_worded_as_an_action_fill() -> None:
    # linear.app: the primary is the light-gray invert CTA #e5e5e6, not a brand accent.
    analysis = load_analysis_sample()
    primary = next(token for token in analysis.colors.tokens if token.name == "primary")
    primary.value = "#e5e5e6"
    _spec(analysis, "button-primary").properties["backgroundColor"] = "#e5e5e6"
    text = render_design_md(analysis, lang="en")
    entry = next(line for line in text.splitlines() if line.startswith("- **primary**"))
    assert "primary action (neutral fill)" in entry
    assert "brand accent" not in entry
    do = next(line for line in text.splitlines() if "for the main call to action" in line)
    assert "and accents" not in do
    vi = render_design_md(analysis, lang="vi")
    assert "hành động chính (nền trung tính)" in vi
    assert "và các điểm nhấn" not in vi


def test_chromatic_primary_keeps_the_brand_wording() -> None:
    text = render_design_md(load_analysis_sample(), lang="en")
    entry = next(line for line in text.splitlines() if line.startswith("- **primary**"))
    assert "brand accent / primary action" in entry
    assert any("main call to action and accents" in line for line in text.splitlines())


def test_contrast_table_keeps_the_page_weighted_ranking() -> None:
    # colors_contrast ranks pairs by page-weighted occurrences: a home-page pair seen 3 times
    # leads sub-page pairs seen 50 times. When the table is full, the rows kept are the first
    # ones of that ranking, not the most frequent raw counts.
    analysis = load_analysis_sample()
    sub_pages = [
        _pair("#0f172a", "ink", 50 + index, f"pricing table cell {index}")
        for index in range(MAX_CONTRAST_ROWS)
    ]
    analysis.colors.contrast_pairs = [_pair("#475569", "muted", 3, "home lead paragraph")]
    analysis.colors.contrast_pairs += sub_pages
    text = render_design_md(analysis, lang="en")
    assert "home lead paragraph" in text
    assert text.index("home lead paragraph") < text.index("pricing table cell 0")
    assert f"pricing table cell {MAX_CONTRAST_ROWS - 1}" not in text


def test_guide_intro_does_not_claim_every_value_is_a_token() -> None:
    # Prompts quote measured paddings, heights and translucent fills that have no token.
    text = render_design_md(load_analysis_sample(), lang="en")
    guide = _section(text, "Agent Prompt Guide")
    assert "Every value below is a token" not in guide
    assert "values given without a token are measured component values" in _flat(guide)
