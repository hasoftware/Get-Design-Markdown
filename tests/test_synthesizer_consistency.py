"""Consistency of the rule-built DESIGN.md with its own token values (fix round 1).

Small synthetic analyses reproduce what the verifiers found on real captures:
- ratios quoted next to token hex values must recompute from those values (a member measured
  on #fafbfd must not lend its 4.54:1 to a canvas token whose value gives 4.45:1);
- one wording for the canvas color across Overview and Colors, and CSS font hints built on
  the measured stack;
- a gradient stop equal to a measured text color is not a "decorative accent";
- hero/card prompts and Do's pick levels by what was measured (h1, card samples), not names;
- translucent fills paired with text keep their measured value; the prose documents the
  ratio over the canvas and what an alpha-blind checker (the official linter) reports.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from get_design_markdown.analyzer.css_values import contrast_levels, contrast_ratio
from get_design_markdown.models.analysis import (
    ColorCluster,
    ColorPalette,
    ColorRole,
    ColorToken,
    ComponentKind,
    ComponentSpec,
    ContrastPair,
    DesignAnalysis,
    FontFamilyInfo,
    FontRole,
    LayoutInfo,
    SiteInfo,
    TypeLevel,
    Typography,
)
from get_design_markdown.models.raw import LayoutSnapshot
from get_design_markdown.synthesizer.facts import DesignFacts
from get_design_markdown.synthesizer.frontmatter import build_front_matter
from get_design_markdown.synthesizer.render import render_design_md
from get_design_markdown.synthesizer.validate import split_front_matter, validate_design_md

# ─── Builders ────────────────────────────────────────────────────────────────


def _pair(
    foreground: str,
    background: str,
    fg_token: str | None,
    bg_token: str | None,
    occurrences: int = 10,
    context: str = "body text",
) -> ContrastPair:
    ratio = round(contrast_ratio(foreground, background), 2)
    passes_aa, passes_large, passes_aaa = contrast_levels(ratio)
    return ContrastPair(
        foreground=foreground,
        background=background,
        foreground_token=fg_token,
        background_token=bg_token,
        ratio=ratio,
        passes_aa=passes_aa,
        passes_aa_large=passes_large,
        passes_aaa=passes_aaa,
        occurrences=occurrences,
        context=context,
    )


def _level(name: str, size: float, tags: list[str], samples: list[str] | None = None) -> TypeLevel:
    return TypeLevel(
        name=name,
        size_px=size,
        font_family="Brandsans",
        weight=400,
        line_height=1.4,
        tags=tags,
        count=5,
        samples=samples or [],
    )


def _light_site() -> DesignAnalysis:
    """Off-white canvas, indigo primary that passes AA on a lighter member of the canvas
    cluster (#fafbfd) but not on the canvas token value (#f6f9fc)."""
    tokens = [
        ColorToken(name="primary", value="#635bff", role=ColorRole.BRAND),
        ColorToken(name="on-primary", value="#ffffff", role=ColorRole.TEXT),
        ColorToken(name="canvas", value="#f6f9fc", role=ColorRole.BACKGROUND),
        ColorToken(name="surface", value="#ffffff", role=ColorRole.SURFACE),
        ColorToken(name="ink", value="#061b31", role=ColorRole.TEXT),
        ColorToken(name="muted", value="#50617a", role=ColorRole.TEXT_MUTED),
    ]
    pairs = [
        _pair("#061b31", "#ffffff", "ink", "surface", 80),
        _pair("#061b31", "#f8fafd", "ink", "canvas", 17, "footer text"),
        _pair("#50617a", "#f8fafd", "muted", "canvas", 90, "link"),
        _pair("#635bff", "#fafbfd", "primary", "canvas", 32, "link"),
        _pair("#635bff", "#f6f9fc", "primary", "canvas", 12, "link"),
        _pair("#635bff", "#ffffff", "primary", "surface", 17, "link"),
        _pair("#ffffff", "#635bff", "on-primary", "primary", 27, "button"),
    ]
    components = [
        ComponentSpec(
            name="button-primary",
            kind=ComponentKind.BUTTON,
            variant="primary",
            count=20,
            properties={"backgroundColor": "#635bff", "textColor": "#ffffff"},
            token_refs={"backgroundColor": "{colors.primary}", "textColor": "{colors.on-primary}"},
            contrast_ratio=4.7,
        ),
        ComponentSpec(
            name="text-link",
            kind=ComponentKind.LINK,
            variant="inline",
            count=18,
            properties={"textColor": "#635bff"},
            token_refs={"textColor": "{colors.primary}"},
            contrast_ratio=4.54,
        ),
        ComponentSpec(
            name="footer",
            kind=ComponentKind.FOOTER,
            count=1,
            properties={"backgroundColor": "#f8fafd", "textColor": "#50617a"},
            token_refs={"backgroundColor": "{colors.canvas}", "textColor": "{colors.muted}"},
            contrast_ratio=6.03,
        ),
    ]
    return DesignAnalysis(
        tool_version="0.1.0",
        analyzed_at=datetime(2026, 10, 15, tzinfo=UTC),
        site=SiteInfo(url="https://light.example/", name="Light"),
        colors=ColorPalette(scheme="light", tokens=tokens, contrast_pairs=pairs),
        components=components,
    )


def _section(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match is not None, title
    return match.group(1)


def _block(text: str, component: str) -> str:
    match = re.search(rf"^### {re.escape(component)}\n(.*?)(?=^### |^## |\Z)", text, re.M | re.S)
    assert match is not None, component
    return match.group(1)


# ─── stripe-r1-05: ratios recompute from the printed token values ───────────


def test_component_contrast_is_recomputed_on_the_printed_token_values() -> None:
    text = render_design_md(_light_site())
    link = _block(text, "text-link")
    # The analyzer's 4.54:1 was measured on #fafbfd; the canvas token #f6f9fc gives 4.45:1.
    assert "4.54" not in link
    assert (
        "⚠ Text contrast: `{colors.primary}` — #635bff on `{colors.canvas}` — #f6f9fc: 4.45:1 "
        "(✗ below WCAG AA 4.5:1 — large text only); `{colors.primary}` — #635bff on "
        "`{colors.surface}` — #ffffff: 4.70:1 (WCAG AA ✓)."
    ) in link
    footer = _block(text, "footer")
    assert "`{colors.muted}` — #50617a on `{colors.canvas}` — #f6f9fc: 5.96:1" in footer
    assert "6.03" not in footer


def test_dos_quote_token_ratios_and_caveat_primary_text() -> None:
    analysis = _light_site()
    # Running text is measured mostly on the canvas here (#f8fafd, a canvas-cluster member).
    analysis.colors.contrast_pairs[1] = _pair("#061b31", "#f8fafd", "ink", "canvas", 120)
    text = render_design_md(analysis)
    dos = _section(text, "Do's and Don'ts")
    # ink/canvas was measured 16.61:1 on #f8fafd; the token values give 16.44:1.
    assert "Do set running text in `{colors.ink}` — #061b31 on `{colors.canvas}` — #f6f9fc " in dos
    assert "(16.44:1)" in dos and "16.61" not in dos
    reserve = next(line for line in dos.splitlines() if line.startswith("- Do reserve"))
    assert "As text it falls below WCAG AA on one surface" in reserve
    assert "`{colors.canvas}` — #f6f9fc: 4.45:1" in reserve
    assert "- Don't set `{colors.primary}` — #635bff text on `{colors.canvas}`" in dos
    assert not [issue for issue in validate_design_md(text) if issue.rule == "contrast-claim"]


def test_best_text_pair_skips_pairs_that_fail_on_token_values() -> None:
    analysis = _light_site()
    # Only primary text is measured on the canvas (its passing member is #fafbfd).
    analysis.colors.contrast_pairs = [
        p for p in analysis.colors.contrast_pairs if p.foreground_token == "primary"
    ]
    pair = DesignFacts(analysis).best_text_pair()
    assert pair is not None
    assert (pair.foreground_token, pair.background_token) == ("primary", "surface")
    assert round(pair.ratio, 2) == 4.7


def test_running_text_pair_follows_measured_body_text_not_the_role() -> None:
    # linear.app: ink is only measured as large text (headings); running text is faint/muted.
    analysis = _light_site()
    analysis.colors.tokens.append(
        ColorToken(name="faint", value="#5c6b82", role=ColorRole.TEXT_MUTED)
    )
    analysis.colors.contrast_pairs = [
        _pair("#061b31", "#f6f9fc", "ink", "canvas", 70, "body text (large)"),
        _pair("#5c6b82", "#f6f9fc", "faint", "canvas", 257),
        _pair("#50617a", "#f6f9fc", "muted", "canvas", 93),
        _pair("#ffffff", "#635bff", "on-primary", "primary", 300),
    ]
    pair = DesignFacts(analysis).best_text_pair()
    assert pair is not None
    assert (pair.foreground_token, pair.background_token) == ("faint", "canvas")
    dos = _section(render_design_md(analysis), "Do's and Don'ts")
    assert "Do set running text in `{colors.faint}` — #5c6b82 on `{colors.canvas}`" in dos


def test_validator_flags_a_ratio_that_does_not_match_the_tokens() -> None:
    front = '---\nversion: alpha\nname: X\ncolors:\n  ink: "#061b31"\n  canvas: "#f6f9fc"\n---\n'
    wrong = front + (
        "- Do set text in `{colors.ink}` — #061b31 on `{colors.canvas}` — #f6f9fc (16.61:1).\n"
    )
    right = wrong.replace("16.61", "16.44")
    approx = front + (
        "| `{colors.ink}` — #061b31 | #f8fafd (≈ `{colors.canvas}` — #f6f9fc) | 16.61:1 |\n"
    )
    single = front + "- `{colors.ink}` — #061b31: 16.4:1 on canvas; WCAG AA needs 4.5:1.\n"
    issues = [i for i in validate_design_md(wrong) if i.rule == "contrast-claim"]
    assert len(issues) == 1 and issues[0].severity == "warning"
    assert "16.61:1" in issues[0].message and "16.44:1" in issues[0].message
    assert "Dòng 8" in issues[0].message
    for text in (right, approx, single):
        assert not [i for i in validate_design_md(text) if i.rule == "contrast-claim"]


# ─── stripe-r1-09: one canvas wording, loadable font stacks ──────────────────


def test_canvas_wording_matches_the_color_name() -> None:
    analysis = _light_site()
    text = render_design_md(analysis)
    assert "light interface built on an off-white canvas" in text
    assert "light interface on an off-white canvas" in split_front_matter(text)[0]["description"]
    assert "**canvas** `{colors.canvas}` — #f6f9fc: off-white" in text
    assert "bright white" not in text
    for token in analysis.colors.tokens:
        if token.name == "canvas":
            token.value = "#ffffff"
    assert "built on a bright white canvas" in render_design_md(analysis)


def test_css_font_hints_use_the_measured_stack() -> None:
    analysis = _light_site()
    analysis.typography = Typography(
        families=[
            FontFamilyInfo(
                name="Brandsans",
                stack=["brandsans-var", "Helvetica Neue", "Arial", "sans-serif"],
                role=FontRole.PRIMARY,
                category="sans-serif",
            ),
            FontFamilyInfo(
                name="Inter",
                stack=["Inter Variable", "SF Pro Display", "-apple-system", "Segoe UI", "Roboto"],
                role=FontRole.HEADING,
                category="sans-serif",
                is_google_font=True,
            ),
        ],
        levels=[
            TypeLevel(name="display", size_px=56, font_family="Inter", weight=500, line_height=1),
            _level("body", 16, ["p"]),
        ],
    )
    text = render_design_md(analysis)
    guide = _section(text, "Agent Prompt Guide")
    assert '`400 16px/1.4 brandsans-var, "Helvetica Neue", Arial, sans-serif`' in guide
    assert (
        '`500 56px/1 Inter, "Inter Variable", "SF Pro Display", -apple-system, sans-serif`'
    ) in guide
    assert "Brandsans, sans-serif" not in guide


# ─── linear-LIN-3: gradient stops equal to structural colors ─────────────────


def test_gradient_stop_matching_a_text_cluster_is_not_decorative() -> None:
    analysis = _light_site()
    analysis.colors.clusters = [
        ColorCluster(id="text-3", channel="text", hex="#d0d6e0", count=175, weight=2000.0),
        ColorCluster(
            id="gradient-4", channel="gradient", hex="#d0d6e0", weight=9e5, decorative=True
        ),
        ColorCluster(
            id="gradient-5", channel="gradient", hex="#d1d6e1", weight=8e5, decorative=True
        ),
        ColorCluster(
            id="gradient-7", channel="gradient", hex="#b2d5ff", weight=7e5, decorative=True
        ),
        ColorCluster(
            id="gradient-8", channel="gradient", hex="#d0d6e080", weight=6e5, decorative=True
        ),
    ]
    hexes = [cluster.hex for cluster in DesignFacts(analysis).decorative_clusters()]
    # #d0d6e0 equals a text cluster, #d1d6e1 is within ΔE 3 of it; the half-alpha stop is not.
    assert hexes == ["#b2d5ff", "#d0d6e080"]
    dos = _section(render_design_md(analysis), "Do's and Don'ts")
    assert "(#b2d5ff and #d0d6e080) as text, button or surface colors" in dos


# ─── linear-LIN-6: hero and cards use measured levels ────────────────────────


def _levels_site() -> DesignAnalysis:
    analysis = _light_site()
    analysis.typography = Typography(
        levels=[
            _level("display", 72, ["h2"], ["Built for the future."]),
            _level("heading", 64, ["h1"], ["The product system"]),
            _level("title", 48, ["h2", "p", "h1"], ["A new species of tool"]),
            _level("subtitle", 32, ["p"], ["A long customer quote"]),
            _level("body-lg", 20, ["a", "p", "span"], ["Faster app launch"]),
            _level("body", 15, ["span", "a", "p"], ["Purpose-built for planning"]),
        ]
    )
    analysis.layout = LayoutInfo(snapshots=[LayoutSnapshot(viewport_width=1440, main_columns=4)])
    return analysis


def test_hero_and_dos_use_the_h1_level_and_the_measured_lead() -> None:
    text = render_design_md(_levels_site())
    guide = _section(text, "Agent Prompt Guide")
    assert "headline in `{typography.heading}` — Brandsans 64px" in guide
    assert "supporting copy in `{typography.body}` — Brandsans 15px" in guide
    assert "Type: headlines `{typography.heading}`" in guide
    assert "Do set headlines in `{typography.heading}`" in _section(text, "Do's and Don'ts")
    assert "{typography.display}` — Brandsans 72px" not in guide


def test_lead_is_a_p_level_slightly_larger_than_body() -> None:
    analysis = _levels_site()
    analysis.typography.levels[4].tags = ["p", "a"]
    assert DesignFacts(analysis).lead_level() == analysis.typography.levels[4]
    # A 32px <p> is a pull quote, never the hero lead.
    analysis.typography.levels[4].tags = ["a", "p"]
    assert DesignFacts(analysis).lead_level() == analysis.typography.levels[5]


def test_card_prompt_needs_a_measured_card() -> None:
    analysis = _levels_site()
    guide = _section(render_design_md(analysis), "Agent Prompt Guide")
    assert "feature grid" not in guide and "card titles" not in guide
    analysis.components.append(
        ComponentSpec(
            name="card",
            kind=ComponentKind.CARD,
            count=6,
            properties={"backgroundColor": "#ffffff", "rounded": "8px", "padding": "24px"},
            token_refs={"backgroundColor": "{colors.surface}"},
            samples=["Faster app launch"],
        )
    )
    guide = _section(render_design_md(analysis), "Agent Prompt Guide")
    assert "4-column feature grid: each item is a card (background `{colors.surface}`" in guide
    assert "card titles in `{typography.body-lg}` — Brandsans 20px" in guide
    assert "grid gap" not in guide


# ─── linear-LIN-12: translucent fills and alpha-blind checkers ───────────────


def _dark_site() -> DesignAnalysis:
    tokens = [
        ColorToken(name="canvas", value="#08090a", role=ColorRole.BACKGROUND),
        ColorToken(name="ink", value="#f7f8f8", role=ColorRole.TEXT),
        ColorToken(name="hairline", value="#ffffff14", role=ColorRole.BORDER),
    ]
    components = [
        ComponentSpec(
            name="button-secondary",
            kind=ComponentKind.BUTTON,
            variant="secondary",
            count=4,
            properties={"backgroundColor": "#ffffff0d", "textColor": "#f7f8f8"},
            token_refs={"textColor": "{colors.ink}"},
        ),
        ComponentSpec(
            name="nav-link",
            kind=ComponentKind.LINK,
            variant="nav",
            count=5,
            properties={"textColor": "#f7f8f8"},
            token_refs={"textColor": "{colors.ink}"},
            states={"hover": {"backgroundColor": "#ffffff14"}},
        ),
        ComponentSpec(
            name="top-nav",
            kind=ComponentKind.NAVIGATION,
            count=1,
            properties={"backgroundColor": "#0b0b0bc2", "backdropFilter": "blur(20px)"},
        ),
    ]
    return DesignAnalysis(
        tool_version="0.1.0",
        analyzed_at=datetime(2026, 10, 15, tzinfo=UTC),
        site=SiteInfo(url="https://dark.example/", name="Dark"),
        colors=ColorPalette(scheme="dark", tokens=tokens),
        components=components,
    )


def test_translucent_fills_keep_their_value_and_document_alpha_blind_checkers() -> None:
    analysis = _dark_site()
    components = build_front_matter(analysis)["components"]
    # Measured values stay in the front matter (no invented opaque colors).
    assert components["button-secondary"]["backgroundColor"] == "#ffffff0d"
    assert components["nav-link-hover"] == {"backgroundColor": "#ffffff14"}
    assert components["top-nav"]["backgroundColor"] == "#0b0b0bc2"
    pairs = {pair.entry: pair for pair in DesignFacts(analysis).alpha_blind_pairs()}
    assert set(pairs) == {"button-secondary", "nav-link-hover"}
    assert round(pairs["button-secondary"].alpha_blind_ratio, 2) == 1.06
    text = render_design_md(analysis)
    assert "- Background: #ffffff0d (≈ #151616 over the canvas)" in text
    assert "Hover: background → #ffffff14 (≈ #1b1c1d over the canvas)" in text
    assert (
        "- Translucent fill: `button-secondary` keeps the measured #ffffff0d; layered over the "
        "canvas its text measures 17.04:1, while checkers that ignore alpha (such as the "
        "DESIGN.md linter) report 1.06:1 for the same values."
    ) in text
    assert "`nav-link-hover` keeps the measured #ffffff14" in text
    assert "`top-nav` keeps" not in text
    assert "has the background #0b0b0bc2 — translucent (alpha 76%)" in text
    assert not [i for i in validate_design_md(text) if i.rule == "contrast-ratio"]
