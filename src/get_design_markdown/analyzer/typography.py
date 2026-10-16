"""Typography: font roles, semantic type levels, scale ratio.

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_typography(pages, stylesheets) -> Typography

How it works (pure, deterministic):
1. Samples: every rendered, non-overlay element that renders its own text (has_own_text),
   weighted by min(len(text), 50) x page weight. Screen-reader-only boxes, icon/emoji fonts,
   decorative subtrees (DOM-built mockups and graphics, see common.decorative_indexes()) and
   animated illustrations (shapes.animation_indexes()) are skipped. A stack that starts with a
   system alias (system-ui, -apple-system...) is the "system-ui" family; otherwise the first
   non-generic, non-icon family of the stack. Inline wrappers take the tag of their
   heading/link/button/paragraph ancestor (semantic_tag()), so the spans that hold an h1's text
   count as h1.
2. Families: canonical display names come from fonts.lookup_font(); roles are primary
   (largest non-monospace share), heading (dominant family of h1-h3 / text >= 28px when it
   differs from primary), display (family of the largest text >= 48px when it differs from both)
   and mono (heaviest monospace family). Proprietary fonts list the free Google fonts of their
   own stack first among the substitutes. A family's weights follow the weights_used rule
   (family_weights()): >= 2% of its text or >= 1% of all text, its levels' weights and declared
   font-weight tokens three of its elements use, so the two lists never disagree.
3. Levels: samples grouped by (size rounded to 0.5px, family); groups of the same family less
   than 1px apart merge into the heavier one; one-off groups are dropped unless they hold an h1/h2,
   are >= 32px or monospace. Whenever groups must merge (at most 10 levels overall, 5 above
   body, body-sm/caption below it), the cheapest merge goes first: usage x log distance to the
   nearest size of the same family, where sub-page text counts half again (the main page leads)
   and groups holding >= 8% of the text merge last. The body group, the h1 group and the group
   of the main page's largest heading never merge away. Merged groups only add to a level's
   count: its size, weight, line height, letter spacing, tags and samples come from the text
   measured at the level's own size, and samples prefer text in the level's weight (then line
   height), so a 16px/400 button label is no sample of a 16px/300 body level.
4. Names come from naming.TYPE_LEVEL_ORDER. "body" is the heaviest 14-18px running-text group.
   A rare (<= 2 elements) text at most 25% larger than the h1 joins the h1 level (a big stat, a
   sub-page headline) unless it is the main page's largest heading. Larger groups (<= 5)
   get display / heading / title / subtitle / body-lg through a monotonic assignment (a larger
   level never gets a later name) that minimizes the usage-weighted cost of departing from each
   group's preferred name (h1 -> display, h2 or >= 28px -> heading, h3/h4 or 20-28px semibold ->
   title, semibold -> subtitle, regular -> body-lg). Smaller groups get code (monospace), ui
   (interactive-dominated, weight >= 500; or, when three or more small groups remain, the
   heaviest interactive-dominated one), overline (uppercase, tracking >= 0.5px), then body-sm
   for the largest remaining and caption for the smallest, after the cheapest extra groups
   merged into their neighbours.
5. Scale ratio: the distinct non-code level sizes >= the base size are fitted to
   base x r^k for the classic ratios. The fit error is measured in scale steps (log distance to
   the nearest step / log r), so fine ratios cannot win just by having a denser grid. A ratio is
   claimed only when at least three sizes above the base sit on distinct steps, the mean step
   error is <= 0.15 and the mean relative size error < 6%; otherwise None.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from itertools import combinations

from get_design_markdown.analyzer.common import (
    HEADING_TAGS,
    ElementRef,
    ancestors,
    element_index,
)
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
from get_design_markdown.analyzer.shapes import iter_foundation_elements
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
#: Weights used by less than this share of the text are ignored in weights_used...
MIN_WEIGHT_SHARE = 0.01
#: ...unless a font-weight custom property declares them and this many text elements use them.
MIN_TOKEN_WEIGHT_ELEMENTS = 3
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
_BELOW_NAMES = ("body-sm", "caption")
_INFINITE = 1e9
#: A group holding at least this share of the text merges only after every smaller one.
KEEP_SHARE = 0.08
#: Sub-page text counts this much again when ranking merges, so the main page leads.
SUB_PAGE_RANK = 0.5


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
    for ref in iter_foundation_elements(pages):
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
    return FontFamilyInfo(
        name=name,
        stack=list(stack),
        role=role,
        category=category,
        share=round(min(max(share, 0.0), 1.0), 2),
        weights=family_weights(members),
        is_google_font=is_google,
        substitutes=substitutes,
    )


def family_weights(
    members: Sequence[_Sample],
    *,
    total: float | None = None,
    level_weights: Iterable[int] = (),
    declared: Iterable[int] = (),
) -> list[int]:
    """Weights of one family, by the rule of weights_used (see _weights_used()): weights
    carrying >= 2% of the family's text or >= 1% of all text (`total`), the weights of the
    family's reported levels, and declared font-weight tokens at least three of its text
    elements use. Linear's Inter lists its 590 semibold accent like the page summary does."""
    family_total = sum(sample.value for sample in members) or 1.0
    values: dict[int, float] = defaultdict(float)
    counts: dict[int, int] = defaultdict(int)
    for sample in members:
        values[sample.weight] += sample.value
        counts[sample.weight] += 1
    weights = {
        weight
        for weight, value in values.items()
        if value / family_total >= FAMILY_WEIGHT_MIN_SHARE
        or (total is not None and total > 0 and value / total >= MIN_WEIGHT_SHARE)
        or len(values) == 1
    }
    weights.update(weight for weight in level_weights if weight in values)
    weights.update(weight for weight in declared if counts[weight] >= MIN_TOKEN_WEIGHT_ELEMENTS)
    return sorted(weights)


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
    #: Every sample mapped to the level, merged groups included (count, tags, usage).
    members: list[_Sample] = field(default_factory=list)
    #: Samples measured at the group's own size (within 1px): the level's style and samples.
    core: list[_Sample] = field(default_factory=list)

    def add(self, sample: _Sample) -> None:
        self.members.append(sample)
        self.core.append(sample)

    @property
    def value(self) -> float:
        return sum(sample.value for sample in self.members)

    @property
    def rank_value(self) -> float:
        """Usage for merge decisions: sub-page text counts SUB_PAGE_RANK again."""
        return sum(s.value * (1.0 if s.page_index == 0 else SUB_PAGE_RANK) for s in self.members)

    @property
    def count(self) -> int:
        return len(self.members)

    @property
    def mono(self) -> bool:
        return sum(s.value for s in self.members if s.mono) * 2 > self.value

    @property
    def weight(self) -> int:
        mode = _weighted_mode((s.weight, s.value) for s in self.core or self.members)
        assert isinstance(mode, int)
        return mode

    def tag_values(self) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for sample in self.members:
            totals[sample.tag] += sample.value
        return totals

    @property
    def tags(self) -> list[str]:
        """Tags of the text measured at the level's own size, by usage. Merged groups only add
        to the level's count: their tags are set at another size (a 72px h2 folded into a
        64px h1 level must not read "h2 is set at 64px")."""
        core = self.core or self.members
        own: dict[str, float] = defaultdict(float)
        for sample in core:
            own[sample.tag] += sample.value
        first = {sample.tag: sample.order for sample in reversed(core)}
        return sorted(own, key=lambda tag: (-own[tag], first[tag]))

    def share(self, predicate_attr: str) -> float:
        total = self.value or 1.0
        return sum(s.value for s in self.members if getattr(s, predicate_attr)) / total

    @property
    def letter_spacing_px(self) -> float:
        mode = _weighted_mode((s.letter_spacing_px, s.value) for s in self.core or self.members)
        assert isinstance(mode, float)
        return mode

    def absorb(self, other: _Group) -> None:
        """Map another group's text to this level without changing the level's style."""
        self.members.extend(other.members)


