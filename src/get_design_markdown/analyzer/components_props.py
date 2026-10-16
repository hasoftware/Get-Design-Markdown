"""Internal helpers of the component analyzer: properties, token references, hover states,
contrast, class hints and samples.

Private to analyzer/components*.py (stream analyzer-components); not a public interface.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from get_design_markdown.analyzer.common import (
    effective_background,
    meaningful_classes,
)
from get_design_markdown.analyzer.components_context import (
    Member,
    PageContext,
    backdrop,
    canonical_color,
    corner_radii,
    css_color,
    first_visible_side,
    has_text,
    is_pill,
    padding_px,
    rounded_value,
    shadow_layers,
    text_color,
    uneven_corners,
    visible_border,
)
from get_design_markdown.analyzer.css_values import (
    DEFAULT_FONT_SIZE_PX,
    NORMAL_LINE_HEIGHT,
    RGBA,
    composite,
    contrast_ratio,
    delta_e_2000,
    format_box_shadow,
    format_px,
    format_sides_px,
    parse_color,
    parse_font_family,
    parse_font_weight,
    parse_letter_spacing,
    parse_line_height,
    parse_opacity,
    parse_px,
    primary_font_family,
    to_oklch,
    visible_shadow_layers,
)
from get_design_markdown.analyzer.naming import (
    COLORS,
    FULL_RADIUS_NAME,
    HOVER_STATE,
    PILL_RADIUS_PX,
    ROUNDED,
    SPACING,
    TYPOGRAPHY,
    match_color_token,
    match_scale_step,
    match_type_level,
    token_ref,
)
from get_design_markdown.models.analysis import (
    ColorPalette,
    ColorRole,
    ColorToken,
    ScaleStep,
    SpacingSystem,
    Typography,
)
from get_design_markdown.models.raw import ElementSample, join_sides, split_sides

#: Roles preferred when a background color is ambiguous between tokens.
SURFACE_ROLES = frozenset(
    {ColorRole.BACKGROUND, ColorRole.SURFACE, ColorRole.BRAND, ColorRole.ACCENT}
)
#: Roles preferred for text-like colors.
TEXT_ROLES = frozenset({ColorRole.TEXT, ColorRole.TEXT_MUTED, ColorRole.BRAND, ColorRole.ACCENT})
#: Borders: a "hairline" beats a surface token of the same value.
BORDER_ROLES = frozenset({ColorRole.BORDER})
_ROLES_BY_KEY: dict[str, frozenset[ColorRole]] = {
    "backgroundColor": SURFACE_ROLES,
    "borderColor": BORDER_ROLES,
    "textColor": TEXT_ROLES,
    "placeholderColor": TEXT_ROLES,
}
#: Fills and text never reference a border token (a translucent hover fill that happens to
#: equal the hairline is still a fill), and translucent colors only match the same alpha.
_FILL_KEYS = frozenset({"backgroundColor", "textColor", "placeholderColor"})
_REF_MAX_ALPHA_DIFF = 0.01
REF_MAX_DELTA_E = 3.0
#: Hover colors this dark (OKLCH lightness, as seen) only snap to a token this close:
#: CIEDE2000 compresses lightness steps near black (#191a1b vs #0f1011 is only ΔE 2.8).
DARK_LIGHTNESS = 0.3
DARK_REF_MAX_DELTA_E = 1.5
#: Smaller lightness changes (OKLCH) do not give a hover a direction.
HOVER_LIGHTNESS_EPSILON = 0.005
#: A component's letter-spacing must match its type level's within this (px).
_LETTER_SPACING_TOLERANCE_PX = 0.05

#: Canonical order of ComponentSpec.properties keys.
PROPERTY_ORDER = (
    "backgroundColor",
    "textColor",
    "borderColor",
    "borderWidth",
    "rounded",
    "padding",
    "height",
    "width",
    "gap",
    "fontFamily",
    "fontSize",
    "fontWeight",
    "lineHeight",
    "letterSpacing",
    "textTransform",
    "textDecoration",
    "placeholderColor",
    "shadow",
    "opacity",
    "position",
    "backdropFilter",
)
_SURFACE_COLOR_KEYS = ("backgroundColor", "borderColor")
_TEXT_COLOR_KEYS = ("textColor", "placeholderColor")
#: ComputedStyle field -> property key, for hover diffs.
HOVER_FIELDS = {
    "background_color": "backgroundColor",
    "color": "textColor",
    "border_color": "borderColor",
    "box_shadow": "shadow",
    "text_decoration_line": "textDecoration",
    "opacity": "opacity",
}
_TYPE_SIZE_TOLERANCE_PX = 0.5
#: A {typography.*} reference needs the level's line height within this many px.
_LINE_HEIGHT_TOLERANCE_PX = 1.0
_UTILITY_PREFIX = (
    r"p|px|py|pt|pr|pb|pl|ps|pe|m|mx|my|mt|mr|mb|ml|ms|me|w|h|size|min-w|min-h|max-w|max-h|"
    r"gap|gap-x|gap-y|space-x|space-y|text|bg|border|border-[trblxyse]|rounded|"
    r"rounded-(?:[trblse]|tl|tr|bl|br)|shadow|font|leading|tracking|items|justify|self|"
    r"place-items|place-content|content|basis|flex|grow|shrink|order|col|col-span|row|row-span|"
    r"grid-cols|grid-rows|z|top|left|right|bottom|inset|inset-[xy]|opacity|transition|duration|"
    r"ease|delay|cursor|overflow|overflow-[xy]|whitespace|line-clamp|ring|ring-offset|outline|"
    r"fill|stroke|translate-[xyz]|scale|rotate|aspect|object|decoration|underline-offset|"
    r"backdrop-blur|blur|divide|divide-[xy]|from|via|to|mix-blend|select|pointer-events|"
    r"outline-offset|align|list|will-change|break|hyphens|isolation|appearance|resize|"
    r"snap|touch|caret|accent|columns|float|clear|indent|origin|skew-[xy]|tabular|ordinal"
)
#: Color-like utilities with a custom palette name: bg-background-200, text-copy-14...
_UTILITY_SHADE_RE = re.compile(
    r"^-?(?:bg|text|border|ring|fill|stroke|from|via|to|divide|outline|decoration|shadow|"
    r"accent|caret)-[a-z]+(?:-[a-z]+)*-\d{1,4}(?:/\d{1,3})?$"
)
_UTILITY_COLOR = (
    r"(?:slate|gray|grey|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|"
    r"cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|white|black|transparent|current|"
    r"inherit)(?:-\d{2,3})?(?:/\d{1,3})?"
)
_UTILITY_VALUE = (
    r"(?:\d+(?:\.\d+)?|\d+/\d+|px|full|auto|screen|svh|dvh|min|max|fit|none|xs|sm|md|lg|"
    r"xl|[2-9]xl|base|" + _UTILITY_COLOR + r"|left|center|right|justify|start|end|between|"
    r"around|evenly|stretch|baseline|col|row|wrap|nowrap|reverse|hidden|visible|scroll|clip|"
    r"thin|extralight|light|normal|medium|semibold|bold|extrabold|tight|tighter|snug|relaxed|"
    r"loose|wide|wider|widest|sans|serif|mono|inner|ellipsis|pointer|default|grow|shrink|"
    r"in|out|in-out|linear|all|colors|opacity|transform|cover|contain|square|video|"
    r"[xytblrse]|\[[^\]]+\])"
)
_UTILITY_CLASS_RE = re.compile(rf"^-?(?:{_UTILITY_PREFIX})(?:-{_UTILITY_VALUE}){{0,2}}$")
_UTILITY_WORDS = frozenset(
    {
        "flex",
        "inline-flex",
        "grid",
        "inline-grid",
        "block",
        "inline-block",
        "inline",
        "hidden",
        "contents",
        "relative",
        "absolute",
        "fixed",
        "sticky",
        "static",
        "truncate",
        "uppercase",
        "lowercase",
        "capitalize",
        "underline",
        "no-underline",
        "italic",
        "antialiased",
        "transition",
        "transform",
        "sr-only",
        "group",
        "peer",
    }
)


@dataclass(frozen=True, slots=True)
class TokenContext:
    """The other analyses, used to resolve token references."""

    palette: ColorPalette
    typography: Typography
    spacing: SpacingSystem
    radii: Sequence[ScaleStep]


# ─── Text styles ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TextStyle:
    color: RGBA | None
    family: str
    size_px: float
    weight: int
    line_height_px: float
    letter_spacing_px: float
    text_transform: str
    #: The computed line-height was "normal" (line_height_px is then size x 1.2).
    line_height_normal: bool = False


def text_source(ctx: PageContext, element: ElementSample) -> ElementSample:
    """The element rendering the label: itself when it has own text, else the descendant with
    the longest readable own text (e.g. <a><span>Label</span></a>)."""
    if element.has_own_text and has_text(element.text):
        return element
    best: ElementSample | None = None
    for node in ctx.descendants(element, max_depth=6):
        if not node.has_own_text or not has_text(node.text):
            continue
        if best is None or len(node.text) > len(best.text):
            best = node
    return best if best is not None else element


def font_family_name(element: ElementSample, typography: Typography) -> str:
    """Canonical family name (as in typography.families) of the element's font stack."""
    stack = parse_font_family(element.styles.font_family)
    primary = primary_font_family(stack)
    candidates = [name for name in ([primary] if primary else []) + stack if name]
    for candidate in candidates:
        lowered = candidate.lower()
        for family in typography.families:
            if family.name.lower() == lowered or (
                family.stack and family.stack[0].lower() == lowered
            ):
                return family.name
    return primary or "sans-serif"


