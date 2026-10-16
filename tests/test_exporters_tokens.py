"""exporters/tokens.py + exporters/dtcg.py: W3C Design Tokens (DTCG 2025.10) export."""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from get_design_markdown.analyzer.css_values import RGBA
from get_design_markdown.exporters.dtcg import (
    EXTENSION_KEY,
    color_value,
    dimension,
    iter_tokens,
    number,
    token_name,
    validate_tokens,
)
from get_design_markdown.exporters.tokens import build_tokens
from get_design_markdown.models.analysis import (
    Breakpoint,
    ColorPalette,
    ColorRole,
    ColorToken,
    ComponentKind,
    ComponentSpec,
    DesignAnalysis,
    FontFamilyInfo,
    FontRole,
    ScaleStep,
    ShadowLevel,
    SiteInfo,
    ThemeAnalysis,
    TypeLevel,
    Typography,
)
from tests.conftest import FIXED_TIME
from tests.fixture_data import load_analysis_sample

GOLDEN = Path(__file__).parent / "golden" / "exporters" / "tokens.json"
UPDATE_GOLDEN = os.environ.get("GDM_UPDATE_GOLDEN") == "1"


@pytest.fixture(scope="module")
def tokens() -> dict[str, Any]:
    return build_tokens(load_analysis_sample())


def _empty_analysis() -> DesignAnalysis:
    return DesignAnalysis(
        tool_version="0.1.0",
        analyzed_at=FIXED_TIME,
        site=SiteInfo(url="https://empty.example/", name="Empty"),
    )


def _walk_names(node: Mapping[str, Any]) -> Iterator[str]:
    for key, child in node.items():
        if key.startswith("$"):
            continue
        yield key
        if isinstance(child, Mapping) and "$value" not in child:
            yield from _walk_names(child)


# ─── Sample document ─────────────────────────────────────────────────────────


def test_tokens_are_valid_dtcg(tokens: dict[str, Any]) -> None:
    assert validate_tokens(tokens) == []
    paths = [path for path, _, _ in iter_tokens(tokens)]
    assert len(paths) > 100
    for name in _walk_names(tokens):
        assert not name.startswith("$")
        assert not any(char in name for char in "{}.")


def test_tokens_are_json_serializable_and_deterministic(tokens: dict[str, Any]) -> None:
    text = json.dumps(tokens, indent=2, ensure_ascii=False)
    assert json.loads(text) == tokens
    again = json.dumps(build_tokens(load_analysis_sample()), indent=2, ensure_ascii=False)
    assert again == text


def test_tokens_match_golden(tokens: dict[str, Any]) -> None:
    text = json.dumps(tokens, indent=2, ensure_ascii=False) + "\n"
    if UPDATE_GOLDEN:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8", newline="\n")
    assert GOLDEN.read_text(encoding="utf-8") == text


def test_root_metadata(tokens: dict[str, Any]) -> None:
    assert tokens["$description"] == (
        "Design tokens extracted from https://acme.example/ — reference only, "
        "not an official design system"
    )
    meta = tokens["$extensions"][EXTENSION_KEY]
    assert meta["source"] == "https://acme.example/"
    assert meta["collectedAt"].startswith("2026-10-15T08:00:00")
    assert meta["toolVersion"] == "0.1.0"
    assert meta["pagesAnalyzed"] == ["home"]


def test_groups_present(tokens: dict[str, Any]) -> None:
    for group in (
        "color",
        "font-family",
        "font-weight",
        "typography",
        "spacing",
        "rounded",
        "layout",
        "shadow",
        "breakpoint",
        "component",
        "dark",
    ):
        assert group in tokens, group
    assert tokens["color"]["$type"] == "color"
    assert tokens["spacing"]["$type"] == "dimension"
    assert tokens["shadow"]["$type"] == "shadow"


def test_colors(tokens: dict[str, Any]) -> None:
    sample = load_analysis_sample()
    assert list(name for name in tokens["color"] if not name.startswith("$")) == [
        token.name for token in sample.colors.tokens
    ]
    primary = tokens["color"]["primary"]
    assert primary["$value"] == {
        "colorSpace": "srgb",
        "components": [0.3098, 0.2745, 0.898],
        "alpha": 1,
        "hex": "#4f46e5",
    }
    assert primary["$description"] == "brand — background of button-primary; text of links"
    assert primary["$extensions"][EXTENSION_KEY] == {
        "role": "brand",
        "cssVariable": "--color-primary",
    }
    assert tokens["dark"]["color"]["primary"]["$value"]["hex"] == "#818cf8"
    assert tokens["dark"]["color"]["canvas"]["$value"]["hex"] == "#0b1020"


