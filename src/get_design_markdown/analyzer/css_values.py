"""Shared parsing helpers for computed CSS values.

Every analyzer, the synthesizer and the exporters use these helpers instead of re-parsing CSS.
All functions are pure and tolerate malformed input: they return None / [] / a neutral default
instead of raising (except contrast_ratio() given an unparseable color string).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from coloraide import Color

DEFAULT_FONT_SIZE_PX = 16.0
#: Approximation of `line-height: normal` used when a caller needs a number.
NORMAL_LINE_HEIGHT = 1.2
#: OKLCH chroma under which a color is treated as a (possibly tinted) neutral.
NEUTRAL_MAX_CHROMA = 0.06

#: WCAG 2.x thresholds.
AA_NORMAL = 4.5
AA_LARGE = 3.0
AAA_NORMAL = 7.0
AAA_LARGE = 4.5

_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_LENGTH_RE = re.compile(rf"^({_NUMBER})([a-z%]*)$")
_ABSOLUTE_UNITS: dict[str, float] = {
    "px": 1.0,
    "pt": 96.0 / 72.0,
    "pc": 16.0,
    "in": 96.0,
    "cm": 96.0 / 2.54,
    "mm": 96.0 / 25.4,
    "q": 96.0 / 101.6,
}
_NO_VALUE = frozenset(
    {"", "normal", "auto", "none", "initial", "inherit", "unset", "revert", "revert-layer"}
)

# ─── Numbers & lengths ───────────────────────────────────────────────────────


def parse_number(value: str | None) -> float | None:
    """Plain number ("1.5", "-2", ".5") or None."""
    if value is None:
        return None
    text = value.strip()
    if not re.fullmatch(_NUMBER, text):
        return None
    number = float(text)
    return number if math.isfinite(number) else None


def parse_px(
    value: str | None,
    *,
    font_size_px: float = DEFAULT_FONT_SIZE_PX,
    root_font_size_px: float = DEFAULT_FONT_SIZE_PX,
    percent_of: float | None = None,
    viewport_width: float = 1440.0,
    viewport_height: float = 900.0,
) -> float | None:
    """Convert a CSS length to px.

    "12px" -> 12.0, "1.5em" -> 1.5 * font_size_px, "2rem" -> 2 * root_font_size_px,
    "50%" -> half of percent_of (None without it), "10vw" -> 144.0, "0" -> 0.0.
    Keywords (normal, auto, none...), unitless non-zero numbers and calc() give None.
    """
    if value is None:
        return None
    text = value.strip().lower()
    if text in _NO_VALUE:
        return None
    match = _LENGTH_RE.match(text)
    if match is None:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    if not math.isfinite(number):
        return None
    if unit == "":
        return 0.0 if number == 0 else None
    if unit in _ABSOLUTE_UNITS:
        return number * _ABSOLUTE_UNITS[unit]
    if unit == "em":
        return number * font_size_px
    if unit == "rem":
        return number * root_font_size_px
    if unit in ("ch", "ex"):
        return number * font_size_px * 0.5
    if unit == "%":
        return None if percent_of is None else number / 100.0 * percent_of
    if unit in ("vw", "svw", "lvw", "dvw"):
        return number * viewport_width / 100.0
    if unit in ("vh", "svh", "lvh", "dvh"):
        return number * viewport_height / 100.0
    if unit == "vmin":
        return number * min(viewport_width, viewport_height) / 100.0
    if unit == "vmax":
        return number * max(viewport_width, viewport_height) / 100.0
    return None


def parse_sides_px(
    sides: Sequence[str],
    *,
    font_size_px: float = DEFAULT_FONT_SIZE_PX,
    percent_of: float | None = None,
) -> tuple[float, float, float, float]:
    """Four computed sides (top, right, bottom, left) to px; unparseable sides count as 0.

    Fewer than four values are expanded like the CSS shorthand (1, 2 or 3 values).
    """
    values = [
        parse_px(side, font_size_px=font_size_px, percent_of=percent_of) or 0.0
        for side in list(sides)[:4]
    ]
    if not values:
        return (0.0, 0.0, 0.0, 0.0)
    if len(values) == 1:
        values = values * 4
    elif len(values) == 2:
        values = [values[0], values[1], values[0], values[1]]
    elif len(values) == 3:
        values = [values[0], values[1], values[2], values[1]]
    return (values[0], values[1], values[2], values[3])


def format_px(value: float, *, precision: int = 2) -> str:
    """16.0 -> "16px", 0.5 -> "0.5px", 12.345 -> "12.35px", -0.0 -> "0px"."""
    rounded = round(value, precision)
    if rounded == 0:
        return "0px"
    text = f"{rounded:.{precision}f}".rstrip("0").rstrip(".")
    return f"{text}px"


def css_shorthand(values: Sequence[str]) -> str:
    """Collapse four sides (top, right, bottom, left) to the shortest CSS shorthand."""
    if len(values) != 4:
        return " ".join(values)
    top, right, bottom, left = values
    if top == right == bottom == left:
        return top
    if top == bottom and right == left:
        return f"{top} {right}"
    if right == left:
        return f"{top} {right} {bottom}"
    return f"{top} {right} {bottom} {left}"


def format_sides_px(values: Sequence[float]) -> str:
    """(12, 20, 12, 20) -> "12px 20px"."""
    return css_shorthand([format_px(value) for value in values])


def split_top_level(value: str, separator: str = ",") -> list[str]:
    """Split on `separator` outside parentheses and quotes.

    separator=" " splits on any run of whitespace. Empty parts are dropped; parts are stripped.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    whitespace = separator == " "
    for char in value:
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        is_separator = char.isspace() if whitespace else char == separator
        if depth == 0 and is_separator:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


