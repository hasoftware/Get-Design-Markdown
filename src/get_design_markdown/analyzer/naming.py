"""Canonical token names, token references ("{colors.primary}") and token lookups.

Shared contract between analyzers (which name tokens), the synthesizer (which writes them into
the DESIGN.md front matter) and the exporters. Group names follow the Google Labs DESIGN.md spec.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from typing import Final

from get_design_markdown.analyzer.css_values import RGBA, delta_e_2000, parse_color
from get_design_markdown.models.analysis import ColorRole, ColorToken, ScaleStep, TypeLevel

# ─── Front-matter groups ─────────────────────────────────────────────────────

COLORS: Final = "colors"
TYPOGRAPHY: Final = "typography"
ROUNDED: Final = "rounded"
SPACING: Final = "spacing"
COMPONENTS: Final = "components"
TOKEN_GROUPS: Final = (COLORS, TYPOGRAPHY, ROUNDED, SPACING, COMPONENTS)

# ─── Canonical names ─────────────────────────────────────────────────────────

#: Semantic color token names in front-matter order, with the role each one carries.
COLOR_TOKEN_ROLES: Final[dict[str, ColorRole]] = {
    "primary": ColorRole.BRAND,
    "secondary": ColorRole.ACCENT,
    "tertiary": ColorRole.ACCENT,
    "on-primary": ColorRole.TEXT,
    "on-secondary": ColorRole.TEXT,
    "canvas": ColorRole.BACKGROUND,
    "surface": ColorRole.SURFACE,
    "surface-raised": ColorRole.SURFACE,
    "surface-inverse": ColorRole.SURFACE,
    "hairline": ColorRole.BORDER,
    "hairline-strong": ColorRole.BORDER,
    "ink": ColorRole.TEXT,
    "muted": ColorRole.TEXT_MUTED,
    "faint": ColorRole.TEXT_MUTED,
    "on-inverse": ColorRole.TEXT,
    "success": ColorRole.SUCCESS,
    "warning": ColorRole.WARNING,
    "danger": ColorRole.DANGER,
    "info": ColorRole.INFO,
}
COLOR_TOKEN_ORDER: Final = tuple(COLOR_TOKEN_ROLES)

#: Semantic type levels, largest to smallest (a site uses a subset, typically 5–8).
TYPE_LEVEL_ORDER: Final = (
    "display",
    "heading",
    "title",
    "subtitle",
    "body-lg",
    "body",
    "body-sm",
    "ui",
    "caption",
    "overline",
    "code",
)
SPACING_STEP_NAMES: Final = ("xxs", "xs", "sm", "md", "lg", "xl", "xxl", "xxxl")
RADIUS_STEP_NAMES: Final = ("xs", "sm", "md", "lg", "xl", "xxl", "xxxl")
#: Radius step name for pills/circles (value >= PILL_RADIUS_PX, or 50%).
FULL_RADIUS_NAME: Final = "full"
PILL_RADIUS_PX: Final = 500.0
FULL_RADIUS_PX: Final = 9999.0
SHADOW_LEVEL_NAMES: Final = ("xs", "sm", "md", "lg", "xl", "xxl")
BREAKPOINT_NAMES: Final = ("xs", "sm", "md", "lg", "xl", "xxl", "xxxl")
#: Component names in the order the synthesizer lists them; variants follow "<kind>-<variant>".
COMPONENT_NAME_ORDER: Final = (
    "button-primary",
    "button-secondary",
    "button-tertiary",
    "button-outline",
    "button-ghost",
    "button-link",
    "text-link",
    "nav-link",
    "text-input",
    "card",
    "card-elevated",
    "badge",
    "top-nav",
    "sidebar",
    "footer",
)
HOVER_STATE: Final = "hover"

_TOKEN_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
TOKEN_REF_RE: Final = re.compile(
    r"\{(colors|typography|rounded|spacing|components)\.([a-z0-9]+(?:-[a-z0-9]+)*)\}"
)


def is_token_name(name: str) -> bool:
    return _TOKEN_NAME_RE.match(name) is not None


def slugify(text: str, *, fallback: str = "item") -> str:
    """Any text -> kebab-case token name ("Button Primary" -> "button-primary")."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or fallback


def token_ref(group: str, name: str) -> str:
    """token_ref("colors", "primary") -> "{colors.primary}"."""
    if group not in TOKEN_GROUPS:
        raise ValueError(f"Unknown token group: {group!r}")
    if not is_token_name(name):
        raise ValueError(f"Invalid token name: {name!r}")
    return "{" + group + "." + name + "}"