def text_style(element: ElementSample, typography: Typography) -> TextStyle:
    styles = element.styles
    size = parse_px(styles.font_size) or DEFAULT_FONT_SIZE_PX
    line_height = parse_line_height(styles.line_height, size, normal=NORMAL_LINE_HEIGHT)
    transform = styles.text_transform.strip().lower() or "none"
    return TextStyle(
        color=text_color(element),
        family=font_family_name(element, typography),
        size_px=size,
        weight=parse_font_weight(styles.font_weight),
        line_height_px=size * NORMAL_LINE_HEIGHT if line_height is None else line_height,
        letter_spacing_px=parse_letter_spacing(styles.letter_spacing, size),
        text_transform=transform,
        line_height_normal=styles.line_height.strip().lower() == "normal",
    )


def text_properties(style: TextStyle) -> dict[str, str]:
    properties = {
        "textColor": css_color(style.color),
        "fontFamily": style.family,
        "fontSize": format_px(style.size_px),
        "fontWeight": str(style.weight),
        "lineHeight": format_px(style.line_height_px),
    }
    if abs(style.letter_spacing_px) >= 0.005:
        properties["letterSpacing"] = format_px(style.letter_spacing_px)
    if style.text_transform != "none":
        properties["textTransform"] = style.text_transform
    return properties