# ─── Colors ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RGBA:
    """An sRGB color with 8-bit channels and alpha in 0..1 (rounded to 3 decimals)."""

    r: int
    g: int
    b: int
    alpha: float = 1.0

    @property
    def hex(self) -> str:
        """ "#rrggbb" (alpha dropped)."""
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    @property
    def hex8(self) -> str:
        """ "#rrggbbaa" (always 8 digits)."""
        return f"{self.hex}{round(self.alpha * 255):02x}"

    @property
    def css_hex(self) -> str:
        """Canonical value for HexColor fields: #rrggbb when opaque, else #rrggbbaa."""
        return self.hex if self.is_opaque else self.hex8

    @property
    def is_transparent(self) -> bool:
        return round(self.alpha * 255) == 0

    @property
    def is_opaque(self) -> bool:
        return round(self.alpha * 255) == 255

    def with_alpha(self, alpha: float) -> RGBA:
        return rgba(self.r, self.g, self.b, alpha)

    def to_css(self) -> str:
        """ "rgb(79, 70, 229)" or "rgba(15, 23, 42, 0.06)"."""
        if self.is_opaque:
            return f"rgb({self.r}, {self.g}, {self.b})"
        alpha = f"{self.alpha:.3f}".rstrip("0").rstrip(".") or "0"
        return f"rgba({self.r}, {self.g}, {self.b}, {alpha})"

    def to_color(self) -> Color:
        return Color("srgb", [self.r / 255.0, self.g / 255.0, self.b / 255.0], self.alpha)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def rgba(r: float, g: float, b: float, alpha: float = 1.0) -> RGBA:
    """Build a normalized RGBA (channels rounded and clamped to 0..255, alpha to 0..1)."""
    return RGBA(
        int(_clamp(round(r), 0, 255)),
        int(_clamp(round(g), 0, 255)),
        int(_clamp(round(b), 0, 255)),
        round(_clamp(alpha, 0.0, 1.0), 3),
    )


WHITE = RGBA(255, 255, 255, 1.0)
BLACK = RGBA(0, 0, 0, 1.0)
TRANSPARENT = RGBA(0, 0, 0, 0.0)


