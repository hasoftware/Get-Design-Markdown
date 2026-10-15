"""validate_design_md / split_front_matter on crafted documents."""

from __future__ import annotations

from get_design_markdown.models.synthesis import ValidationIssue
from get_design_markdown.synthesizer.validate import (
    body_sections,
    split_front_matter,
    validate_design_md,
)

FRONT = """---
version: alpha
name: Test Analysis
colors:
  primary: "#4f46e5"
  on-primary: "#ffffff"
  canvas: "#ffffff"
  faint: "#94a3b8"
typography:
  body:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.6
rounded:
  md: 8px
spacing:
  md: 16px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "{spacing.md}"
  button-primary-hover:
    backgroundColor: "#4338ca"
---
"""
BODY = """
# Test — Design Reference

## Overview

Uses `{colors.primary}` — #4f46e5.

## Colors

Text.
"""


def _rules(text: str, severity: str | None = None) -> list[str]:
    return [
        issue.rule
        for issue in validate_design_md(text)
        if severity is None or issue.severity == severity
    ]


def _messages(issues: list[ValidationIssue], rule: str) -> list[str]:
    return [issue.message for issue in issues if issue.rule == rule]


def test_valid_document_has_only_info() -> None:
    issues = validate_design_md(FRONT + BODY)
    assert {issue.severity for issue in issues} == {"info"}
    assert "token-summary" in [issue.rule for issue in issues]
    summary = _messages(issues, "token-summary")[0]
    assert summary == (
        "DESIGN.md định nghĩa 4 màu, 1 cấp chữ, 1 mức bo góc, 1 bước khoảng cách, 2 component."
    )
    assert _messages(issues, "orphaned-tokens") == [
        "Token màu chưa được component nào tham chiếu: canvas, faint."
    ]


def test_missing_and_invalid_front_matter() -> None:
    assert _rules("# Title\n\n## Overview\n") == ["front-matter"]
    assert "front-matter" in _rules("---\nversion: alpha\nname: x\n# no closing line\n")
    invalid = validate_design_md("---\nversion: [alpha\n---\n# x\n")
    assert invalid[0].rule == "front-matter" and "YAML" in invalid[0].message
    assert _rules("---\n- a\n- b\n---\n", "error") == ["front-matter"]


def test_version_name_and_group_shapes() -> None:
    text = FRONT.replace("version: alpha", "version: beta").replace("name: Test Analysis\n", "")
    messages = _messages(validate_design_md(text + BODY), "front-matter")
    assert any("'alpha'" in message for message in messages)
    assert any("thiếu trường name" in message for message in messages)
    bad_group = FRONT.replace("rounded:\n  md: 8px\n", "rounded: 8px\n")
    assert "front-matter" in _rules(bad_group + BODY, "error")


def test_token_values_rejected_like_the_official_linter() -> None:
    text = (
        FRONT.replace('faint: "#94a3b8"', "faint: notacolor")
        .replace("md: 8px\nspacing", "md: 8pt\nspacing")
        .replace("fontWeight: 400", "fontWeight: bold")
        .replace("fontSize: 16px", "fontSize: large")
    )
    errors = _messages(validate_design_md(text + BODY), "token-value")
    assert len(errors) == 4
    assert any("colors.faint" in message for message in errors)
    assert any("rounded.md" in message for message in errors)
    assert any("fontWeight" in message for message in errors)
    assert any("fontSize" in message for message in errors)


def test_broken_references_in_front_matter_and_body() -> None:
    text = FRONT.replace("{colors.on-primary}", "{colors.white}") + BODY.replace(
        "Text.", "See `{rounded.xl}` and {colors.primary}.\n\n```css\n/* {spacing.xxl} */\n```"
    )
    messages = _messages(validate_design_md(text), "broken-ref")
    assert any(
        "{colors.white}" in m and "components.button-primary.textColor" in m for m in messages
    )
    assert any("{rounded.xl}" in m and m.startswith("Dòng ") for m in messages)
    assert any("{spacing.xxl}" in m for m in messages)
    assert not any("{colors.primary}" in m for m in messages)


def test_components_may_not_reference_components() -> None:
    text = FRONT.replace(
        'backgroundColor: "#4338ca"', 'backgroundColor: "{components.button-primary}"'
    )
    assert "broken-ref" in _rules(text + BODY, "error")