def dominant_text(ctx: PageContext, root: ElementSample) -> ElementSample | None:
    """Own-text descendant carrying the most common text color/style (weighted by length)."""
    weights: Counter[tuple[str, str, str, str]] = Counter()
    first: dict[tuple[str, str, str, str], ElementSample] = {}
    nodes = [root, *ctx.descendants(root)]
    for node in nodes:
        if not node.has_own_text or not has_text(node.text):
            continue
        styles = node.styles
        key = (
            canonical_color(styles.color) or "",
            styles.font_size,
            styles.font_weight,
            styles.font_family,
        )
        weights[key] += len(node.text)
        first.setdefault(key, node)
    if not weights:
        return None
    key, _ = weights.most_common(1)[0]
    return first[key]


# ─── Box properties ──────────────────────────────────────────────────────────


def background_property(element: ElementSample) -> str:
    return css_color(parse_color(element.styles.background_color))


def box_properties(
    element: ElementSample,
    *,
    background: bool,
    height: bool,
    padding_from: ElementSample | None = None,
) -> dict[str, str]:
    """backgroundColor, border, rounded, padding, height, shadow, backdropFilter."""
    properties: dict[str, str] = {}
    if background:
        properties["backgroundColor"] = background_property(element)
    border = visible_border(element)
    if border is not None:
        color, widths = border
        properties["borderColor"] = css_color(color)
        properties["borderWidth"] = format_sides_px(widths)
    rounded = rounded_value(corner_radii(element))
    if rounded is not None:
        properties["rounded"] = rounded
    padding = padding_px(padding_from or element)
    if any(side > 0 for side in padding):
        properties["padding"] = format_sides_px(padding)
    if height:
        properties["height"] = format_px(element.box.height)
    layers = shadow_layers(element)
    if layers:
        properties["shadow"] = format_box_shadow(layers)
    backdrop_filter = element.styles.backdrop_filter.strip()
    if backdrop_filter and backdrop_filter.lower() != "none":
        properties["backdropFilter"] = backdrop_filter
    return properties


