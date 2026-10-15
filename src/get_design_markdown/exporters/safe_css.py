"""Rebuild untrusted CSS values from the analyzed site into safe, normalized CSS.

Every value that reaches preview.html's <style> block comes from a web page we do not control,
so nothing is copied verbatim: colors are re-emitted from `parse_color`, lengths from
`parse_px` + `format_px`, shadows from `parse_box_shadow` + `format_box_shadow`, font names are
stripped to a small character whitelist and keywords are checked against fixed lists. Every
function returns None (or an empty result) when the input cannot be rebuilt.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from get_design_markdown.analyzer.css_values import (
    RGBA,
    format_box_shadow,
    format_px,
    is_generic_family,
    parse_box_shadow,
    parse_color,
    parse_font_family,
    parse_font_weight,
    parse_number,
    parse_px,
)
from get_design_markdown.models.analysis import FontCategory

#: Largest absolute length accepted (pill radii are written 9999px).
MAX_LENGTH_PX = 100_000.0
#: Punctuation kept in a font family name besides letters and digits (any script).
_FONT_NAME_PUNCTUATION = frozenset(" _.'-")
_IDENT_DROP_RE = re.compile(r"[^a-z0-9-]+")
_PERCENT_RE = re.compile(r"^(\d+(?:\.\d+)?)%$")
_FILTER_RE = re.compile(r"([a-z-]+)\(([^()]*)\)")
_LANG_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")

GENERIC_FALLBACK: dict[FontCategory, str] = {
    "sans-serif": "sans-serif",
    "serif": "serif",
    "monospace": "monospace",
    "display": "sans-serif",
    "handwriting": "cursive",
    "unknown": "sans-serif",
}
TEXT_TRANSFORMS = frozenset({"none", "uppercase", "lowercase", "capitalize"})
TEXT_DECORATIONS = ("none", "underline", "overline", "line-through")
_FILTER_NUMBER_FUNCTIONS = frozenset(
    {"saturate", "brightness", "contrast", "opacity", "grayscale", "invert", "sepia"}
)


def ident(name: str, *, fallback: str = "x") -> str:
    """Any text -> a safe CSS identifier fragment made of [a-z0-9-] ("Body LG" -> "body-lg")."""
    slug = _IDENT_DROP_RE.sub("-", name.lower()).strip("-")
    return slug or fallback


def css_color(value: str | RGBA | None) -> str | None:
    """Any CSS color -> "#rrggbb" / "#rrggbbaa" (None when unparseable)."""
    color = value if isinstance(value, RGBA) else parse_color(value)
    return None if color is None else color.css_hex


def css_length(value: str | None, *, allow_negative: bool = True) -> str | None:
    """A CSS length -> normalized px ("1.5rem" -> "24px"); percentages are kept as "50%"."""
    if value is None:
        return None
    text = value.strip().lower()
    percent = _PERCENT_RE.match(text)
    if percent is not None:
        number = float(percent.group(1))
        return f"{format_px(min(number, 100.0)).removesuffix('px')}%"
    px = parse_px(text)
    if px is None or abs(px) > MAX_LENGTH_PX or (px < 0 and not allow_negative):
        return None
    return format_px(px)


def length_px(value: str | None) -> float | None:
    """A CSS length in px (None for percentages, keywords and out-of-range values)."""
    px = parse_px(value)
    if px is None or abs(px) > MAX_LENGTH_PX:
        return None
    return px


def css_sides(value: str | None, *, allow_negative: bool = False) -> str | None:
    """A 1–4 value length shorthand ("12px 20px", "0px 0px 1px") rebuilt side by side."""
    if value is None:
        return None
    parts = value.split()
    if not 1 <= len(parts) <= 4:
        return None
    rebuilt = [css_length(part, allow_negative=allow_negative) for part in parts]
    if any(part is None or part.endswith("%") for part in rebuilt):
        return None
    return " ".join(part for part in rebuilt if part is not None)


def sides_px(value: str | None) -> tuple[float, float, float, float] | None:
    """A 1–4 value shorthand -> (top, right, bottom, left) in px (None when any part fails)."""
    if value is None:
        return None
    parts = value.split()
    if not 1 <= len(parts) <= 4:
        return None
    numbers = [length_px(part) for part in parts]
    if any(number is None for number in numbers):
        return None
    values = [number for number in numbers if number is not None]
    if len(values) == 1:
        values = values * 4
    elif len(values) == 2:
        values = [values[0], values[1], values[0], values[1]]
    elif len(values) == 3:
        values = [values[0], values[1], values[2], values[1]]
    return (values[0], values[1], values[2], values[3])


def css_shadow(value: str | None) -> str | None:
    """A box-shadow list rebuilt layer by layer (None when no layer can be parsed)."""
    layers = [
        layer
        for layer in parse_box_shadow(value)
        if all(
            abs(number) <= MAX_LENGTH_PX
            for number in (layer.offset_x, layer.offset_y, layer.blur, layer.spread)
        )
    ]
    return format_box_shadow(layers) if layers else None


def clean_font_name(name: str) -> str:
    """Font family name reduced to letters and digits (any script, so "Söhne" or CJK names
    survive) plus [ _.'-], spaces collapsed; "" when nothing is left. None of the kept
    characters can end a quoted CSS string or the <style> element."""
    kept = "".join(char for char in name if char.isalnum() or char in _FONT_NAME_PUNCTUATION)
    return " ".join(kept.split())


def css_font_stack(
    families: Iterable[str] | str | None, *, category: FontCategory = "unknown"
) -> str:
    """A font-family list with every name quoted and cleaned; generic keywords stay bare and a
    generic fallback for the category is appended when the stack has none."""
    if families is None or isinstance(families, str):
        names = parse_font_family(families)
    else:
        names = list(families)
    parts: list[str] = []
    seen: set[str] = set()
    has_generic = False
    for name in names:
        if is_generic_family(name):
            keyword = name.strip().lower()
            has_generic = has_generic or keyword not in ("-apple-system", "blinkmacsystemfont")
            entry = keyword
        else:
            cleaned = clean_font_name(name)
            if not cleaned:
                continue
            entry = f'"{cleaned}"'
        if entry.lower() not in seen:
            seen.add(entry.lower())
            parts.append(entry)
    if not has_generic:
        parts.append(GENERIC_FALLBACK[category])
    return ", ".join(parts)


def css_font_weight(value: str | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return str(max(1, min(1000, value)))
    text = value.strip().lower()
    if parse_number(text) is None and text not in ("normal", "bold", "lighter", "bolder"):
        return None
    return str(parse_font_weight(text))


def css_line_height(value: str | None) -> str | None:
    """Unitless multipliers stay unitless (clamped to 0–10); lengths become px."""
    if value is None:
        return None
    text = value.strip().lower()
    if text == "normal":
        return "normal"
    number = parse_number(text)
    if number is not None:
        return format_px(max(0.0, min(number, 10.0)), precision=3).removesuffix("px")
    return css_length(text, allow_negative=False)


def opacity_value(value: str | None) -> float | None:
    """ "0.6" / "60%" -> 0.6 (clamped to 0..1); anything else -> None."""
    text = (value or "").strip()
    percent = _PERCENT_RE.match(text)
    number = float(percent.group(1)) / 100.0 if percent is not None else parse_number(text)
    return None if number is None else max(0.0, min(1.0, number))


def css_opacity(value: str | None) -> str | None:
    number = opacity_value(value)
    return None if number is None else format_px(number, precision=3).removesuffix("px")


def css_text_transform(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    return text if text in TEXT_TRANSFORMS else None


def css_text_decoration(value: str | None) -> str | None:
    """The decoration line of a (possibly long-form) value: "underline solid rgb(...)" ->
    "underline"."""
    words = set((value or "").strip().lower().split())
    lines = [line for line in TEXT_DECORATIONS[1:] if line in words]
    if lines:
        return " ".join(lines)
    return "none" if "none" in words else None


def css_backdrop_filter(value: str | None) -> str | None:
    """blur()/saturate()/brightness()... functions rebuilt with numeric arguments only."""
    if value is None:
        return None
    text = value.strip().lower()
    if text in ("", "none"):
        return None
    functions: list[str] = []
    for name, argument in _FILTER_RE.findall(text):
        argument = argument.strip()
        if name == "blur":
            length = css_length(argument or "0px", allow_negative=False)
            if length is not None and not length.endswith("%"):
                functions.append(f"blur({length})")
        elif name in _FILTER_NUMBER_FUNCTIONS:
            percent = _PERCENT_RE.match(argument)
            number = (
                float(percent.group(1)) / 100.0 if percent is not None else parse_number(argument)
            )
            if number is not None and 0 <= number <= 100:
                functions.append(f"{name}({format_px(number, precision=3).removesuffix('px')})")
        elif name == "hue-rotate" and argument.endswith("deg"):
            degrees = parse_number(argument.removesuffix("deg"))
            if degrees is not None:
                degrees_text = format_px(degrees, precision=2).removesuffix("px")
                functions.append(f"hue-rotate({degrees_text}deg)")
    return " ".join(functions) if functions else None


def html_lang(value: str | None) -> str:
    """A BCP 47-looking language tag, else "en"."""
    text = (value or "").strip()
    return text if _LANG_RE.match(text) else "en"


def http_url(value: str | None) -> str | None:
    """The URL when it is an absolute http(s) URL without spaces or quotes, else None."""
    text = (value or "").strip()
    lowered = text.lower()
    if not lowered.startswith(("http://", "https://")):
        return None
    if any(char.isspace() or char in "\"'<>`\\" for char in text):
        return None
    return text


def unique(values: Sequence[str]) -> list[str]:
    """Order-preserving de-duplication."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
