"""Internal helpers of the component analyzer: which elements are buttons, links, inputs,
cards, badges and page chrome (top nav, sidebar, footer).

Private to analyzer/components*.py; not a public interface.

Besides overlays (skipped by common.iter_elements), decorative subtrees are ignored: product
illustrations and app mock-ups built from real DOM (<figure> and role="img" containers,
"*-graphic" /
"illustration" / "mockup" classes, and framed "app windows" holding their own <nav>/<aside>
inside <main>). Their fake buttons, labels and cards are not the site's components.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from get_design_markdown.analyzer.common import (
    CAPTURE_VIEWPORT_WIDTH,
    is_rendered,
    meaningful_classes,
    own_background,
    visible_area,
)
from get_design_markdown.analyzer.components_context import (
    VIEWPORT_HEIGHT,
    Member,
    PageContext,
    backdrop,
    has_text,
    in_landmark,
    is_distinct_fill,
    make_member,
    padding_px,
    radius_px,
    shadow_layers,
    text_color,
    visible_border,
    visible_border_sides,
    width_ratio,
)
from get_design_markdown.analyzer.components_props import text_source
from get_design_markdown.analyzer.components_surface import (
    content_blocks,
    hugs,
    resolve_surface,
    surface_is_distinct,
    text_inset,
)
from get_design_markdown.analyzer.css_values import (
    RGBA,
    composite,
    is_neutral,
    parse_px,
)
from get_design_markdown.models.raw import ElementSample

BUTTON_INPUT_TYPES = frozenset({"submit", "button", "reset"})
TEXT_INPUT_TYPES = frozenset({"text", "email", "search", "password", "url", "tel", "number"})
CARD_TAGS = frozenset({"div", "article", "section", "li", "a", "button"})
BADGE_TAGS = frozenset({"span", "div", "a", "p", "small", "strong", "mark", "label"})
INLINE_DISPLAYS = frozenset({"inline", "inline-block", "inline-flex"})
NAV_LANDMARKS = frozenset({"nav", "header"})
HEADER_LANDMARKS = frozenset({"header"})
FOOTER_LANDMARKS = frozenset({"footer"})
CHROME_LANDMARKS = frozenset({"nav", "header", "footer"})
#: A container with role="img" renders its whole subtree as one picture.
DECORATIVE_ROLES = frozenset({"img"})
#: <figure> holds illustrations, diagrams and product shots (with their fake UI).
DECORATIVE_TAGS = frozenset({"figure"})
_DECORATIVE_CLASS_RE = re.compile(
    r"(?:^|[-_])(?:graphics?|illustrations?|mockups?|artwork|screenshots?)(?:$|[-_])",
    re.IGNORECASE,
)

BUTTON_MIN_HEIGHT = 24.0
BUTTON_MAX_HEIGHT = 80.0
BUTTON_MAX_WIDTH = 480.0
BUTTON_MIN_PADDING_X = 8.0
LINK_MAX_PADDING = 2.0
CARD_MIN_RADIUS = 4.0
CARD_MIN_PADDING = 12.0
CARD_MIN_WIDTH = 160.0
CARD_MAX_WIDTH = 720.0
CARD_MIN_HEIGHT = 80.0
CARD_MAX_WIDTH_RATIO = 0.9
BADGE_MAX_HEIGHT = 32.0
BADGE_MIN_HEIGHT = 12.0
BADGE_MAX_WIDTH = 260.0
BADGE_MAX_FONT_PX = 14.0
BADGE_MAX_TEXT = 30
BADGE_MIN_RADIUS = 2.0
#: Elements whose classes call them badges may be a little taller (hero announcement pills).
BADGE_NAMED_MAX_HEIGHT = 40.0
BADGE_NAMED_MAX_WIDTH = 480.0
BADGE_NAMED_MAX_TEXT = 60
BADGE_NAMED_MAX_FONT = 16.0
BADGE_CLASS_TOKENS = frozenset({"badge", "chip", "tag", "eyebrow", "announcement"})
BUTTON_CLASS_TOKENS = frozenset({"btn", "button"})
INPUT_MIN_WIDTH = 60.0
INPUT_MIN_HEIGHT = 20.0
CHROME_MIN_WIDTH_RATIO = 0.9
TOP_NAV_MAX_Y = 10.0
TOP_NAV_FALLBACK_MAX_Y = 150.0
TOP_NAV_MAX_HEIGHT = 200.0
SIDEBAR_MIN_HEIGHT = VIEWPORT_HEIGHT * 0.5
SIDEBAR_MAX_WIDTH = 400.0
SIDEBAR_MIN_WIDTH = 120.0
#: A styled child replaces its unstyled <a>/<button> parent when the boxes are this close.
WRAPPED_BUTTON_SLACK_PX = 8.0
#: Elements mostly outside the 1440px viewport (carousel slides) are not components.
MIN_VISIBLE_FRACTION = 0.5
#: Average glyph advance in em, to tell whether a label can fit in its box.
GLYPH_WIDTH_EM = 0.45
#: An "app window" mock-up: framed, at least this tall, not full-bleed.
FRAME_MIN_HEIGHT = 200.0
FRAME_MAX_WIDTH_RATIO = 0.98


class ButtonStyle(Enum):
    FILLED = "filled"
    OUTLINE = "outline"


@dataclass
class Detection:
    """Candidates of every component kind, over all pages."""

    buttons: list[tuple[Member, ButtonStyle]] = field(default_factory=list)
    ghosts: list[Member] = field(default_factory=list)
    nav_links: list[Member] = field(default_factory=list)
    text_links: list[Member] = field(default_factory=list)
    inputs: list[Member] = field(default_factory=list)
    #: Multi-line fields (<textarea>): their height follows rows/content, not the design.
    text_areas: list[Member] = field(default_factory=list)
    cards: list[Member] = field(default_factory=list)
    badges: list[Member] = field(default_factory=list)
    top_nav: Member | None = None
    sidebar: Member | None = None
    footer: Member | None = None

    def in_top_nav(self, member: Member) -> bool:
        top = self.top_nav
        if top is None or member.ctx.page_index != top.ctx.page_index:
            return False
        return any(node.index == top.element.index for node in member.ctx.lineage(member.element))


# ─── Decorative subtrees ─────────────────────────────────────────────────────


def _is_main(element: ElementSample) -> bool:
    return element.tag == "main" or element.role == "main"


def _is_framed(element: ElementSample) -> bool:
    return (
        radius_px(element) >= CARD_MIN_RADIUS
        and element.box.height >= FRAME_MIN_HEIGHT
        and width_ratio(element) < FRAME_MAX_WIDTH_RATIO
        and (
            own_background(element) is not None
            or visible_border(element) is not None
            or bool(shadow_layers(element))
        )
    )


def _mock_frames(ctx: PageContext) -> set[int]:
    """Framed boxes inside <main> that hold their own navigation: app-window mock-ups."""
    frames: set[int] = set()
    for element in ctx.page.elements:
        if element.tag not in ("nav", "aside") and element.role not in (
            "navigation",
            "complementary",
        ):
            continue
        lineage = list(ctx.lineage(element))[1:]
        if not any(_is_main(node) for node in lineage):
            continue
        for node in lineage:
            if _is_main(node):
                break
            if _is_framed(node):
                frames.add(node.index)
                break
    return frames


def _is_decorative_root(element: ElementSample) -> bool:
    if element.tag in DECORATIVE_TAGS:
        return True
    if (element.role or "").lower() in DECORATIVE_ROLES and element.child_count > 0:
        return True
    return any(_DECORATIVE_CLASS_RE.search(name) for name in element.classes)


def decorative_elements(ctx: PageContext) -> set[int]:
    """Indexes of elements inside illustrations and mock-ups (roots included)."""
    roots = _mock_frames(ctx)
    for element in ctx.page.elements:
        if _is_decorative_root(element):
            roots.add(element.index)
    decorative: set[int] = set()
    for element in ctx.page.elements:  # document order: parents come first
        if element.index in roots or (
            element.parent_index is not None and element.parent_index in decorative
        ):
            decorative.add(element.index)
    return decorative


def mostly_visible(element: ElementSample) -> bool:
    area = element.box.area
    return area > 0 and visible_area(element) / area >= MIN_VISIBLE_FRACTION


# ─── Predicates ──────────────────────────────────────────────────────────────


def is_button_element(element: ElementSample) -> bool:
    return (
        element.tag == "button"
        or element.role == "button"
        or (element.tag == "input" and (element.input_type or "").lower() in BUTTON_INPUT_TYPES)
    )


def is_clickable(element: ElementSample) -> bool:
    return element.tag == "a" or is_button_element(element)


def is_text_input(element: ElementSample) -> bool:
    if element.tag in ("textarea", "select"):
        return True
    if element.tag != "input":
        return False
    kind = (element.input_type or "text").lower()
    return kind in TEXT_INPUT_TYPES


def is_multiline(element: ElementSample) -> bool:
    """A multi-line text field (<textarea>)."""
    return element.tag == "textarea"


def _is_labelled(element: ElementSample) -> bool:
    if element.tag == "input":
        return True  # submit/button inputs render their value
    return has_text(element.text)


def is_icon_only(element: ElementSample) -> bool:
    """No visible label: a square-ish box whose text is a glyph or cannot fit (sr-only)."""
    box = element.box
    if box.width > box.height * 1.5:
        return False
    letters = sum(1 for char in element.text if char.isalnum())
    if letters <= 2:
        return box.width <= box.height * 1.25
    size = parse_px(element.styles.font_size) or 16.0
    return len(element.text.strip()) * size * GLYPH_WIDTH_EM > box.width * 1.15


def button_sized(element: ElementSample) -> bool:
    box = element.box
    return (
        BUTTON_MIN_HEIGHT <= box.height <= BUTTON_MAX_HEIGHT
        and box.width <= BUTTON_MAX_WIDTH
        and _is_labelled(element)
        and not is_icon_only(element)
    )


def label_inset_ok(ctx: PageContext, element: ElementSample) -> bool:
    """Button-like horizontal padding: its own, or the inset of its label's box."""
    _, right, _, left = padding_px(element)
    if min(left, right) >= BUTTON_MIN_PADDING_X:
        return True
    source = text_source(ctx, element)
    if source.index == element.index:
        return False
    inner, outer = source.box, element.box
    _, inner_right, _, inner_left = padding_px(source)
    inset_left = inner.x - outer.x + inner_left
    inset_right = outer.x + outer.width - (inner.x + inner.width) + inner_right
    return min(inset_left, inset_right) >= BUTTON_MIN_PADDING_X