def ordered(properties: Mapping[str, str]) -> dict[str, str]:
    """Properties in PROPERTY_ORDER (unknown keys last, alphabetically)."""
    rank = {key: position for position, key in enumerate(PROPERTY_ORDER)}
    keys = sorted(properties, key=lambda key: (rank.get(key, len(rank)), key))
    return {key: properties[key] for key in keys}


# ─── Token references ────────────────────────────────────────────────────────


def color_ref(value: str, tokens: TokenContext, *, key: str) -> str | None:
    """{colors.<token>} of the closest palette token; near-ties go to the roles of `key`."""
    if value == "transparent":
        return None
    token = match_color_token(
        value,
        _ref_candidates(tokens, key),
        prefer_roles=_ROLES_BY_KEY[key],
        max_alpha_diff=_REF_MAX_ALPHA_DIFF,
        max_delta_e=REF_MAX_DELTA_E,
    )
    return None if token is None else token_ref(COLORS, token.name)


def _ref_candidates(tokens: TokenContext, key: str) -> list[ColorToken]:
    return [
        token
        for token in tokens.palette.tokens
        if key not in _FILL_KEYS or token.role != ColorRole.BORDER
    ]


def radius_ref(
    element: ElementSample, tokens: TokenContext, radius: float | None = None
) -> str | None:
    """{rounded.<step>} for the element's radius (or `radius` measured elsewhere); None when
    the corners differ, since a step stands for all four."""
    if radius is None:
        corners = corner_radii(element)
        if uneven_corners(corners):
            return None
        radius = max(corners)
    if radius <= 0:
        return None
    step = match_scale_step(radius, tokens.radii)
    if step is None and radius < PILL_RADIUS_PX and is_pill(element, radius):
        # A radius large enough to round the ends is rendered as a pill.
        step = next((item for item in tokens.radii if item.name == FULL_RADIUS_NAME), None)
    return None if step is None else token_ref(ROUNDED, step.name)


def padding_ref(element: ElementSample, tokens: TokenContext) -> str | None:
    sides = padding_px(element)
    if sides[0] <= 0 or any(abs(side - sides[0]) > 0.01 for side in sides):
        return None
    step = match_scale_step(sides[0], tokens.spacing.steps)
    return None if step is None else token_ref(SPACING, step.name)


def typography_ref(style: TextStyle, tokens: TokenContext) -> str | None:
    """Type level with the same size, the exact same weight, the same family, the same
    letter-spacing and (unless the element's line-height is "normal") the same line height
    within 1px: a reference stands for every value of the level, so a 14px/14px nav link is
    not a 14px / 1.2 level."""
    level = match_type_level(
        style.size_px,
        style.weight,
        tokens.typography.levels,
        family=style.family,
        tolerance_px=_TYPE_SIZE_TOLERANCE_PX,
    )
    if level is None or level.weight != style.weight:
        return None
    if level.font_family and level.font_family.lower() != style.family.lower():
        return None
    if abs(level.letter_spacing_px - style.letter_spacing_px) > _LETTER_SPACING_TOLERANCE_PX:
        return None
    if not style.line_height_normal:
        expected = level.line_height_px
        if expected is None and level.line_height is not None:
            expected = level.line_height * level.size_px
        if expected is not None and abs(expected - style.line_height_px) > (
            _LINE_HEIGHT_TOLERANCE_PX
        ):
            return None
    return token_ref(TYPOGRAPHY, level.name)


def color_refs(properties: Mapping[str, str], tokens: TokenContext) -> dict[str, str]:
    refs: dict[str, str] = {}
    for key in (*_SURFACE_COLOR_KEYS, *_TEXT_COLOR_KEYS):
        value = properties.get(key)
        if value is None:
            continue
        ref = color_ref(value, tokens, key=key)
        if ref is not None:
            refs[key] = ref
    return refs


def ordered_refs(refs: Mapping[str, str]) -> dict[str, str]:
    rank = {key: position for position, key in enumerate(PROPERTY_ORDER)}
    keys = sorted(refs, key=lambda key: (key == "typography", rank.get(key, len(rank)), key))
    return {key: refs[key] for key in keys}


# ─── Hover states ────────────────────────────────────────────────────────────


