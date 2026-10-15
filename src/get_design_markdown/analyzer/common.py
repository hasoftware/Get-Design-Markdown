"""Helpers shared by every analyzer module: element iteration, weights, backgrounds, selectors.

Conventions (all analyzers follow them so results stay consistent):
- pages[0] is the main page (weight 1.0); discovered sub-pages weigh 0.5 each.
- Elements inside overlays (cookie banners, modals) are skipped unless asked for.
- Areas are clipped to the 1440px capture viewport horizontally.
- iter_design_elements() also skips decorative subtrees (DOM-built mockups and graphics) and
  off-canvas elements; the foundation analyzers (typography, spacing, shapes) read through it.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from get_design_markdown.analyzer.css_values import (
    RGBA,
    WHITE,
    composite,
    parse_color,
    parse_opacity,
    parse_px,
)
from get_design_markdown.models.raw import ElementSample, PageCapture

PAGE_WEIGHT_MAIN = 1.0
PAGE_WEIGHT_SUB = 0.5
CAPTURE_VIEWPORT_WIDTH = 1440.0

HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
INTERACTIVE_TAGS = frozenset({"a", "button", "input", "select", "textarea", "summary", "label"})
INTERACTIVE_ROLES = frozenset(
    {"button", "link", "tab", "menuitem", "switch", "checkbox", "radio", "option"}
)
FORM_FIELD_TAGS = frozenset({"input", "select", "textarea"})


def page_weight(page_index: int) -> float:
    return PAGE_WEIGHT_MAIN if page_index == 0 else PAGE_WEIGHT_SUB


@dataclass(frozen=True, slots=True)
class ElementRef:
    page_index: int
    page: PageCapture
    element: ElementSample
    #: Page weight (1.0 main page, 0.5 sub-pages).
    weight: float


def is_rendered(element: ElementSample) -> bool:
    """Non-empty box, not fully transparent, not display:none/contents."""
    if element.box.width <= 0 or element.box.height <= 0:
        return False
    if parse_opacity(element.styles.opacity) <= 0.01:
        return False
    return element.styles.display not in ("none", "contents")


def iter_elements(
    pages: Sequence[PageCapture], *, include_overlays: bool = False
) -> Iterator[ElementRef]:
    """Rendered elements of every page in document order, with their page weight."""
    for page_index, page in enumerate(pages):
        weight = page_weight(page_index)
        for element in page.elements:
            if element.in_overlay and not include_overlays:
                continue
            if is_rendered(element):
                yield ElementRef(page_index, page, element, weight)


# ─── Design elements: decorative subtrees and off-canvas boxes skipped ────────

#: Class-name segments of DOM-built illustrations: product mockups, animated graphics...
_DECORATIVE_CLASS_RE = re.compile(
    r"(?:^|[-_])(?:graphics?|illustrations?|mockups?|artworks?)(?:$|[-_])", re.IGNORECASE
)
_DECORATIVE_ROLES = frozenset({"img"})


def is_decorative_root(element: ElementSample) -> bool:
    """An element that starts an illustration: role="img", or a class segment such as
    "payments-graphic__card" / "GJ9TEa_illustration" / "hero-mockup"."""
    if (element.role or "").strip().lower() in _DECORATIVE_ROLES:
        return True
    return any(_DECORATIVE_CLASS_RE.search(name) for name in element.classes)


def decorative_indexes(page: PageCapture) -> frozenset[int]:
    """Indexes of the elements inside a decorative subtree (the root included).

    Pages often draw product mockups and animated graphics with real DOM text, radii and
    shadows (tiny 9-12px labels, dashboard cards). They illustrate, they are not the site's
    own typography, spacing or elevation, so the foundation analyzers skip them.
    """
    flagged: set[int] = set()
    # Document order: a sampled ancestor always comes before its descendants.
    for element in sorted(page.elements, key=lambda item: item.index):
        parent = element.parent_index
        if is_decorative_root(element) or (parent is not None and parent in flagged):
            flagged.add(element.index)
    return frozenset(flagged)


def is_off_canvas(element: ElementSample) -> bool:
    """Entirely outside the capture viewport horizontally (a closed drawer parked at
    x = 1440, a slide-in menu translated to the left)."""
    box = element.box
    return box.x >= CAPTURE_VIEWPORT_WIDTH or box.x + box.width <= 0


def iter_design_elements(pages: Sequence[PageCapture]) -> Iterator[ElementRef]:
    """iter_elements() without decorative subtrees (see decorative_indexes()) and
    off-canvas elements."""
    skipped = {position: decorative_indexes(page) for position, page in enumerate(pages)}
    for ref in iter_elements(pages):
        if ref.element.index not in skipped[ref.page_index] and not is_off_canvas(ref.element):
            yield ref


def visible_area(
    element: ElementSample,
    *,
    viewport_width: float = CAPTURE_VIEWPORT_WIDTH,
    max_height: float | None = None,
) -> float:
    """Box area clipped horizontally to the viewport (and vertically to max_height)."""
    left = max(element.box.x, 0.0)
    right = min(element.box.x + element.box.width, viewport_width)
    width = max(right - left, 0.0)
    height = max(element.box.height, 0.0)
    if max_height is not None:
        height = min(height, max_height)
    return width * height


def element_index(page: PageCapture) -> dict[int, ElementSample]:
    return {element.index: element for element in page.elements}


def ancestors(element: ElementSample, index: dict[int, ElementSample]) -> Iterator[ElementSample]:
    """Sampled ancestors, nearest first (cycle-safe)."""
    seen = {element.index}
    parent = element.parent_index
    while parent is not None and parent not in seen:
        node = index.get(parent)
        if node is None:
            return
        seen.add(parent)
        yield node
        parent = node.parent_index


def own_background(element: ElementSample) -> RGBA | None:
    """The element's background-color, or None when (fully) transparent."""
    color = parse_color(element.styles.background_color)
    if color is None or color.is_transparent:
        return None
    return color


