"""YAML front matter of DESIGN.md per the Google Labs DESIGN.md spec (version: alpha).

Groups (each omitted when empty): colors {token: "#hex"} (alpha kept as #rrggbbaa),
typography {level: {fontFamily, fontSize, fontWeight, lineHeight (unitless), letterSpacing
(only when != 0)}}, rounded {step: "Npx"} ("full" -> "9999px"), spacing {step: "Npx"},
components {name: spec properties} with "<name>-hover" right after its component.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from get_design_markdown.analyzer.naming import TOKEN_REF_RE
from get_design_markdown.models.analysis import DesignAnalysis, TypeLevel
from get_design_markdown.synthesizer.catalog import Phrases, load_phrases
from get_design_markdown.synthesizer.examples import build_examples
from get_design_markdown.synthesizer.facts import DesignFacts
from get_design_markdown.synthesizer.formatting import fmt_px
from get_design_markdown.synthesizer.traits import description

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def front_matter_name(analysis: DesignAnalysis) -> str:
    site = analysis.site
    return f"{(site.name.strip() or site.url).strip()} Analysis"


def typography_entry(facts: DesignFacts, level: TypeLevel) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "fontFamily": level.font_family,
        "fontSize": fmt_px(level.size_px),
        "fontWeight": int(level.weight),
    }
    ratio = facts.line_height_ratio(level)
    if ratio is not None:
        entry["lineHeight"] = ratio
    if round(level.letter_spacing_px, 2) != 0:
        entry["letterSpacing"] = fmt_px(level.letter_spacing_px)
    return entry


def build_front_matter_from_facts(
    facts: DesignFacts, phrases: Phrases, *, examples: bool = False
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": "alpha",
        "name": front_matter_name(facts.analysis),
        "description": description(facts, phrases),
    }
    if facts.colors:
        data["colors"] = {name: token.value for name, token in facts.colors.items()}
    if facts.levels:
        data["typography"] = {
            name: typography_entry(facts, level) for name, level in facts.levels.items()
        }
    if facts.radii:
        data["rounded"] = {name: facts.radius_value(step) for name, step in facts.radii.items()}
    if facts.spacing:
        data["spacing"] = {name: fmt_px(step.value_px) for name, step in facts.spacing.items()}
    components = facts.front_matter_components()
    if examples:
        for example in build_examples(facts):
            components.setdefault(example.name, dict(example.props))
    if components:
        data["components"] = components
    return data


def build_front_matter(
    analysis: DesignAnalysis, *, examples: bool = False, phrases: Phrases | None = None
) -> dict[str, Any]:
    """{"version": "alpha", "name", "description", "colors", "typography", "rounded",
    "spacing", "components"} — components include "<name>-hover" state variants and only the
    spec properties (backgroundColor, textColor, typography, rounded, padding, size, height,
    width); values are token references when ComponentSpec.token_refs has one.

    `phrases` sets the language of `description` (English by default).
    """
    return build_front_matter_from_facts(
        DesignFacts(analysis), phrases or load_phrases("en"), examples=examples
    )


class _QuotedStr(str):
    """A string that must be written in double quotes (hex colors, token references)."""


class _FrontMatterDumper(yaml.SafeDumper):
    """SafeDumper that never emits anchors/aliases."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _represent_quoted(dumper: yaml.SafeDumper, data: _QuotedStr) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


_FrontMatterDumper.add_representer(_QuotedStr, _represent_quoted)


def _needs_quotes(value: str) -> bool:
    return bool(_HEX_RE.match(value)) or TOKEN_REF_RE.search(value) is not None


def _mark(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _mark(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mark(item) for item in value]
    if isinstance(value, str) and _needs_quotes(value):
        return _QuotedStr(value)
    return value


def dump_front_matter(data: dict[str, Any]) -> str:
    """YAML text between "---" lines, keys in insertion order, hex colors quoted."""
    text = yaml.dump(
        _mark(data),
        Dumper=_FrontMatterDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1_000_000,
        indent=2,
    )
    return text if text.endswith("\n") else text + "\n"
