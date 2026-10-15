"""Color sampling for the color analyzer: one weighted sample per (element, channel, color).

Channels and weights (all multiplied by the page weight: 1.0 main page, 0.5 sub-pages):
- background: own background-color; weight = painted area = box area minus the areas painted
  over by descendants with an opaque background (floor 10% of the box unless one descendant
  covers it entirely). The root background (html, else body, else the browser's white canvas)
  covers the whole document. Translucent backgrounds keep their alpha.
- text: elements rendering their own text; weight = min(len(text), 50) x (font-size / 16)^2.
  Input placeholders count as text with weight 5.
- border: every visible side; weight = side length x width. Backgrounds that only draw a line
  (thin rules, <hr>, childless "border"/"divider" boxes painted through a mask, 1-3px padding
  rings around a covering child) count as borders too.
- gradient: color stops of background-image gradients; weight = area / number of stops.
Images, pictures, videos and iframes paint over the backgrounds behind them. Text drawn over an
image, video, canvas or opaque gradient has an unknowable background (skipped for contrast).
Box-shadow colors are not palette colors and are ignored.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from get_design_markdown.analyzer.colors_hints import name_words
from get_design_markdown.analyzer.common import (
    CAPTURE_VIEWPORT_WIDTH,
    ElementRef,
    ancestors,
    effective_background,
    element_index,
    has_visible_border,
    is_heading,
    is_interactive,
    iter_elements,
    own_background,
    page_weight,
)
from get_design_markdown.analyzer.css_values import (
    RGBA,
    WHITE,
    gradient_colors,
    is_gradient,
    parse_color,
    parse_px,
    parse_sides_px,
)
from get_design_markdown.models.analysis import ColorChannel
from get_design_markdown.models.raw import ElementSample, PageCapture

TEXT_LENGTH_CAP = 50
PLACEHOLDER_WEIGHT = 5.0
#: Minimum share of its box an element keeps when descendants paint over it.
BACKGROUND_FLOOR = 0.1
#: One descendant covering this share of the box hides the background completely.
FULL_COVER = 0.98
#: Gradients whose most opaque stop reaches this alpha hide what is behind them.
OPAQUE_GRADIENT_ALPHA = 0.5
ROOT_TAGS = frozenset({"html", "body"})
LANDMARK_TAGS = frozenset({"html", "body", "main"})
#: An element at least this wide (share of the capture viewport) is a full-width band.
FULL_WIDTH_SHARE = 0.9
#: Clickable boxes larger than this are cards or banners, not buttons.
BUTTON_MAX_WIDTH = 480.0
BUTTON_MAX_HEIGHT = 96.0
BUTTON_INPUT_TYPES = frozenset({"submit", "button", "reset"})
#: Replaced content that paints over the backgrounds behind it.
COVER_TAGS = frozenset({"img", "picture", "video", "iframe"})
#: Media under text makes its contrast unknowable (large canvases and SVGs included).
MEDIA_TAGS = COVER_TAGS | frozenset({"canvas", "svg"})
MEDIA_MIN_AREA = 10_000.0
#: Share of a text box over media that makes its background unknowable.
TEXT_ON_MEDIA_SHARE = 0.5
#: Thickness under which a filled, childless box is a line (rule, divider).
LINE_MAX_THICKNESS = 2.5
#: Padding (px) of a "ring" box whose child covers everything but a thin outline.
RING_MAX_PADDING = 3.0
LINE_WORDS = frozenset(
    {"border", "borders", "divider", "separator", "stroke", "outline", "hairline", "rule", "line"}
)
SyntheticKind = Literal["browser-canvas"]


@dataclass(frozen=True, slots=True)
class ElementTraits:
    """What an element is, as far as color roles are concerned."""

    interactive: bool
    button_like: bool
    link: bool
    heading: bool
    card_like: bool
    full_width: bool
    page_root: bool
    landmark: str | None
    #: Box area clipped to the viewport (whole document for the root background provider).
    area: float
    #: innerText is empty: no text anywhere inside the element.
    textless: bool
    #: The element's own background-image (url() or opaque gradient) hides its background-color.
    hides_background: bool
    #: Text of the element sits on an image or opaque gradient: its contrast is unknowable.
    on_image: bool
    #: Opaque color behind the element's content (own and ancestor backgrounds on the page base).
    effective_background: RGBA


@dataclass(slots=True)
class ColorSample:
    channel: ColorChannel
    color: RGBA
    weight: float
    ref: ElementRef
    traits: ElementTraits
    placeholder: bool = False
    #: Set for the browser's default white canvas behind a transparent html/body.
    synthetic: SyntheticKind | None = None

    @property
    def key(self) -> tuple[int, int]:
        """Identity of the contributing element across pages."""
        return (self.ref.page_index, self.ref.element.index)


@dataclass(slots=True)
class SampleSet:
    samples: dict[ColorChannel, list[ColorSample]] = field(
        default_factory=lambda: {"background": [], "text": [], "border": [], "gradient": []}
    )
    #: Document area (px², 1440px wide) of every analyzed page, by page index.
    page_areas: list[float] = field(default_factory=list)
    #: Sum of the text sample weights.
    text_weight_total: float = 0.0
    #: Pages without a sampled html/body element (their base color is a guess).
    rootless_pages: int = 0

    def add(self, sample: ColorSample) -> None:
        if sample.weight > 0:
            self.samples[sample.channel].append(sample)
            if sample.channel == "text":
                self.text_weight_total += sample.weight


# ─── Geometry ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Rect:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def area(self) -> float:
        return max(self.right - self.left, 0.0) * max(self.bottom - self.top, 0.0)

    def intersect(self, other: _Rect) -> float:
        width = min(self.right, other.right) - max(self.left, other.left)
        height = min(self.bottom, other.bottom) - max(self.top, other.top)
        return max(width, 0.0) * max(height, 0.0)


def _element_rect(element: ElementSample) -> _Rect:
    box = element.box
    left = max(box.x, 0.0)
    right = min(box.x + max(box.width, 0.0), CAPTURE_VIEWPORT_WIDTH)
    return _Rect(left, box.y, max(right, left), box.y + max(box.height, 0.0))


def _document_rect(page: PageCapture, root: ElementSample | None) -> _Rect:
    height = page.document_height
    if root is not None:
        height = max(height, root.box.y + root.box.height)
    return _Rect(0.0, 0.0, CAPTURE_VIEWPORT_WIDTH, max(height, 0.0))


# ─── Element traits ──────────────────────────────────────────────────────────


def gradient_hides_background(background_image: str) -> bool:
    """True for url() images and gradients with an (almost) opaque stop."""
    text = background_image.strip().lower()
    if not text or text == "none":
        return False
    if "url(" in text:
        return True
    if is_gradient(text):
        stops = gradient_colors(background_image)
        return any(stop.alpha >= OPAQUE_GRADIENT_ALPHA for stop in stops)
    return False


def is_link(element: ElementSample) -> bool:
    return (element.tag == "a" and element.href is not None) or element.role == "link"


def is_button_like(element: ElementSample) -> bool:
    """Real buttons, and small clickable boxes with a fill or a border plus padding."""
    if element.role == "button" or element.tag == "button":
        return True
    if element.tag == "input" and (element.input_type or "") in BUTTON_INPUT_TYPES:
        return True
    if not is_interactive(element) or element.tag in ("input", "select", "textarea"):
        return False
    if own_background(element) is None and not has_visible_border(element):
        return False
    if max(parse_sides_px(element.styles.padding)) <= 0:
        return False
    return element.box.width <= BUTTON_MAX_WIDTH and element.box.height <= BUTTON_MAX_HEIGHT


def is_card_like(element: ElementSample) -> bool:
    """A bounded box with content and an edge (border, shadow or radius): cards, popovers."""
    box = element.box
    if box.width >= CAPTURE_VIEWPORT_WIDTH * FULL_WIDTH_SHARE or box.height < 40:
        return False
    if element.child_count <= 0 and not element.text:
        return False
    styles = element.styles
    has_edge = (
        has_visible_border(element)
        or styles.box_shadow.strip().lower() not in ("", "none")
        or max(parse_sides_px(styles.border_radius)) > 0
    )
    return has_edge


class _PageContext:
    """Per-page lookups: element index, root background provider, painted areas."""

    def __init__(self, page: PageCapture, rendered: set[int]) -> None:
        self.page = page
        self.index = element_index(page)
        self.rendered = rendered
        roots = [element for element in page.elements if element.tag in ROOT_TAGS]
        self.has_root = bool(roots)
        html = next((element for element in roots if element.tag == "html"), None)
        body = next((element for element in roots if element.tag == "body"), None)
        #: The element whose background paints the whole document (CSS root propagation).
        self.root_provider: ElementSample | None = None
        if html is not None and own_background(html) is not None:
            self.root_provider = html
        elif body is not None and own_background(body) is not None:
            self.root_provider = body
        self.document = _document_rect(page, html or body)
        #: Browser canvas behind transparent html/body (white) — only when they were sampled.
        self.synthetic_root = self.root_provider is None and (html or body) is not None
        self.synthetic_root_element = html or body

    def rect(self, element: ElementSample) -> _Rect:
        if self.root_provider is not None and element.index == self.root_provider.index:
            return self.document
        return _element_rect(element)

    def base(self, fallback: RGBA) -> RGBA:
        """What shows behind a transparent page: white when html/body were sampled (an opaque
        root background is found by walking ancestors), else the guessed canvas."""
        return WHITE if self.has_root else fallback


def _is_cover(element: ElementSample, context: _PageContext) -> bool:
    """Opaque background or replaced media (an <img> inside a sampled <picture> is skipped)."""
    color = own_background(element)
    if color is not None and color.is_opaque:
        return True
    if element.tag not in COVER_TAGS:
        return False
    parent = context.index.get(element.parent_index) if element.parent_index is not None else None
    return not (element.tag == "img" and parent is not None and parent.tag == "picture")


def _painted_areas(
    context: _PageContext,
) -> tuple[dict[int, float], dict[int, float], float]:
    """Painted (visible) area of every rendered element with a background, the largest area
    one descendant covers in each of them, and the painted area of the synthetic canvas.

    Opaque backgrounds and media hide the translucent layers and the first opaque layer below.
    """
    covered: dict[int, float] = {}
    largest: dict[int, float] = {}
    synthetic_covered = 0.0
    synthetic_full = False
    document = context.document
    for element in context.page.elements:
        if element.index not in context.rendered or not _is_cover(element, context):
            continue
        rect = context.rect(element)
        reached_opaque = False
        for ancestor in ancestors(element, context.index):
            ancestor_color = own_background(ancestor)
            if ancestor_color is None:
                continue
            ancestor_rect = context.rect(ancestor)
            overlap = rect.intersect(ancestor_rect)
            covered[ancestor.index] = covered.get(ancestor.index, 0.0) + overlap
            largest[ancestor.index] = max(largest.get(ancestor.index, 0.0), overlap)
            if ancestor_color.is_opaque:
                reached_opaque = True
                break
        if not reached_opaque and context.synthetic_root:
            overlap = rect.intersect(document)
            synthetic_covered += overlap
            if overlap >= FULL_COVER * document.area > 0:
                synthetic_full = True
    painted: dict[int, float] = {}
    for element in context.page.elements:
        if element.index not in context.rendered or own_background(element) is None:
            continue
        area = context.rect(element).area
        if area > 0 and largest.get(element.index, 0.0) >= FULL_COVER * area:
            painted[element.index] = 0.0
            continue
        remaining = area - covered.get(element.index, 0.0)
        painted[element.index] = max(remaining, BACKGROUND_FLOOR * area)
    synthetic_area = 0.0
    if context.synthetic_root and not synthetic_full:
        synthetic_area = max(document.area - synthetic_covered, 0.0)
    return painted, largest, synthetic_area


def _media_rects(context: _PageContext) -> list[tuple[int, _Rect]]:
    result: list[tuple[int, _Rect]] = []
    for element in context.page.elements:
        if element.index not in context.rendered or element.tag not in MEDIA_TAGS:
            continue
        rect = _element_rect(element)
        if rect.area >= MEDIA_MIN_AREA:
            result.append((element.index, rect))
    return result


def _over_media(element: ElementSample, media: Sequence[tuple[int, _Rect]]) -> bool:
    """Text painted after (above) a large image/video/canvas/SVG it mostly overlaps."""
    rect = _element_rect(element)
    if rect.area <= 0:
        return False
    return any(
        index < element.index and rect.intersect(media_rect) >= TEXT_ON_MEDIA_SHARE * rect.area
        for index, media_rect in media
    )


def line_weight(element: ElementSample) -> float | None:
    """Length x thickness when the element's background only draws a line, else None.

    Thin childless boxes (<= 2.5px), <hr>, and childless boxes named like a border
    ("card__border", "divider") whose fill is shown through a mask as an outline.
    """
    if element.text.strip() or element.child_count > 0:
        return None
    width = max(element.box.width, 0.0)
    height = max(element.box.height, 0.0)
    thickness = min(width, height)
    if element.tag == "hr" or 0 < thickness <= LINE_MAX_THICKNESS:
        return max(width, height) * max(thickness, 1.0)
    words: list[str] = []
    for name in element.classes:
        words.extend(name_words(name))
    if element.id:
        words.extend(name_words(element.id))
    if LINE_WORDS & set(words):
        return 2.0 * (width + height)
    return None


def ring_weight(element: ElementSample, largest_cover: float) -> float | None:
    """Perimeter x padding for a box whose child hides everything but a 1-3px padding ring
    (largest_cover: the largest area one descendant paints over the box)."""
    top, right, bottom, left = parse_sides_px(element.styles.padding)
    if not all(0 < side <= RING_MAX_PADDING for side in (top, right, bottom, left)):
        return None
    width = max(element.box.width, 0.0)
    height = max(element.box.height, 0.0)
    content = max(width - left - right, 0.0) * max(height - top - bottom, 0.0)
    if content <= 0 or largest_cover < FULL_COVER * content:
        return None
    return 2.0 * (width + height) * ((top + right + bottom + left) / 4.0)


def _traits(
    element: ElementSample,
    context: _PageContext,
    base: RGBA,
    media: Sequence[tuple[int, _Rect]] = (),
) -> ElementTraits:
    has_text = element.has_own_text or bool(element.placeholder_color)
    on_image = bool(media) and has_text and _over_media(element, media)
    if not on_image:
        for node in (element, *ancestors(element, context.index)):
            if gradient_hides_background(node.styles.background_image):
                on_image = True
                break
            color = own_background(node)
            if color is not None and color.is_opaque:
                break
    full_width = element.box.width >= CAPTURE_VIEWPORT_WIDTH * FULL_WIDTH_SHARE
    return ElementTraits(
        interactive=is_interactive(element),
        button_like=is_button_like(element),
        link=is_link(element),
        heading=is_heading(element),
        card_like=is_card_like(element),
        full_width=full_width,
        page_root=element.tag in LANDMARK_TAGS,
        landmark=element.landmark,
        area=context.rect(element).area,
        textless=not element.text.strip(),
        hides_background=gradient_hides_background(element.styles.background_image),
        on_image=on_image,
        effective_background=effective_background(element, context.index, base=base),
    )


def _font_size(element: ElementSample) -> float:
    size = parse_px(element.styles.font_size)
    return size if size is not None and size > 0 else 16.0


def _border_samples(element: ElementSample) -> dict[str, tuple[RGBA, float]]:
    """css_hex -> (color, weight before page weighting) for every visible side."""
    styles = element.styles
    result: dict[str, tuple[RGBA, float]] = {}
    lengths = (element.box.width, element.box.height, element.box.width, element.box.height)
    for width_text, style, color_text, length in zip(
        styles.border_width, styles.border_style, styles.border_color, lengths, strict=False
    ):
        width = parse_px(width_text) or 0.0
        if width <= 0 or style.strip().lower() in ("none", "hidden"):
            continue
        color = parse_color(color_text, current_color=styles.color)
        if color is None or color.is_transparent:
            continue
        weight = max(length, 0.0) * width
        previous = result.get(color.css_hex)
        result[color.css_hex] = (color, weight + (previous[1] if previous else 0.0))
    return result


def collect_samples(pages: Sequence[PageCapture], *, canvas_guess: RGBA = WHITE) -> SampleSet:
    """Weighted color samples of every rendered, non-overlay element of the pages.

    canvas_guess is the page base used for effective backgrounds when a page has no sampled
    html/body element (normally the browser shows white behind transparent roots).
    """
    result = SampleSet()
    refs_by_page: dict[int, list[ElementRef]] = {}
    for ref in iter_elements(pages):
        refs_by_page.setdefault(ref.page_index, []).append(ref)
    for page_index, page in enumerate(pages):
        refs = refs_by_page.get(page_index, [])
        context = _PageContext(page, {ref.element.index for ref in refs})
        base = context.base(canvas_guess)
        painted, largest_cover, synthetic_area = _painted_areas(context)
        media = _media_rects(context)
        weight_of_page = page_weight(page_index)
        result.page_areas.append(context.document.area)
        if not context.has_root:
            result.rootless_pages += 1
        if synthetic_area > 0 and context.synthetic_root_element is not None:
            root = context.synthetic_root_element
            root_ref = ElementRef(page_index, page, root, weight_of_page)
            traits = _traits(root, context, base)
            result.add(
                ColorSample(
                    "background",
                    WHITE,
                    synthetic_area * weight_of_page,
                    root_ref,
                    traits,
                    synthetic="browser-canvas",
                )
            )
        for ref in refs:
            element = ref.element
            traits = _traits(element, context, base, media)
            styles = element.styles
            background = own_background(element)
            if background is not None:
                line = line_weight(element)
                if line is None and element.index in largest_cover:
                    line = ring_weight(element, largest_cover[element.index])
                if line is not None:
                    result.add(ColorSample("border", background, line * ref.weight, ref, traits))
                else:
                    weight = painted.get(element.index, 0.0) * ref.weight
                    result.add(ColorSample("background", background, weight, ref, traits))
            if element.has_own_text:
                color = parse_color(styles.color)
                if color is not None and not color.is_transparent:
                    length = min(max(len(element.text), 1), TEXT_LENGTH_CAP)
                    weight = length * (_font_size(element) / 16.0) ** 2 * ref.weight
                    result.add(ColorSample("text", color, weight, ref, traits))
            if element.placeholder_color:
                color = parse_color(element.placeholder_color, current_color=styles.color)
                if color is not None and not color.is_transparent:
                    result.add(
                        ColorSample(
                            "text",
                            color,
                            PLACEHOLDER_WEIGHT * ref.weight,
                            ref,
                            traits,
                            placeholder=True,
                        )
                    )
            for color, weight in _border_samples(element).values():
                result.add(ColorSample("border", color, weight * ref.weight, ref, traits))
            stops = [
                stop for stop in gradient_colors(styles.background_image) if not stop.is_transparent
            ]
            if stops:
                share = traits.area / len(stops) * ref.weight
                for stop in stops:
                    result.add(ColorSample("gradient", stop, share, ref, traits))
    return result