def surface_style(ctx: PageContext, element: ElementSample, canvas: RGBA) -> ButtonStyle | None:
    """FILLED (visible fill, or a raised own background), OUTLINE (border on no fill), None."""
    if is_distinct_fill(ctx, element, canvas):
        return ButtonStyle.FILLED
    if visible_border(element) is not None:
        return ButtonStyle.OUTLINE
    if own_background(element) is not None and shadow_layers(element):
        return ButtonStyle.FILLED
    return None


def button_style(ctx: PageContext, element: ElementSample, canvas: RGBA) -> ButtonStyle | None:
    """Style of a button-like element, None when the element is not button-like."""
    if not button_sized(element):
        return None
    style = surface_style(ctx, element, canvas)
    if style is None:
        return None
    if element.tag == "a" and not label_inset_ok(ctx, element):
        return None
    return style


def _boxes_match(first: ElementSample, second: ElementSample) -> bool:
    a, b = first.box, second.box
    return (
        abs(a.width - b.width) <= WRAPPED_BUTTON_SLACK_PX
        and abs(a.height - b.height) <= WRAPPED_BUTTON_SLACK_PX
    )


def _styled_child(
    ctx: PageContext, element: ElementSample, canvas: RGBA
) -> tuple[ElementSample, ButtonStyle] | None:
    """<a><div class="btn">…</div></a>: the styled child filling an unstyled clickable."""
    for child in ctx.children.get(element.index, []):
        if not is_rendered(child) or is_clickable(child) or not _boxes_match(element, child):
            continue
        style = button_style(ctx, child, canvas)
        if style is not None and label_inset_ok(ctx, child):
            return child, style
        nested = _styled_child(ctx, child, canvas)
        if nested is not None:
            return nested
    return None