def _group_samples(samples: Sequence[_Sample]) -> list[_Group]:
    groups: dict[tuple[float, str], _Group] = {}
    for sample in samples:
        key = (round_half(sample.size), sample.family)
        group = groups.get(key)
        if group is None:
            group = groups[key] = _Group(key[0], key[1])
        group.add(sample)
    # Sizes of the same family less than 1px apart merge into the heavier group (same level).
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
            for sample in group.members:
                target.add(sample)
    return kept


def _keep_group(group: _Group) -> bool:
    if group.count >= 2 or group.size >= 32 or group.mono:
        return True
    return any(tag in ("h1", "h2") for tag in group.tag_values())


def _nearest_same_family(group: _Group, others: Iterable[_Group]) -> _Group | None:
    """Closest size (log distance) of the same family; the heavier group wins ties."""
    candidates = [other for other in others if other is not group and other.family == group.family]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda other: (abs(math.log(other.size / group.size)), -other.value, -other.size),
    )


def _merge_cheapest(
    pool: list[_Group],
    limit: int,
    groups: list[_Group],
    *,
    total: float,
    targets: Sequence[_Group] = (),
    fixed: Sequence[_Group] = (),
) -> None:
    """Merge groups of `pool` into their nearest same-family neighbour (in the pool or in
    `targets`) until `limit` remain, cheapest first. Cost = main-page-weighted usage x log size
    distance, so a rare size next to another goes before a well-used or distinct one; groups
    with >= KEEP_SHARE of the text go last, `fixed` groups never, and a group without a
    same-family neighbour only when nothing else can merge."""
    while len(pool) > limit:
        best: tuple[tuple[bool, bool, float, float, float], _Group, _Group | None] | None = None
        for group in pool:
            if any(group is other for other in fixed):
                continue
            target = _nearest_same_family(group, [*pool, *targets])
            distance = 0.0 if target is None else abs(math.log(target.size / group.size))
            key = (
                group.value >= KEEP_SHARE * total,
                target is None,
                group.rank_value * distance,
                group.rank_value,
                -group.size,
            )
            if best is None or key < best[0]:
                best = (key, group, target)
        if best is None:
            return
        _, group, target = best
        pool.remove(group)
        if group in groups:
            groups.remove(group)
        if target is not None:
            target.absorb(group)


