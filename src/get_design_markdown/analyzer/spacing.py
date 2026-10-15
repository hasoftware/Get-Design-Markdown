"""Spacing: base unit (4 or 8px), spacing scale, container width, section rhythm.

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_spacing(pages) -> SpacingSystem

Rules (pure, deterministic; page weights from analyzer/common.py):
- Every foundation analyzer (typography, spacing, shapes) reads elements through
  common.iter_design_elements(): common.iter_elements() minus decorative subtrees (DOM-built
  mockups and graphics: role="img" or a graphic/illustration/mockup/artwork class segment) and
  off-canvas elements (entirely outside the 1440px viewport).
- Values: padding sides, positive margins and row/column gaps of rendered, non-overlay
  elements, in px rounded to 0.5; values under 2px are ignored, and so are auto-centering
  margins (left ~ right >= 32px on a horizontally centered element, or a side >= 25% of the
  parent width). Each element counts once per distinct value.
- base_unit_px: 8 when >= 75% of the weighted values are on an 8px grid, else 4 when >= 60% are
  on a 4px grid, else None. A value one pixel short of a multiple (11px padding inside a 1px
  border) counts as on the grid.
- steps: values used by >= 2 elements (or as a gap) up to 128px; values >= 80px that only occur
  as the vertical padding of full-width sections feed section_gap_px instead. Values within 1px
  merge (multiples of the base unit win, so an 11px "12px minus a border" joins 12px); at most 10
  steps and at most three below 16px (off-grid, then least used values go first), so the names
  from naming.SPACING_STEP_NAMES always put "md" on the value closest to 16px.
- container_max_width_px: the dominant px max-width of horizontally centered elements >= 600px
  wide; else the widest centered width (< 1440px) seen at least twice; else the container width
  measured by the collector at 1440px.
- section_gap_px: the dominant vertical padding (>= 48px) of sections / full-width blocks of the
  main content and of the centered container right inside them; else the dominant vertical gap
  between directly adjacent full-width sections.
- common_paddings: the four most used non-zero padding shorthands of interactive and card-like
  elements.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from get_design_markdown.analyzer.common import (
    CAPTURE_VIEWPORT_WIDTH,
    ElementRef,
    element_index,
    has_visible_border,
    is_interactive,
    iter_design_elements,
    own_background,
    short_selector,
)
from get_design_markdown.analyzer.css_values import (
    format_sides_px,
    parse_px,
    parse_sides_px,
    visible_shadow_layers,
)
from get_design_markdown.analyzer.naming import SPACING_STEP_NAMES, name_scale
from get_design_markdown.models.analysis import ScaleStep, SpacingSystem
from get_design_markdown.models.raw import ElementSample, PageCapture

MIN_SPACING_PX = 2.0
MAX_STEP_PX = 128.0
SECTION_ONLY_MIN_PX = 80.0
SECTION_GAP_MIN_PX = 48.0
AUTO_MARGIN_MIN_PX = 32.0
AUTO_MARGIN_PARENT_SHARE = 0.25
MAX_STEPS = 10
#: Names below "md" (xxs, xs, sm).
MAX_STEPS_BELOW_ANCHOR = SPACING_STEP_NAMES.index("md")
MERGE_DISTANCE_PX = 1.0
ANCHOR_PX = 16.0
CONTAINER_MIN_WIDTH_PX = 600.0
CENTER_TOLERANCE_PX = 2.0
FULL_WIDTH_SHARE = 0.9
MAX_COMMON_PADDINGS = 4
MAX_USED_ON = 3

_PX_LITERAL_RE = re.compile(r"(\d+(?:\.\d+)?)px")


@dataclass
class _Value:
    value: float
    weight: float = 0.0
    count: int = 0
    gap: bool = False
    #: Used anywhere else than as the vertical padding of a full-width section.
    general: bool = False
    selectors: dict[str, float] = field(default_factory=dict)

    def add(self, weight: float, selector: str, *, gap: bool, general: bool) -> None:
        self.weight += weight
        self.count += 1
        self.gap = self.gap or gap
        self.general = self.general or general
        self.selectors[selector] = self.selectors.get(selector, 0.0) + weight

    def absorb(self, other: _Value) -> None:
        self.weight += other.weight
        self.count += other.count
        self.gap = self.gap or other.gap
        self.general = self.general or other.general
        for selector, weight in other.selectors.items():
            self.selectors[selector] = self.selectors.get(selector, 0.0) + weight


def round_half(value: float) -> float:
    return round(value * 2.0) / 2.0


def _parent_box(element: ElementSample, index: dict[int, ElementSample]) -> tuple[float, float]:
    """(left, width) of the sampled parent, or of the capture viewport."""
    parent = None if element.parent_index is None else index.get(element.parent_index)
    if parent is None or parent.box.width <= 0:
        return 0.0, CAPTURE_VIEWPORT_WIDTH
    return parent.box.x, parent.box.width


def is_horizontally_centered(
    element: ElementSample,
    index: dict[int, ElementSample],
    *,
    tolerance: float = CENTER_TOLERANCE_PX,
) -> bool:
    """Left and right gaps to the parent box (or viewport) equal within tolerance."""
    left, width = _parent_box(element, index)
    gap_left = element.box.x - left
    gap_right = (left + width) - (element.box.x + element.box.width)
    return abs(gap_left - gap_right) <= tolerance and gap_left >= -tolerance


def is_full_width(
    element: ElementSample, *, viewport_width: float = CAPTURE_VIEWPORT_WIDTH
) -> bool:
    return element.box.width >= FULL_WIDTH_SHARE * viewport_width


def _font_size(element: ElementSample, page: PageCapture) -> float:
    size = parse_px(element.styles.font_size, root_font_size_px=page.root_font_size_px)
    return size if size is not None and size > 0 else 16.0


def _is_section_like(element: ElementSample, index: dict[int, ElementSample]) -> bool:
    """<section>s and full-width blocks of the page body / main content."""
    if element.tag in ("section", "header", "footer") and element.box.width >= 600:
        return True
    if not is_full_width(element):
        return False
    parent = None if element.parent_index is None else index.get(element.parent_index)
    if parent is None:
        return element.tag not in ("html", "body")
    return parent.tag in ("main", "body", "section", "article") or parent.landmark == "main"


def _element_values(
    ref: ElementRef, index: dict[int, ElementSample]
) -> Iterator[tuple[float, bool, bool]]:
    """(value px, is_gap, is_general) for every spacing value of one element."""
    element = ref.element
    styles = element.styles
    font_size = _font_size(element, ref.page)
    section = _is_section_like(element, index)
    padding = parse_sides_px(styles.padding, font_size_px=font_size)
    for position, value in enumerate(padding):
        vertical = position in (0, 2)
        yield value, False, not (section and vertical)
    margins = parse_sides_px(styles.margin, font_size_px=font_size)
    _, right, _, left = margins
    _, parent_width = _parent_box(element, index)
    centering = (
        abs(left - right) <= 1.0
        and left >= AUTO_MARGIN_MIN_PX
        and is_horizontally_centered(element, index)
    )
    for position, value in enumerate(margins):
        if value <= 0:
            continue
        horizontal = position in (1, 3)
        if horizontal and (centering or value >= AUTO_MARGIN_PARENT_SHARE * parent_width):
            continue
        yield value, False, True
    for raw in (styles.row_gap, styles.column_gap):
        gap = parse_px(raw, font_size_px=font_size)
        if gap is not None:
            yield gap, True, True


def collect_values(pages: Sequence[PageCapture]) -> dict[float, _Value]:
    """Spacing values (rounded to 0.5px) with their weighted usage."""
    values: dict[float, _Value] = {}
    indexes = {position: element_index(page) for position, page in enumerate(pages)}
    for ref in iter_design_elements(pages):
        index = indexes[ref.page_index]
        seen: dict[float, tuple[bool, bool]] = {}
        for raw, gap, general in _element_values(ref, index):
            value = round_half(raw)
            if value < MIN_SPACING_PX or not math.isfinite(value):
                continue
            was_gap, was_general = seen.get(value, (False, False))
            seen[value] = (was_gap or gap, was_general or general)
        if not seen:
            continue
        selector = short_selector(ref.element)
        for value, (gap, general) in seen.items():
            entry = values.get(value)
            if entry is None:
                entry = values[value] = _Value(value)
            entry.add(ref.weight, selector, gap=gap, general=general)
    return values


def on_grid(value: float, unit: int) -> bool:
    """A multiple of unit, or one pixel short of it — the usual way a 1px border is
    compensated (padding: 11px on a bordered 12px-grid button)."""
    return value % unit == 0 or (value >= 3 and (value + 1) % unit == 0)


def detect_base_unit(values: dict[float, _Value]) -> int | None:
    total = sum(entry.weight for entry in values.values())
    if total <= 0:
        return None

    def share(unit: int) -> float:
        return sum(e.weight for v, e in values.items() if on_grid(v, unit)) / total

    if share(8) >= 0.75:
        return 8
    if share(4) >= 0.6:
        return 4
    return None


def _is_multiple(value: float, base: int | None) -> bool:
    return base is not None and value % base == 0


def build_steps(values: dict[float, _Value], base: int | None) -> list[ScaleStep]:
    candidates = [
        entry
        for value, entry in values.items()
        if value <= MAX_STEP_PX
        and (entry.count >= 2 or entry.gap)
        and (value < SECTION_ONLY_MIN_PX or entry.general)
    ]
    kept: list[_Value] = []
    ranked = sorted(
        candidates,
        key=lambda e: (not _is_multiple(e.value, base), -e.weight, e.value),
    )
    for entry in ranked:
        target = next(
            (k for k in kept if abs(k.value - entry.value) <= MERGE_DISTANCE_PX),
            None,
        )
        if target is None:
            kept.append(_Value(entry.value, selectors={}))
            kept[-1].absorb(entry)
        else:
            target.absorb(entry)

    def prune(pool: list[_Value]) -> None:
        weakest = min(pool, key=lambda e: (_is_multiple(e.value, base), e.weight, -e.value))
        kept.remove(weakest)

    # md stays on the value closest to 16px: only xxs, xs and sm exist below it.
    while kept:
        anchor = min(kept, key=lambda e: (abs(e.value - ANCHOR_PX), e.value)).value
        below = [e for e in kept if e.value < anchor]
        if len(below) <= MAX_STEPS_BELOW_ANCHOR:
            break
        prune(below)
    while len(kept) > MAX_STEPS:
        prune(kept)
    kept.sort(key=lambda e: e.value)
    names = name_scale([e.value for e in kept], SPACING_STEP_NAMES, anchor_value=ANCHOR_PX)
    return [
        ScaleStep(
            name=name,
            value_px=entry.value,
            count=entry.count,
            used_on=_top_selectors(entry.selectors),
        )
        for name, entry in zip(names, kept, strict=True)
    ]


def _top_selectors(selectors: dict[str, float], limit: int = MAX_USED_ON) -> list[str]:
    ordered = sorted(selectors.items(), key=lambda item: -item[1])
    return [selector for selector, _ in ordered[:limit]]


# ─── Container & sections ────────────────────────────────────────────────────


def _max_width_px(element: ElementSample) -> float | None:
    raw = element.styles.max_width.strip().lower()
    value = parse_px(raw)
    if value is not None:
        return value
    if raw.startswith(("min(", "clamp(")):
        literals = [float(match) for match in _PX_LITERAL_RE.findall(raw)]
        return max(literals) if literals else None
    return None


def _weighted_mode(weights: dict[float, float]) -> float | None:
    if not weights:
        return None
    return max(weights, key=lambda value: (weights[value], value))


def container_max_width(pages: Sequence[PageCapture]) -> float | None:
    """Dominant max-width of the centered page container (see module docstring)."""
    max_widths: dict[float, float] = defaultdict(float)
    centered_widths: dict[float, int] = defaultdict(int)
    indexes = {position: element_index(page) for position, page in enumerate(pages)}
    for ref in iter_design_elements(pages):
        element = ref.element
        if element.box.width < CONTAINER_MIN_WIDTH_PX:
            continue
        if not is_horizontally_centered(element, indexes[ref.page_index]):
            continue
        max_width = _max_width_px(element)
        if max_width is not None and CONTAINER_MIN_WIDTH_PX <= max_width < 4000:
            weight = ref.weight * (2.0 if element.child_count >= 2 else 1.0)
            max_widths[round_half(max_width)] += weight
        if ref.page_index == 0 and element.box.width < CAPTURE_VIEWPORT_WIDTH - 2:
            centered_widths[round_half(element.box.width)] += 1
    found = _weighted_mode(max_widths)
    if found is not None:
        return found
    repeated = [width for width, count in centered_widths.items() if count >= 2]
    if repeated:
        return max(repeated)
    if pages:
        for snapshot in pages[0].layouts:
            if snapshot.viewport_width == int(CAPTURE_VIEWPORT_WIDTH):
                return snapshot.container_width_px
    return None


def _is_section_container(element: ElementSample, index: dict[int, ElementSample]) -> bool:
    """The centered content container directly inside a section (where many sites put the
    section padding: <section><div class="container" style="padding: 96px 16px">)."""
    parent = None if element.parent_index is None else index.get(element.parent_index)
    if parent is None or not _is_section_like(parent, index):
        return False
    return element.box.width >= CONTAINER_MIN_WIDTH_PX and is_horizontally_centered(element, index)


def section_gap(pages: Sequence[PageCapture]) -> float | None:
    paddings: dict[float, float] = defaultdict(float)
    indexes = {position: element_index(page) for position, page in enumerate(pages)}
    for ref in iter_design_elements(pages):
        element = ref.element
        index = indexes[ref.page_index]
        if element.landmark in ("header", "footer", "nav") or element.tag in ("header", "footer"):
            continue
        if not (_is_section_like(element, index) or _is_section_container(element, index)):
            continue
        font_size = _font_size(element, ref.page)
        top, _, bottom, _ = parse_sides_px(element.styles.padding, font_size_px=font_size)
        for value in (top, bottom):
            if value >= SECTION_GAP_MIN_PX:
                paddings[round_half(value)] += ref.weight
    found = _weighted_mode(paddings)
    if found is not None:
        return found
    return _gap_between_sections(pages, indexes)


def _gap_between_sections(
    pages: Sequence[PageCapture], indexes: dict[int, dict[int, ElementSample]]
) -> float | None:
    gaps: dict[float, float] = defaultdict(float)
    for position, page in enumerate(pages):
        index = indexes[position]
        siblings: dict[int | None, list[ElementSample]] = defaultdict(list)
        for ref in iter_design_elements([page]):
            siblings[ref.element.parent_index].append(ref.element)
        weight = 1.0 if position == 0 else 0.5
        for group in siblings.values():
            group.sort(key=lambda e: (e.box.y, e.index))
            # Only directly adjacent sections: anything in between would be counted as gap.
            for previous, current in pairwise(group):
                if not (_is_main_section(previous, index) and _is_main_section(current, index)):
                    continue
                gap = current.box.y - (previous.box.y + previous.box.height)
                if gap >= SECTION_GAP_MIN_PX:
                    gaps[round_half(gap)] += weight
    return _weighted_mode(gaps)


def _is_main_section(element: ElementSample, index: dict[int, ElementSample]) -> bool:
    return _is_section_like(element, index) and element.landmark not in ("header", "footer")


def _is_card_like(element: ElementSample) -> bool:
    if element.tag in ("html", "body") or element.box.width >= FULL_WIDTH_SHARE * 1440:
        return False
    painted = (
        own_background(element) is not None
        or has_visible_border(element)
        or bool(visible_shadow_layers(element.styles.box_shadow))
    )
    return painted and element.child_count >= 1


def common_paddings(pages: Sequence[PageCapture]) -> list[str]:
    counts: dict[str, float] = defaultdict(float)
    first_seen: dict[str, int] = {}
    for ref in iter_design_elements(pages):
        element = ref.element
        if not (is_interactive(element) or _is_card_like(element)):
            continue
        sides = parse_sides_px(element.styles.padding, font_size_px=_font_size(element, ref.page))
        if not any(value >= 1 for value in sides):
            continue
        shorthand = format_sides_px([round_half(value) for value in sides])
        counts[shorthand] += ref.weight
        first_seen.setdefault(shorthand, len(first_seen))
    ordered = sorted(counts, key=lambda key: (-counts[key], first_seen[key]))
    return ordered[:MAX_COMMON_PADDINGS]


def analyze_spacing(pages: Sequence[PageCapture]) -> SpacingSystem:
    """Spacing system from paddings, margins and gaps (pure)."""
    values = collect_values(pages)
    if not values:
        return SpacingSystem(
            container_max_width_px=container_max_width(pages),
            section_gap_px=section_gap(pages),
        )
    base = detect_base_unit(values)
    return SpacingSystem(
        base_unit_px=base,
        steps=build_steps(values, base),
        container_max_width_px=container_max_width(pages),
        section_gap_px=section_gap(pages),
        common_paddings=common_paddings(pages),
    )