def test_typography(tokens: dict[str, Any]) -> None:
    assert tokens["font-family"]["heading"]["$value"] == [
        "Space Grotesk",
        "Inter",
        "system-ui",
        "sans-serif",
    ]
    assert tokens["font-weight"]["bold"]["$value"] == 700
    assert list(name for name in tokens["font-weight"] if not name.startswith("$")) == [
        "regular",
        "medium",
        "semi-bold",
        "bold",
    ]
    display = tokens["typography"]["display"]
    assert display["$value"] == {
        "fontFamily": "{font-family.heading}",
        "fontSize": {"value": 64, "unit": "px"},
        "fontWeight": "{font-weight.bold}",
        "letterSpacing": {"value": -1.5, "unit": "px"},
        "lineHeight": 1.05,
    }
    assert display["$extensions"][EXTENSION_KEY]["tags"] == ["h1"]
    assert tokens["typography"]["code"]["$value"]["fontFamily"] == "{font-family.mono}"


def test_scales_shadows_breakpoints(tokens: dict[str, Any]) -> None:
    assert tokens["spacing"]["md"]["$value"] == {"value": 16, "unit": "px"}
    assert tokens["spacing"]["4xl"]["$value"] == {"value": 64, "unit": "px"}
    assert tokens["rounded"]["full"]["$value"] == {"value": 9999, "unit": "px"}
    assert tokens["layout"]["container-max-width"]["$value"] == {"value": 1200, "unit": "px"}
    md = tokens["shadow"]["md"]["$value"]
    assert len(md) == 2
    assert md[0]["offsetY"] == {"value": 8, "unit": "px"}
    assert md[0]["spread"] == {"value": -4, "unit": "px"}
    assert md[0]["color"]["alpha"] == 0.12
    assert md[0]["inset"] is False
    assert tokens["breakpoint"]["md"]["$value"] == {"value": 768, "unit": "px"}


def test_components_alias_design_md_refs(tokens: dict[str, Any]) -> None:
    components = tokens["component"]
    primary = components["button-primary"]
    assert primary["background-color"] == {"$type": "color", "$value": "{color.primary}"}
    assert primary["text-color"]["$value"] == "{color.on-primary}"
    assert primary["border-radius"]["$value"] == "{rounded.md}"
    assert primary["typography"]["$value"] == "{typography.ui}"
    assert primary["shadow"]["$value"] == "{shadow.sm}"
    assert primary["padding-block"]["$value"] == {"value": 12, "unit": "px"}
    assert primary["padding-inline"]["$value"] == {"value": 20, "unit": "px"}
    assert primary["hover"]["background-color"]["$value"]["hex"] == "#4338ca"
    assert primary["$extensions"][EXTENSION_KEY]["contrastRatio"] == 6.29

    outline = components["button-outline"]
    assert outline["background-color"]["$value"]["alpha"] == 0
    assert outline["hover"]["background-color"]["$value"] == "{color.surface}"
    assert outline["border-width"]["$value"] == {"value": 1, "unit": "px"}

    card = components["card"]
    assert card["padding-block"]["$value"] == "{spacing.xxl}"
    assert card["padding-inline"]["$value"] == "{spacing.xxl}"
    assert card["shadow"]["$value"] == "{shadow.md}"

    nav = components["top-nav"]
    assert nav["border-bottom-width"]["$value"] == {"value": 1, "unit": "px"}
    assert "border-top-width" not in nav
    assert nav["$extensions"][EXTENSION_KEY]["css"] == {
        "position": "sticky",
        "backdropFilter": "blur(12px)",
    }
    footer = components["footer"]
    assert footer["padding-top"]["$value"] == {"value": 64, "unit": "px"}
    assert footer["padding-bottom"]["$value"] == {"value": 48, "unit": "px"}
    assert footer["typography"]["$value"] == "{typography.body-sm}"

    link = components["text-link"]
    assert link["typography"]["$value"]["fontFamily"] == "{font-family.primary}"
    assert link["typography"]["$value"]["lineHeight"] == 1.6
    assert link["hover"]["$extensions"][EXTENSION_KEY]["css"] == {"textDecoration": "underline"}
    assert components["text-input"]["placeholder-color"]["$value"] == "{color.faint}"
    assert components["badge"]["border-radius"]["$value"] == "{rounded.full}"


# ─── Edge cases ──────────────────────────────────────────────────────────────


def test_empty_analysis_exports_root_only() -> None:
    document = build_tokens(_empty_analysis())
    assert validate_tokens(document) == []
    assert set(document) == {"$description", "$extensions"}
    json.dumps(document)