def _h1_group(groups: Sequence[_Group]) -> _Group | None:
    """The group holding most of the h1 text (main page first)."""
    best: tuple[float, _Group] | None = None
    for group in groups:
        h1 = sum(
            s.value * (1.0 if s.page_index == 0 else SUB_PAGE_RANK)
            for s in group.members
            if s.tag == "h1"
        )
        if h1 > 0 and (best is None or h1 > best[0]):
            best = (h1, group)
    return None if best is None else best[1]


def _top_heading_group(groups: Sequence[_Group]) -> _Group | None:
    """The group holding the largest heading measured on the main page (pages[0]).

    It is the page's biggest headline (stripe.com's 56px h2 above a 48px h1, linear.app's 72px
    closing h2 above a 64px h1), so it keeps a level of its own instead of folding into a
    smaller one."""
    best: tuple[float, float, _Group] | None = None
    for group in groups:
        headings = [s for s in group.core if s.page_index == 0 and s.heading]
        if not headings:
            continue
        key = (max(s.size for s in headings), group.value)
        if best is None or key > best[:2]:
            best = (key[0], key[1], group)
    return None if best is None else best[2]


def _protected(*groups: _Group | None) -> list[_Group]:
    kept: list[_Group] = []
    for group in groups:
        if group is not None and not any(group is other for other in kept):
            kept.append(group)
    return kept


def _limit_groups(
    groups: list[_Group], limit: int, *, body: _Group | None, total: float
) -> list[_Group]:
    groups = list(groups)
    fixed = _protected(body, _h1_group(groups), _top_heading_group(groups))
    _merge_cheapest(list(groups), limit, groups, total=total, fixed=fixed)
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


