"""Front matter: spec groups, component properties, hover variants, YAML dump."""

from __future__ import annotations

import re
from typing import Any

import yaml

from get_design_markdown.analyzer.naming import TOKEN_REF_RE, parse_token_ref
from get_design_markdown.models.analysis import ColorRole, ColorToken, ComponentKind, ComponentSpec
from get_design_markdown.synthesizer.catalog import load_phrases
from get_design_markdown.synthesizer.facts import SPEC_COMPONENT_KEYS
from get_design_markdown.synthesizer.frontmatter import build_front_matter, dump_front_matter
from tests.fixture_data import load_analysis_sample

GROUPS = ("colors", "typography", "rounded", "spacing")


def _defined(data: dict[str, Any]) -> set[tuple[str, str]]:
    return {(group, name) for group in GROUPS for name in data.get(group, {})}


def _all_refs(data: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for props in data.get("components", {}).values():
        refs.extend(value for value in props.values() if TOKEN_REF_RE.search(str(value)))
    return refs


def test_header_and_token_groups_match_the_analysis() -> None:
    analysis = load_analysis_sample()
    data = build_front_matter(analysis)
    assert list(data)[:3] == ["version", "name", "description"]
    assert data["version"] == "alpha"
    assert data["name"] == "Acme Cloud Analysis"
    assert data["description"].startswith("Measured design language of Acme Cloud")
    assert data["colors"] == {token.name: token.value for token in analysis.colors.tokens}
    assert set(data["typography"]) == {level.name for level in analysis.typography.levels}
    assert data["typography"]["display"] == {
        "fontFamily": "Space Grotesk",
        "fontSize": "64px",
        "fontWeight": 700,
        "lineHeight": 1.05,
        "letterSpacing": "-1.5px",
    }
    assert "letterSpacing" not in data["typography"]["body"]
    assert data["typography"]["body"]["lineHeight"] == 1.6
    assert data["rounded"] == {
        "sm": "6px",
        "md": "8px",
        "lg": "12px",
        "xl": "16px",
        "full": "9999px",
    }
    assert data["spacing"] == {step.name: f"{step.value_px:g}px" for step in analysis.spacing.steps}


def test_components_use_spec_keys_and_token_refs() -> None:
    data = build_front_matter(load_analysis_sample())
    components = data["components"]
    assert components["button-primary"] == {
        "backgroundColor": "{colors.primary}",
        "textColor": "{colors.on-primary}",
        "typography": "{typography.ui}",
        "rounded": "{rounded.md}",
        "padding": "12px 20px",
        "height": "44px",
    }
    assert components["button-primary-hover"] == {"backgroundColor": "#4338ca"}
    assert components["button-outline-hover"] == {"backgroundColor": "{colors.surface}"}
    assert components["text-link-hover"] == {"textColor": "#4338ca"}
    assert components["nav-link-hover"] == {"textColor": "{colors.ink}"}
    assert components["card"]["padding"] == "{spacing.xxl}"
    assert components["top-nav"]["backgroundColor"] == "#ffffffcc"
    assert "text-input-hover" not in components
    for props in components.values():
        assert set(props) <= set(SPEC_COMPONENT_KEYS)
        assert "transparent" not in props.values()
    names = list(components)
    for name in names:
        if name.endswith("-hover"):
            assert names[names.index(name) - 1] == name.removesuffix("-hover")


def test_every_reference_resolves() -> None:
    data = build_front_matter(load_analysis_sample(), examples=True)
    defined = _defined(data)
    for ref in _all_refs(data):
        parsed = parse_token_ref(ref)
        assert parsed is not None and parsed in defined, ref


def test_unknown_token_refs_fall_back_to_raw_values() -> None:
    analysis = load_analysis_sample()
    analysis.components = [
        ComponentSpec(
            name="button-primary",
            kind=ComponentKind.BUTTON,
            properties={"backgroundColor": "#123456", "rounded": "7px"},
            token_refs={
                "backgroundColor": "{colors.brand}",
                "rounded": "{rounded.huge}",
                "typography": "{typography.missing}",
                "textColor": "{components.card}",
            },
        )
    ]
    data = build_front_matter(analysis)
    assert data["components"] == {
        "button-primary": {"backgroundColor": "#123456", "rounded": "7px"}
    }


def test_hover_that_changes_nothing_spec_relevant_is_omitted() -> None:
    analysis = load_analysis_sample()
    analysis.components = [
        ComponentSpec(
            name="button-primary",
            kind=ComponentKind.BUTTON,
            properties={"backgroundColor": "#4f46e5", "textColor": "#ffffff"},
            token_refs={"backgroundColor": "{colors.primary}"},
            states={"hover": {"backgroundColor": "#4f46e5", "borderColor": "#000000"}},
        ),
        ComponentSpec(
            name="button-ghost",
            kind=ComponentKind.BUTTON,
            properties={"backgroundColor": "#eef2ff", "textColor": "#4f46e5"},
            states={"hover": {"backgroundColor": "transparent"}},
        ),
    ]
    components = build_front_matter(analysis)["components"]
    assert "button-primary-hover" not in components
    assert components["button-ghost-hover"] == {"backgroundColor": "transparent"}


def test_translucent_tokens_keep_eight_digit_hex() -> None:
    analysis = load_analysis_sample()
    analysis.colors.tokens.append(
        ColorToken(name="surface-raised", value="#ffffffcc", role=ColorRole.SURFACE)
    )
    assert build_front_matter(analysis)["colors"]["surface-raised"] == "#ffffffcc"


def test_examples_are_opt_in_and_pass_contrast() -> None:
    analysis = load_analysis_sample()
    assert not [n for n in build_front_matter(analysis)["components"] if n.startswith("ex-")]
    components = build_front_matter(analysis, examples=True)["components"]
    examples = [name for name in components if name.startswith("ex-")]
    assert examples == ["ex-hero", "ex-feature-card", "ex-pricing-tier", "ex-signup-form"]
    for name in examples:
        assert all(TOKEN_REF_RE.fullmatch(value) for value in components[name].values())


def test_examples_with_low_contrast_are_dropped() -> None:
    analysis = load_analysis_sample()
    for token in analysis.colors.tokens:
        if token.name == "ink":
            token.value = "#f1f5f9"
    components = build_front_matter(analysis, examples=True)["components"]
    assert not [name for name in components if name.startswith("ex-")]


def test_description_follows_the_phrase_language() -> None:
    data = build_front_matter(load_analysis_sample(), phrases=load_phrases("vi"))
    assert data["description"].startswith("Ngôn ngữ thiết kế đo được của Acme Cloud")


def test_dump_round_trips_and_quotes_hex_and_refs() -> None:
    data = build_front_matter(load_analysis_sample(), examples=True)
    text = dump_front_matter(data)
    assert yaml.safe_load(text) == data
    assert text.endswith("\n")
    for line in text.splitlines():
        value = line.split(": ", 1)[1] if ": " in line else ""
        if re.fullmatch(r"#[0-9a-f]{6,8}", value.strip("\"'")) or TOKEN_REF_RE.search(value):
            assert value.startswith('"') and value.endswith('"'), line
    assert "&" not in text and "*id" not in text


def test_dump_is_deterministic_and_keeps_unicode() -> None:
    data = build_front_matter(load_analysis_sample(), phrases=load_phrases("vi"))
    first = dump_front_matter(data)
    assert first == dump_front_matter(
        build_front_matter(load_analysis_sample(), phrases=load_phrases("vi"))
    )
    assert "Ngôn ngữ" in first
    assert "\\u" not in first