def test_dark_group_absent_without_dark_capture() -> None:
    analysis = load_analysis_sample()
    analysis.dark = None
    assert "dark" not in build_tokens(analysis)


def test_unresolvable_refs_fall_back_to_values() -> None:
    analysis = load_analysis_sample()
    analysis.components.append(
        ComponentSpec(
            name="button-tertiary",
            kind=ComponentKind.BUTTON,
            properties={
                "backgroundColor": "#123456",
                "rounded": "50%",
                "padding": "7px",
                "fontFamily": "Unknown Sans",
                "fontSize": "15px",
                "fontWeight": "450",
                "lineHeight": "normal",
            },
            token_refs={
                "backgroundColor": "{colors.does-not-exist}",
                "textColor": "{colors.missing}",
                "rounded": "{rounded.huge}",
                "padding": "{spacing.md}",
                "typography": "{typography.nope}",
            },
            states={"hover": {"textDecoration": "underline", "opacity": "0.6"}, "focus": {}},
        )
    )
    document = build_tokens(analysis)
    assert validate_tokens(document) == []
    tertiary = document["component"]["button-tertiary"]
    assert tertiary["background-color"]["$value"]["hex"] == "#123456"
    assert "text-color" not in tertiary
    assert "border-radius" not in tertiary  # percentages are not DTCG dimensions
    # The ref points at 16px, the padding is 7px: the value wins.
    assert tertiary["padding-block"]["$value"] == {"value": 7, "unit": "px"}
    typography = tertiary["typography"]["$value"]
    assert typography["fontFamily"] == ["Unknown Sans"]
    assert typography["fontWeight"] == 450
    assert typography["lineHeight"] == 1.2
    assert "focus" not in tertiary
    assert tertiary["hover"]["opacity"] == {"$type": "number", "$value": 0.6}


def test_hostile_names_are_sanitized() -> None:
    analysis = _empty_analysis()
    analysis.breakpoints = [
        Breakpoint(name="Tablet.Large {x}", min_width_px=900, occurrences=2),
        Breakpoint(name="$root", max_width_px=599.98),
        Breakpoint(name="none", min_width_px=None, max_width_px=None),
        Breakpoint(name="tablet-large-x", min_width_px=1000),
    ]
    analysis.typography = Typography(
        families=[
            FontFamilyInfo(name="Inter", stack=["Inter", "sans-serif"], role=FontRole.PRIMARY),
            FontFamilyInfo(name="Inter Tight", stack=[], role=FontRole.PRIMARY),
        ],
        levels=[
            TypeLevel(name="body", size_px=16, font_family="Inter Tight", weight=430),
            TypeLevel(name="body", size_px=15, font_family="Inter", weight=400),
        ],
    )
    analysis.radii = [ScaleStep(name="sm", value_px=4), ScaleStep(name="sm", value_px=6)]
    analysis.shadows = [ShadowLevel(name="sm", css="none"), ShadowLevel(name="md", css="0 1px red")]
    document = build_tokens(analysis)
    assert validate_tokens(document) == []
    assert list(name for name in document["breakpoint"] if not name.startswith("$")) == [
        "tablet-large-x",
        "root",
        "tablet-large-x-2",
    ]
    assert document["breakpoint"]["root"]["$value"] == {"value": 599.98, "unit": "px"}
    assert set(document["font-family"]) >= {"primary", "primary-2"}
    assert document["font-family"]["primary-2"]["$value"] == ["Inter Tight"]
    assert document["typography"]["body"]["$value"]["fontWeight"] == "{font-weight.weight-430}"
    assert document["typography"]["body-2"]["$value"]["fontFamily"] == "{font-family.primary}"
    assert set(document["rounded"]) >= {"sm", "sm-2"}
    assert list(name for name in document["shadow"] if not name.startswith("$")) == ["md"]


def test_dark_palette_with_unparseable_values_is_skipped() -> None:
    analysis = _empty_analysis()
    analysis.dark = ThemeAnalysis(colors=ColorPalette(scheme="dark"))
    assert "dark" not in build_tokens(analysis)
    analysis.dark.colors.tokens.append(
        ColorToken(name="canvas", value="#000000", role=ColorRole.BACKGROUND)
    )
    assert build_tokens(analysis)["dark"]["color"]["canvas"]["$value"]["hex"] == "#000000"


# ─── Value builders ──────────────────────────────────────────────────────────


def test_value_builders() -> None:
    assert number(16.0) == 16 and isinstance(number(16.0), int)
    assert number(0.123456) == 0.1235
    assert number(-0.0) == 0
    assert dimension(1.5) == {"value": 1.5, "unit": "px"}
    assert color_value(RGBA(255, 0, 0, 0.5)) == {
        "colorSpace": "srgb",
        "components": [1, 0, 0],
        "alpha": 0.5,
        "hex": "#ff0000",
    }
    assert token_name("$Body.LG {x}") == "body-lg-x"
    assert token_name("$$$") == "token"