def _absorb_into_h1(above: list[_Group], groups: list[_Group], *, keep: _Group | None) -> None:
    """One-off text slightly larger than the page's h1 (a big stat, a sub-page headline)
    joins the h1 level. `keep` (the main page's largest heading) never does: the biggest
    headline the page shows keeps its own level."""
    h1 = _h1_group(above)
    if h1 is None:
        return
    for group in [g for g in above if g.size > h1.size]:
        if (
            group is not keep
            and group.family == h1.family
            and group.count <= 2
            and "h1" not in group.tag_values()
            and group.size <= h1.size * DISPLAY_ABSORB_RATIO
        ):
            h1.absorb(group)
            above.remove(group)
            groups.remove(group)


def _name_below(
    below: list[_Group],
    names: dict[int, str],
    groups: list[_Group],
    *,
    targets: Sequence[_Group],
    total: float,
) -> None:
    """ui / overline / body-sm / caption for the groups smaller than body (largest first)."""
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
    if len(below) > len(_BELOW_NAMES) and "ui" not in names.values():
        # Three small sizes in use: an interactive-dominated one (nav/footer links in a
        # regular weight) is the UI text rather than an extra size to fold away.
        interactive = [g for g in below if g.share("interactive") > 0.5]
        if interactive:
            chosen = max(interactive, key=lambda g: (g.value, g.size))
            names[id(chosen)] = "ui"
            below.remove(chosen)
    named = [g for g in groups if id(g) in names and names[id(g)] != "code"]
    _merge_cheapest(below, len(_BELOW_NAMES), groups, total=total, targets=named)
    below.sort(key=lambda g: -g.size)
    if len(below) == 1:
        names[id(below[0])] = "body-sm" if below[0].size >= 13.5 else "caption"
    elif len(below) == 2:
        names[id(below[0])] = "body-sm"
        names[id(below[1])] = "caption"


def _name_groups(
    groups: list[_Group], body: _Group, *, primary_is_mono: bool, total: float
) -> list[tuple[str, _Group]]:
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
    top_heading = _top_heading_group(above)
    _absorb_into_h1(above, groups, keep=top_heading)
    _merge_cheapest(
        above,
        len(_ABOVE_NAMES),
        groups,
        total=total,
        targets=[body],
        fixed=_protected(_h1_group(above), top_heading),
    )
    above.sort(key=lambda g: -g.size)
    names.update(_name_above(above))

    below.sort(key=lambda g: -g.size)
    _name_below(below, names, groups, targets=[body], total=total)
    return [(names[id(g)], g) for g in groups if id(g) in names]


