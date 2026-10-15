"""render_design_md: structure, honesty rules, narrative rules, partial analyses, goldens.

Regenerate the golden files after an intended wording change with
`GDM_UPDATE_GOLDEN=1 python -m uv run pytest tests/test_synthesizer_render.py`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from get_design_markdown.analyzer.naming import TOKEN_REF_RE
from get_design_markdown.models.analysis import (
    ColorRole,
    ColorToken,
    ComponentKind,
    ComponentSpec,
    DesignAnalysis,
    ScaleStep,
    SiteInfo,
)
from get_design_markdown.synthesizer.catalog import UnsupportedLanguageError, load_phrases
from get_design_markdown.synthesizer.narrative import build_sections
from get_design_markdown.synthesizer.render import DESIGN_SECTIONS, render_design_md
from get_design_markdown.synthesizer.synthesize import section_titles
from get_design_markdown.synthesizer.validate import split_front_matter, validate_design_md
from tests.fixture_data import load_analysis_sample

GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "synthesizer"
UPDATE_ENV = "GDM_UPDATE_GOLDEN"


def _section(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match is not None, title
    return match.group(1)


def _errors(text: str) -> list[str]:
    return [issue.message for issue in validate_design_md(text) if issue.severity == "error"]


def _empty_analysis() -> DesignAnalysis:
    return DesignAnalysis(
        tool_version="0.1.0",
        analyzed_at=datetime(2026, 10, 15, tzinfo=UTC),
        site=SiteInfo(url="https://empty.example/", name="Empty"),
    )


# ─── Structure ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", ["en", "vi"])
def test_sample_has_every_section_in_order_and_validates(lang: str) -> None:
    text = render_design_md(load_analysis_sample(), lang=lang)
    assert section_titles(text) == list(DESIGN_SECTIONS)
    assert _errors(text) == []
    assert text.startswith("---\nversion: alpha\n")
    assert "\n# Acme Cloud — Design Reference\n" in text


@pytest.mark.parametrize("lang", ["en", "vi"])
def test_sample_with_examples_has_no_validation_warnings(lang: str) -> None:
    text = render_design_md(load_analysis_sample(), lang=lang, examples=True)
    severities = {issue.severity for issue in validate_design_md(text)}
    assert severities == {"info"}


def test_output_is_deterministic_lf_and_tidy() -> None:
    first = render_design_md(load_analysis_sample(), examples=True)
    assert first == render_design_md(load_analysis_sample(), examples=True)
    assert "\r" not in first
    assert first.endswith("\n") and not first.endswith("\n\n")
    assert "\n\n\n" not in first
    assert all(line == line.rstrip() for line in first.split("\n"))


def test_reference_note() -> None:
    text = render_design_md(load_analysis_sample())
    note = text.split("# Acme Cloud — Design Reference", 1)[1].split("## Overview", 1)[0]
    assert "https://acme.example/" in note
    assert "2026-10-15" in note
    assert "**Pages analyzed:** home" in note
    assert "get-design-markdown 0.1.0" in note
    assert "not the official design system of Acme Cloud" in note
    assert "no logos, images or other copyrighted assets" in note
    vi_note = render_design_md(load_analysis_sample(), lang="vi")
    assert "không phải design system chính thức của Acme Cloud" in vi_note


def test_every_body_reference_is_defined_and_shown_with_its_value() -> None:
    text = render_design_md(load_analysis_sample(), examples=True)
    data, body = split_front_matter(text)
    for group, name in [(m.group(1), m.group(2)) for m in TOKEN_REF_RE.finditer(body)]:
        assert name in data[group], f"{group}.{name}"
    prose = _section(text, "Do's and Don'ts") + _section(text, "Agent Prompt Guide").split("###")[1]
    for match in re.finditer(r"`\{colors\.[a-z0-9-]+\}`", prose):
        assert prose[match.end() : match.end() + 4] == " — #", prose[
            match.start() : match.end() + 20
        ]


def test_front_matter_matches_body_components() -> None:
    text = render_design_md(load_analysis_sample())
    data, body = split_front_matter(text)
    headings = re.findall(r"^### ([a-z0-9-]+)$", _section(text, "Components"), re.M)
    assert headings == [c.name for c in load_analysis_sample().components]
    for name in data["components"]:
        assert f"`{name}`" in body


# ─── Honesty rules (lessons from DESIGN-1win.md) ─────────────────────────────


def test_failing_pairs_become_donts_with_measured_ratios() -> None:
    text = render_design_md(load_analysis_sample())
    donts = [
        line
        for line in _section(text, "Do's and Don'ts").splitlines()
        if line.startswith("- Don't")
    ]
    joined = "\n".join(donts)
    for ratio in ("2.56:1", "3.98:1", "3.30:1", "4.23:1"):
        assert ratio in joined
    assert "`{colors.faint}` — #94a3b8 text on `{colors.canvas}` — #ffffff" in joined
    lowered = text.lower()
    assert "legible" not in lowered and "readable" not in lowered
    vi = render_design_md(load_analysis_sample(), lang="vi").lower()
    assert "dễ đọc" not in vi


def test_contrast_table_flags_failing_pairs() -> None:
    contrast = _section(render_design_md(load_analysis_sample()), "Colors").split("### Contrast")[1]
    rows = [line for line in contrast.splitlines() if line.startswith("| ") and "---" not in line]
    failing = [row for row in rows if "✗ fails AA" in row]
    assert len(failing) == 3
    assert any("2.56:1" in row and "(large text only)" not in row for row in failing)
    assert all("✗" not in row for row in rows if "17.85:1" in row)


def test_breakpoint_table_lists_only_measured_widths() -> None:
    layout = _section(render_design_md(load_analysis_sample()), "Layout")
    table = layout.split("### Responsive Strategy")[1].split("\n\n")[2]
    widths = re.findall(r"≥ (\d+)px", table)
    assert widths == ["640", "768", "1024", "1280"]


def test_decorative_colors_are_not_tokens() -> None:
    text = render_design_md(load_analysis_sample())
    data, _ = split_front_matter(text)
    assert "#6366f12e" not in data["colors"].values()
    assert "### Decorative Accents" in text and "#6366f12e" in _section(text, "Colors")


def test_examples_section_only_with_flag() -> None:
    plain = render_design_md(load_analysis_sample())
    assert "ex-hero" not in plain
    text = render_design_md(load_analysis_sample(), examples=True)
    components = _section(text, "Components")
    assert "### Example Surfaces" in components
    assert "`ex-hero`" in components and "(contrast " in components


def test_vietnamese_prose_with_english_structure() -> None:
    text = render_design_md(load_analysis_sample(), lang="vi")
    assert section_titles(text) == list(DESIGN_SECTIONS)
    assert "### Đặc điểm chính" in text
    assert "- Nên dành `{colors.primary}` — #4f46e5" in text
    assert "- Không nên đặt chữ `{colors.faint}` — #94a3b8" in text
    assert "giao diện sáng" in text
    data, _ = split_front_matter(text)
    assert data["colors"]["primary"] == "#4f46e5"


# ─── Narrative rules ─────────────────────────────────────────────────────────


def _set_token(analysis: DesignAnalysis, name: str, value: str) -> None:
    for token in analysis.colors.tokens:
        if token.name == name:
            token.value = value


def test_rule_canvas_lightness() -> None:
    analysis = load_analysis_sample()
    assert "bright white canvas" in render_design_md(analysis)
    _set_token(analysis, "canvas", "#08090a")
    analysis.colors.scheme = "dark"
    text = render_design_md(analysis)
    assert "dark interface built on a near-black canvas" in text


def test_rule_heavy_weights_only() -> None:
    analysis = load_analysis_sample()
    analysis.typography.weights_used = [600, 700]
    text = render_design_md(analysis)
    assert "heavy weights only — no regular 400" in text.lower()
    assert "Don't fall back to regular 400" in text


def test_rule_action_signals() -> None:
    analysis = load_analysis_sample()
    assert "a single indigo accent (#4f46e5)" in render_design_md(analysis)
    analysis.components[1].properties["backgroundColor"] = "#16a34a"
    analysis.components[1].token_refs["backgroundColor"] = "{colors.success}"
    text = render_design_md(analysis)
    assert "two-signal action system" in text
    assert "Don't add a second accent" not in text


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({"xl": 20}, "Corners are soft and rounded"),
        ({"full": 20}, "Corners are soft and rounded, with pill-shaped controls"),
        ({"sm": 20}, "Corners are moderately rounded"),
    ],
)
def test_rule_radius_softness(counts: dict[str, int], expected: str) -> None:
    analysis = load_analysis_sample()
    for step in analysis.radii:
        step.count = counts.get(step.name, 1)
    assert expected in render_design_md(analysis)


def test_rule_sharp_corners() -> None:
    analysis = load_analysis_sample()
    analysis.radii = [ScaleStep(name="xs", value_px=2, count=9), ScaleStep(name="sm", value_px=4)]
    analysis.components = []
    text = render_design_md(analysis)
    assert "Corners are sharp and precise" in text
    assert "sharp, precise corners" in text


def test_rule_no_shadows_means_tonal_depth() -> None:
    analysis = load_analysis_sample()
    analysis.shadows = []
    text = render_design_md(analysis)
    assert "depth comes from tonal surface steps, not shadows" in text
    assert "Don't add drop shadows" in text


def test_rule_hamburger_breakpoint() -> None:
    text = render_design_md(load_analysis_sample())
    assert "The top nav collapses to a hamburger below 1024px" in text
    assert "Do collapse the top nav into a hamburger menu below 1024px." in text


def test_change_without_params_uses_fallback_phrase() -> None:
    analysis = load_analysis_sample()
    for change in analysis.layout.changes:
        change.params = {}
    text = render_design_md(analysis)
    assert "The top nav collapses to a hamburger between 1440px and 768px." in text
    assert "The main grid loses columns between 1440px and 768px." in text
    assert _errors(text) == []


def test_site_text_is_escaped() -> None:
    analysis = load_analysis_sample()
    analysis.components[0].samples = ["Use {colors.fake} | now", "*bold* `code`"]
    analysis.colors.contrast_pairs[0].context = "a | b {typography.nope}"
    analysis.site.name = "Acme_Cloud"
    text = render_design_md(analysis)
    assert _errors(text) == []
    assert "\\{colors.fake\\} \\| now" in text
    assert "Acme\\_Cloud — Design Reference" in text


def test_unknown_language_raises() -> None:
    with pytest.raises(UnsupportedLanguageError):
        render_design_md(load_analysis_sample(), lang="fr")


def test_build_sections_matches_render() -> None:
    sections = build_sections(load_analysis_sample(), load_phrases("en"))
    assert [section.title for section in sections] == list(DESIGN_SECTIONS)
    assert all(not section.body.startswith("## ") for section in sections)


# ─── Partial analyses ────────────────────────────────────────────────────────


def test_empty_analysis_renders_and_validates() -> None:
    text = render_design_md(_empty_analysis())
    assert section_titles(text) == ["Overview"]
    assert _errors(text) == []
    data, _ = split_front_matter(text)
    assert data == {
        "version": "alpha",
        "name": "Empty Analysis",
        "description": data["description"],
    }
    assert "Not measured in this capture" in text


def _drop_colors(a: DesignAnalysis) -> None:
    a.colors.tokens = []
    a.colors.contrast_pairs = []
    a.dark = None


def _drop_typography(a: DesignAnalysis) -> None:
    a.typography.levels = []
    a.typography.families = []
    a.typography.weights_used = []


def _drop_layout(a: DesignAnalysis) -> None:
    a.spacing.steps = []
    a.spacing.base_unit_px = None
    a.layout.snapshots = []
    a.layout.changes = []
    a.breakpoints = []


def _drop_shapes(a: DesignAnalysis) -> None:
    a.radii = []
    a.shadows = []


def _drop_components(a: DesignAnalysis) -> None:
    a.components = []


def _only_colors(a: DesignAnalysis) -> None:
    for drop in (_drop_typography, _drop_layout, _drop_shapes, _drop_components):
        drop(a)
    a.layout.shell = "unknown"
    a.layout.container_max_width_px = None
    a.layout.gutter_px = None
    a.spacing.container_max_width_px = None
    a.spacing.section_gap_px = None
    a.spacing.common_paddings = []


@pytest.mark.parametrize(
    "mutate",
    [_drop_colors, _drop_typography, _drop_layout, _drop_shapes, _drop_components, _only_colors],
)
@pytest.mark.parametrize("lang", ["en", "vi"])
def test_partial_analyses_degrade_gracefully(
    mutate: Callable[[DesignAnalysis], None], lang: str
) -> None:
    analysis = load_analysis_sample()
    mutate(analysis)
    text = render_design_md(analysis, lang=lang, examples=True)
    assert _errors(text) == []
    titles = section_titles(text)
    assert titles == [title for title in DESIGN_SECTIONS if title in titles]
    yaml.safe_load(text.split("---\n")[1])


def test_only_colors_keeps_relevant_sections() -> None:
    analysis = load_analysis_sample()
    _only_colors(analysis)
    titles = section_titles(render_design_md(analysis))
    assert titles == [
        "Overview",
        "Colors",
        "Elevation & Depth",
        "Do's and Don'ts",
        "Agent Prompt Guide",
    ]


def test_unusual_component_names_and_states() -> None:
    analysis = load_analysis_sample()
    analysis.components.append(
        ComponentSpec(
            name="chip",
            kind=ComponentKind.BADGE,
            variant="filter",
            count=2,
            properties={"backgroundColor": "#f1f5f9", "textColor": "#94a3b8", "rounded": "4px"},
            states={"focus": {"textColor": "#0f172a"}},
            contrast_ratio=2.4,
        )
    )
    analysis.colors.tokens.append(
        ColorToken(name="warning", value="#f59e0b", role=ColorRole.WARNING)
    )
    text = render_design_md(analysis)
    data, _ = split_front_matter(text)
    assert data["components"]["chip-focus"] == {"textColor": "#0f172a"}
    # Ratios are recomputed from the printed colors (2.34:1), not the analyzer's 2.40.
    assert "### chip" in text and "⚠ Text contrast: #94a3b8 on #f1f5f9: 2.34:1" in text
    assert "#94a3b8 on #f1f5f9 (2.34:1, chip)" in text
    assert _errors(text) == []


# ─── Goldens ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "lang", "examples"),
    [("acme-en.md", "en", False), ("acme-vi.md", "vi", False), ("acme-en-examples.md", "en", True)],
)
def test_golden_snapshots(name: str, lang: str, examples: bool) -> None:
    text = render_design_md(load_analysis_sample(), lang=lang, examples=examples)
    path = GOLDEN_DIR / name
    if os.environ.get(UPDATE_ENV) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    assert path.is_file(), f"Missing golden {path}; run with {UPDATE_ENV}=1"
    expected = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert text == expected