# ─── Validator ───────────────────────────────────────────────────────────────

COLOR = {"colorSpace": "srgb", "components": [0, 0, 0], "alpha": 1, "hex": "#000000"}
PX = {"value": 1, "unit": "px"}


def _valid() -> dict[str, Any]:
    return {
        "color": {"$type": "color", "ink": {"$value": COLOR}},
        "size": {"$type": "dimension", "sm": {"$value": PX}},
        "alias": {"text": {"$value": "{color.ink}"}},
        "font": {"$type": "fontFamily", "base": {"$value": ["Inter", "sans-serif"]}},
        "weight": {"bold": {"$type": "fontWeight", "$value": "bold"}},
        "type": {
            "body": {
                "$type": "typography",
                "$value": {
                    "fontFamily": "{font.base}",
                    "fontSize": PX,
                    "fontWeight": "{weight.bold}",
                    "letterSpacing": PX,
                    "lineHeight": 1.5,
                },
            }
        },
        "shadow": {
            "md": {
                "$type": "shadow",
                "$value": [
                    {"color": "{color.ink}", "offsetX": PX, "offsetY": PX, "blur": PX, "spread": PX}
                ],
            }
        },
    }


def test_validator_accepts_reference_document() -> None:
    assert validate_tokens(_valid()) == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["alias"].update({"x": {"$value": 3}}), "no effective $type"),
        (lambda d: d["alias"].update({"x": {"$value": "{color.nope}"}}), "does not resolve"),
        (lambda d: d["alias"].update({"x": {"$value": "{color}"}}), "does not resolve"),
        (
            lambda d: d["alias"].update(
                {"a": {"$value": "{alias.b}"}, "b": {"$value": "{alias.a}"}}
            ),
            "circular",
        ),
        (
            lambda d: d["size"].update({"x": {"$type": "dimension", "$value": "{color.ink}"}}),
            "expected 'dimension'",
        ),
        (
            lambda d: d["type"]["body"]["$value"].update({"fontSize": "{color.ink}"}),
            "expected 'dimension'",
        ),
        (
            lambda d: d["color"]["ink"].update({"$value": {**COLOR, "components": [0, 2, 0]}}),
            "outside 0..1",
        ),
        (lambda d: d["color"]["ink"].update({"$value": {**COLOR, "hex": "#0008"}}), "hex"),
        (lambda d: d["size"]["sm"].update({"$value": {"value": 1, "unit": "em"}}), "dimension"),
        (lambda d: d["size"].update({"a.b": {"$value": PX}}), "name contains"),
        (lambda d: d["color"]["ink"].update({"child": {"$value": COLOR}}), "token has a child"),
        (lambda d: d["color"].update({"$bogus": 1}), "unknown property"),
        (lambda d: d["type"]["body"]["$value"].pop("lineHeight"), "misses 'lineHeight'"),
        (lambda d: d["shadow"]["md"]["$value"][0].pop("color"), "misses 'color'"),
        (lambda d: d["weight"]["bold"].update({"$value": "Bold"}), "invalid fontWeight"),
        (lambda d: d["font"]["base"].update({"$value": []}), "invalid fontFamily"),
        (lambda d: d["color"].update({"$type": "colour"}), "unknown $type"),
    ],
)
def test_validator_rejects(mutate: Any, message: str) -> None:
    document = copy.deepcopy(_valid())
    mutate(document)
    problems = validate_tokens(document)
    assert any(message in problem for problem in problems), problems


def test_uneven_corner_radii_become_one_token_per_corner() -> None:
    # stripe.com's pricing tab badge: rounded on top only, so no {rounded.*} step applies.
    analysis = load_analysis_sample()
    badge = next(spec for spec in analysis.components if spec.name == "badge")
    badge.properties["rounded"] = "4px 4px 0px 0px"
    badge.token_refs.pop("rounded", None)
    document = build_tokens(analysis)
    assert validate_tokens(document) == []
    tokens = document["component"]["badge"]
    assert "border-radius" not in tokens
    assert {
        corner: tokens[f"border-{corner}-radius"]["$value"]
        for corner in ("top-left", "top-right", "bottom-right", "bottom-left")
    } == {
        "top-left": {"value": 4, "unit": "px"},
        "top-right": {"value": 4, "unit": "px"},
        "bottom-right": {"value": 0, "unit": "px"},
        "bottom-left": {"value": 0, "unit": "px"},
    }