def ghost_shaped(ctx: PageContext, element: ElementSample) -> bool:
    """Unfilled, borderless clickable with button padding and rounded corners."""
    return (
        (element.tag in ("a", "button") or element.role == "button")
        and button_sized(element)
        and label_inset_ok(ctx, element)
        and radius_px(element) > 0
        and not in_landmark(ctx, element, NAV_LANDMARKS)
    )


def _decorated(element: ElementSample) -> bool:
    return "underline" in element.styles.text_decoration_line


def text_link_candidate(ctx: PageContext, element: ElementSample, canvas: RGBA) -> bool:
    if element.tag != "a" or element.styles.display not in INLINE_DISPLAYS:
        return False
    if any(side > LINK_MAX_PADDING for side in padding_px(element)):
        return False
    if visible_border_sides(element) >= 3 or is_distinct_fill(ctx, element, canvas):
        return False  # boxed: a button without padding, not an inline link
    if in_landmark(ctx, element, CHROME_LANDMARKS):
        return False
    source = text_source(ctx, element)
    if not source.has_own_text or not has_text(source.text):
        return False
    color = text_color(source)
    chromatic = color is not None and not is_neutral(color)
    return chromatic or _decorated(element) or _decorated(source)


def nav_link_candidate(ctx: PageContext, element: ElementSample) -> bool:
    if not (element.tag == "a" or is_button_element(element)):
        return False
    if not in_landmark(ctx, element, NAV_LANDMARKS) or in_landmark(ctx, element, FOOTER_LANDMARKS):
        return False
    return has_text(element.text) and not is_icon_only(element)