def _hover_value(field_name: str, value: str, element: ElementSample) -> str | None:
    if field_name == "background_color":
        return canonical_color(value)
    if field_name == "color":
        return canonical_color(value)
    if field_name == "border_color":
        side = split_sides(value)[first_visible_side(element)]
        return canonical_color(side, current_color=element.styles.color)
    if field_name == "box_shadow":
        return format_box_shadow(visible_shadow_layers(value))
    if field_name == "text_decoration_line":
        text = " ".join(value.split()).lower()
        return text or None
    if field_name == "opacity":
        return format(round(parse_opacity(value), 3), "g")
    return None


def _base_value(field_name: str, element: ElementSample) -> str | None:
    """The element's own (resting) value of a hover field, canonicalized."""
    value = getattr(element.styles, field_name, None)
    if isinstance(value, tuple):
        value = join_sides((value[0], value[1], value[2], value[3]))
    return None if not isinstance(value, str) else _hover_value(field_name, value, element)


def hover_changes(member: Member) -> dict[str, str]:
    """Changed properties of one hovered member (canonical values).

    Dropped: unchanged values, values equal to the element's resting style (a diff taken
    mid-transition) and border colors of an element without a visible border.
    """
    state = member.hover
    if state is None:
        return {}
    element = member.element
    changes: dict[str, str] = {}
    for field_name, (before, after) in state.changed.items():
        key = HOVER_FIELDS.get(field_name)
        if key is None:
            continue
        if (
            field_name == "border_color"
            and visible_border(element) is None
            and "border_width" not in state.changed
        ):
            continue
        old = _hover_value(field_name, before, element)
        new = _hover_value(field_name, after, element)
        if new is None or new == old or new == _base_value(field_name, element):
            continue
        changes[key] = new
    return ordered(changes)


def _shared_changes(changed: Sequence[Mapping[str, str]]) -> dict[str, str]:
    """Properties changed by more than half of the change sets, each with its most common
    value (ties: the first set's, i.e. the most prominent member's)."""
    values: dict[str, Counter[str]] = {}
    for changes in changed:
        for key, value in changes.items():
            values.setdefault(key, Counter())[value] += 1
    shared: dict[str, str] = {}
    for key, counter in values.items():
        if 2 * counter.total() <= len(changed):
            continue
        # Counter keeps insertion order and max() returns the first maximum.
        shared[key] = max(counter, key=lambda value: counter[value])
    return ordered(shared)


def hover_state(members: Sequence[Member]) -> dict[str, str]:
    """Hover changes shared by most hovered members (members come most prominent first).

    Only members whose hover visibly changes something vote; a property is kept when more
    than half of them change it, with its most common value (ties: the most prominent
    member's). A fade seen on one of two hovered CTAs is that instance's, not the component's.
    """
    changed = [changes for changes in (hover_changes(member) for member in members) if changes]
    if not changed:
        return {}
    return _shared_changes(changed)


