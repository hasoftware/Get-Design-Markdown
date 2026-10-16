"""Internal helpers of the component analyzer: page context, members, colors and geometry.

Private to analyzer/components*.py (stream analyzer-components); not a public interface.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from get_design_markdown.analyzer.common import (
    CAPTURE_VIEWPORT_WIDTH,
    ancestors,
    effective_background,
    element_index,
    own_background,
    page_weights,
    visible_area,
)
from get_design_markdown.analyzer.css_values import (
    RGBA,
    WHITE,
    ShadowLayer,
    composite,
    delta_e_2000,
    flatten,
    format_px,
    format_sides_px,
    gradient_colors,
    parse_color,
    parse_number,
    parse_px,
    parse_sides_px,
    visible_shadow_layers,
)
from get_design_markdown.analyzer.naming import FULL_RADIUS_PX, PILL_RADIUS_PX
from get_design_markdown.models.analysis import ColorPalette
from get_design_markdown.models.raw import ElementSample, HoverState, PageCapture

#: Elements starting above this y (the first two 900px screens) count double.
FIRST_SCREENS_PX = 1800.0
#: Colors closer than this (CIEDE2000) are the same color in a component signature.
SAME_COLOR_DELTA_E = 3.0
SAME_ALPHA_DIFF = 0.05
#: A background must differ this much from what is behind it to read as a filled surface.
DISTINCT_FILL_DELTA_E = 2.0
#: A fill at least this opaque hides a background image behind it.
OPAQUE_ENOUGH = 0.5
VIEWPORT_HEIGHT = 900.0
HIDDEN_BORDER_STYLES = frozenset({"none", "hidden"})
#: Corner radii closer than this (px) are one radius.
CORNER_TOLERANCE_PX = 0.5

#: (top-left, top-right, bottom-right, bottom-left) in px.
Corners = tuple[float, float, float, float]


# ─── Page context & members ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PageContext:
    """Lookups for one captured page."""

    page_index: int
    page: PageCapture
    weight: float
    index: dict[int, ElementSample]
    children: dict[int, list[ElementSample]]
    hovers: dict[int, HoverState]

    def descendants(
        self, element: ElementSample, *, max_depth: int = 64
    ) -> Iterator[ElementSample]:
        """Sampled descendants in document order (depth-first, cycle-safe)."""
        seen = {element.index}
        stack: list[tuple[ElementSample, int]] = [
            (child, 1) for child in reversed(self.children.get(element.index, []))
        ]
        while stack:
            node, depth = stack.pop()
            if node.index in seen:
                continue
            seen.add(node.index)
            yield node
            if depth < max_depth:
                stack.extend(
                    (child, depth + 1) for child in reversed(self.children.get(node.index, []))
                )

    def parent(self, element: ElementSample) -> ElementSample | None:
        return None if element.parent_index is None else self.index.get(element.parent_index)

    def lineage(self, element: ElementSample) -> Iterator[ElementSample]:
        """The element itself, then its sampled ancestors (nearest first)."""
        yield element
        yield from ancestors(element, self.index)


def build_contexts(pages: Sequence[PageCapture]) -> list[PageContext]:
    contexts: list[PageContext] = []
    weights = page_weights(pages)
    for page_index, page in enumerate(pages):
        children: dict[int, list[ElementSample]] = {}
        for element in page.elements:
            if element.parent_index is not None:
                children.setdefault(element.parent_index, []).append(element)
        hovers: dict[int, HoverState] = {}
        for state in page.hover_states:
            hovers.setdefault(state.element_index, state)
        contexts.append(
            PageContext(
                page_index=page_index,
                page=page,
                weight=weights[page_index],
                index=element_index(page),
                children=children,
                hovers=hovers,
            )
        )
    return contexts


@dataclass(frozen=True, slots=True)
class Member:
    """One element that belongs to a component (with its page and prominence)."""

    ctx: PageContext
    element: ElementSample
    prominence: float

    @property
    def hover(self) -> HoverState | None:
        return self.ctx.hovers.get(self.element.index)


def prominence(element: ElementSample, weight: float) -> float:
    """Visible area, doubled in the first two screens, times the page weight."""
    factor = 2.0 if element.box.y < FIRST_SCREENS_PX else 1.0
    return visible_area(element) * factor * weight


def make_member(ctx: PageContext, element: ElementSample) -> Member:
    return Member(ctx, element, prominence(element, ctx.weight))


def by_prominence(members: Iterable[Member]) -> list[Member]:
    """Most prominent first; ties keep page/document order."""
    return sorted(members, key=lambda m: (-m.prominence, m.ctx.page_index, m.element.index))


@dataclass
class Group:
    """Members sharing one visual signature (most prominent member first)."""

    key: tuple[object, ...]
    members: list[Member] = field(default_factory=list)

    @property
    def representative(self) -> Member:
        return self.members[0]

    @property
    def prominence(self) -> float:
        return sum(member.prominence for member in self.members)

    @property
    def count(self) -> int:
        return len(self.members)

    @property
    def weight(self) -> float:
        """Members counted at their page weight (see common.page_weights): a long sub-page
        listing 18 links of one style weighs less than the 5 inline links of the main page."""
        return sum(member.ctx.weight for member in self.members)

    def merge(self, other: Group) -> None:
        self.members = by_prominence([*self.members, *other.members])


# ─── Colors ──────────────────────────────────────────────────────────────────


def text_color(element: ElementSample) -> RGBA | None:
    color = parse_color(element.styles.color)
    if color is None or color.is_transparent:
        return None
    return color


def same_color(first: RGBA | None, second: RGBA | None) -> bool:
    """Both absent, or within ΔE2000 3 with a similar alpha."""
    if first is None or second is None:
        return first is None and second is None
    if abs(first.alpha - second.alpha) > SAME_ALPHA_DIFF:
        return False
    return delta_e_2000(first, second) < SAME_COLOR_DELTA_E


def css_color(color: RGBA | None) -> str:
    """Canonical property value: css_hex, or "transparent"."""
    if color is None or color.is_transparent:
        return "transparent"
    return color.css_hex


def canonical_color(value: str | None, *, current_color: str | None = None) -> str | None:
    """Computed color string -> canonical hex / "transparent" (None when unparseable)."""
    color = parse_color(value, current_color=current_color)
    return None if color is None else css_color(color)


def canvas_color(palette: ColorPalette) -> RGBA:
    for token in palette.tokens:
        if token.name == "canvas":
            color = parse_color(token.value)
            if color is not None:
                return color
    return WHITE


def backdrop(ctx: PageContext, element: ElementSample, base: RGBA) -> RGBA:
    """Opaque color behind the element (its parent's effective background)."""
    parent = ctx.parent(element)
    if parent is None:
        return base if base.is_opaque else flatten(base)
    return effective_background(parent, ctx.index, base=base)


def _image_behind(ctx: PageContext, element: ElementSample, fill: RGBA, base: RGBA) -> bool:
    """A background image (or a gradient with a contrasting stop) painted behind the element,
    above the nearest opaque background color."""
    for node in ancestors(element, ctx.index):
        image = node.styles.background_image.strip().lower()
        if image and image != "none":
            if "url(" in image:
                return True
            behind = backdrop(ctx, node, base)
            for stop in gradient_colors(image):
                shown = composite(stop, behind).with_alpha(1.0)
                if delta_e_2000(shown, fill) >= DISTINCT_FILL_DELTA_E:
                    return True
        color = own_background(node)
        if color is not None and color.is_opaque:
            return False
    return False


def is_distinct_fill(ctx: PageContext, element: ElementSample, base: RGBA) -> bool:
    """Own background visibly different from what is behind the element (an opaque-ish fill
    over a background image always is)."""
    color = own_background(element)
    if color is None:
        return False
    behind = backdrop(ctx, element, base)
    seen = composite(color, behind).with_alpha(1.0)
    if delta_e_2000(seen, behind) >= DISTINCT_FILL_DELTA_E:
        return True
    return color.alpha >= OPAQUE_ENOUGH and _image_behind(ctx, element, seen, base)


def visible_border(element: ElementSample) -> tuple[RGBA, tuple[float, float, float, float]] | None:
    """(color of the first visible side, the four widths in px) or None without a border."""
    styles = element.styles
    widths = parse_sides_px(styles.border_width)
    first: RGBA | None = None
    for width, style, value in zip(widths, styles.border_style, styles.border_color, strict=False):
        if width <= 0 or style in HIDDEN_BORDER_STYLES:
            continue
        color = parse_color(value, current_color=styles.color)
        if color is not None and not color.is_transparent:
            first = color
            break
    if first is None:
        return None
    shown = tuple(
        width if style not in HIDDEN_BORDER_STYLES else 0.0
        for width, style in zip(widths, styles.border_style, strict=False)
    )
    return first, (shown[0], shown[1], shown[2], shown[3])


def visible_border_sides(element: ElementSample) -> int:
    """Number of border sides with a width, a visible style and a non-transparent color."""
    styles = element.styles
    widths = parse_sides_px(styles.border_width)
    count = 0
    for width, style, value in zip(widths, styles.border_style, styles.border_color, strict=False):
        if width <= 0 or style in HIDDEN_BORDER_STYLES:
            continue
        color = parse_color(value, current_color=styles.color)
        if color is not None and not color.is_transparent:
            count += 1
    return count


def first_visible_side(element: ElementSample) -> int:
    """Index (0..3) of the first border side with a width and a visible style."""
    widths = parse_sides_px(element.styles.border_width)
    for position, (width, style) in enumerate(
        zip(widths, element.styles.border_style, strict=False)
    ):
        if width > 0 and style not in HIDDEN_BORDER_STYLES:
            return position
    return 0


# ─── Geometry ────────────────────────────────────────────────────────────────


def _corner_px(corner: str, shortest: float) -> float:
    token = corner.strip().split(" ")[0] if corner.strip() else ""
    if token.endswith("%"):
        percent = parse_number(token[:-1]) or 0.0
        if percent >= 50:
            return FULL_RADIUS_PX
        value = percent / 100.0 * shortest
    else:
        value = parse_px(token) or 0.0
    return FULL_RADIUS_PX if value >= PILL_RADIUS_PX else max(value, 0.0)


def corner_radii(element: ElementSample) -> Corners:
    """(top-left, top-right, bottom-right, bottom-left) radii in px, the order of the
    border-radius shorthand; pills/circles (>= 500px or >= 50%) give 9999."""
    box = element.box
    shortest = max(min(box.width, box.height), 0.0)
    tl, tr, br, bl = (_corner_px(corner, shortest) for corner in element.styles.border_radius)
    return tl, tr, br, bl


def radius_px(element: ElementSample) -> float:
    """Largest corner radius in px; pills/circles (>= 500px or >= 50%) give 9999."""
    return max(corner_radii(element))


def uneven_corners(corners: Sequence[float]) -> bool:
    """The corners differ (a tab "4px 4px 0px 0px", a speech bubble...)."""
    return max(corners, default=0.0) - min(corners, default=0.0) > CORNER_TOLERANCE_PX


def rounded_value(corners: Sequence[float]) -> str | None:
    """`rounded` property: one length when the corners agree, else the four corners as a
    border-radius shorthand ("4px 4px 0px 0px" is a tab, not a 4px box); None when square."""
    largest = max(corners, default=0.0)
    if largest <= 0:
        return None
    if not uneven_corners(corners):
        return format_px(largest)
    return format_sides_px(corners)


def is_pill(element: ElementSample, radius: float | None = None) -> bool:
    """Rounded ends: the radius (by default the smallest corner: a tab rounded on top only
    is no pill) covers at least half of the height (1px tolerance)."""
    value = min(corner_radii(element)) if radius is None else radius
    return value > 0 and value >= element.box.height / 2.0 - 1.0


def padding_px(element: ElementSample) -> tuple[float, float, float, float]:
    return parse_sides_px(element.styles.padding)


def shadow_layers(element: ElementSample) -> list[ShadowLayer]:
    return visible_shadow_layers(element.styles.box_shadow)


def shadow_strength(layers: Sequence[ShadowLayer]) -> float:
    """Rough elevation of outer shadows: offset + blur + spread, weighted by opacity."""
    strength = 0.0
    for layer in layers:
        if layer.inset:
            continue
        alpha = 1.0 if layer.color is None else layer.color.alpha
        size = abs(layer.offset_y) + abs(layer.offset_x) * 0.5 + layer.blur + max(layer.spread, 0)
        strength += size * alpha
    return strength


def width_ratio(element: ElementSample) -> float:
    return element.box.width / CAPTURE_VIEWPORT_WIDTH


def has_text(text: str) -> bool:
    return any(char.isalnum() for char in text)


def in_landmark(ctx: PageContext, element: ElementSample, landmarks: frozenset[str]) -> bool:
    """True when the element or one of its sampled ancestors sits in one of `landmarks`."""
    return any(node.landmark in landmarks for node in ctx.lineage(element))