def input_candidate(element: ElementSample) -> bool:
    box = element.box
    if box.width < INPUT_MIN_WIDTH or box.height < INPUT_MIN_HEIGHT:
        return False
    return text_color(element) is not None  # transparent text: an overlay editing trick


def card_candidate(ctx: PageContext, element: ElementSample, canvas: RGBA) -> bool:
    """A bounded, rounded, padded box with at least two blocks of content.

    The paint may come from children hugging the box and the padding may sit on an inner
    wrapper: the surface and the text inset are what the reader sees.
    """
    box = element.box
    if element.tag not in CARD_TAGS or element.child_count < 1:
        return False
    if not (CARD_MIN_WIDTH <= box.width <= CARD_MAX_WIDTH and box.height >= CARD_MIN_HEIGHT):
        return False
    if width_ratio(element) >= CARD_MAX_WIDTH_RATIO:
        return False
    if in_landmark(ctx, element, CHROME_LANDMARKS):
        return False
    surface = resolve_surface(ctx, element)
    if surface.radius < CARD_MIN_RADIUS:
        return False
    if not (
        surface_is_distinct(ctx, element, surface, canvas)
        or surface.border is not None
        or surface.shadow
    ):
        return False
    if content_blocks(ctx, element) < 2:
        return False
    if min(padding_px(element)) >= CARD_MIN_PADDING:
        return True
    inset = text_inset(ctx, element)
    return inset is not None and inset >= CARD_MIN_PADDING


def badge_named(element: ElementSample) -> bool:
    """Class names say badge/chip/tag (and not button): e.g. Bootstrap's "badge" pill links."""
    tokens: set[str] = set()
    for name in meaningful_classes(element.classes):
        tokens.update(token for token in re.split(r"[-_]+", name.lower()) if token)
    return bool(tokens & BADGE_CLASS_TOKENS) and not tokens & BUTTON_CLASS_TOKENS


