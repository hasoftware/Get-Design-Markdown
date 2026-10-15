"""Typography: font roles, semantic type levels, scale ratio
(stream: analyzer-foundations, phase GĐ4).

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_typography(pages, stylesheets) -> Typography

How it works (pure, deterministic):
1. Samples: every rendered, non-overlay element that renders its own text (has_own_text),
   weighted by min(len(text), 50) x page weight. Screen-reader-only boxes, icon/emoji fonts and
   decorative subtrees (DOM-built mockups and graphics, see spacing.decorative_indexes()) are
   skipped. A stack that starts with a system alias (system-ui, -apple-system...) is the
   "system-ui" family; otherwise the first non-generic, non-icon family of the stack. Inline
   wrappers take the tag of their heading/link/button/paragraph ancestor (semantic_tag()), so
   the spans that hold an h1's text count as h1.
2. Families: canonical display names come from fonts.lookup_font(); roles are primary
   (largest non-monospace share), heading (dominant family of h1-h3 / text >= 28px when it
   differs from primary), display (family of the largest text >= 48px when it differs from both)
   and mono (heaviest monospace family). Proprietary fonts list the free Google fonts of their
   own stack first among the substitutes.
3. Levels: samples grouped by (size rounded to 0.5px, family); groups of the same family less
   than 1px apart merge into the heavier one; one-off groups are dropped unless they hold an h1/h2,
   are >= 32px or monospace; at most 10 groups survive: the least prominent (usage x size^2, so a
   single hero headline outweighs many small labels; h1/h2 and >= 32px groups last) merge into
   the nearest size of the same family.
4. Names come from naming.TYPE_LEVEL_ORDER. "body" is the heaviest 14-18px running-text group.
   A rare (<= 2 elements) headline at most 25% larger than the h1 joins the h1 level, so
   "display" is the hero headline rather than a one-off call-to-action. Larger groups (<= 5,
   the least prominent merged first, never the h1) get display / heading / title / subtitle /
   body-lg through a monotonic assignment (a larger level never gets a later name) that
   minimizes the usage-weighted cost of departing from each group's preferred name (h1 ->
   display, h2 or >= 28px -> heading, h3/h4 or 20-28px semibold -> title, semibold -> subtitle,
   regular -> body-lg). Smaller groups get code (monospace), ui (interactive-dominated, weight
   >= 500), overline (uppercase, tracking >= 0.5px), then body-sm for the largest remaining and
   caption for the smallest; groups left without a sensible name merge into the nearest named
   level of the same family.
5. Scale ratio: the distinct non-code level sizes >= the base size are fitted to
   base x r^k for the classic ratios. The fit error is measured in scale steps (log distance to
   the nearest step / log r), so fine ratios cannot win just by having a denser grid. A ratio is
   claimed only when at least three sizes above the base sit on distinct steps, the mean step
   error is <= 0.15 and the mean relative size error < 6%; otherwise None.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from itertools import combinations

from get_design_markdown.analyzer.common import HEADING_TAGS, ElementRef, ancestors, element_index
from get_design_markdown.analyzer.css_values import (
    NORMAL_LINE_HEIGHT,
    is_generic_family,
    is_monospace_stack,
    parse_font_family,
    parse_font_weight,
    parse_letter_spacing,
    parse_line_height,
    parse_px,
    primary_font_family,
)
from get_design_markdown.analyzer.fonts import (
    font_key,
    generic_substitutes,
    is_icon_font,
    is_known_font,
    lookup_font,
)
from get_design_markdown.analyzer.naming import TYPE_LEVEL_ORDER
from get_design_markdown.analyzer.spacing import iter_design_elements
from get_design_markdown.models.analysis import (
    FontCategory,
    FontFamilyInfo,
    FontRole,
    TypeLevel,
    Typography,
)
from get_design_markdown.models.raw import ElementSample, PageCapture, StylesheetInfo

MAX_TEXT_WEIGHT = 50
MAX_LEVELS = 10
MAX_TAGS = 4
MAX_SAMPLES = 3
MIN_FONT_SIZE_PX = 6.0
#: Weights used by less than this share of the text are ignored in weights_used.
MIN_WEIGHT_SHARE = 0.01
#: Weights used by less than this share of a family's own text are left out of its weights.
FAMILY_WEIGHT_MIN_SHARE = 0.02
DEFAULT_BASE_SIZE_PX = 16.0

SCALE_RATIOS: tuple[tuple[float, str], ...] = (
    (1.067, "minor second"),
    (1.125, "major second"),
    (1.2, "minor third"),
    (1.25, "major third"),
    (1.333, "perfect fourth"),
    (1.414, "augmented fourth"),
    (1.5, "perfect fifth"),
    (1.618, "golden ratio"),
)
MAX_SCALE_STEP_ERROR = 0.15
MAX_SCALE_RELATIVE_ERROR = 0.06
MIN_SCALE_SIZES = 3

_SYSTEM_ALIASES = frozenset({"system-ui", "-apple-system", "blinkmacsystemfont", "ui-sans-serif"})
_BODY_TAGS = frozenset({"p", "li", "span", "td", "dd"})
_TOP_HEADING_TAGS = frozenset({"h1", "h2", "h3"})
_INLINE_TAGS = frozenset(
    {"span", "em", "strong", "b", "i", "u", "s", "small", "sup", "sub", "mark", "abbr", "time"}
)
_SEMANTIC_TAGS = frozenset(
    {
        *HEADING_TAGS,
        "a",
        "button",
        "label",
        "summary",
        "p",
        "li",
        "td",
        "th",
        "dd",
        "dt",
        "figcaption",
        "blockquote",
        "q",
        "legend",
        "caption",
        "code",
        "pre",
    }
)
MAX_SEMANTIC_DEPTH = 3
#: A rare (<= 2 elements) group larger than the h1 by at most this ratio joins the h1 level.
DISPLAY_ABSORB_RATIO = 1.25
_INTERACTIVE_TAGS = frozenset({"a", "button", "label", "summary", "select", "option"})
_INTERACTIVE_ROLES = frozenset({"button", "link", "tab", "menuitem", "option"})
_GOOGLE_FONT_HOSTS = ("fonts.gstatic.com", "fonts.googleapis.com")
_GENERIC_CATEGORIES: dict[str, FontCategory] = {
    "serif": "serif",
    "ui-serif": "serif",
    "sans-serif": "sans-serif",
    "ui-sans-serif": "sans-serif",
    "system-ui": "sans-serif",
    "monospace": "monospace",
    "ui-monospace": "monospace",
    "cursive": "handwriting",
    "fantasy": "display",
}

_ABOVE_NAMES = ("display", "heading", "title", "subtitle", "body-lg")
_INFINITE = 1e9


# ─── Samples ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Sample:
    page_index: int
    order: int
    size: float
    family: str
    raw_family: str
    stack: tuple[str, ...]
    mono: bool
    weight: int
    line_height_px: float | None
    letter_spacing_px: float
    transform: str
    tag: str
    text: str
    interactive: bool
    uppercase: bool
    heading: bool
    value: float


def round_half(value: float) -> float:
    """Round to the nearest 0.5 (15.33 -> 15.5, 13.2 -> 13.0)."""
    return round(value * 2.0) / 2.0


def family_of_stack(stack: Sequence[str]) -> str | None:
    """Family that renders a stack: "system-ui" for system stacks, None for icon fonts,
    else the first non-generic, non-emoji family (css_values.primary_font_family)."""
    if not stack:
        return None
    first = stack[0].strip()
    if first.lower() in _SYSTEM_ALIASES:
        return "system-ui"
    if is_icon_font(first):
        return None
    candidates = [name for name in stack if not is_icon_font(name)]
    return primary_font_family(candidates)


def _is_uppercase_text(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return len(letters) >= 4 and all(char.isupper() for char in letters)


def semantic_tag(element: ElementSample, index: dict[int, ElementSample]) -> str:
    """Tag that gives a text run its role: inline wrappers (span, em, strong...) take the
    nearest heading/link/button/paragraph ancestor within three levels, so the spans inside
    <h1> count as h1 and a button label span counts as button."""
    tag = element.tag.lower()
    if tag not in _INLINE_TAGS:
        return tag
    for depth, ancestor in enumerate(ancestors(element, index)):
        if depth >= MAX_SEMANTIC_DEPTH:
            break
        ancestor_tag = ancestor.tag.lower()
        if ancestor_tag in _SEMANTIC_TAGS:
            return ancestor_tag
        if ancestor_tag not in _INLINE_TAGS and ancestor_tag != "div":
            break
    return tag


def _sample(ref: ElementRef, index: dict[int, ElementSample]) -> _Sample | None:
    element = ref.element
    if not element.has_own_text or not element.text.strip():
        return None
    if element.box.width <= 2 or element.box.height <= 2:
        return None  # visually hidden (sr-only) text
    styles = element.styles
    size = parse_px(styles.font_size, root_font_size_px=ref.page.root_font_size_px)
    if size is None or size < MIN_FONT_SIZE_PX:
        return None
    stack = tuple(parse_font_family(styles.font_family))
    raw_family = family_of_stack(stack)
    if raw_family is None:
        return None
    line_height = parse_line_height(styles.line_height, size, normal=NORMAL_LINE_HEIGHT)
    transform = styles.text_transform.strip().lower() or "none"
    text = element.text.strip()
    role = (element.role or "").lower()
    tag = semantic_tag(element, index)
    return _Sample(
        page_index=ref.page_index,
        order=element.index,
        size=size,
        family=lookup_font(raw_family).name,
        raw_family=raw_family,
        stack=stack,
        mono=is_monospace_stack(stack) or lookup_font(raw_family).category == "monospace",
        weight=parse_font_weight(styles.font_weight),
        line_height_px=None if line_height is None else round(line_height, 2),
        letter_spacing_px=round(parse_letter_spacing(styles.letter_spacing, size), 2),
        transform=transform,
        tag=tag,
        text=text,
        interactive=tag in _INTERACTIVE_TAGS
        or role in _INTERACTIVE_ROLES
        or element.landmark == "nav",
        uppercase=transform == "uppercase" or _is_uppercase_text(text),
        heading=tag in HEADING_TAGS or role == "heading",
        value=min(len(text), MAX_TEXT_WEIGHT) * ref.weight,
    )


def collect_samples(pages: Sequence[PageCapture]) -> list[_Sample]:
    samples = []
    indexes = {position: element_index(page) for position, page in enumerate(pages)}
    for ref in iter_design_elements(pages):
        sample = _sample(ref, indexes[ref.page_index])
        if sample is not None:
            samples.append(sample)
    return samples


def _weighted_mode(pairs: Iterable[tuple[object, float]]) -> object:
    totals: dict[object, float] = defaultdict(float)
    first_seen: dict[object, int] = {}
    for position, (key, value) in enumerate(pairs):
        totals[key] += value
        first_seen.setdefault(key, position)
    return max(totals, key=lambda key: (totals[key], -first_seen[key]))


# ─── Families ────────────────────────────────────────────────────────────────


def _google_face_keys(stylesheets: Sequence[StylesheetInfo]) -> set[str]:
    keys: set[str] = set()
    for sheet in stylesheets:
        for face in sheet.font_faces:
            sources = " ".join(face.src).lower()
            if any(host in sources for host in _GOOGLE_FONT_HOSTS):
                keys.add(font_key(face.family))
    return keys


def _stack_category(stack: Sequence[str]) -> FontCategory | None:
    for name in reversed(stack):
        category = _GENERIC_CATEGORIES.get(name.strip().lower())
        if category is not None:
            return category
    return None


def _family_info(
    name: str,
    role: FontRole,
    members: Sequence[_Sample],
    share: float,
    google_faces: set[str],
) -> FontFamilyInfo:
    stack = _weighted_mode((sample.stack, sample.value) for sample in members)
    assert isinstance(stack, tuple)
    raw_family = _weighted_mode((sample.raw_family, sample.value) for sample in members)
    assert isinstance(raw_family, str)
    lookup = lookup_font(raw_family)
    category: FontCategory = lookup.category
    substitutes = list(lookup.substitutes)
    if not is_known_font(raw_family):
        guessed = _stack_category(stack)
        if guessed is not None and guessed != category and lookup.category == "sans-serif":
            category = guessed
            substitutes = list(generic_substitutes(category))
    is_google = lookup.is_google_font or font_key(raw_family) in google_faces
    if is_google:
        substitutes = []
    else:
        # Free fallbacks the site itself chose ("Graphik, Inter, sans-serif") come first.
        from_stack = [
            lookup_font(entry).name
            for entry in stack
            if not is_generic_family(entry)
            and lookup_font(entry).is_google_font
            and lookup_font(entry).name != name
        ]
        substitutes = list(dict.fromkeys([*from_stack, *substitutes]))[:3]
    family_total = sum(sample.value for sample in members) or 1.0
    weight_totals: dict[int, float] = defaultdict(float)
    for sample in members:
        weight_totals[sample.weight] += sample.value
    weights = sorted(
        weight
        for weight, value in weight_totals.items()
        if value / family_total >= FAMILY_WEIGHT_MIN_SHARE or len(weight_totals) == 1
    )
    return FontFamilyInfo(
        name=name,
        stack=list(stack),
        role=role,
        category=category,
        share=round(min(max(share, 0.0), 1.0), 2),
        weights=weights,
        is_google_font=is_google,
        substitutes=substitutes,
    )


def _dominant(samples: Iterable[_Sample]) -> str | None:
    totals: dict[str, float] = defaultdict(float)
    for sample in samples:
        totals[sample.family] += sample.value
    if not totals:
        return None
    return max(sorted(totals), key=lambda family: totals[family])


def analyze_families(
    samples: Sequence[_Sample], stylesheets: Sequence[StylesheetInfo] = ()
) -> list[FontFamilyInfo]:
    """Font families with a role, ordered primary, heading, display, mono."""
    if not samples:
        return []
    by_family: dict[str, list[_Sample]] = defaultdict(list)
    for sample in samples:
        by_family[sample.family].append(sample)
    totals = {family: sum(s.value for s in members) for family, members in by_family.items()}
    grand_total = sum(totals.values()) or 1.0
    mono = {
        family
        for family, members in by_family.items()
        if sum(s.value for s in members if s.mono) * 2 > totals[family]
    }
    ordered = sorted(totals, key=lambda family: (-totals[family], family))
    non_mono = [family for family in ordered if family not in mono]
    primary = non_mono[0] if non_mono else ordered[0]
    roles: list[tuple[str, FontRole]] = [(primary, FontRole.PRIMARY)]

    heading = _dominant(s for s in samples if s.tag in _TOP_HEADING_TAGS or s.size >= 28)
    if heading is not None and heading != primary and heading not in mono:
        roles.append((heading, FontRole.HEADING))
    large = [s for s in samples if s.size >= 48]
    if large:
        top_size = max(s.size for s in large)
        display = _dominant(s for s in large if s.size == top_size)
        if display is not None and display not in (primary, heading) and display not in mono:
            roles.append((display, FontRole.DISPLAY))
    mono_families = [family for family in ordered if family in mono and family != primary]
    if mono_families:
        roles.append((mono_families[0], FontRole.MONO))

    google_faces = _google_face_keys(stylesheets)
    return [
        _family_info(family, role, by_family[family], totals[family] / grand_total, google_faces)
        for family, role in roles
    ]


# ─── Level groups ────────────────────────────────────────────────────────────


@dataclass(eq=False)
class _Group:
    size: float
    family: str
    members: list[_Sample] = field(default_factory=list)

    @property
    def value(self) -> float:
        return sum(sample.value for sample in self.members)

    @property
    def count(self) -> int:
        return len(self.members)

    @property
    def mono(self) -> bool:
        return sum(s.value for s in self.members if s.mono) * 2 > self.value

    @property
    def weight(self) -> int:
        mode = _weighted_mode((s.weight, s.value) for s in self.members)
        assert isinstance(mode, int)
        return mode

    def tag_values(self) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for sample in self.members:
            totals[sample.tag] += sample.value
        return totals

    @property
    def tags(self) -> list[str]:
        totals = self.tag_values()
        first = {sample.tag: sample.order for sample in reversed(self.members)}
        return sorted(totals, key=lambda tag: (-totals[tag], first[tag]))

    def share(self, predicate_attr: str) -> float:
        total = self.value or 1.0
        return sum(s.value for s in self.members if getattr(s, predicate_attr)) / total

    @property
    def prominence(self) -> float:
        """Usage weighted by visual size: one 48px headline outweighs a dozen 10px labels."""
        return self.value * (self.size / 16.0) ** 2

    @property
    def protected(self) -> bool:
        """Display/heading text that is never merged away for being rare."""
        return self.size >= 32 or any(tag in ("h1", "h2") for tag in self.tag_values())

    @property
    def letter_spacing_px(self) -> float:
        mode = _weighted_mode((s.letter_spacing_px, s.value) for s in self.members)
        assert isinstance(mode, float)
        return mode

    def absorb(self, other: _Group) -> None:
        self.members.extend(other.members)


def _group_samples(samples: Sequence[_Sample]) -> list[_Group]:
    groups: dict[tuple[float, str], _Group] = {}
    for sample in samples:
        key = (round_half(sample.size), sample.family)
        group = groups.get(key)
        if group is None:
            group = groups[key] = _Group(key[0], key[1])
        group.members.append(sample)
    # Sizes of the same family less than 1px apart merge into the heavier group.
    kept: list[_Group] = []
    for group in sorted(groups.values(), key=lambda g: (-g.value, -g.size)):
        target = next(
            (
                other
                for other in kept
                if other.family == group.family and abs(other.size - group.size) < 1.0
            ),
            None,
        )
        if target is None:
            kept.append(group)
        else:
            target.absorb(group)
    return kept


def _keep_group(group: _Group) -> bool:
    if group.count >= 2 or group.size >= 32 or group.mono:
        return True
    return any(tag in ("h1", "h2") for tag in group.tag_values())


def _nearest_same_family(group: _Group, others: Iterable[_Group]) -> _Group | None:
    candidates = [other for other in others if other is not group and other.family == group.family]
    if not candidates:
        return None
    return min(candidates, key=lambda other: (abs(other.size - group.size), -other.value))


def _limit_groups(groups: list[_Group], limit: int) -> list[_Group]:
    groups = list(groups)
    while len(groups) > limit:
        weakest = min(groups, key=lambda g: (g.protected, g.prominence, g.count, -g.size))
        target = _nearest_same_family(weakest, groups)
        groups.remove(weakest)
        if target is not None:
            target.absorb(weakest)
    return groups


# ─── Level names ─────────────────────────────────────────────────────────────


def _pick_body(groups: Sequence[_Group]) -> _Group | None:
    text_groups = [g for g in groups if not g.mono] or list(groups)
    stages: tuple[Callable[[_Group], bool], ...] = (
        lambda g: 14 <= g.size <= 18 and any(tag in _BODY_TAGS for tag in g.tag_values()),
        lambda g: 14 <= g.size <= 18,
        lambda g: 12 <= g.size <= 20,
        lambda g: True,
    )
    for stage in stages:
        candidates = [g for g in text_groups if stage(g)]
        if candidates:
            return max(candidates, key=lambda g: (g.value, g.count, g.size))
    return None


def _above_costs(group: _Group) -> tuple[float, ...]:
    tags = group.tag_values()
    size = group.size
    heavy = group.weight >= 600
    big = size >= 28
    display = 0.0 if size >= 40 or "h1" in tags else _INFINITE
    heading = 0.0 if "h2" in tags or big else (1.0 if "h1" in tags else 3.0)
    if "h3" in tags or "h4" in tags or (20 <= size < 28 and heavy):
        title = 0.0
    else:
        title = 1.0 if big or heavy else 2.0
    subtitle = 0.0 if heavy else 2.0
    body_lg = (0.0 if not heavy else 3.0) + (2.0 if big else 0.0)
    return (display, heading, title, subtitle, body_lg)


def _name_above(groups: Sequence[_Group]) -> dict[int, str]:
    """Monotonic names for the groups larger than body (sorted largest first, <= 5)."""
    if not groups:
        return {}
    total = sum(g.value for g in groups) or 1.0
    importance = [0.5 + g.value / total for g in groups]
    costs = [_above_costs(g) for g in groups]
    best: tuple[float, tuple[int, ...]] | None = None
    for slots in combinations(range(len(_ABOVE_NAMES)), len(groups)):
        cost = sum(importance[i] * costs[i][slot] for i, slot in enumerate(slots))
        if best is None or cost < best[0] - 1e-9:
            best = (cost, slots)
    assert best is not None
    return {id(group): _ABOVE_NAMES[slot] for group, slot in zip(groups, best[1], strict=True)}


def _merge_into_named(
    leftovers: Iterable[_Group], named: Sequence[_Group], all_groups: list[_Group]
) -> None:
    for group in leftovers:
        target = _nearest_same_family(group, named)
        all_groups.remove(group)
        if target is not None:
            target.absorb(group)


def _absorb_into_h1(above: list[_Group], groups: list[_Group]) -> None:
    """One-off headlines slightly larger than the page's h1 (a closing call-to-action, a big
    stat) join the h1 level, so "display" names the hero headline."""
    h1_groups = [g for g in above if g.tag_values().get("h1", 0.0) > 0]
    if not h1_groups:
        return
    h1 = max(h1_groups, key=lambda g: g.tag_values()["h1"])
    for group in [g for g in above if g.size > h1.size]:
        if (
            group.family == h1.family
            and group.count <= 2
            and "h1" not in group.tag_values()
            and group.size <= h1.size * DISPLAY_ABSORB_RATIO
        ):
            h1.absorb(group)
            above.remove(group)
            groups.remove(group)


def _name_groups(groups: list[_Group], primary_is_mono: bool) -> list[tuple[str, _Group]]:
    body = _pick_body(groups)
    if body is None:
        return []
    names: dict[int, str] = {id(body): "body"}

    def is_code(group: _Group) -> bool:
        return (
            group is not body and not primary_is_mono and group.mono and group.size <= body.size + 2
        )

    code = sorted((g for g in groups if is_code(g)), key=lambda g: (-g.value, -g.size))
    if code:
        names[id(code[0])] = "code"
        _merge_into_named(code[1:], code[:1], groups)

    rest = [g for g in groups if id(g) not in names]
    above = [
        g
        for g in rest
        if g.size > body.size
        or (g.size == body.size and (g.weight >= 600 or any(s.heading for s in g.members)))
    ]
    below = [g for g in rest if g not in above]

    above.sort(key=lambda g: -g.size)
    _absorb_into_h1(above, groups)
    while len(above) > len(_ABOVE_NAMES):
        weakest = min(
            above, key=lambda g: ("h1" in g.tag_values(), g.protected, g.prominence, g.count)
        )
        target = _nearest_same_family(weakest, above)
        above.remove(weakest)
        groups.remove(weakest)
        if target is not None:
            target.absorb(weakest)
    names.update(_name_above(above))

    below.sort(key=lambda g: -g.size)
    ui = [g for g in below if g.share("interactive") > 0.5 and g.weight >= 500]
    if ui:
        chosen = max(ui, key=lambda g: (g.value, g.size))
        names[id(chosen)] = "ui"
        below.remove(chosen)
    overline = [g for g in below if g.share("uppercase") > 0.5 and g.letter_spacing_px >= 0.5]
    if overline:
        chosen = max(overline, key=lambda g: (g.value, g.size))
        names[id(chosen)] = "overline"
        below.remove(chosen)
    if len(below) == 1:
        names[id(below[0])] = "body-sm" if below[0].size >= 13.5 else "caption"
    elif len(below) >= 2:
        names[id(below[0])] = "body-sm"
        names[id(below[-1])] = "caption"
        named = [g for g in groups if id(g) in names]
        _merge_into_named(below[1:-1], named, groups)

    return [(names[id(g)], g) for g in groups if id(g) in names]


def _level(name: str, group: _Group) -> TypeLevel:
    members = group.members
    size_mode = _weighted_mode((s.size, s.value) for s in members)
    assert isinstance(size_mode, float)
    size = round(size_mode, 2)
    heights = [(s.line_height_px, s.value) for s in members if s.line_height_px is not None]
    line_height_px: float | None = None
    if heights:
        mode = _weighted_mode(heights)
        assert isinstance(mode, float)
        line_height_px = round(mode, 2)
    transform = _weighted_mode((s.transform, s.value) for s in members)
    assert isinstance(transform, str)
    samples: list[str] = []
    for sample in sorted(
        members, key=lambda s: (s.page_index, 0 if len(s.text) >= 4 else 1, s.order)
    ):
        if sample.text not in samples:
            samples.append(sample.text)
        if len(samples) == MAX_SAMPLES:
            break
    return TypeLevel(
        name=name,
        size_px=size,
        font_family=group.family,
        weight=group.weight,
        line_height_px=line_height_px,
        line_height=None if line_height_px is None else round(line_height_px / size, 2),
        letter_spacing_px=group.letter_spacing_px,
        text_transform=transform,
        tags=group.tags[:MAX_TAGS],
        count=group.count,
        samples=samples,
    )


def analyze_levels(samples: Sequence[_Sample], *, primary_is_mono: bool = False) -> list[TypeLevel]:
    """Semantic type levels, largest first."""
    groups = [g for g in _group_samples(samples) if _keep_group(g)]
    groups = _limit_groups(groups, MAX_LEVELS)
    levels = [_level(name, group) for name, group in _name_groups(groups, primary_is_mono)]
    order = {name: position for position, name in enumerate(TYPE_LEVEL_ORDER)}
    levels.sort(key=lambda level: (-level.size_px, order.get(level.name, len(order))))
    return levels


# ─── Scale ratio ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ScaleFit:
    ratio: float
    name: str
    step_error: float
    relative_error: float


def fit_scale_ratio(sizes: Iterable[float], base: float) -> ScaleFit | None:
    """Best classic modular ratio for sizes above `base`, or None when none fits."""
    if base <= 0:
        return None
    values = sorted({round(size, 2) for size in sizes if size > base + 0.25})
    if len(values) < MIN_SCALE_SIZES:
        return None
    best: ScaleFit | None = None
    for ratio, name in SCALE_RATIOS:
        log_ratio = math.log(ratio)
        steps: list[int] = []
        step_errors: list[float] = []
        relative_errors: list[float] = []
        for value in values:
            exact = math.log(value / base) / log_ratio
            step = max(round(exact), 0)
            steps.append(step)
            step_errors.append(abs(exact - step))
            relative_errors.append(abs(value - base * ratio**step) / value)
        if len(set(steps)) < len(steps):
            continue  # two sizes on the same step: not this scale
        fit = ScaleFit(
            ratio,
            name,
            sum(step_errors) / len(step_errors),
            sum(relative_errors) / len(relative_errors),
        )
        if best is None or fit.step_error < best.step_error - 0.005:
            best = fit
        elif abs(fit.step_error - best.step_error) <= 0.005:
            best = fit  # near tie: the larger ratio (iterated later) is the simpler story
    if best is None:
        return None
    if best.step_error > MAX_SCALE_STEP_ERROR or best.relative_error >= MAX_SCALE_RELATIVE_ERROR:
        return None
    return best


# ─── Entry point ─────────────────────────────────────────────────────────────


def _weights_used(samples: Sequence[_Sample]) -> list[int]:
    total = sum(sample.value for sample in samples)
    if total <= 0:
        return []
    totals: dict[int, float] = defaultdict(float)
    for sample in samples:
        totals[sample.weight] += sample.value
    return sorted(weight for weight, value in totals.items() if value / total >= MIN_WEIGHT_SHARE)


def analyze_typography(
    pages: Sequence[PageCapture], stylesheets: Sequence[StylesheetInfo] = ()
) -> Typography:
    """Families (primary/heading/mono/display + Google-font lookup), levels largest-first
    named from naming.TYPE_LEVEL_ORDER, weights used, base size and scale ratio (pure)."""
    samples = collect_samples(pages)
    if not samples:
        return Typography()
    families = analyze_families(samples, stylesheets)
    primary = families[0] if families else None
    primary_is_mono = primary is not None and primary.category == "monospace"
    levels = analyze_levels(samples, primary_is_mono=primary_is_mono)
    body = next((level for level in levels if level.name == "body"), None)
    base = body.size_px if body is not None else DEFAULT_BASE_SIZE_PX
    fit = fit_scale_ratio((level.size_px for level in levels if level.name != "code"), base)
    return Typography(
        families=families,
        levels=levels,
        weights_used=_weights_used(samples),
        base_size_px=base,
        scale_ratio=None if fit is None else fit.ratio,
        scale_ratio_name=None if fit is None else fit.name,
    )
