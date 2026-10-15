"""Layout shell, container and responsive changes from the 1440/768/390 LayoutSnapshots
(stream: analyzer-foundations, phase GĐ4).

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_layout(pages, breakpoints) -> LayoutInfo

- snapshots: the main page's LayoutSnapshots, widest first.
- shell: "top-nav" when a navigation bar sits at the top of the page (header/nav landmark near
  the top, or a visible nav in a snapshot that is not just the sidebar), "sidebar" when only a
  sidebar is visible at the widest width, "top-nav+sidebar" when both, else "unknown". Without
  snapshots the landmarks of the main page decide.
- container_max_width_px: same rule as spacing.container_max_width(); gutter_px: the container
  padding measured at the widest viewport.
- changes: for each consecutive pair of snapshots (wider -> narrower), in ResponsiveChangeKind
  order — nav collapsing to a hamburger (nav links gone and a menu button visible at the
  narrower width; below_px = the most used breakpoint in (narrow, wide], else the wider width),
  nav hidden (no menu button), sidebar hidden, fewer columns, largest
  heading shrinking >= 10%, container becoming fluid (once), gutter shrinking >= 2px,
  horizontal scrollers appearing and tap targets under 24px at phone widths (<= 480px).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from itertools import pairwise
from typing import Literal

from get_design_markdown.analyzer.common import CAPTURE_VIEWPORT_WIDTH, iter_elements
from get_design_markdown.analyzer.css_values import format_px
from get_design_markdown.analyzer.spacing import container_max_width
from get_design_markdown.models.analysis import (
    Breakpoint,
    LayoutInfo,
    ResponsiveChange,
    ResponsiveChangeKind,
)
from get_design_markdown.models.raw import LayoutSnapshot, PageCapture

HEADING_SHRINK_SHARE = 0.10
GUTTER_SHRINK_PX = 2.0
FLUID_TOLERANCE_PX = 2.0
PHONE_MAX_WIDTH = 480
MIN_TAP_TARGET_PX = 24.0
TOP_BAR_MAX_Y = 160.0
TOP_BAR_MAX_HEIGHT = 200.0
TOP_BAR_MIN_WIDTH = 600.0
SIDEBAR_MAX_WIDTH = 400.0
SIDEBAR_MIN_HEIGHT = 500.0
SIDEBAR_MAX_Y = 200.0

Kind = ResponsiveChangeKind
Shell = Literal["top-nav", "sidebar", "top-nav+sidebar", "unknown"]


def _num(value: float) -> str:
    """64.0 -> "64", 15.5 -> "15.5" (evidence text)."""
    return format_px(value)[:-2]


def _has_top_bar(page: PageCapture) -> bool:
    """A header/nav landmark spanning the top of the page."""
    for ref in iter_elements([page]):
        element = ref.element
        if element.landmark not in ("header", "nav") and element.tag not in ("header", "nav"):
            continue
        box = element.box
        if (
            box.y <= TOP_BAR_MAX_Y
            and 0 < box.height <= TOP_BAR_MAX_HEIGHT
            and box.width >= TOP_BAR_MIN_WIDTH
        ):
            return True
    return False


def _has_sidebar(page: PageCapture) -> bool:
    """A tall, narrow aside/nav landmark on screen at the left or right edge, starting near
    the top of the page (not an off-canvas drawer parked outside the viewport)."""
    for ref in iter_elements([page]):
        element = ref.element
        if element.landmark not in ("aside", "nav") and element.tag not in ("aside", "nav"):
            continue
        box = element.box
        on_screen = box.x >= -1 and box.x + box.width <= CAPTURE_VIEWPORT_WIDTH + 1
        at_edge = box.x <= 40 or box.x + box.width >= CAPTURE_VIEWPORT_WIDTH - 40
        if (
            on_screen
            and at_edge
            and box.y <= SIDEBAR_MAX_Y
            and box.width <= SIDEBAR_MAX_WIDTH
            and box.height >= SIDEBAR_MIN_HEIGHT
        ):
            return True
    return False


def layout_shell(page: PageCapture | None, snapshots: Sequence[LayoutSnapshot]) -> Shell:
    top_bar = page is not None and _has_top_bar(page)
    if snapshots:
        widest = snapshots[0]
        sidebar = widest.sidebar_visible
        nav_any = any(snapshot.nav_visible for snapshot in snapshots)
        top_nav = top_bar or (nav_any and not sidebar)
    else:
        sidebar = page is not None and _has_sidebar(page)
        top_nav = top_bar
    if top_nav and sidebar:
        return "top-nav+sidebar"
    if top_nav:
        return "top-nav"
    if sidebar:
        return "sidebar"
    return "unknown"


def _collapse_breakpoint(breakpoints: Sequence[Breakpoint], narrow: int, wide: int) -> float | None:
    inside = [
        bp for bp in breakpoints if bp.min_width_px is not None and narrow < bp.min_width_px <= wide
    ]
    if not inside:
        return None
    best = max(inside, key=lambda bp: (bp.occurrences, bp.min_width_px or 0.0))
    return best.min_width_px


def _pair_changes(
    wide: LayoutSnapshot,
    narrow: LayoutSnapshot,
    breakpoints: Sequence[Breakpoint],
    container_px: float | None,
    fluid_done: bool,
) -> Iterator[ResponsiveChange]:
    widths = {"from_width": wide.viewport_width, "to_width": narrow.viewport_width}
    nav_lost = wide.nav_visible and not narrow.nav_visible
    # A menu button already reported at the wider width (a "More" toggle...) does not change
    # the story: the links went away and a menu button is what remains.
    if nav_lost and narrow.hamburger_visible:
        below = _collapse_breakpoint(breakpoints, narrow.viewport_width, wide.viewport_width)
        yield ResponsiveChange(
            **widths,
            kind=Kind.NAV_COLLAPSES_TO_HAMBURGER,
            params={
                "below_px": float(below if below is not None else wide.viewport_width),
                "links": float(wide.visible_nav_links),
            },
            evidence=(
                f"{wide.visible_nav_links} visible nav links at {wide.viewport_width}px, "
                f"hamburger at {narrow.viewport_width}px"
                + ("" if below is not None else " (no matching @media breakpoint)")
            ),
        )
    elif nav_lost:
        yield ResponsiveChange(
            **widths,
            kind=Kind.NAV_HIDDEN,
            params={"links": float(wide.visible_nav_links)},
            evidence=f"navigation visible at {wide.viewport_width}px, hidden at "
            f"{narrow.viewport_width}px without a menu button",
        )
    if wide.sidebar_visible and not narrow.sidebar_visible:
        yield ResponsiveChange(
            **widths,
            kind=Kind.SIDEBAR_HIDDEN,
            evidence=f"sidebar visible at {wide.viewport_width}px, hidden at "
            f"{narrow.viewport_width}px",
        )
    if narrow.main_columns < wide.main_columns:
        yield ResponsiveChange(
            **widths,
            kind=Kind.COLUMNS_REDUCE,
            params={"from": float(wide.main_columns), "to": float(narrow.main_columns)},
            evidence=f"main grid {wide.main_columns} -> {narrow.main_columns} "
            + ("column" if narrow.main_columns == 1 else "columns"),
        )
    if (
        wide.max_heading_px is not None
        and narrow.max_heading_px is not None
        and narrow.max_heading_px <= wide.max_heading_px * (1 - HEADING_SHRINK_SHARE)
    ):
        yield ResponsiveChange(
            **widths,
            kind=Kind.HEADING_SCALES_DOWN,
            params={"from_px": wide.max_heading_px, "to_px": narrow.max_heading_px},
            evidence=f"largest heading {_num(wide.max_heading_px)}px -> "
            f"{_num(narrow.max_heading_px)}px",
        )
    if (
        not fluid_done
        and wide.container_width_px is not None
        and narrow.container_width_px is not None
        and wide.container_width_px < wide.viewport_width - FLUID_TOLERANCE_PX
        and narrow.container_width_px >= narrow.viewport_width - FLUID_TOLERANCE_PX
    ):
        max_width = container_px if container_px is not None else wide.container_width_px
        yield ResponsiveChange(
            **widths,
            kind=Kind.CONTAINER_BECOMES_FLUID,
            params={"max_width_px": float(max_width)},
            evidence=f"container {_num(max_width)}px -> full width",
        )
    if (
        wide.gutter_px is not None
        and narrow.gutter_px is not None
        and wide.gutter_px - narrow.gutter_px >= GUTTER_SHRINK_PX
    ):
        yield ResponsiveChange(
            **widths,
            kind=Kind.GUTTER_SHRINKS,
            params={"from_px": wide.gutter_px, "to_px": narrow.gutter_px},
            evidence=f"container padding {_num(wide.gutter_px)}px -> {_num(narrow.gutter_px)}px",
        )
    if wide.horizontal_scrollers == 0 and narrow.horizontal_scrollers > 0:
        yield ResponsiveChange(
            **widths,
            kind=Kind.HORIZONTAL_SCROLL_APPEARS,
            params={"count": float(narrow.horizontal_scrollers)},
            evidence=f"{narrow.horizontal_scrollers} horizontally scrolling row(s) at "
            f"{narrow.viewport_width}px",
        )
    if (
        narrow.viewport_width <= PHONE_MAX_WIDTH
        and narrow.min_tap_target_px is not None
        and narrow.min_tap_target_px < MIN_TAP_TARGET_PX
    ):
        yield ResponsiveChange(
            **widths,
            kind=Kind.TAP_TARGETS_SMALL,
            params={"min_px": narrow.min_tap_target_px},
            evidence=f"smallest tap target {_num(narrow.min_tap_target_px)}px at "
            f"{narrow.viewport_width}px",
        )


def responsive_changes(
    snapshots: Sequence[LayoutSnapshot],
    breakpoints: Sequence[Breakpoint] = (),
    *,
    container_px: float | None = None,
) -> list[ResponsiveChange]:
    """Changes between consecutive snapshots (sorted widest first)."""
    changes: list[ResponsiveChange] = []
    fluid_done = False
    for wide, narrow in pairwise(snapshots):
        if narrow.viewport_width >= wide.viewport_width:
            continue
        for change in _pair_changes(wide, narrow, breakpoints, container_px, fluid_done):
            fluid_done = fluid_done or change.kind == Kind.CONTAINER_BECOMES_FLUID
            changes.append(change)
    return changes


def analyze_layout(
    pages: Sequence[PageCapture], breakpoints: Sequence[Breakpoint] = ()
) -> LayoutInfo:
    """Shell type, container max width, gutter, snapshots of the main page and the
    ResponsiveChange list between consecutive widths (pure)."""
    main = pages[0] if pages else None
    snapshots: list[LayoutSnapshot] = []
    if main is not None:
        by_width: dict[int, LayoutSnapshot] = {}
        for snapshot in main.layouts:
            by_width.setdefault(snapshot.viewport_width, snapshot)
        snapshots = sorted(by_width.values(), key=lambda s: -s.viewport_width)
    container_px = container_max_width(pages)
    return LayoutInfo(
        container_max_width_px=container_px,
        gutter_px=snapshots[0].gutter_px if snapshots else None,
        shell=layout_shell(main, snapshots),
        snapshots=snapshots,
        changes=responsive_changes(snapshots, breakpoints, container_px=container_px),
    )