def badge_candidate(ctx: PageContext, element: ElementSample, canvas: RGBA) -> bool:
    box = element.box
    if element.tag not in BADGE_TAGS:
        return False
    # Elements named "badge"/"chip"/"tag" may be hero announcement pills: larger limits.
    named = badge_named(element)
    max_height, max_width, max_text, max_font = (
        (BADGE_NAMED_MAX_HEIGHT, BADGE_NAMED_MAX_WIDTH, BADGE_NAMED_MAX_TEXT, BADGE_NAMED_MAX_FONT)
        if named
        else (BADGE_MAX_HEIGHT, BADGE_MAX_WIDTH, BADGE_MAX_TEXT, BADGE_MAX_FONT_PX)
    )
    if not (BADGE_MIN_HEIGHT <= box.height <= max_height and box.width <= max_width):
        return False
    if box.width < box.height or radius_px(element) < BADGE_MIN_RADIUS:
        return False
    text = element.text.strip()
    if not has_text(text) or len(text) > max_text:
        return False
    source = text_source(ctx, element)
    size = parse_px(source.styles.font_size) or 16.0
    if size > max_font:
        return False
    return is_distinct_fill(ctx, element, canvas) or visible_border_sides(element) >= 3


def _contains_links(ctx: PageContext, element: ElementSample, minimum: int) -> bool:
    count = 0
    for node in ctx.descendants(element):
        if node.tag == "a" or is_button_element(node):
            count += 1
            if count >= minimum:
                return True
    return False


def _outermost(candidates: Sequence[ElementSample]) -> ElementSample | None:
    if not candidates:
        return None
    return min(candidates, key=lambda element: (element.depth, element.index))


def find_top_nav(ctx: PageContext, decorative: set[int]) -> ElementSample | None:
    rendered = [
        e
        for e in ctx.page.elements
        if is_rendered(e) and not e.in_overlay and e.index not in decorative
    ]
    wide = [e for e in rendered if width_ratio(e) >= CHROME_MIN_WIDTH_RATIO]

    def is_header(element: ElementSample) -> bool:
        return element.tag == "header" or element.role == "banner"

    def is_nav(element: ElementSample) -> bool:
        return element.tag == "nav" or element.role == "navigation"

    for predicate, max_y in (
        (is_header, TOP_NAV_MAX_Y),
        (is_nav, TOP_NAV_MAX_Y),
        (is_header, TOP_NAV_FALLBACK_MAX_Y),
    ):
        found = _outermost([e for e in wide if predicate(e) and e.box.y <= max_y])
        if found is not None:
            return found
    pinned = [
        e
        for e in wide
        if is_header(e)
        and e.styles.position in ("fixed", "sticky")
        and e.box.height <= TOP_NAV_MAX_HEIGHT
    ]
    found = _outermost(pinned)
    if found is not None:
        return found  # pinned to the top whatever the scroll position was when measured
    bars = [
        e
        for e in wide
        if e.styles.position in ("fixed", "sticky")
        and e.box.y <= TOP_NAV_MAX_Y
        and 32 <= e.box.height <= TOP_NAV_MAX_HEIGHT
        and _contains_links(ctx, e, 2)
    ]
    return _outermost(bars)


def find_footer(ctx: PageContext) -> ElementSample | None:
    candidates = [
        e
        for e in ctx.page.elements
        if is_rendered(e)
        and not e.in_overlay
        and (e.tag == "footer" or e.role == "contentinfo")
        and width_ratio(e) >= CHROME_MIN_WIDTH_RATIO
    ]
    outer = [
        e
        for e in candidates
        if not any(
            node.tag == "footer" or node.role == "contentinfo"
            for node in ctx.lineage(e)
            if node.index != e.index
        )
    ]
    pool = outer or candidates
    if not pool:
        return None
    return max(pool, key=lambda element: (element.box.area, -element.index))


def find_sidebar(ctx: PageContext, decorative: set[int]) -> ElementSample | None:
    candidates = []
    for element in ctx.page.elements:
        if not is_rendered(element) or element.in_overlay or element.index in decorative:
            continue
        if element.tag not in ("aside", "nav") and element.role not in (
            "navigation",
            "complementary",
        ):
            continue
        box = element.box
        if box.height <= SIDEBAR_MIN_HEIGHT:
            continue
        if not SIDEBAR_MIN_WIDTH <= box.width < SIDEBAR_MAX_WIDTH:
            continue
        if box.x + box.width / 2.0 >= CAPTURE_VIEWPORT_WIDTH / 3.0:
            continue
        if in_landmark(ctx, element, HEADER_LANDMARKS | FOOTER_LANDMARKS):
            continue
        candidates.append(element)
    if not candidates:
        return None
    return max(candidates, key=lambda element: (element.box.height, -element.depth))