def _level(name: str, group: _Group) -> TypeLevel:
    # Style and samples come from the text measured at the level's own size; merged groups
    # only add to the count and tags.
    core = group.core or group.members
    size_mode = _weighted_mode((s.size, s.value) for s in core)
    assert isinstance(size_mode, float)
    size = round(size_mode, 2)
    heights = [(s.line_height_px, s.value) for s in core if s.line_height_px is not None]
    line_height_px: float | None = None
    if heights:
        mode = _weighted_mode(heights)
        assert isinstance(mode, float)
        line_height_px = round(mode, 2)
    transform = _weighted_mode((s.transform, s.value) for s in core)
    assert isinstance(transform, str)
    weight = group.weight

    def off_style(s: _Sample) -> bool:
        return abs(s.size - size) > 0.01 or s.weight != weight

    # Samples are text set as the level is (size and weight, line height preferred): a
    # 16px/400 button label is no sample of a 16px/300 body level. Only a level without such
    # text falls back to the rest of its own size.
    exact = [s for s in core if not off_style(s)]
    samples: list[str] = []
    for sample in sorted(
        exact or core,
        key=lambda s: (
            abs(s.size - size) > 0.01,
            s.line_height_px != line_height_px,
            s.page_index,
            0 if len(s.text) >= 4 else 1,
            s.order,
        ),
    ):
        if sample.text not in samples:
            samples.append(sample.text)
        if len(samples) == MAX_SAMPLES:
            break
    return TypeLevel(
        name=name,
        size_px=size,
        font_family=group.family,
        weight=weight,
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
    body = _pick_body(groups)
    if body is None:
        return []
    total = sum(group.value for group in groups) or 1.0
    groups = _limit_groups(groups, MAX_LEVELS, body=body, total=total)
    named = _name_groups(groups, body, primary_is_mono=primary_is_mono, total=total)
    levels = [_level(name, group) for name, group in named]
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


_WEIGHT_VARIABLE_RE = re.compile(r"weight|(?:^|-)fw(?:-|$)", re.IGNORECASE)


def token_weights(
    pages: Sequence[PageCapture], stylesheets: Sequence[StylesheetInfo] = ()
) -> set[int]:
    """Numeric weights declared by font-weight custom properties (--font-weight-semibold: 590)."""
    variables = [
        *(variable for page in pages for variable in page.css_variables),
        *(variable for sheet in stylesheets for variable in sheet.variables),
    ]
    weights: set[int] = set()
    for variable in variables:
        if not _WEIGHT_VARIABLE_RE.search(variable.name):
            continue
        for raw in (variable.resolved, variable.declared):
            value = (raw or "").strip()
            if value.isdigit() and 1 <= int(value) <= 1000:
                weights.add(int(value))
                break
    return weights


def _weights_used(
    samples: Sequence[_Sample],
    levels: Sequence[TypeLevel] = (),
    declared: Iterable[int] = (),
) -> list[int]:
    """Weights carrying >= 1% of the text, the weights of the reported levels, and weights a
    font-weight custom property declares that at least three text elements use (an accent such
    as Linear's 590 semibold on tier names stays listed, never forbidden)."""
    total = sum(sample.value for sample in samples)
    if total <= 0:
        return []
    totals: dict[int, float] = defaultdict(float)
    counts: dict[int, int] = defaultdict(int)
    for sample in samples:
        totals[sample.weight] += sample.value
        counts[sample.weight] += 1
    used = {weight for weight, value in totals.items() if value / total >= MIN_WEIGHT_SHARE}
    used.update(level.weight for level in levels)
    used.update(weight for weight in declared if counts.get(weight, 0) >= MIN_TOKEN_WEIGHT_ELEMENTS)
    return sorted(used)


def _with_used_weights(
    families: Sequence[FontFamilyInfo],
    samples: Sequence[_Sample],
    levels: Sequence[TypeLevel],
    declared: Iterable[int],
) -> list[FontFamilyInfo]:
    """Families whose weight lists follow the weights_used rule (family_weights()), so a
    family never omits a weight the page summary lists for its text."""
    total = sum(sample.value for sample in samples)
    declared = tuple(declared)
    by_family: dict[str, list[_Sample]] = defaultdict(list)
    for sample in samples:
        by_family[sample.family].append(sample)
    result: list[FontFamilyInfo] = []
    for family in families:
        weights = family_weights(
            by_family.get(family.name, []),
            total=total,
            level_weights=(level.weight for level in levels if level.font_family == family.name),
            declared=declared,
        )
        result.append(family.model_copy(update={"weights": weights}) if weights else family)
    return result


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
    declared = token_weights(pages, stylesheets)
    families = _with_used_weights(families, samples, levels, declared)
    weights_used = set(_weights_used(samples, levels, declared))
    weights_used.update(weight for family in families for weight in family.weights)
    body = next((level for level in levels if level.name == "body"), None)
    base = body.size_px if body is not None else DEFAULT_BASE_SIZE_PX
    fit = fit_scale_ratio((level.size_px for level in levels if level.name != "code"), base)
    return Typography(
        families=families,
        levels=levels,
        weights_used=sorted(weights_used),
        base_size_px=base,
        scale_ratio=None if fit is None else fit.ratio,
        scale_ratio_name=None if fit is None else fit.name,
    )