@lru_cache(maxsize=8192)
def _parse_color_text(text: str) -> RGBA | None:
    try:
        color = Color(text)
    except (ValueError, TypeError, KeyError):
        return None
    srgb = color.convert("srgb")
    if not srgb.in_gamut():
        srgb = srgb.fit("srgb")
    red, green, blue = srgb.coords(nans=False)
    alpha = srgb.alpha(nans=False)
    if not all(math.isfinite(channel) for channel in (red, green, blue, alpha)):
        return None
    return rgba(red * 255, green * 255, blue * 255, alpha)


def parse_color(value: str | None, *, current_color: str | RGBA | None = None) -> RGBA | None:
    """Parse any CSS color (hex, rgb/rgba, hsl, hwb, lab, lch, oklab, oklch, color(), named).

    "transparent" -> fully transparent black; "currentcolor" -> current_color (None without it);
    out-of-gamut colors are gamut-mapped to sRGB. Unknown values give None.
    """
    if value is None:
        return None
    text = " ".join(value.strip().lower().split())
    if text in _NO_VALUE:
        return None
    if text == "transparent":
        return TRANSPARENT
    if text == "currentcolor":
        if isinstance(current_color, RGBA):
            return current_color
        if current_color is None or current_color.strip().lower() == "currentcolor":
            return None
        return parse_color(current_color)
    return _parse_color_text(text)


def to_hex(value: str | None, *, current_color: str | RGBA | None = None) -> str | None:
    """CSS color -> canonical lowercase "#rrggbb" / "#rrggbbaa" (None when unparseable)."""
    color = parse_color(value, current_color=current_color)
    return None if color is None else color.css_hex


def composite(foreground: RGBA, background: RGBA) -> RGBA:
    """Source-over alpha compositing of foreground on background (sRGB, like browsers)."""
    fa = foreground.alpha
    ba = background.alpha
    out_alpha = fa + ba * (1.0 - fa)
    if out_alpha <= 0:
        return TRANSPARENT

    def channel(front: int, back: int) -> float:
        return (front * fa + back * ba * (1.0 - fa)) / out_alpha

    return rgba(
        channel(foreground.r, background.r),
        channel(foreground.g, background.g),
        channel(foreground.b, background.b),
        out_alpha,
    )


def flatten(color: RGBA, background: RGBA = WHITE) -> RGBA:
    """Opaque color seen when `color` is painted on `background` (itself flattened on white)."""
    base = background if background.is_opaque else composite(background, WHITE)
    return composite(color, base).with_alpha(1.0)


_URL_RE = re.compile(r"url\((?:[^()\"']|\"[^\"]*\"|'[^']*')*\)", re.IGNORECASE)
_GRADIENT_RE = re.compile(r"(?:repeating-)?(?:linear|radial|conic)-gradient\(", re.IGNORECASE)


def is_gradient(value: str | None) -> bool:
    """True when a background-image value contains a CSS gradient."""
    return bool(value) and _GRADIENT_RE.search(value or "") is not None


def _gradient_bodies(text: str) -> list[str]:
    bodies: list[str] = []
    for match in _GRADIENT_RE.finditer(text):
        depth = 1
        position = match.end()
        while position < len(text) and depth > 0:
            if text[position] == "(":
                depth += 1
            elif text[position] == ")":
                depth -= 1
            position += 1
        bodies.append(text[match.end() : position - 1])
    return bodies


def gradient_colors(background_image: str | None) -> list[RGBA]:
    """Color stops of every linear/radial/conic (repeating-)gradient, in order."""
    if not background_image or not is_gradient(background_image):
        return []
    text = _URL_RE.sub(" ", background_image)
    colors: list[RGBA] = []
    for body in _gradient_bodies(text):
        for argument in split_top_level(body, ","):
            for token in split_top_level(argument, " "):
                if token[0] in "0123456789.+-":
                    continue
                color = parse_color(token)
                if color is not None:
                    colors.append(color)
    return colors


