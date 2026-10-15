"""Internal helpers of the component analyzer: the painted surface of a container.

Private to analyzer/components*.py (stream analyzer-components); not a public interface.

Many cards are drawn by layers rather than by the card element itself: Bootstrap keeps the
padding on .card-body, and gradient/hairline borders are often a wrapper painting a 1-3px
frame around an inner fill (Stripe's bento cards). resolve_surface() reads the paint from the
element and the children hugging its box; text_inset() measures the padding the reader sees.
"""

from __future__ import annotations

from dataclasses import dataclass

from get_design_markdown.analyzer.common import is_rendered, own_background
from get_design_markdown.analyzer.components_context import (
    DISTINCT_FILL_DELTA_E,
    PageContext,
    backdrop,
    has_text,
    padding_px,
    radius_px,
    shadow_layers,
    shadow_strength,
    visible_border,
)
from get_design_markdown.analyzer.css_values import (
    RGBA,
    ShadowLayer,
    composite,
    delta_e_2000,
)
from get_design_markdown.models.raw import ElementSample

#: A child within this many px of every edge paints the parent's surface.
HUG_SLACK_PX = 8.0
HUG_MAX_DEPTH = 3
#: A fill showing 0.5-3px around an inner fill reads as a border.
FRAME_MIN_PX = 0.5
FRAME_MAX_PX = 3.0
MEDIA_TAGS = frozenset({"img", "svg", "picture", "video", "canvas"})
#: Top and left text insets this close read as one visual padding.
VISUAL_PADDING_TOLERANCE_PX = 2.0


@dataclass(frozen=True, slots=True)
class Surface:
    """What a container looks like, wherever the paint comes from."""

    #: Fill seen inside the container (innermost hugging fill), None when transparent.
    background: RGBA | None
    #: (color, four widths) of a real border or of a framing fill.
    border: tuple[RGBA, tuple[float, float, float, float]] | None
    radius: float
    shadow: tuple[ShadowLayer, ...]

    @property
    def strength(self) -> float:
        return shadow_strength(self.shadow)


def hugs(outer: ElementSample, inner: ElementSample, slack: float = HUG_SLACK_PX) -> bool:
    a, b = outer.box, inner.box
    return (
        abs(a.x - b.x) <= slack
        and abs(a.y - b.y) <= slack
        and abs(a.x + a.width - b.x - b.width) <= slack
        and abs(a.y + a.height - b.y - b.height) <= slack
    )


def hugging_chain(ctx: PageContext, element: ElementSample) -> list[ElementSample]:
    """The element, then the descendants covering its box (outermost first)."""
    chain = [element]
    frontier = [element]
    for _ in range(HUG_MAX_DEPTH):
        level: list[ElementSample] = []
        for node in frontier:
            for child in ctx.children.get(node.index, []):
                if is_rendered(child) and hugs(element, child):
                    level.append(child)
        chain.extend(level)
        frontier = level
        if not frontier:
            break
    return chain


def resolve_surface(ctx: PageContext, element: ElementSample) -> Surface:
    chain = hugging_chain(ctx, element)
    radius = max(radius_px(node) for node in chain)
    shadow: tuple[ShadowLayer, ...] = ()
    for node in chain:
        layers = shadow_layers(node)
        if layers:
            shadow = tuple(layers)
            break
    border = None
    for node in chain:
        border = visible_border(node)
        if border is not None:
            break
    fills = [(node, color) for node in chain if (color := own_background(node)) is not None]
    background = fills[-1][1] if fills else None
    if border is None and len(fills) >= 2:
        border = _frame_border(fills[0], fills[-1])
    return Surface(background=background, border=border, radius=radius, shadow=shadow)


def _frame_border(
    outer: tuple[ElementSample, RGBA], inner: tuple[ElementSample, RGBA]
) -> tuple[RGBA, tuple[float, float, float, float]] | None:
    outer_node, outer_color = outer
    inner_node, inner_color = inner
    a, b = outer_node.box, inner_node.box
    insets = (
        b.y - a.y,
        a.x + a.width - (b.x + b.width),
        a.y + a.height - (b.y + b.height),
        b.x - a.x,
    )
    if not all(FRAME_MIN_PX <= inset <= FRAME_MAX_PX for inset in insets):
        return None
    if delta_e_2000(outer_color, inner_color) < DISTINCT_FILL_DELTA_E:
        return None
    width = round(min(insets), 2)
    return outer_color, (width, width, width, width)


def surface_is_distinct(
    ctx: PageContext, element: ElementSample, surface: Surface, canvas: RGBA
) -> bool:
    """The surface's fill differs from what is behind the container."""
    if surface.background is None:
        return False
    behind = backdrop(ctx, element, canvas)
    seen = composite(surface.background, behind).with_alpha(1.0)
    return delta_e_2000(seen, behind) >= DISTINCT_FILL_DELTA_E


def text_nodes(ctx: PageContext, element: ElementSample) -> list[ElementSample]:
    return [
        node
        for node in ctx.descendants(element, max_depth=8)
        if node.has_own_text and has_text(node.text) and is_rendered(node)
    ]


def content_blocks(ctx: PageContext, element: ElementSample) -> int:
    """Own-text and media descendants (a card shows at least two)."""
    return sum(
        1
        for node in ctx.descendants(element, max_depth=8)
        if is_rendered(node)
        and ((node.has_own_text and has_text(node.text)) or node.tag in MEDIA_TAGS)
    )


def text_insets(ctx: PageContext, element: ElementSample) -> tuple[float, float, float] | None:
    """(top, right, left) distances from the container's edges to its text, None without
    text. Bottom is ignored: footers and full-bleed media often sit there."""
    nodes = [node for node in text_nodes(ctx, element) if _inside(element, node)]
    if not nodes:
        return None
    box = element.box
    left = min(node.box.x for node in nodes) - box.x
    top = min(node.box.y for node in nodes) - box.y
    right = box.x + box.width - max(node.box.x + node.box.width for node in nodes)
    return top, right, left


def text_inset(ctx: PageContext, element: ElementSample) -> float | None:
    """Smallest of text_insets()."""
    insets = text_insets(ctx, element)
    return None if insets is None else min(insets)


def visual_padding(ctx: PageContext, element: ElementSample) -> tuple[float, ...] | None:
    """Padding as seen when no CSS padding exists: the text's top/left inset (rounded),
    used on all four sides; None when the insets disagree or are tiny."""
    insets = text_insets(ctx, element)
    if insets is None:
        return None
    top, _, left = insets
    if abs(top - left) > VISUAL_PADDING_TOLERANCE_PX or min(top, left) < 1:
        return None
    value = float(round(min(top, left)))
    return (value, value, value, value)


def _inside(outer: ElementSample, inner: ElementSample) -> bool:
    a, b = outer.box, inner.box
    return (
        b.x >= a.x - 1
        and b.y >= a.y - 1
        and b.x + b.width <= a.x + a.width + 1
        and b.y + b.height <= a.y + a.height + 1
    )


def content_padding_holder(ctx: PageContext, element: ElementSample) -> ElementSample:
    """The element when it has a padding, else the full-width content wrapper that has one
    (Bootstrap's .card-body)."""
    if any(side > 0 for side in padding_px(element)):
        return element
    box = element.box
    for node in ctx.descendants(element, max_depth=3):
        if (
            abs(node.box.x - box.x) <= HUG_SLACK_PX
            and abs(node.box.width - box.width) <= 2 * HUG_SLACK_PX
            and any(side > 0 for side in padding_px(node))
            and node.child_count > 0
        ):
            return node
    return element
