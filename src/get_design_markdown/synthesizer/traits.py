"""Rule-based style traits shared by the front-matter description and the Overview section.

Thresholds (CLAUDE.md, "Synthesizer — dựng bằng luật"):
- canvas OKLCH lightness < 0.15 -> near-black; > 0.95 -> bright white (tinted when chroma
  >= 0.015);
- every weight >= 600 -> "heavy weights only — no regular 400";
- chromatic button backgrounds grouped into hue families; families with at least 15% of the
  chromatic button instances count: 0 monochrome, 1 single accent, 2 two-signal, 3+ multi
  (rarer families are reported as minor accents);
- most common radius >= 16px or pill -> soft, rounded (pill: with pill-shaped controls);
  <= 4px -> sharp, precise;
- no shadow -> depth from tonal surface steps; only rings/inset highlights -> "rings";
  otherwise the strongest drop-shadow alpha decides subtle / moderate / pronounced, with
  higher thresholds on a dark canvas where black shadows barely show.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from get_design_markdown.analyzer.css_values import to_oklch
from get_design_markdown.analyzer.naming import PILL_RADIUS_PX
from get_design_markdown.models.analysis import ComponentKind, FontFamilyInfo, FontRole, ScaleStep
from get_design_markdown.synthesizer.catalog import Phrases
from get_design_markdown.synthesizer.colornames import color_name
from get_design_markdown.synthesizer.facts import ActionColor, DesignFacts

NEAR_BLACK_MAX_L: Final = 0.15
BRIGHT_WHITE_MIN_L: Final = 0.95
TINTED_MIN_CHROMA: Final = 0.015
SOFT_RADIUS_MIN_PX: Final = 16.0
SHARP_RADIUS_MAX_PX: Final = 4.0
HEAVY_WEIGHT_MIN: Final = 600
#: (subtle max, pronounced min) strongest drop-shadow alpha, on a light / dark canvas.
SHADOW_ALPHA_LIGHT: Final = (0.16, 0.4)
SHADOW_ALPHA_DARK: Final = (0.4, 0.7)
DARK_CANVAS_MAX_L: Final = 0.4
#: Accent families below this share of chromatic button instances are minor accents.
MINOR_ACCENT_SHARE: Final = 0.15

CanvasKey = Literal["near_black", "dark", "mid_tone", "light", "bright_white", "tinted_white"]
AccentKey = Literal["none", "single", "two", "multi"]
SoftnessKey = Literal["pill", "soft", "moderate", "sharp"]
DepthKey = Literal["none", "rings", "subtle", "moderate", "pronounced"]
WeightKey = Literal["heavy_only", "no_bold", "range"]


@dataclass(frozen=True)
class Traits:
    theme: Literal["light", "dark"] | None
    canvas: CanvasKey | None
    canvas_tint: Literal["warm", "cool"] | None
    accent: AccentKey | None
    actions: tuple[ActionColor, ...]
    #: Rare accent families (below MINOR_ACCENT_SHARE of chromatic button instances).
    minor_actions: tuple[ActionColor, ...]
    softness: SoftnessKey | None
    common_radius: ScaleStep | None
    depth: DepthKey | None
    weight: WeightKey | None
    heading_family: FontFamilyInfo | None
    body_family: FontFamilyInfo | None
    mono_family: FontFamilyInfo | None


def _canvas_key(facts: DesignFacts) -> tuple[CanvasKey | None, Literal["warm", "cool"] | None]:
    if not facts.colors:
        return None, None
    oklch = to_oklch(facts.canvas)
    tint: Literal["warm", "cool"] | None = None
    if oklch.chroma >= TINTED_MIN_CHROMA and oklch.hue is not None:
        tint = "cool" if 180.0 <= oklch.hue < 320.0 else "warm"
    if oklch.lightness < NEAR_BLACK_MAX_L:
        return "near_black", tint
    if oklch.lightness < 0.4:
        return "dark", tint
    if oklch.lightness < 0.75:
        return "mid_tone", tint
    if oklch.lightness <= BRIGHT_WHITE_MIN_L:
        return "light", tint
    return ("tinted_white" if tint is not None else "bright_white"), tint


def softness_of(step: ScaleStep | None) -> SoftnessKey | None:
    if step is None:
        return None
    if step.name == "full" or step.value_px >= PILL_RADIUS_PX:
        return "pill"
    if step.value_px >= SOFT_RADIUS_MIN_PX:
        return "soft"
    if step.value_px <= SHARP_RADIUS_MAX_PX:
        return "sharp"
    return "moderate"


def depth_of(facts: DesignFacts) -> DepthKey | None:
    """None when shadows were not analyzed (skipped analyzer), so nothing is claimed."""
    if not facts.analysis.shadows:
        return None if "shadows" in facts.skipped_parts() else "none"
    profile = facts.shadow_profile()
    if not profile.has_drop:
        return "rings"
    dark = to_oklch(facts.canvas).lightness < DARK_CANVAS_MAX_L
    subtle, pronounced = SHADOW_ALPHA_DARK if dark else SHADOW_ALPHA_LIGHT
    if profile.drop_alpha <= subtle:
        return "subtle"
    return "pronounced" if profile.drop_alpha >= pronounced else "moderate"


def compute_traits(facts: DesignFacts) -> Traits:
    canvas, tint = _canvas_key(facts)
    theme: Literal["light", "dark"] | None = None
    if facts.colors:
        theme = "dark" if to_oklch(facts.canvas).lightness < 0.5 else "light"
    families = facts.action_colors()
    total = sum(family.count for family in families)
    actions = tuple(
        family
        for index, family in enumerate(families)
        if index == 0 or family.count >= MINOR_ACCENT_SHARE * total
    )
    minor = tuple(family for family in families if family not in actions)
    accent: AccentKey | None = None
    if facts.components_of_kind(ComponentKind.BUTTON):
        count = len(actions)
        accent = (
            "none" if count == 0 else "single" if count == 1 else "two" if count == 2 else "multi"
        )
    return_depth = depth_of(facts)
    common = facts.most_common_radius()
    weights = facts.weights()
    weight: WeightKey | None = None
    if weights:
        if min(weights) >= HEAVY_WEIGHT_MIN:
            weight = "heavy_only"
        elif max(weights) < HEAVY_WEIGHT_MIN:
            weight = "no_bold"
        else:
            weight = "range"
    body = facts.family_with_role(FontRole.PRIMARY)
    heading = facts.family_with_role(FontRole.HEADING, FontRole.DISPLAY)
    if heading is None:
        level = facts.heading_level()
        if level is not None:
            heading = facts.families.get(level.font_family)
    return Traits(
        theme=theme,
        canvas=canvas,
        canvas_tint=tint,
        accent=accent,
        actions=actions,
        minor_actions=minor,
        softness=softness_of(common),
        common_radius=common,
        depth=return_depth,
        weight=weight,
        heading_family=heading,
        body_family=body,
        mono_family=facts.family_with_role(FontRole.MONO),
    )


# ─── Localized fragments ─────────────────────────────────────────────────────


def canvas_words(traits: Traits, phrases: Phrases) -> str | None:
    if traits.canvas is None:
        return None
    tint = phrases.get(f"trait.tint.{traits.canvas_tint}") if traits.canvas_tint else ""
    return phrases.get(f"trait.canvas.{traits.canvas}", tint=tint).strip()


def family_hexes(action: ActionColor) -> str:
    """ "#635bff" or "#635bff / #533afd" for a family with variants."""
    return " / ".join([action.hex, *action.variants])


def action_words(traits: Traits, facts: DesignFacts, phrases: Phrases) -> str | None:
    """ "a single indigo accent (#4f46e5)" ... or None without button data."""
    if traits.accent is None:
        return None
    colors = [
        phrases.get(
            "trait.named_color", name=color_name(action.hex, phrases), hex=family_hexes(action)
        )
        for action in traits.actions
    ]
    first = traits.actions[0] if traits.actions else None
    return phrases.get(
        f"trait.accent.{traits.accent}",
        name=color_name(first.hex, phrases) if first else "",
        hex=family_hexes(first) if first else "",
        colors=phrases.join(colors),
        count=len(colors),
    )


def type_words(traits: Traits, phrases: Phrases) -> str | None:
    heading = traits.heading_family
    body = traits.body_family
    if heading is not None and body is not None and heading.name != body.name:
        return phrases.get("trait.type.pair", heading=heading.name, body=body.name)
    family = body or heading
    if family is None:
        return None
    return phrases.get("trait.type.single", family=family.name)


def shape_words(traits: Traits, facts: DesignFacts, phrases: Phrases) -> str | None:
    if traits.softness is None or traits.common_radius is None:
        return None
    return phrases.get(
        f"trait.softness.{traits.softness}",
        radius=facts.radius_value(traits.common_radius),
    )


def description(facts: DesignFacts, phrases: Phrases) -> str:
    """One rule-built sentence for the front-matter `description`."""
    traits = compute_traits(facts)
    fragments: list[str] = []
    canvas = canvas_words(traits, phrases)
    if canvas is not None and traits.theme is not None:
        fragments.append(
            phrases.get(
                "description.theme_canvas",
                theme=phrases.get(f"trait.theme.{traits.theme}"),
                canvas=canvas,
            )
        )
    for fragment in (
        action_words(traits, facts, phrases),
        type_words(traits, phrases),
        shape_words(traits, facts, phrases),
    ):
        if fragment:
            fragments.append(fragment)
    site = facts.analysis.site
    name = site.name.strip() or site.url
    if not fragments:
        return phrases.get("description.minimal", site=name)
    return phrases.get("description.sentence", site=name, details=phrases.join(fragments))