def fill_on(ctx: PageContext, element: ElementSample, canvas: RGBA) -> RGBA | None:
    """Own background composited on what is behind it (None when transparent)."""
    color = own_background(element)
    if color is None:
        return None
    return composite(color, backdrop(ctx, element, canvas)).with_alpha(1.0)


def is_chromatic_fill(ctx: PageContext, element: ElementSample, canvas: RGBA) -> bool:
    color = fill_on(ctx, element, canvas)
    return color is not None and not is_neutral(color)


# ─── Detection pass ──────────────────────────────────────────────────────────


def _inside(ctx: PageContext, element: ElementSample, claimed: set[int]) -> bool:
    return any(
        node.index in claimed for node in ctx.lineage(element) if node.index != element.index
    )


def detect(contexts: Sequence[PageContext], canvas: RGBA) -> Detection:
    """Sort every rendered, non-overlay, non-decorative element into candidate pools."""
    found = Detection()
    decorative = {ctx.page_index: decorative_elements(ctx) for ctx in contexts}
    for ctx in contexts:
        _detect_page(ctx, canvas, decorative[ctx.page_index], found)
    for ctx in contexts:
        top = find_top_nav(ctx, decorative[ctx.page_index])
        if top is not None:
            found.top_nav = make_member(ctx, top)
            break
    for ctx in contexts:
        footer = find_footer(ctx)
        if footer is not None:
            found.footer = make_member(ctx, footer)
            break
    if contexts:
        main = contexts[0]
        sidebar = find_sidebar(main, decorative[main.page_index])
        if sidebar is not None:
            found.sidebar = make_member(main, sidebar)
    return found


def _detect_page(ctx: PageContext, canvas: RGBA, decorative: set[int], found: Detection) -> None:
    elements = [
        e
        for e in ctx.page.elements
        if is_rendered(e) and not e.in_overlay and e.index not in decorative and mostly_visible(e)
    ]
    # Pass 1: button-like elements (a clickable, or the styled child filling one).
    styles: dict[int, ButtonStyle] = {}
    delegated: set[int] = set()
    for element in elements:
        if not is_clickable(element) or badge_named(element):
            continue
        style = button_style(ctx, element, canvas)
        if style is not None:
            styles[element.index] = style
            continue
        wrapped = _styled_child(ctx, element, canvas)
        if wrapped is not None and button_sized(element):
            child, child_style = wrapped
            styles[child.index] = child_style
            delegated.add(element.index)
    claimed: set[int] = set(styles) | delegated
    ghosts: set[int] = set()
    for element in elements:
        if element.index in claimed or not is_clickable(element):
            continue
        if ghost_shaped(ctx, element) and surface_style(ctx, element, canvas) is None:
            ghosts.add(element.index)
    claimed |= ghosts

    # Pass 2: sort elements into pools.
    cards: set[int] = set()
    for element in elements:
        index = element.index
        if index in styles:
            found.buttons.append((make_member(ctx, element), styles[index]))
            continue
        if index in ghosts:
            found.ghosts.append(make_member(ctx, element))
            continue
        if index in delegated or _inside(ctx, element, claimed):
            continue
        if is_text_input(element):
            if input_candidate(element):
                pool = found.text_areas if is_multiline(element) else found.inputs
                pool.append(make_member(ctx, element))
            continue
        if is_clickable(element) and nav_link_candidate(ctx, element):
            found.nav_links.append(make_member(ctx, element))
            continue
        if text_link_candidate(ctx, element, canvas):
            found.text_links.append(make_member(ctx, element))
            continue
        if card_candidate(ctx, element, canvas):
            parent = ctx.parent(element)
            if parent is not None and parent.index in cards and hugs(parent, element):
                continue  # the inner layer of a card already found
            cards.add(index)
            found.cards.append(make_member(ctx, element))
            continue
        if badge_candidate(ctx, element, canvas):
            found.badges.append(make_member(ctx, element))
