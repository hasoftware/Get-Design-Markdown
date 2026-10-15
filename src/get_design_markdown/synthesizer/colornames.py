"""Descriptive color names from OKLCH ("indigo", "deep blue", "cool near-black")."""

from __future__ import annotations

from typing import Final

from get_design_markdown.analyzer.css_values import OKLCH, RGBA, parse_color, to_oklch
from get_design_markdown.synthesizer.catalog import Phrases

#: Below this OKLCH chroma a color is described as a (possibly tinted) neutral.
CHROMATIC_MIN_CHROMA: Final = 0.04
#: Neutrals with at least this chroma get a "cool"/"warm" tint word.
TINT_MIN_CHROMA: Final = 0.012

#: Upper bound (exclusive, degrees) of each OKLCH hue family, calibrated on Tailwind 500 colors.
_HUES: Final = (
    (10.0, "pink"),
    (35.0, "red"),
    (75.0, "orange"),
    (100.0, "yellow"),
    (140.0, "lime"),
    (172.0, "green"),
    (200.0, "teal"),
    (230.0, "cyan"),
    (268.0, "blue"),
    (288.0, "indigo"),
    (315.0, "purple"),
    (340.0, "magenta"),
    (360.0, "pink"),
)


def hue_family(hue: float) -> str:
    """OKLCH hue in degrees -> family key ("indigo")."""
    angle = hue % 360.0
    for bound, name in _HUES:
        if angle < bound:
            return name
    return "pink"


def neutral_key(lightness: float) -> str:
    if lightness < 0.25:
        return "near_black"
    if lightness < 0.45:
        return "dark_gray"
    if lightness < 0.7:
        return "gray"
    if lightness < 0.95:
        return "light_gray"
    if lightness < 0.995:
        return "off_white"
    return "white"


def is_chromatic(color: RGBA | OKLCH) -> bool:
    oklch = color if isinstance(color, OKLCH) else to_oklch(color)
    return oklch.chroma >= CHROMATIC_MIN_CHROMA and oklch.hue is not None


def color_name(value: str | RGBA, phrases: Phrases) -> str:
    """Localized descriptive name of a color; "" when the value is not a color."""
    color = value if isinstance(value, RGBA) else parse_color(value)
    if color is None:
        return ""
    oklch = to_oklch(color)
    if is_chromatic(oklch) and oklch.hue is not None:
        family = hue_family(oklch.hue)
        if family in ("orange", "yellow") and oklch.lightness < 0.55:
            family = "brown"
        hue_word = phrases.get(f"color_name.hue.{family}")
        if oklch.lightness < 0.35:
            return phrases.get("color_name.deep", hue=hue_word)
        if oklch.lightness > 0.88:
            return phrases.get("color_name.pale", hue=hue_word)
        return hue_word
    base = phrases.get(f"color_name.neutral.{neutral_key(oklch.lightness)}")
    if oklch.chroma >= TINT_MIN_CHROMA and oklch.hue is not None and base:
        tint = "cool" if 180.0 <= oklch.hue < 320.0 else "warm"
        return phrases.get(f"color_name.{tint}", name=base)
    return base