# ─── Shadows ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ShadowLayer:
    offset_x: float
    offset_y: float
    blur: float = 0.0
    spread: float = 0.0
    color: RGBA | None = None
    inset: bool = False

    @property
    def is_visible(self) -> bool:
        if self.color is not None and self.color.is_transparent:
            return False
        return any(value != 0 for value in (self.offset_x, self.offset_y, self.blur, self.spread))

    def to_css(self) -> str:
        """ "0px 1px 2px 0px rgba(15, 23, 42, 0.06)" (offsets, blur, spread, color)."""
        parts = [format_px(v) for v in (self.offset_x, self.offset_y, self.blur, self.spread)]
        if self.color is not None:
            parts.append(self.color.to_css())
        text = " ".join(parts)
        return f"inset {text}" if self.inset else text


def parse_box_shadow(
    value: str | None, *, current_color: str | RGBA | None = None
) -> list[ShadowLayer]:
    """All layers of a box-shadow; accepts color-first (computed) and color-last orders."""
    if value is None or value.strip().lower() in _NO_VALUE:
        return []
    layers: list[ShadowLayer] = []
    for part in split_top_level(value, ","):
        inset = False
        color: RGBA | None = None
        lengths: list[float] = []
        for token in split_top_level(part, " "):
            if token.lower() == "inset":
                inset = True
                continue
            length = parse_px(token)
            if length is not None:
                lengths.append(length)
                continue
            parsed = parse_color(token, current_color=current_color)
            if parsed is not None and color is None:
                color = parsed
        if len(lengths) < 2:
            continue
        lengths.extend([0.0] * (4 - len(lengths)))
        layers.append(
            ShadowLayer(lengths[0], lengths[1], max(lengths[2], 0.0), lengths[3], color, inset)
        )
    return layers


def format_box_shadow(layers: Sequence[ShadowLayer]) -> str:
    """Normalized CSS for shadow layers ("none" when empty)."""
    return ", ".join(layer.to_css() for layer in layers) if layers else "none"


def visible_shadow_layers(value: str | None) -> list[ShadowLayer]:
    """parse_box_shadow() without invisible layers (transparent or all-zero)."""
    return [layer for layer in parse_box_shadow(value) if layer.is_visible]


# ─── Fonts ───────────────────────────────────────────────────────────────────

GENERIC_FONT_FAMILIES = frozenset(
    {
        "serif",
        "sans-serif",
        "monospace",
        "cursive",
        "fantasy",
        "system-ui",
        "ui-serif",
        "ui-sans-serif",
        "ui-monospace",
        "ui-rounded",
        "math",
        "emoji",
        "fangsong",
    }
)
SYSTEM_FONT_ALIASES = frozenset({"-apple-system", "blinkmacsystemfont"})
_MONO_NAMES = ("mono", "code", "consolas", "courier", "menlo", "monaco")


def parse_font_family(value: str | None) -> list[str]:
    """'"Sohne", Inter, sans-serif' -> ["Sohne", "Inter", "sans-serif"] (quotes removed)."""
    if not value:
        return []
    families: list[str] = []
    for part in split_top_level(value, ","):
        name = " ".join(part.strip().strip("\"'").split())
        if name:
            families.append(name)
    return families


def is_generic_family(name: str) -> bool:
    """CSS generic family keywords and the -apple-system / BlinkMacSystemFont aliases."""
    lowered = name.strip().lower()
    return lowered in GENERIC_FONT_FAMILIES or lowered in SYSTEM_FONT_ALIASES


def primary_font_family(value: str | Sequence[str] | None) -> str | None:
    """First non-generic family of a stack, else the first entry ("system-ui")."""
    stack = parse_font_family(value) if isinstance(value, str) or value is None else list(value)
    for name in stack:
        if not is_generic_family(name):
            return name
    return stack[0] if stack else None