def effective_background(
    element: ElementSample, index: dict[int, ElementSample], *, base: RGBA = WHITE
) -> RGBA:
    """Opaque color behind the element's content: its own and its ancestors' background-colors
    composited until an opaque one is found, on `base` (the canvas) otherwise.

    Gradient/background images are ignored (they are decorative or unknowable).
    """
    layers: list[RGBA] = []
    for node in (element, *ancestors(element, index)):
        color = own_background(node)
        if color is None:
            continue
        layers.append(color)
        if color.is_opaque:
            break
    result = base if base.is_opaque else composite(base, WHITE)
    for layer in reversed(layers):
        result = composite(layer, result)
    return result.with_alpha(1.0)


def has_visible_border(element: ElementSample) -> bool:
    """At least one side with width > 0, a visible style and a non-transparent color."""
    styles = element.styles
    for width, style, color in zip(
        styles.border_width, styles.border_style, styles.border_color, strict=False
    ):
        if (parse_px(width) or 0.0) <= 0 or style in ("none", "hidden"):
            continue
        parsed = parse_color(color, current_color=styles.color)
        if parsed is not None and not parsed.is_transparent:
            return True
    return False


def is_heading(element: ElementSample) -> bool:
    return element.tag in HEADING_TAGS or element.role == "heading"


def is_interactive(element: ElementSample) -> bool:
    return (
        element.tag in INTERACTIVE_TAGS
        or (element.role or "") in INTERACTIVE_ROLES
        or element.styles.cursor == "pointer"
    )


# ─── Class names & selectors ─────────────────────────────────────────────────

_SEGMENT_SPLIT_RE = re.compile(r"_+|-+")
_READABLE_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_GENERATED_PREFIXES = frozenset(
    {"css", "sc", "jsx", "emotion", "styled", "svelte", "astro", "module", "styles", "tw"}
)


def _is_hash_segment(segment: str) -> bool:
    if not segment:
        return True
    has_digit = any(char.isdigit() for char in segment)
    has_alpha = any(char.isalpha() for char in segment)
    if has_digit and len(segment) >= 5 and (has_alpha or segment.isdigit()):
        return True
    uppercase = sum(1 for char in segment if char.isupper())
    if uppercase >= 2 and 4 <= len(segment) <= 10:
        runs = [run for run in re.split(r"[A-Z]", segment) if run]
        average = sum(len(run) for run in runs) / len(runs) if runs else 0.0
        if average < 3:
            return True
    return False


def clean_class_name(name: str) -> str | None:
    """Readable part of a class name, or None when it is fully generated.

    "btn-primary" -> "btn-primary"; "Button_primary__3xK9z" -> "button-primary";
    "css-1q2w3e4" -> None; "sc-bdVaJa" -> None; "kKDMPh" -> None; "md:px-4" -> "md:px-4".
    """
    text = name.strip()
    if not text:
        return None
    if ":" in text or "/" in text or "[" in text:
        return text  # Tailwind variants / arbitrary values are readable as-is.
    segments = [segment for segment in _SEGMENT_SPLIT_RE.split(text) if segment]
    kept = [segment for segment in segments if not _is_hash_segment(segment)]
    if not kept or all(segment.lower() in _GENERATED_PREFIXES for segment in kept):
        return None
    if kept == segments:
        return text
    readable = [segment for segment in kept if segment.lower() not in _GENERATED_PREFIXES]
    return "-".join(segment.lower() for segment in readable)


def is_hashed_class(name: str) -> bool:
    return clean_class_name(name) is None


def meaningful_classes(classes: Iterable[str]) -> list[str]:
    """Readable class names (deduplicated, original order)."""
    result: list[str] = []
    for name in classes:
        cleaned = clean_class_name(name)
        if cleaned is not None and cleaned not in result:
            result.append(cleaned)
    return result


def short_selector(element: ElementSample, *, max_classes: int = 2) -> str:
    """Readable selector for evidence/samples: "a.btn.btn-primary", "nav#main", "input"."""
    selector = element.tag
    if (
        element.id
        and _READABLE_ID_RE.fullmatch(element.id)
        and clean_class_name(element.id) == element.id
    ):
        selector += f"#{element.id}"
    for name in meaningful_classes(element.classes)[:max_classes]:
        selector += f".{name}"
    return selector


def top_class_hints(elements: Iterable[ElementSample], *, limit: int = 5) -> list[str]:
    """Most frequent readable class names across elements (component class hints)."""
    counter: Counter[str] = Counter()
    for element in elements:
        counter.update(meaningful_classes(element.classes))
    return [name for name, _ in counter.most_common(limit)]
