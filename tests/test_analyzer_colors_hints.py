"""Role hints of the color analyzer: variable names, class words, status symbols."""

from __future__ import annotations

import pytest

from get_design_markdown.analyzer import analyze
from get_design_markdown.analyzer.colors_hints import (
    action_hint,
    collect_color_variables,
    is_generated_name,
    name_words,
    parse_variable_color,
    status_from_text,
    status_from_words,
    variable_hints,
)
from get_design_markdown.models.raw import CssVariable, StylesheetInfo
from tests.conftest import FIXED_TIME
from tests.fixture_data import load_raw_sample, load_site_contract


def test_name_words() -> None:
    assert name_words("--color-textPrimary_2") == ["color", "text", "primary", "2"]
    assert name_words("--bs-body-bg") == ["bs", "body", "bg"]
    assert name_words("") == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("--color-primary", {"primary"}),
        ("--brand-500", {"primary"}),
        ("--accent", {"primary"}),
        ("--color-secondary", {"secondary"}),
        ("--bg-canvas", {"canvas"}),
        ("--background", {"canvas"}),
        ("--bs-body-bg", {"canvas"}),
        ("--color-bg-primary", {"canvas"}),
        ("--color-bg-secondary", {"surface"}),
        ("--card", {"surface"}),
        ("--surface-raised", {"surface-raised"}),
        ("--popover", {"surface-raised"}),
        ("--bg-inverse", {"surface-inverse"}),
        ("--foreground", {"ink"}),
        ("--color-text-primary", {"ink"}),
        ("--card-foreground", {"ink"}),
        ("--bs-body-color", {"ink"}),
        ("--text-muted", {"muted"}),
        ("--color-text-secondary", {"muted"}),
        ("--muted-foreground", {"muted"}),
        ("--color-text-tertiary", {"faint"}),
        ("--text-placeholder", {"faint"}),
        ("--text-inverse", {"on-inverse"}),
        ("--primary-foreground", {"on-primary"}),
        ("--color-on-primary", {"on-primary"}),
        ("--secondary-foreground", {"on-secondary"}),
        ("--border", {"hairline"}),
        ("--border-subtle", {"hairline"}),
        ("--color-divider", {"hairline"}),
        ("--border-strong", {"hairline-strong"}),
        ("--color-brand-bg", {"primary"}),
        ("--hds-color-accent-border-solid", {"primary"}),
        ("--accent-bg", {"primary"}),
        ("--color-success", {"success"}),
        ("--bs-danger-rgb", {"danger"}),
        ("--destructive", {"danger"}),
        ("--text-error", {"danger"}),
        ("--color-warning", {"warning"}),
        ("--info", {"info"}),
        ("--hds-color-button-primary-bg", {"primary"}),
        ("--btn-secondary", {"secondary"}),
        ("--cta-primary-background", {"primary"}),
        ("--hds-color-button-primary-text", {"on-primary"}),
        ("--button-secondary-fg", {"on-secondary"}),
        ("--hds-color-action-text-secondary", {"muted"}),
        ("--hds-color-button-secondary-borderDisabled", {"hairline"}),
        ("--color-button-invert-bg", {"surface-inverse"}),
        ("--color-fg-primary", {"ink"}),
        ("--color-fg-secondary", {"muted"}),
        ("--color-fg-tertiary", {"faint"}),
        ("--color-fg-quaternary", {"faint"}),
        ("--primary-contrast", {"on-primary"}),
        ("--blue-500", set()),
        ("--color-primary-hover", set()),
        ("--button-primary-bg-hover", set()),
        ("--link-active", set()),
        ("--radius-md", set()),
    ],
)
def test_variable_hints(name: str, expected: set[str]) -> None:
    assert set(variable_hints(name)) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("--hds-color-button-primary-bg", "primary"),
        ("--btn-primary", "primary"),
        ("--cta-secondary", "secondary"),
        ("--action-tertiary-fill", "tertiary"),
        ("--button-primary-text", None),
        ("--button-primary-border", None),
        ("--button-primary-bg-hover", None),
        ("--text-on-button-primary", None),
        ("--brand-500", None),
        ("--color-primary", None),
        ("--color-bg-primary", None),
    ],
)
def test_action_hint(name: str, expected: str | None) -> None:
    assert action_hint(name) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#635bff", "#635bff"),
        ("rgb(99, 91, 255)", "#635bff"),
        ("99 91 255", "#635bff"),
        ("99, 91, 255", "#635bff"),
        ("0 0% 100%", "#ffffff"),
        ("222.2 84% 4.9%", "#020817"),
        ("rgba(255, 255, 255, 0.08)", "#ffffff14"),
        ("var(--x)", None),
        ("linear-gradient(red, blue)", None),
        ("8px", None),
        ("10px 20px 30px", None),
        ("300 20 20", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_variable_color(value: str | None, expected: str | None) -> None:
    color = parse_variable_color(value)
    assert (None if color is None else color.css_hex) == expected


def test_is_generated_name() -> None:
    assert is_generated_name("--sx-1gcjx5j")
    assert is_generated_name("--x8f3a2b")
    assert not is_generated_name("--brand-500")
    assert not is_generated_name("--gray-a3")
    assert not is_generated_name("--space-2xl")
    assert not is_generated_name("--hds-color-core-neutralDark-990")


def test_status_from_words_and_text() -> None:
    assert status_from_words(["alert", "danger"]) == "danger"
    assert status_from_words(["is", "invalid"]) == "danger"
    assert status_from_words(["valid"]) == "success"
    assert status_from_words(["warn"]) == "warning"
    assert status_from_words(["notice"]) == "info"
    assert status_from_words(["primary", "button"]) is None
    assert status_from_text("✓ No credit card required") == "success"
    assert status_from_text("⚠ Careful") == "warning"
    assert status_from_text("✕ Failed") == "danger"
    assert status_from_text("Customer success stories") is None


def test_collect_color_variables_prefers_page_values() -> None:
    page_variables = [
        CssVariable(name="--color-primary", declared="#818cf8", resolved=None, source="root"),
        CssVariable(name="--space-4", declared="1rem", resolved="16px", source="root"),
    ]
    sheet = StylesheetInfo(
        url="https://x.test/a.css",
        variables=[
            CssVariable(name="--color-primary", declared="#4f46e5", source="stylesheet"),
            CssVariable(name="--color-border", declared="226 232 240", source="stylesheet"),
            CssVariable(name="--color-border", declared="226 232 240", source="stylesheet"),
            CssVariable(name="--clear", declared="transparent", source="stylesheet"),
        ],
    )
    variables = collect_color_variables(page_variables, [sheet])
    assert [(v.name, v.color.css_hex) for v in variables] == [
        ("--color-primary", "#818cf8"),
        ("--color-border", "#e2e8f0"),
    ]
    assert variables[1].hints == frozenset({"hairline"})
    assert variables[0].action is None


def test_collect_color_variables_marks_action_variables() -> None:
    variables = collect_color_variables(
        [CssVariable(name="--button-primary-bg", declared="#533afd", source="root")], []
    )
    assert [(v.name, v.hints, v.action) for v in variables] == [
        ("--button-primary-bg", frozenset({"primary"}), "primary")
    ]


def test_analyze_capture_uses_real_color_analyzer() -> None:
    """The frozen orchestrator runs analyze_colors on the light pages and the dark capture."""
    result = analyze.analyze_capture(load_raw_sample(), now=FIXED_TIME)
    contract = load_site_contract()
    assert {token.name: token.value for token in result.colors.tokens} == contract.palette
    assert result.dark is not None
    assert {token.name: token.value for token in result.dark.colors.tokens} == (
        contract.dark_palette
    )
    assert result.stats.color_clusters == len(result.colors.clusters)
