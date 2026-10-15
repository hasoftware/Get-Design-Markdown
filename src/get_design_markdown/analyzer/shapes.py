"""Border-radius scale and shadow (elevation) levels.

PUBLIC ENTRY POINTS (frozen signatures, called by analyzer/analyze.py):
    analyze_radii(pages) -> list[ScaleStep]
    analyze_shadows(pages) -> list[ShadowLevel]

Radii: the largest corner of every rendered, non-overlay element that shows its shape (painted
background/border/shadow/image, clipping, media, or interactive so a hover fill reveals it).
Percentages resolve against min(width, height). Pills and circles (radius >= half the short
side and >= 16px, a percentage >= 50%, or >= naming.PILL_RADIUS_PX) become the "full" step
(9999px). Other values merge within 0.5px; with more than six distinct values the one-off ones
are dropped and at most seven remain. Names come from naming.RADIUS_STEP_NAMES with the dominant
radius of button-like elements as "md"; "full" is always last.

Shadows: visible box-shadow layers of rendered, non-overlay elements, grouped by their
normalized CSS. Inset layers and pure rings ("0 0 0 1px", a border drawn with box-shadow) do not
lift anything, so shadows made only of them are ignored. Elevation score = max over lifting
layers of (offset_y + blur / 2 + spread) x alpha; groups whose scores differ by less than 10%
merge into the more used one. With more than four levels, one-off shadows are dropped; at most
six remain. Levels are ordered by score and named sm, md, lg... (or from xs when there are
six).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from get_design_markdown.analyzer.common import (
    ElementRef,
    has_visible_border,
    is_interactive,
    iter_design_elements,
    own_background,
    short_selector,
)
from get_design_markdown.analyzer.css_values import (
    ShadowLayer,
    format_box_shadow,
    parse_number,
    parse_px,
    visible_shadow_layers,
)
from get_design_markdown.analyzer.naming import (
    FULL_RADIUS_NAME,
    FULL_RADIUS_PX,
    PILL_RADIUS_PX,
    RADIUS_STEP_NAMES,
    SHADOW_LEVEL_NAMES,
    name_scale,
)
from get_design_markdown.models.analysis import ScaleStep, ShadowLevel
from get_design_markdown.models.raw import ElementSample, PageCapture

MIN_SHAPE_SIZE_PX = 6.0
PILL_MIN_RADIUS_PX = 16.0
RADIUS_MERGE_PX = 0.5
MAX_DISTINCT_BEFORE_PRUNE = 6
#: With more than this many distinct radii, values used by < max(2, 2%) of the elements go.
MIN_VALUES_BEFORE_NOISE_FILTER = 4
NOISE_SHARE = 0.02
MAX_RADIUS_STEPS = len(RADIUS_STEP_NAMES)
SHADOW_MERGE_SHARE = 0.10
MAX_SHADOW_LEVELS = len(SHADOW_LEVEL_NAMES)
#: With more shadow levels than this, levels used by a single element are dropped.
MAX_LEVELS_BEFORE_ONE_OFF_FILTER = 4
MAX_USED_ON = 3

_MEDIA_TAGS = frozenset({"img", "video", "picture", "svg", "canvas", "iframe"})
_FORM_TAGS = frozenset({"input", "select", "textarea"})
_BUTTON_INPUT_TYPES = frozenset({"button", "submit", "reset"})


@dataclass
class _Usage:
    weight: float = 0.0
    count: int = 0
    selectors: Counter[str] = field(default_factory=Counter)

    def add(self, weight: float, selector: str) -> None:
        self.weight += weight
        self.count += 1
        self.selectors[selector] += 1

    def absorb(self, other: _Usage) -> None:
        self.weight += other.weight
        self.count += other.count
        self.selectors.update(other.selectors)

    def used_on(self, limit: int = MAX_USED_ON) -> list[str]:
        return [selector for selector, _ in self.selectors.most_common(limit)]


# ─── Radii ───────────────────────────────────────────────────────────────────


def _corner_px(value: str, short_side: float) -> tuple[float, bool]:
    """(radius px, is percentage >= 50%) of one computed corner ("8px", "50%", "8px 4px")."""
    first = value.strip().split()[0] if value.strip() else ""
    if first.endswith("%"):
        percent = parse_number(first[:-1])
        if percent is None:
            return 0.0, False
        return percent / 100.0 * short_side, percent >= 50
    return parse_px(first) or 0.0, False


def element_radius(element: ElementSample) -> tuple[float, bool] | None:
    """(largest corner px, is pill/circle) of an element, None when it has no radius."""
    short_side = min(element.box.width, element.box.height)
    largest = 0.0
    full = False
    for corner in element.styles.border_radius:
        radius, half = _corner_px(corner, short_side)
        if radius > largest:
            largest = radius
        full = full or half
    if largest <= 0:
        return None
    if largest >= PILL_RADIUS_PX:
        return largest, True
    if full or (largest >= short_side / 2 - 0.5 and largest >= PILL_MIN_RADIUS_PX):
        return largest, True
    return largest, False


def shows_shape(element: ElementSample) -> bool:
    """True when a border-radius is visible: painted, clipping, media or interactive."""
    styles = element.styles
    return (
        own_background(element) is not None
        or has_visible_border(element)
        or bool(visible_shadow_layers(styles.box_shadow))
        or styles.background_image.strip().lower() not in ("", "none")
        or element.tag in _MEDIA_TAGS
        or styles.overflow_x in ("hidden", "clip")
        or is_interactive(element)
    )


def _is_button_like(element: ElementSample) -> bool:
    if element.tag == "button" or (element.role or "") == "button":
        return True
    if element.tag == "input":
        return (element.input_type or "").lower() in _BUTTON_INPUT_TYPES
    if element.tag in _FORM_TAGS or not is_interactive(element):
        return False
    return own_background(element) is not None or has_visible_border(element)


def _radius_refs(pages: Sequence[PageCapture]) -> list[tuple[ElementRef, float, bool]]:
    refs: list[tuple[ElementRef, float, bool]] = []
    for ref in iter_design_elements(pages):
        element = ref.element
        if min(element.box.width, element.box.height) < MIN_SHAPE_SIZE_PX:
            continue
        measured = element_radius(element)
        if measured is None or not shows_shape(element):
            continue
        radius, full = measured
        refs.append((ref, round(radius * 2.0) / 2.0, full))
    return refs


def analyze_radii(pages: Sequence[PageCapture]) -> list[ScaleStep]:
    """Radius steps ascending, named from naming.RADIUS_STEP_NAMES (+ "full" for pills)."""
    usages: dict[float, _Usage] = {}
    full = _Usage()
    button_radii: dict[float, float] = {}
    for ref, radius, is_full in _radius_refs(pages):
        selector = short_selector(ref.element)
        if is_full:
            full.add(ref.weight, selector)
            continue
        usages.setdefault(radius, _Usage()).add(ref.weight, selector)
        if _is_button_like(ref.element):
            button_radii[radius] = button_radii.get(radius, 0.0) + ref.weight

    # Merge values within 0.5px into the more used one.
    merged: dict[float, _Usage] = {}
    for radius in sorted(usages, key=lambda value: (-usages[value].weight, value)):
        target = next((kept for kept in merged if abs(kept - radius) <= RADIUS_MERGE_PX), None)
        if target is None:
            merged[radius] = _Usage()
            merged[radius].absorb(usages[radius])
        else:
            merged[target].absorb(usages[radius])
    anchor = _anchor_radius(merged, button_radii)
    if len(merged) > MIN_VALUES_BEFORE_NOISE_FILTER:
        # Rare values (stray 2px/3px corners) are noise once a scale clearly exists.
        total = sum(usage.count for usage in merged.values())
        floor = max(2.0, NOISE_SHARE * total)
        pruned = {r: u for r, u in merged.items() if u.count >= floor or r == anchor}
        merged = pruned or merged
    if len(merged) > MAX_DISTINCT_BEFORE_PRUNE:
        pruned = {r: u for r, u in merged.items() if u.count >= 2 or r == anchor}
        merged = pruned or merged
    if len(merged) > MAX_RADIUS_STEPS:
        ranked = sorted(merged, key=lambda r: (r != anchor, -merged[r].weight, r))
        merged = {r: merged[r] for r in ranked[:MAX_RADIUS_STEPS]}

    values = sorted(merged)
    names = name_scale(values, RADIUS_STEP_NAMES, anchor_value=anchor)
    steps = [
        ScaleStep(
            name=name,
            value_px=value,
            count=merged[value].count,
            used_on=merged[value].used_on(),
        )
        for name, value in zip(names, values, strict=True)
    ]
    if full.count:
        steps.append(
            ScaleStep(
                name=FULL_RADIUS_NAME,
                value_px=FULL_RADIUS_PX,
                count=full.count,
                used_on=full.used_on(),
            )
        )
    return steps


def _anchor_radius(merged: dict[float, _Usage], button_radii: dict[float, float]) -> float | None:
    if not merged:
        return None
    candidates: dict[float, float] = {}
    for radius, weight in button_radii.items():
        target = min(merged, key=lambda kept: abs(kept - radius))
        if abs(target - radius) <= RADIUS_MERGE_PX:
            candidates[target] = candidates.get(target, 0.0) + weight
    source = candidates or {radius: usage.weight for radius, usage in merged.items()}
    return max(sorted(source), key=lambda radius: source[radius])


# ─── Shadows ─────────────────────────────────────────────────────────────────


def _is_ring(layer: ShadowLayer) -> bool:
    return layer.offset_x == 0 and layer.offset_y == 0 and layer.blur == 0


def elevation_score(layers: Sequence[ShadowLayer]) -> float:
    """max over lifting layers of (offset_y + blur / 2 + spread) x alpha (0 when none lifts)."""
    score = 0.0
    for layer in layers:
        if layer.inset or _is_ring(layer):
            continue
        alpha = 1.0 if layer.color is None else layer.color.alpha
        lift = layer.offset_y + layer.blur / 2.0 + layer.spread
        score = max(score, max(lift, 0.0) * alpha)
    return score


@dataclass
class _ShadowGroup:
    css: str
    layers: int
    score: float
    usage: _Usage = field(default_factory=_Usage)


def analyze_shadows(pages: Sequence[PageCapture]) -> list[ShadowLevel]:
    """Shadow levels from subtlest to strongest, named from naming.SHADOW_LEVEL_NAMES."""
    groups: dict[str, _ShadowGroup] = {}
    for ref in iter_design_elements(pages):
        layers = visible_shadow_layers(ref.element.styles.box_shadow)
        if not layers:
            continue
        score = elevation_score(layers)
        if score <= 0:
            continue
        css = format_box_shadow(layers)
        group = groups.get(css)
        if group is None:
            group = groups[css] = _ShadowGroup(css, len(layers), score)
        group.usage.add(ref.weight, short_selector(ref.element))

    kept: list[_ShadowGroup] = []
    for group in sorted(groups.values(), key=lambda g: (-g.usage.weight, g.score, g.css)):
        target = next(
            (
                other
                for other in kept
                if abs(other.score - group.score)
                < SHADOW_MERGE_SHARE * max(other.score, group.score)
            ),
            None,
        )
        if target is None:
            kept.append(group)
        else:
            target.usage.absorb(group.usage)
    if len(kept) > MAX_LEVELS_BEFORE_ONE_OFF_FILTER:
        # Many levels: shadows used by a single element (a lone popover, an image) are noise.
        reused = [group for group in kept if group.usage.count >= 2]
        kept = reused or kept
    while len(kept) > MAX_SHADOW_LEVELS:
        kept.remove(min(kept, key=lambda g: (g.usage.weight, g.usage.count, -g.score)))
    kept.sort(key=lambda g: (g.score, g.css))
    count = len(kept)
    names = SHADOW_LEVEL_NAMES[1 : 1 + count] if count <= 5 else SHADOW_LEVEL_NAMES[:count]
    return [
        ShadowLevel(
            name=name,
            css=group.css,
            layers=group.layers,
            count=group.usage.count,
            used_on=group.usage.used_on(),
        )
        for name, group in zip(names, kept, strict=True)
    ]