def test_section_order_duplicates_and_aliases() -> None:
    wrong = FRONT + "\n## Colors\n\nx\n\n## Overview\n\ny\n"
    assert _rules(wrong, "error") == ["section-order"]
    dup = FRONT + "\n## Overview\n\n## Colors\n\n## Colors\n"
    assert _rules(dup, "error") == ["duplicate-section"]
    alias = (
        FRONT
        + "\n## Brand & Style\n\n## Layout & Spacing\n\n## Elevation\n\n## Agent Prompt Guide\n"
    )
    assert _rules(alias, "error") == []
    alias_dup = FRONT + "\n## Overview\n\n## Brand & Style\n"
    assert _rules(alias_dup, "error") == ["duplicate-section"]
    unknown = FRONT + "\n## Overview\n\n## Iconography\n\n## Colors\n"
    assert _rules(unknown, "error") == []
    assert "unknown-section" in _rules(unknown, "info")


def test_headings_inside_fences_are_ignored() -> None:
    body = (
        "\n## Overview\n\n```markdown\n## Colors\n## Overview\n```\n\n"
        "~~~~\n## Shapes\n~~~\n## Layout\n~~~~\n\n## Colors\n"
    )
    assert body_sections(body) == ["Overview", "Colors"]
    assert _rules(FRONT + body, "error") == []


def test_yaml_blocks_in_the_body() -> None:
    clash = FRONT + BODY + '\n```yaml\ncolors:\n  primary: "#000000"\n```\n'
    assert _rules(clash, "error") == ["yaml-block"]
    harmless = FRONT + BODY + "\n```yml\nexample: 1\n```\n"
    assert "yaml-block" in _rules(harmless, "warning")
    assert _rules(FRONT + BODY + "\n```css\n:root { --x: 1px; }\n```\n", "error") == []


def test_missing_primary_and_typography_warnings() -> None:
    text = FRONT.replace('  primary: "#4f46e5"\n', "").replace("{colors.primary}", "#4f46e5")
    assert "missing-primary" in _rules(text + BODY.replace("`{colors.primary}`", "x"), "warning")
    no_type = FRONT.replace(
        "typography:\n  body:\n    fontFamily: Inter\n    fontSize: 16px\n    fontWeight: 400\n"
        "    lineHeight: 1.6\n",
        "",
    ).replace('    typography: "{typography.body}"\n', "")
    issues = validate_design_md(no_type + BODY)
    assert "missing-typography" in [issue.rule for issue in issues]


def test_contrast_warning_including_inherited_hover_colors() -> None:
    low = FRONT.replace('on-primary: "#ffffff"', 'on-primary: "#6366f1"')
    messages = _messages(validate_design_md(low + BODY), "contrast-ratio")
    assert any("components.button-primary:" in m for m in messages)
    assert any("button-primary-hover (kế thừa màu từ button-primary)" in m for m in messages)
    assert all("4.5:1" in m for m in messages)


def test_unknown_component_property_is_a_warning() -> None:
    text = FRONT.replace(
        '    padding: "{spacing.md}"\n', '    padding: "{spacing.md}"\n    borderColor: "#000000"\n'
    )
    issues = validate_design_md(text + BODY)
    assert any(i.rule == "unknown-property" and i.severity == "warning" for i in issues)


def test_crlf_input() -> None:
    text = (FRONT + BODY).replace("\n", "\r\n")
    assert _rules(text, "error") == []
    data, body = split_front_matter(text)
    assert data["name"] == "Test Analysis" and "## Overview" in body


def test_split_front_matter() -> None:
    data, body = split_front_matter(FRONT + BODY)
    assert data["version"] == "alpha"
    assert body.startswith("\n# Test")
    assert split_front_matter("# no front matter") == ({}, "# no front matter")
    broken = "---\nversion: [alpha\n---\nbody"
    assert split_front_matter(broken) == ({}, broken)


def test_messages_are_vietnamese_and_markup_safe() -> None:
    text = "---\nversion: beta\n---\n## Colors\n\n## Overview\n\n{colors.x}\n"
    for issue in validate_design_md(text):
        assert "[" not in issue.message
        assert any(
            ch in issue.message for ch in "ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"
        )