def parse_token_ref(value: str) -> tuple[str, str] | None:
    """ "{colors.primary}" -> ("colors", "primary"); anything else -> None."""
    match = TOKEN_REF_RE.fullmatch(value.strip())
    return None if match is None else (match.group(1), match.group(2))


def find_token_refs(text: str) -> list[tuple[str, str]]:
    """Every {group.name} reference in a text, in order (duplicates kept)."""
    return [(match.group(1), match.group(2)) for match in TOKEN_REF_RE.finditer(text)]


def state_variant_name(component: str, state: str = HOVER_STATE) -> str:
    """ "button-primary" -> "button-primary-hover" (front-matter name of a state variant)."""
    return f"{component}-{state}"


def name_scale(
    values: Sequence[float],
    names: Sequence[str],
    *,
    anchor_value: float | None = None,
    anchor_name: str = "md",
) -> list[str]:
    """Names for ascending scale values, aligning the value closest to anchor_value with
    anchor_name. Extra values beyond the name list get "4xl", "5xl"...

    name_scale([4, 8, 16, 24], SPACING_STEP_NAMES, anchor_value=16) -> ["xs", "sm", "md", "lg"].
    """
    count = len(values)
    if count == 0:
        return []
    offset = 0
    if anchor_value is not None and anchor_name in names:
        anchor_index = min(range(count), key=lambda i: abs(values[i] - anchor_value))
        offset = names.index(anchor_name) - anchor_index
    offset = max(0, min(offset, max(len(names) - count, 0)))
    result: list[str] = []
    for position in range(count):
        slot = offset + position
        result.append(names[slot] if slot < len(names) else f"{slot - len(names) + 4}xl")
    return result


# ─── Lookups ─────────────────────────────────────────────────────────────────


def match_color_token(
    value: str | RGBA,
    tokens: Sequence[ColorToken],
    *,
    max_delta_e: float = 3.0,
    max_alpha_diff: float = 0.05,
    prefer_roles: Collection[ColorRole] = (),
    tie_delta_e: float = 1.0,
) -> ColorToken | None:
    """Palette token matching a color (ΔE2000 <= max_delta_e and similar alpha).

    The closest token wins. Among near-ties (within tie_delta_e of the closest distance),
    tokens whose role is in prefer_roles win, then the closer one, then token order — e.g.
    #ffffff text prefers "on-primary" (TEXT) over "canvas", while a #0f172a background still
    maps to the exact "ink" rather than a 2.6-ΔE-away "surface-inverse".
    """
    color = value if isinstance(value, RGBA) else parse_color(value)
    if color is None:
        return None
    candidates: list[tuple[float, int, ColorToken]] = []
    for position, token in enumerate(tokens):
        candidate = parse_color(token.value)
        if candidate is None or abs(candidate.alpha - color.alpha) > max_alpha_diff:
            continue
        distance = delta_e_2000(candidate, color)
        if distance <= max_delta_e:
            candidates.append((distance, position, token))
    if not candidates:
        return None
    closest = min(distance for distance, _, _ in candidates)
    near = [item for item in candidates if item[0] <= closest + tie_delta_e]

    def rank(item: tuple[float, int, ColorToken]) -> tuple[int, float, int]:
        distance, position, token = item
        return (0 if token.role in prefer_roles else 1, distance, position)

    return min(near, key=rank)[2]


def match_scale_step(
    value_px: float,
    steps: Sequence[ScaleStep],
    *,
    tolerance_px: float = 0.5,
) -> ScaleStep | None:
    """Step whose value equals value_px within tolerance; pills match the "full" step."""
    if value_px >= PILL_RADIUS_PX:
        for step in steps:
            if step.name == FULL_RADIUS_NAME or step.value_px >= PILL_RADIUS_PX:
                return step
        return None
    best: ScaleStep | None = None
    for step in steps:
        difference = abs(step.value_px - value_px)
        if difference <= tolerance_px and (
            best is None or difference < abs(best.value_px - value_px)
        ):
            best = step
    return best


def match_type_level(
    size_px: float,
    weight: int | None,
    levels: Sequence[TypeLevel],
    *,
    family: str | None = None,
    tolerance_px: float = 0.5,
) -> TypeLevel | None:
    """Type level with the same size (within tolerance); ties broken by weight then family."""
    candidates = [level for level in levels if abs(level.size_px - size_px) <= tolerance_px]
    if not candidates:
        return None

    def score(level: TypeLevel) -> tuple[int, int, float]:
        weight_gap = 0 if weight is None else abs(level.weight - weight)
        family_gap = 0 if family is None or level.font_family == family else 1
        return (family_gap, weight_gap, abs(level.size_px - size_px))

    return min(candidates, key=score)
