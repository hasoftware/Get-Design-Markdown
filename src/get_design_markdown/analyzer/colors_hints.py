"""Role hints for the color analyzer: CSS custom property names, class names and text symbols.

Hints never create tokens on their own: they only rank colors that are really used on the page
(a variable hints a cluster when its color is within ΔE2000 < 1 of the cluster).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from get_design_markdown.analyzer.css_values import RGBA, parse_color, parse_number
from get_design_markdown.models.raw import CssVariable, StylesheetInfo

#: Status token name -> words that prove the status (class names, ids, variable names).
STATUS_WORDS: dict[str, frozenset[str]] = {
    "success": frozenset({"success", "successful", "positive", "valid", "succeeded"}),
    "warning": frozenset({"warning", "warn", "caution", "attention"}),
    "danger": frozenset(
        {"danger", "error", "errors", "destructive", "negative", "critical", "invalid", "fail"}
    ),
    "info": frozenset({"info", "notice", "informative", "information"}),
}
#: Status token name -> characters in the element text that prove the status.
STATUS_SYMBOLS: dict[str, str] = {
    "success": "✓✔☑✅",
    "warning": "⚠",
    "danger": "✗✘✕✖❌⛔",
    "info": "ℹ",
}

_TEXT_WORDS = frozenset({"text", "fg", "foreground", "ink", "copy", "heading", "label", "font"})
_BG_WORDS = frozenset(
    {
        "bg",
        "background",
        "backdrop",
        "canvas",
        "page",
        "surface",
        "card",
        "panel",
        "layer",
        "fill",
        "elevated",
        "raised",
        "popover",
        "sheet",
        "overlay",
        "modal",
        "dialog",
        "tile",
        "well",
    }
)
_BORDER_WORDS = frozenset(
    {"border", "borders", "divider", "line", "stroke", "hairline", "separator", "outline", "rule"}
)
_BRAND_WORDS = frozenset({"primary", "brand", "accent"})
#: "primary" alone means "main" next to bg/text/border words ("--bg-primary" is the canvas);
#: "brand"/"accent" always mean the brand color ("--accent-bg" is a brand-colored fill).
_BRAND_ONLY_WORDS = frozenset({"brand", "accent"})
_INVERSE_WORDS = frozenset({"inverse", "inverted", "invert", "contrast"})
_MUTED_WORDS = frozenset({"muted", "subtle", "secondary", "soft", "dim", "weak", "subdued"})
_FAINT_WORDS = frozenset({"faint", "placeholder", "disabled", "tertiary", "quaternary", "ghost"})
_RAISED_WORDS = frozenset(
    {"raised", "elevated", "tertiary", "popover", "overlay", "modal", "dialog", "sheet"}
)
_SURFACE_WORDS = frozenset(
    {
        "surface",
        "card",
        "panel",
        "secondary",
        "subtle",
        "muted",
        "alt",
        "alternate",
        "layer",
        "tile",
        "well",
        "sunken",
    }
)
_STRONG_WORDS = frozenset({"strong", "heavy", "bold", "emphasis", "intense"})
#: Variables for interaction states / shades never hint a base role.
_STATE_WORDS = frozenset({"hover", "active", "pressed", "focus", "visited", "selected", "checked"})
#: Component words that make "primary"/"secondary" mean an action variant ("--button-primary-bg"
#: is the fill of the primary button, not the main page background).
_ACTION_WORDS = frozenset({"button", "buttons", "btn", "cta", "action"})
#: Variant words of action colors, in token-name form.
_VARIANT_WORDS = ("primary", "secondary", "tertiary")
_FG_WORDS = frozenset({"foreground", "fg", "contrast"})

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
_TRIPLET_SPLIT_RE = re.compile(r"[\s,]+")


def name_words(name: str) -> list[str]:
    """ "--color-textPrimary_2" -> ["color", "text", "primary", "2"] (lowercase)."""
    words: list[str] = []
    for chunk in _SPLIT_RE.split(name):
        if not chunk:
            continue
        words.extend(part.lower() for part in _CAMEL_RE.split(chunk) if part)
    return words


def _after(words: Sequence[str], word: str) -> str | None:
    for position, current in enumerate(words[:-1]):
        if current == word:
            return words[position + 1]
    return None


def is_generated_name(name: str) -> bool:
    """Machine-generated custom property ("--sx-1gcjx5j", "--x8f3a2b"): a word mixing letters
    and digits that is not a plain scale step ("500", "a3", "2xl")."""
    for word in name_words(name):
        has_digit = any(char.isdigit() for char in word)
        has_alpha = any(char.isalpha() for char in word)
        if has_digit and has_alpha and len(word) >= 5:
            return True
    return False


def status_from_words(words: Iterable[str]) -> str | None:
    """Status token name proven by any of the words, else None."""
    word_set = set(words)
    for status, vocabulary in STATUS_WORDS.items():
        if word_set & vocabulary:
            return status
    return None


def status_from_text(text: str) -> str | None:
    """Status token name proven by a symbol in the text (✓, ⚠, ✕, ℹ...), else None."""
    for status, symbols in STATUS_SYMBOLS.items():
        if any(symbol in text for symbol in symbols):
            return status
    return None


def _first_position(words: Sequence[str], vocabulary: frozenset[str]) -> int | None:
    return next((position for position, word in enumerate(words) if word in vocabulary), None)


def _action_variant(words: Sequence[str]) -> str | None:
    """ "primary"/"secondary"/"tertiary" when the words name a button/CTA variant."""
    if not set(words) & _ACTION_WORDS:
        return None
    return next((variant for variant in _VARIANT_WORDS if variant in words), None)


def action_hint(name: str) -> str | None:
    """Brand token name of a variable that colors an action variant's fill.

    "--button-primary-bg" / "--hds-color-button-primary-bg" / "--btn-primary" -> "primary",
    "--cta-secondary" -> "secondary"; text, border and state variables ("--button-primary-text",
    "--button-primary-bg-hover") and plain brand names ("--brand-500") -> None. Such a variable
    is stronger evidence for an action color than a generic brand-scale name.
    """
    words = name_words(name)
    word_set = set(words)
    if not words or word_set & (_STATE_WORDS | _TEXT_WORDS | _BORDER_WORDS | _FG_WORDS):
        return None
    if "on" in word_set or status_from_words(words) is not None:
        return None
    return _action_variant(words)


def variable_hints(name: str) -> frozenset[str]:
    """Color token names (naming.COLOR_TOKEN_ROLES) suggested by a custom property name.

    "--color-primary" -> {"primary"}, "--text-muted" -> {"muted"}, "--bg-canvas" ->
    {"canvas"}, "--border-subtle" -> {"hairline"}, "--primary-foreground" -> {"on-primary"},
    "--color-fg-secondary" -> {"muted"} (a text tier), "--button-primary-bg" -> {"primary"},
    "--button-primary-text" -> {"on-primary"}, "--color-success" -> {"success"},
    "--blue-500" -> {} and "--color-primary-hover" -> {}.
    """
    words = name_words(name)
    word_set = set(words)
    if not words or word_set & _STATE_WORDS:
        return frozenset()
    status = status_from_words(words)
    if status is not None:
        return frozenset({status})
    has_text = bool(word_set & _TEXT_WORDS)
    has_bg = bool(word_set & _BG_WORDS)
    has_border = bool(word_set & _BORDER_WORDS)
    variant = _action_variant(words)
    if variant is not None and not has_border:
        text_position = _first_position(words, _TEXT_WORDS | _FG_WORDS)
        if text_position is None:
            # "--button-primary-bg", "--btn-primary": the fill of that action variant.
            return frozenset({variant})
        if words.index(variant) < text_position and variant != "tertiary":
            # "--button-primary-text": text drawn on that variant.
            return frozenset({f"on-{variant}"})
    on_target = _after(words, "on")
    fg_position = _first_position(words, _FG_WORDS)
    if on_target is not None or (fg_position is not None and not has_bg):
        # "--primary-foreground" is the text on primary; "--color-fg-primary" is the primary
        # text tier (ink): only words before the fg word name the color it is drawn on.
        target_words = {on_target} if on_target is not None else set(words[:fg_position])
        if target_words & _BRAND_WORDS:
            return frozenset({"on-primary"})
        if "secondary" in target_words:
            return frozenset({"on-secondary"})
        if target_words & (_INVERSE_WORDS | {"dark"}):
            return frozenset({"on-inverse"})
        if on_target is not None:
            return frozenset({"ink"})
    if has_border:
        if word_set & _BRAND_ONLY_WORDS:
            return frozenset({"primary"})
        return frozenset({"hairline-strong" if word_set & _STRONG_WORDS else "hairline"})
    if has_text or (word_set & {"foreground", "fg"}):
        if word_set & _INVERSE_WORDS:
            return frozenset({"on-inverse"})
        if word_set & _FAINT_WORDS:
            return frozenset({"faint"})
        if word_set & _MUTED_WORDS:
            return frozenset({"muted"})
        return frozenset({"ink"})
    if has_bg:
        if word_set & _BRAND_ONLY_WORDS:
            return frozenset({"primary"})
        if word_set & _INVERSE_WORDS:
            return frozenset({"surface-inverse"})
        if word_set & _RAISED_WORDS:
            return frozenset({"surface-raised"})
        if word_set & _SURFACE_WORDS:
            return frozenset({"surface"})
        return frozenset({"canvas"})
    hints: set[str] = set()
    if word_set & _BRAND_WORDS:
        hints.add("primary")
    if "secondary" in word_set:
        hints.add("secondary")
    if "tertiary" in word_set:
        hints.add("tertiary")
    if word_set & {"muted", "subtle"}:
        hints.add("muted")
    if word_set & {"faint", "placeholder", "disabled"}:
        hints.add("faint")
    if word_set & {"ink", "body"}:
        hints.add("ink")
    return frozenset(hints)


def parse_variable_color(value: str | None) -> RGBA | None:
    """Color of a custom property value, including bare channel lists used with rgb()/hsl().

    "#635bff" / "rgb(99, 91, 255)" -> that color; "99 91 255" / "99, 91, 255" -> rgb;
    "222.2 84% 4.9%" (shadcn style) -> hsl; anything else -> None.
    """
    if value is None:
        return None
    text = value.strip()
    if not text or text.startswith("var(") or "gradient(" in text:
        return None
    color = parse_color(text)
    if color is not None:
        return color
    parts = [part for part in _TRIPLET_SPLIT_RE.split(text) if part]
    if len(parts) != 3:
        return None
    if parts[1].endswith("%") and parts[2].endswith("%"):
        hue = parse_number(parts[0].removesuffix("deg"))
        saturation = parse_number(parts[1][:-1])
        lightness = parse_number(parts[2][:-1])
        if hue is None or saturation is None or lightness is None:
            return None
        return parse_color(f"hsl({hue} {saturation}% {lightness}%)")
    channels = [parse_number(part) for part in parts]
    if any(channel is None or not 0 <= channel <= 255 for channel in channels):
        return None
    red, green, blue = (round(channel or 0.0) for channel in channels)
    return parse_color(f"rgb({red}, {green}, {blue})")


@dataclass(frozen=True, slots=True)
class ColorVariable:
    """A custom property whose value is a color."""

    name: str
    color: RGBA
    hints: frozenset[str]
    #: Brand token name when the variable colors an action variant ("--button-primary-bg").
    action: str | None = None


def collect_color_variables(
    page_variables: Iterable[CssVariable], stylesheets: Sequence[StylesheetInfo]
) -> list[ColorVariable]:
    """Color variables of the pages (resolved value first), then stylesheet-only variables.

    Page values win because they reflect the analyzed color scheme; a stylesheet may declare the
    same name for several schemes, so its values are only used for names the pages do not have.
    """
    result: list[ColorVariable] = []
    seen: set[tuple[str, str]] = set()
    page_names: set[str] = set()

    def add(name: str, color: RGBA | None) -> None:
        if color is None or color.is_transparent:
            return
        key = (name, color.css_hex)
        if key in seen:
            return
        seen.add(key)
        result.append(ColorVariable(name, color, variable_hints(name), action_hint(name)))

    for variable in page_variables:
        page_names.add(variable.name)
        color = parse_variable_color(variable.resolved)
        if color is None:
            color = parse_variable_color(variable.declared)
        add(variable.name, color)
    for sheet in stylesheets:
        for variable in sheet.variables:
            if variable.name in page_names:
                continue
            color = parse_variable_color(variable.resolved)
            if color is None:
                color = parse_variable_color(variable.declared)
            add(variable.name, color)
    return result
