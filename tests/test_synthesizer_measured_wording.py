"""Wording that follows what was measured: one action family per color token, the direction of
a nav collapse threshold, desktop-first breakpoints, the kind of form field and the guidance on
values that are not tokens. Inputs are the Acme sample with targeted edits."""

from __future__ import annotations

from get_design_markdown.analyzer.naming import COLOR_TOKEN_ROLES
from get_design_markdown.models.analysis import (
    Breakpoint,
    ColorToken,
    ComponentKind,
    ComponentSpec,
    DesignAnalysis,
    ResponsiveChangeKind,
)
from get_design_markdown.synthesizer.facts import DesignFacts
from get_design_markdown.synthesizer.render import render_design_md
from get_design_markdown.synthesizer.validate import validate_design_md
from tests.fixture_data import load_analysis_sample


def _flat(text: str) -> str:
    return " ".join(text.split())


def _errors(text: str) -> list[str]:
    return [issue.message for issue in validate_design_md(text) if issue.severity == "error"]


# ─── Action colors ───────────────────────────────────────────────────────────


def _two_indigo_tokens() -> DesignAnalysis:
    """button-primary on {colors.primary} #4f46e5, button-secondary on {colors.secondary}
    #6366f1: the same indigo hue family, two different tokens."""
    analysis = load_analysis_sample()
    analysis.colors.tokens.insert(
        1, ColorToken(name="secondary", value="#6366f1", role=COLOR_TOKEN_ROLES["secondary"])
    )
    secondary = next(spec for spec in analysis.components if spec.name == "button-secondary")
    secondary.properties["backgroundColor"] = "#6366f1"
    secondary.properties["textColor"] = "#ffffff"
    secondary.token_refs["backgroundColor"] = "{colors.secondary}"
    secondary.token_refs["textColor"] = "{colors.on-primary}"
    return analysis


def test_each_color_token_keeps_its_own_action_family() -> None:
    analysis = _two_indigo_tokens()
    actions = DesignFacts(analysis).action_colors()
    assert [(action.token, action.hex, action.variants) for action in actions[:2]] == [
        ("primary", "#4f46e5", ()),
        ("secondary", "#6366f1", ()),
    ]
    text = render_design_md(analysis)
    assert "#4f46e5 / #6366f1" not in text
    assert "`{colors.secondary}` — #6366f1" in text
    assert not _errors(text)


def test_untokenized_shades_still_join_the_family() -> None:
    analysis = load_analysis_sample()
    secondary = next(spec for spec in analysis.components if spec.name == "button-secondary")
    secondary.properties["backgroundColor"] = "#6366f1"
    secondary.token_refs.pop("backgroundColor", None)
    actions = DesignFacts(analysis).action_colors()
    assert actions[0].token == "primary" and actions[0].variants == ("#6366f1",)


def test_measured_states_are_not_called_invented_values() -> None:
    text = _flat(render_design_md(load_analysis_sample()))
    assert "beyond the tokens and the measured component values and states listed here" in text
    assert "that are not tokens in this file" not in text


# ─── Nav collapse threshold ──────────────────────────────────────────────────


def _with_nav_params(params: dict[str, float]) -> DesignAnalysis:
    analysis = load_analysis_sample()
    change = next(
        change
        for change in analysis.layout.changes
        if change.kind == ResponsiveChangeKind.NAV_COLLAPSES_TO_HAMBURGER
    )
    change.params = params
    return analysis


def test_max_width_collapse_reads_at_and_below() -> None:
    # (max-width: 768px): collapsed at 768px and narrower, never "below 769px".
    text = _flat(render_design_md(_with_nav_params({"below_px": 769, "max_px": 768, "links": 5})))
    assert "collapses to a hamburger at 768px and below (seen at 768px)" in text
    assert "Do collapse the top nav into a hamburger menu at 768px and below." in text
    assert "Responsive: the top nav collapses to a hamburger at 768px and below" in text
    assert "769px" not in text
    assert not _errors(render_design_md(_with_nav_params({"below_px": 769, "max_px": 768})))


def test_collapse_without_a_matching_breakpoint_names_no_threshold() -> None:
    text = _flat(render_design_md(_with_nav_params({"links": 5})))
    assert "between 1440px and 768px" in text
    assert "below 1440px" not in text
    assert "hamburger menu between 1440px and 768px (the exact breakpoint was not measured)" in (
        text
    )


def test_min_width_collapse_keeps_below() -> None:
    text = _flat(render_design_md(load_analysis_sample()))
    assert "Do collapse the top nav into a hamburger menu below 1024px." in text


# ─── Desktop-first breakpoints ───────────────────────────────────────────────


def test_desktop_first_breakpoints_are_listed() -> None:
    analysis = load_analysis_sample()
    analysis.breakpoints = [
        Breakpoint(name=name, max_width_px=width, occurrences=10)
        for name, width in (("sm", 640.0), ("md", 768.0), ("lg", 1024.0))
    ]
    text = _flat(render_design_md(analysis))
    assert "Breakpoints (max-width, desktop-first): 640, 768, 1024px" in text
    assert "the styles are written desktop-first" in text
    assert "Breakpoints (min-width)" not in text
    vi = _flat(render_design_md(analysis, lang="vi"))
    assert "Breakpoint (max-width, desktop-first): 640, 768, 1024px" in vi


def test_mobile_first_breakpoints_have_no_desktop_first_note() -> None:
    text = _flat(render_design_md(load_analysis_sample()))
    assert "desktop-first" not in text


# ─── Form fields ─────────────────────────────────────────────────────────────


def _field(analysis: DesignAnalysis, *, name: str, samples: list[str]) -> DesignAnalysis:
    spec = next(spec for spec in analysis.components if spec.kind == ComponentKind.INPUT)
    spec.name = name
    spec.samples = samples
    if name == "text-area":
        spec.variant = "multiline"
        spec.properties.pop("height", None)
    return analysis


def test_email_field_needs_a_measured_email_input() -> None:
    sample = _flat(render_design_md(load_analysis_sample()))
    assert "an email field styled as text-input" in sample
    text = _flat(
        render_design_md(
            _field(load_analysis_sample(), name="text-input", samples=["input[type=text]"])
        )
    )
    assert "email" not in text.lower()
    assert "a text field styled as text-input" in text


def test_a_textarea_is_a_message_field_without_height() -> None:
    analysis = _field(load_analysis_sample(), name="text-area", samples=["textarea"])
    spec = next(spec for spec in analysis.components if spec.name == "text-area")
    assert isinstance(spec, ComponentSpec) and "height" not in spec.properties
    text = _flat(render_design_md(analysis))
    assert "Design a contact form: a message field styled as text-area" in text
    assert "Multi-line text field." in text
    assert "email field" not in text
    assert not _errors(render_design_md(analysis))