def is_monospace_family(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered in ("monospace", "ui-monospace") or any(hint in lowered for hint in _MONO_NAMES)


def is_monospace_stack(value: str | Sequence[str] | None) -> bool:
    """True when the primary family (or the generic fallback) is monospace."""
    stack = parse_font_family(value) if isinstance(value, str) or value is None else list(value)
    primary = primary_font_family(stack)
    if primary is not None and is_monospace_family(primary):
        return True
    return any(name.lower() in ("monospace", "ui-monospace") for name in stack[-1:])


def parse_font_weight(value: str | None) -> int:
    """ "normal" -> 400, "bold" -> 700, "600" -> 600; unknown -> 400."""
    if value is None:
        return 400
    text = value.strip().lower()
    keywords = {"normal": 400, "bold": 700, "lighter": 300, "bolder": 700}
    if text in keywords:
        return keywords[text]
    number = parse_number(text)
    if number is None:
        return 400
    return int(_clamp(round(number), 1, 1000))


def parse_line_height(
    value: str | None, font_size_px: float, *, normal: float | None = None
) -> float | None:
    """Line height in px. Unitless/percent values multiply font_size_px.

    "normal" gives None unless `normal` (a multiplier such as NORMAL_LINE_HEIGHT) is given.
    """
    if value is None:
        return None
    text = value.strip().lower()
    if text == "normal":
        return None if normal is None else normal * font_size_px
    number = parse_number(text)
    if number is not None:
        return number * font_size_px
    if text.endswith("%"):
        percent = parse_number(text[:-1])
        return None if percent is None else percent / 100.0 * font_size_px
    return parse_px(text, font_size_px=font_size_px)


def parse_letter_spacing(value: str | None, font_size_px: float) -> float:
    """Letter spacing in px ("normal" -> 0.0, "-0.02em" -> -0.02 * font_size_px)."""
    result = parse_px(value, font_size_px=font_size_px)
    return 0.0 if result is None else result


def is_large_text(font_size_px: float, weight: int) -> bool:
    """WCAG large text: >= 24px, or >= 18.66px (14pt) when bold (>= 700)."""
    return font_size_px >= 24.0 or (font_size_px >= 18.66 and weight >= 700)


# ─── Contrast, OKLCH, ΔE ─────────────────────────────────────────────────────


def _linear_channel(channel: int) -> float:
    value = channel / 255.0
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(color: RGBA) -> float:
    """WCAG 2.x relative luminance (alpha ignored)."""
    return (
        0.2126 * _linear_channel(color.r)
        + 0.7152 * _linear_channel(color.g)
        + 0.0722 * _linear_channel(color.b)
    )


def _coerce_color(value: RGBA | str) -> RGBA:
    if isinstance(value, RGBA):
        return value
    parsed = parse_color(value)
    if parsed is None:
        raise ValueError(f"Not a CSS color: {value!r}")
    return parsed


def contrast_ratio(foreground: RGBA | str, background: RGBA | str, *, base: RGBA = WHITE) -> float:
    """WCAG contrast ratio (1..21) of foreground painted on background.

    A translucent background is first flattened on `base` (the canvas), then a translucent
    foreground is composited on the result. Raises ValueError for unparseable strings.
    """
    back = flatten(_coerce_color(background), base)
    front = flatten(_coerce_color(foreground), back)
    lighter, darker = sorted((relative_luminance(front), relative_luminance(back)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def contrast_levels(ratio: float) -> tuple[bool, bool, bool]:
    """(passes_aa, passes_aa_large, passes_aaa) for normal-size text."""
    return (ratio >= AA_NORMAL, ratio >= AA_LARGE, ratio >= AAA_NORMAL)


@dataclass(frozen=True, slots=True)
class OKLCH:
    lightness: float
    chroma: float
    #: Degrees 0..360, None for achromatic colors.
    hue: float | None


def to_oklch(color: RGBA) -> OKLCH:
    """OKLCH of the opaque color (lightness 0..1, rounded to 4 decimals; hue to 2)."""
    converted = color.with_alpha(1.0).to_color().convert("oklch")
    lightness, chroma, hue = converted.coords()
    lightness = 0.0 if math.isnan(lightness) else lightness
    chroma = 0.0 if math.isnan(chroma) else max(chroma, 0.0)
    hue_value: float | None = None if math.isnan(hue) or chroma < 1e-4 else hue % 360.0
    return OKLCH(
        round(lightness, 4),
        round(chroma, 4),
        None if hue_value is None else round(hue_value, 2),
    )


def is_neutral(color: RGBA, *, max_chroma: float = NEUTRAL_MAX_CHROMA) -> bool:
    """Grays and tinted neutrals (slate, zinc...) have OKLCH chroma below max_chroma."""
    return to_oklch(color).chroma < max_chroma


@lru_cache(maxsize=65536)
def _delta_e_cached(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    a = Color("srgb", [channel / 255.0 for channel in first])
    b = Color("srgb", [channel / 255.0 for channel in second])
    return float(a.delta_e(b, method="2000"))


def delta_e_2000(first: RGBA, second: RGBA) -> float:
    """CIEDE2000 distance of the opaque colors (alpha must be compared separately)."""
    key_a = (first.r, first.g, first.b)
    key_b = (second.r, second.g, second.b)
    if key_a == key_b:
        return 0.0
    return _delta_e_cached(min(key_a, key_b), max(key_a, key_b))


# ─── Media queries ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MediaWidths:
    min_width_px: float | None = None
    max_width_px: float | None = None
    prefers_color_scheme: Literal["light", "dark"] | None = None


_FEATURE_RE = re.compile(r"\(([^()]*)\)")
_RANGE_SPLIT_RE = re.compile(r"(<=|>=|<|>|=)")


def _media_length(value: str) -> float | None:
    return parse_px(value.strip(), font_size_px=16.0, root_font_size_px=16.0)


def parse_media_query(query: str) -> MediaWidths:
    """Width range and color scheme of a media query.

    Supports (min-width: 768px), (max-width: 47.9375em), range syntax (width >= 768px),
    (640px <= width < 1024px) and (prefers-color-scheme: dark). Queries starting with "not"
    give no widths. For a comma list the first part with a width feature wins.
    """
    min_width: float | None = None
    max_width: float | None = None
    scheme: Literal["light", "dark"] | None = None
    for part in split_top_level(query.lower(), ","):
        if part.startswith("not "):
            continue
        part_min: float | None = None
        part_max: float | None = None
        for feature in _FEATURE_RE.findall(part):
            if ":" in feature:
                name, _, raw = feature.partition(":")
                name = name.strip()
                if name == "min-width":
                    part_min = _media_length(raw)
                elif name == "max-width":
                    part_max = _media_length(raw)
                elif name == "prefers-color-scheme" and raw.strip() in ("light", "dark"):
                    scheme = "dark" if raw.strip() == "dark" else "light"
                continue
            tokens = [token.strip() for token in _RANGE_SPLIT_RE.split(feature)]
            if len(tokens) == 3:
                left, operator, right = tokens
                if left == "width":
                    bound = _media_length(right)
                    if operator in (">", ">="):
                        part_min = bound
                    elif operator in ("<", "<="):
                        part_max = bound
                    else:
                        part_min = part_max = bound
                elif right == "width":
                    bound = _media_length(left)
                    if operator in ("<", "<="):
                        part_min = bound
                    elif operator in (">", ">="):
                        part_max = bound
                    else:
                        part_min = part_max = bound
            elif len(tokens) == 5 and tokens[2] == "width":
                low, first_op, _, second_op, high = tokens
                if first_op in ("<", "<="):
                    part_min = _media_length(low)
                elif first_op in (">", ">="):
                    part_max = _media_length(low)
                if second_op in ("<", "<="):
                    part_max = _media_length(high)
                elif second_op in (">", ">="):
                    part_min = _media_length(high)
        if (
            (part_min is not None or part_max is not None)
            and min_width is None
            and max_width is None
        ):
            min_width, max_width = part_min, part_max
    return MediaWidths(min_width, max_width, scheme)


def parse_opacity(value: str | None) -> float:
    """ "0.5" / "50%" -> 0.5; unknown -> 1.0."""
    if value is None:
        return 1.0
    text = value.strip()
    if text.endswith("%"):
        percent = parse_number(text[:-1])
        return 1.0 if percent is None else _clamp(percent / 100.0, 0.0, 1.0)
    number = parse_number(text)
    return 1.0 if number is None else _clamp(number, 0.0, 1.0)
