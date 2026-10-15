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
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Sequence

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


def query_boundaries(media: MediaQuery) -> Iterator[float]:
    """Width boundaries (px) of one captured media query."""
    if not _is_screen_query(media.query):
        return
    min_width = media.min_width_px
    max_width = media.max_width_px
    if min_width is None and max_width is None:
        parsed = parse_media_query(media.query)
        min_width, max_width = parsed.min_width_px, parsed.max_width_px
    if min_width is not None and min_width > 0:
        yield min_width_boundary(min_width)
    if max_width is not None and max_width > 0:
        yield max_width_boundary(max_width)


def _unique_sheets(stylesheets: Sequence[StylesheetInfo]) -> Iterator[StylesheetInfo]:
    seen: set[str] = set()
    for sheet in stylesheets:
        if sheet.url in seen:
            continue
        seen.add(sheet.url)
        yield sheet


def boundary_counts(stylesheets: Sequence[StylesheetInfo]) -> dict[float, int]:
    counts: dict[float, int] = {}
    for sheet in _unique_sheets(stylesheets):
        for media in sheet.media_queries:
            weight = max(media.occurrences, 1)
            for boundary in query_boundaries(media):
                counts[boundary] = counts.get(boundary, 0) + weight
    return counts


def analyze_breakpoints(stylesheets: Sequence[StylesheetInfo]) -> list[Breakpoint]:
    """Real width breakpoints only (ascending), named from naming.BREAKPOINT_NAMES (pure)."""
    counts = boundary_counts(stylesheets)
    merged: dict[float, int] = {}
    for boundary in sorted(counts, key=lambda value: (-counts[value], value)):
        target = next((kept for kept in merged if abs(kept - boundary) <= MERGE_DISTANCE_PX), None)
        if target is None:
            merged[boundary] = counts[boundary]
        else:
            merged[target] += counts[boundary]
    merged = {
        boundary: count
        for boundary, count in merged.items()
        if MIN_BREAKPOINT_PX <= boundary <= MAX_BREAKPOINT_PX
    }
    if len(merged) >= PRUNE_FROM_COUNT:
        # Component one-offs (a single 1115px query) are not site breakpoints.
        floor = max(2.0, MIN_SHARE * sum(merged.values()))
        merged = {boundary: count for boundary, count in merged.items() if count >= floor}
    if len(merged) > MAX_BREAKPOINTS:
        ranked = sorted(merged, key=lambda value: (-merged[value], value))[:MAX_BREAKPOINTS]
        merged = {boundary: merged[boundary] for boundary in ranked}
    boundaries = sorted(merged)
    names = name_scale(boundaries, BREAKPOINT_NAMES, anchor_value=ANCHOR_PX)
    return [
        Breakpoint(name=name, min_width_px=boundary, occurrences=merged[boundary])
        for name, boundary in zip(names, boundaries, strict=True)
    ]
