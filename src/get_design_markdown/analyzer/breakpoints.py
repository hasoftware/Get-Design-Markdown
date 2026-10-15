"""Breakpoints measured from @media queries (stream: analyzer-foundations, phase GĐ4).

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_breakpoints(stylesheets) -> list[Breakpoint]

Only real breakpoints are reported — no media query, no breakpoint (never the generic
768/1024/1440). Every width boundary of a screen media query counts with the query's
occurrences: min-width X -> round(X); max-width X -> the first width it excludes, so
767.98 -> 768 (fraction >= .5 rounds up), 767 -> 768 (odd integers + 1), 768 -> 768.
Print-only, color-scheme-only and reduced-motion-only queries carry no width and are ignored.
Boundaries within 4px merge into the more frequent one; values below 320px or above 2560px are
dropped, and when more than four remain, boundaries used by fewer than max(2, 2% of all width
queries) are dropped too (component one-offs such as a single 1115px query); a compact set such
as Tailwind's 640/768/1024/1280 keeps a breakpoint written only once. When more than
seven remain, the most frequent seven are kept. Names come from naming.BREAKPOINT_NAMES with
768px as "md".

Each breakpoint keeps the direction most of its queries are written in. Mobile-first
(min-width) boundaries fill min_width_px with the boundary; desktop-first (max-width)
boundaries fill max_width_px with the max-width value written most (768 for
"(max-width: 768px)", which still matches at 768px), so a desktop-first site reads
"<= 768px" instead of a reversed ">= 768px".
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from get_design_markdown.analyzer.css_values import parse_media_query, split_top_level
from get_design_markdown.analyzer.naming import BREAKPOINT_NAMES, name_scale
from get_design_markdown.models.analysis import Breakpoint
from get_design_markdown.models.raw import MediaQuery, StylesheetInfo

MIN_BREAKPOINT_PX = 320.0
MAX_BREAKPOINT_PX = 2560.0
MERGE_DISTANCE_PX = 4.0
PRUNE_FROM_COUNT = 5
#: Share of all width-query occurrences a boundary needs once more than four exist.
MIN_SHARE = 0.02
MAX_BREAKPOINTS = len(BREAKPOINT_NAMES)
ANCHOR_PX = 768.0

_NON_SCREEN_RE = re.compile(r"^\s*(only\s+)?(print|speech)\b")


def _is_screen_query(query: str) -> bool:
    """False for queries that only apply to print/speech media."""
    parts = split_top_level(query.lower(), ",")
    if not parts:
        return True
    return not all(_NON_SCREEN_RE.match(part) for part in parts)


def min_width_boundary(value: float) -> float:
    return float(round(value))


def max_width_boundary(value: float) -> float:
    """First width a (max-width: value) query no longer matches, as designers write it."""
    integer = math.floor(value)
    fraction = value - integer
    if fraction >= 0.5:
        return float(integer + 1)
    if fraction > 0:
        return float(integer)
    return float(integer + 1) if integer % 2 == 1 else float(integer)


def _query_widths(media: MediaQuery) -> tuple[float | None, float | None]:
    """(min-width, max-width) of a screen media query in px; (None, None) otherwise."""
    if not _is_screen_query(media.query):
        return None, None
    min_width = media.min_width_px
    max_width = media.max_width_px
    if min_width is None and max_width is None:
        parsed = parse_media_query(media.query)
        min_width, max_width = parsed.min_width_px, parsed.max_width_px
    return (
        min_width if min_width is not None and min_width > 0 else None,
        max_width if max_width is not None and max_width > 0 else None,
    )


def query_boundaries(media: MediaQuery) -> Iterator[float]:
    """Width boundaries (px) of one captured media query."""
    min_width, max_width = _query_widths(media)
    if min_width is not None:
        yield min_width_boundary(min_width)
    if max_width is not None:
        yield max_width_boundary(max_width)


@dataclass(slots=True)
class BoundaryStats:
    """Occurrences of one width boundary per query direction."""

    min_count: int = 0
    max_count: int = 0
    #: Max-width values written for this boundary (768 or 767.98...) -> occurrences.
    max_values: dict[float, int] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return self.min_count + self.max_count

    @property
    def desktop_first(self) -> bool:
        return self.max_count > self.min_count

    def add(self, other: BoundaryStats) -> None:
        self.min_count += other.min_count
        self.max_count += other.max_count
        for value, count in other.max_values.items():
            self.max_values[value] = self.max_values.get(value, 0) + count

    def max_value(self) -> float | None:
        """The max-width value written most (larger value on ties)."""
        if not self.max_values:
            return None
        return max(self.max_values, key=lambda value: (self.max_values[value], value))


def _unique_sheets(stylesheets: Sequence[StylesheetInfo]) -> Iterator[StylesheetInfo]:
    seen: set[str] = set()
    for sheet in stylesheets:
        if sheet.url in seen:
            continue
        seen.add(sheet.url)
        yield sheet


def boundary_stats(stylesheets: Sequence[StylesheetInfo]) -> dict[float, BoundaryStats]:
    """Occurrences per width boundary and query direction (stylesheets counted once)."""
    stats: dict[float, BoundaryStats] = {}
    for sheet in _unique_sheets(stylesheets):
        for media in sheet.media_queries:
            weight = max(media.occurrences, 1)
            min_width, max_width = _query_widths(media)
            if min_width is not None:
                entry = stats.setdefault(min_width_boundary(min_width), BoundaryStats())
                entry.min_count += weight
            if max_width is not None:
                entry = stats.setdefault(max_width_boundary(max_width), BoundaryStats())
                entry.max_count += weight
                value = round(max_width, 2)
                entry.max_values[value] = entry.max_values.get(value, 0) + weight
    return stats


def boundary_counts(stylesheets: Sequence[StylesheetInfo]) -> dict[float, int]:
    return {boundary: entry.count for boundary, entry in boundary_stats(stylesheets).items()}


def analyze_breakpoints(stylesheets: Sequence[StylesheetInfo]) -> list[Breakpoint]:
    """Real width breakpoints only (ascending), named from naming.BREAKPOINT_NAMES (pure)."""
    stats = boundary_stats(stylesheets)
    merged: dict[float, BoundaryStats] = {}
    for boundary in sorted(stats, key=lambda value: (-stats[value].count, value)):
        target = next((kept for kept in merged if abs(kept - boundary) <= MERGE_DISTANCE_PX), None)
        if target is None:
            merged[boundary] = BoundaryStats()
            target = boundary
        merged[target].add(stats[boundary])
    merged = {
        boundary: entry
        for boundary, entry in merged.items()
        if MIN_BREAKPOINT_PX <= boundary <= MAX_BREAKPOINT_PX
    }
    if len(merged) >= PRUNE_FROM_COUNT:
        # Component one-offs (a single 1115px query) are not site breakpoints.
        floor = max(2.0, MIN_SHARE * sum(entry.count for entry in merged.values()))
        merged = {boundary: entry for boundary, entry in merged.items() if entry.count >= floor}
    if len(merged) > MAX_BREAKPOINTS:
        ranked = sorted(merged, key=lambda value: (-merged[value].count, value))[:MAX_BREAKPOINTS]
        merged = {boundary: merged[boundary] for boundary in ranked}
    boundaries = sorted(merged)
    names = name_scale(boundaries, BREAKPOINT_NAMES, anchor_value=ANCHOR_PX)
    result: list[Breakpoint] = []
    for name, boundary in zip(names, boundaries, strict=True):
        entry = merged[boundary]
        max_value = entry.max_value() if entry.desktop_first else None
        if max_value is not None:
            result.append(Breakpoint(name=name, max_width_px=max_value, occurrences=entry.count))
        else:
            result.append(Breakpoint(name=name, min_width_px=boundary, occurrences=entry.count))
    return result