def states_and_refs(
    members: Sequence[Member], tokens: TokenContext, canvas: RGBA
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    hover = hover_state(members)
    if not hover:
        return {}, {}
    changes = [(member, hover_changes(member)) for member in members]
    refs: dict[str, str] = {}
    for key, value in hover.items():
        if key not in _ROLES_BY_KEY:
            continue
        # The resting color of a member showing this very change gives the hover direction.
        source = next((member for member, seen in changes if seen.get(key) == value), None)
        ref = (
            color_ref(value, tokens, key=key)
            if source is None
            else hover_color_ref(value, source, tokens, key=key, canvas=canvas)
        )
        if ref is not None:
            refs[key] = ref
    return {HOVER_STATE: hover}, ({HOVER_STATE: ordered_refs(refs)} if refs else {})


def _resting_color(member: Member, key: str) -> RGBA | None:
    """The member's resting value of a hover color property (None when unknown)."""
    styles = member.element.styles
    if key == "backgroundColor":
        return parse_color(styles.background_color)
    if key == "textColor":
        return text_color(member.element)
    if key == "borderColor":
        border = visible_border(member.element)
        return None if border is None else border[0]
    return None


def _shown(color: RGBA, behind: RGBA) -> RGBA:
    return composite(color, behind).with_alpha(1.0)


def keeps_change(token: RGBA, rest: RGBA, hover: RGBA, behind: RGBA) -> bool:
    """A token stands for a hover color only when it reads as the hover, not the rest:
    nearer the hover than the resting color, and on the same side of the resting lightness
    (a hover that lightens never snaps to a darker token)."""
    seen_token, seen_rest, seen_hover = (_shown(c, behind) for c in (token, rest, hover))
    if delta_e_2000(seen_token, seen_hover) >= delta_e_2000(seen_token, seen_rest):
        return False
    rest_l = to_oklch(seen_rest).lightness
    change = to_oklch(seen_hover).lightness - rest_l
    if abs(change) < HOVER_LIGHTNESS_EPSILON:
        return True
    return (to_oklch(seen_token).lightness - rest_l) * change > 0


def hover_color_ref(
    value: str, member: Member, tokens: TokenContext, *, key: str, canvas: RGBA
) -> str | None:
    """{colors.<token>} of a hover color, keeping the direction of the change from the
    member's resting color; dark hovers need a closer match (ΔE2000 shrinks near black)."""
    hover = parse_color(value)
    rest = _resting_color(member, key)
    if hover is None or hover.is_transparent:
        return None
    if rest is None:
        return color_ref(value, tokens, key=key)
    behind = backdrop(member.ctx, member.element, canvas)
    dark = to_oklch(_shown(hover, behind)).lightness < DARK_LIGHTNESS
    candidates: list[ColorToken] = []
    for candidate in _ref_candidates(tokens, key):
        color = parse_color(candidate.value)
        if color is not None and keeps_change(color, rest, hover, behind):
            candidates.append(candidate)
    token = match_color_token(
        hover,
        candidates,
        prefer_roles=_ROLES_BY_KEY[key],
        max_alpha_diff=_REF_MAX_ALPHA_DIFF,
        max_delta_e=DARK_REF_MAX_DELTA_E if dark else REF_MAX_DELTA_E,
    )
    return None if token is None else token_ref(COLORS, token.name)


def has_background_hover(members: Iterable[Member]) -> bool:
    """At least one member whose hover paints a (non-transparent) background."""
    for member in members:
        state = member.hover
        if state is None or "background_color" not in state.changed:
            continue
        before, after = state.changed["background_color"]
        new = parse_color(after)
        if new is not None and not new.is_transparent and canonical_color(before) != css_color(new):
            return True
    return False


# ─── Contrast, hints, samples ────────────────────────────────────────────────


def contrast_for(
    ctx: PageContext, element: ElementSample, color: RGBA | None, canvas: RGBA
) -> float | None:
    """WCAG ratio of `color` over the opaque background behind `element`."""
    if color is None:
        return None
    background = effective_background(element, ctx.index, base=canvas)
    return round(contrast_ratio(color, background), 2)


def is_utility_class(name: str) -> bool:
    """Tailwind-style utility classes say nothing about the component."""
    if any(char in name for char in ":[]/(),!"):
        return True
    return (
        name in _UTILITY_WORDS
        or _UTILITY_CLASS_RE.match(name) is not None
        or _UTILITY_SHADE_RE.match(name) is not None
    )


def class_hints(elements: Iterable[ElementSample], *, limit: int = 3) -> list[str]:
    """Most frequent readable, non-utility class names (first-seen order breaks ties)."""
    counter: Counter[str] = Counter()
    for element in elements:
        counter.update(
            name for name in meaningful_classes(element.classes) if not is_utility_class(name)
        )
    return [name for name, _ in counter.most_common(limit)]


def input_sample(element: ElementSample) -> str:
    if element.tag == "input":
        return f"input[type={element.input_type}]" if element.input_type else "input"
    return element.tag


def heading_text(ctx: PageContext, element: ElementSample) -> str:
    """Title of a container: its first heading (or first own text), else its text."""
    first_text = ""
    for node in ctx.descendants(element, max_depth=8):
        if not node.has_own_text or not has_text(node.text):
            continue
        if node.tag in ("h1", "h2", "h3", "h4", "h5", "h6") or node.role == "heading":
            return node.text
        if not first_text:
            first_text = node.text
    return first_text or element.text


def collect_samples(texts: Iterable[str], *, limit: int = 3) -> list[str]:
    samples: list[str] = []
    for text in texts:
        cleaned = " ".join(text.split())
        if cleaned and cleaned not in samples:
            samples.append(cleaned)
        if len(samples) >= limit:
            break
    return samples
