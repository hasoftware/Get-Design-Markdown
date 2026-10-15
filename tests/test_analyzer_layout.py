"""Tests of analyzer/layout.py: shell, container, responsive changes (+ the glue run)."""

from __future__ import annotations

from typing import Any

import pytest

from get_design_markdown.analyzer import analyze
from get_design_markdown.analyzer.layout import (
    analyze_layout,
    discount_scaled_tap_targets,
    layout_shell,
    responsive_changes,
)
from get_design_markdown.models.analysis import (
    Breakpoint,
    LayoutInfo,
    ResponsiveChangeKind,
)
from get_design_markdown.models.raw import (
    BoundingBox,
    ComputedStyle,
    ElementSample,
    LayoutSnapshot,
    PageCapture,
)
from tests.conftest import FIXED_TIME
from tests.fixture_data import load_analysis_sample, load_raw_sample, load_site_contract

Kind = ResponsiveChangeKind


def _snap(width: int, **kwargs: Any) -> LayoutSnapshot:
    return LayoutSnapshot(viewport_width=width, **kwargs)


def _el(
    index: int,
    tag: str,
    box: tuple[float, float, float, float],
    landmark: str | None = None,
    **styles: Any,
) -> ElementSample:
    x, y, width, height = box
    return ElementSample(
        index=index,
        tag=tag,
        box=BoundingBox(x=x, y=y, width=width, height=height),
        landmark=landmark,
        styles=ComputedStyle(**styles),
    )


def _page(
    elements: list[ElementSample] | None = None, layouts: list[LayoutSnapshot] | None = None
) -> PageCapture:
    return PageCapture(
        url="https://x.test/",
        final_url="https://x.test/",
        elements=elements or [],
        layouts=layouts or [],
    )


def _kinds(info: LayoutInfo) -> list[tuple[int, int, Kind]]:
    return [(c.from_width, c.to_width, c.kind) for c in info.changes]


HEADER = _el(0, "header", (0, 0, 1440, 64), landmark="header")
SIDEBAR = _el(1, "aside", (0, 64, 280, 1600), landmark="aside")


# ─── Fixture ─────────────────────────────────────────────────────────────────


def test_fixture_layout() -> None:
    raw = load_raw_sample()
    reference = load_analysis_sample()
    info = analyze_layout(raw.pages, reference.breakpoints)
    contract = load_site_contract().layout
    assert info.shell == contract["shell"] == "top-nav"
    assert info.container_max_width_px == 1200
    assert info.gutter_px == 24
    assert info.snapshots == reference.layout.snapshots
    assert [(c.from_width, c.to_width, c.kind, c.params) for c in info.changes] == [
        (c.from_width, c.to_width, c.kind, c.params) for c in reference.layout.changes
    ]
    collapse = info.changes[0]
    assert collapse.params["below_px"] == contract["hamburger_below_px"]
    assert all(change.evidence for change in info.changes)
    columns = {str(s.viewport_width): s.main_columns for s in info.snapshots}
    assert columns == contract["columns"]


def test_fixture_foundations_through_analyze_capture() -> None:
    """The frozen glue runs this stream's analyzers and assembles the reference answers."""
    raw = load_raw_sample()
    reference = load_analysis_sample()
    result = analyze.analyze_capture(raw, now=FIXED_TIME)
    assert [(lv.name, lv.size_px) for lv in result.typography.levels] == [
        (lv.name, lv.size_px) for lv in reference.typography.levels
    ]
    assert result.typography.scale_ratio == reference.typography.scale_ratio
    assert result.spacing.base_unit_px == reference.spacing.base_unit_px
    assert result.spacing.container_max_width_px == reference.spacing.container_max_width_px
    assert [(s.name, s.value_px) for s in result.radii] == [
        (s.name, s.value_px) for s in reference.radii
    ]
    assert [(s.name, s.css) for s in result.shadows] == [(s.name, s.css) for s in reference.shadows]
    assert result.breakpoints == reference.breakpoints
    assert [(c.kind, c.params) for c in result.layout.changes] == [
        (c.kind, c.params) for c in reference.layout.changes
    ]


# ─── Shell ───────────────────────────────────────────────────────────────────


def test_empty_inputs() -> None:
    assert analyze_layout([]) == LayoutInfo()
    info = analyze_layout([_page()])
    assert info.shell == "unknown" and info.changes == [] and info.snapshots == []


@pytest.mark.parametrize(
    ("elements", "shell"),
    [
        ([HEADER], "top-nav"),
        ([SIDEBAR], "sidebar"),
        ([HEADER, SIDEBAR], "top-nav+sidebar"),
        ([_el(0, "footer", (0, 2000, 1440, 300), landmark="footer")], "unknown"),
    ],
)
def test_shell_from_landmarks_without_snapshots(elements: list[ElementSample], shell: str) -> None:
    info = analyze_layout([_page(elements)])
    assert info.shell == shell
    assert info.changes == []


def test_shell_from_snapshots() -> None:
    nav = [_snap(1440, nav_visible=True)]
    assert layout_shell(_page(), nav) == "top-nav"
    side = [_snap(1440, nav_visible=True, sidebar_visible=True)]
    assert layout_shell(_page(), side) == "sidebar"  # the visible nav is the sidebar itself
    assert layout_shell(_page([HEADER]), side) == "top-nav+sidebar"
    assert layout_shell(None, [_snap(1440)]) == "unknown"


def test_snapshots_sorted_widest_first_and_deduplicated() -> None:
    layouts = [_snap(390), _snap(1440, gutter_px=32), _snap(768), _snap(1440, gutter_px=99)]
    info = analyze_layout([_page(layouts=layouts)])
    assert [s.viewport_width for s in info.snapshots] == [1440, 768, 390]
    assert info.gutter_px == 32


# ─── Responsive changes ──────────────────────────────────────────────────────


def test_nav_collapse_uses_most_used_breakpoint_in_range() -> None:
    snapshots = [
        _snap(1440, nav_visible=True, visible_nav_links=7),
        _snap(768, hamburger_visible=True),
    ]
    breakpoints = [
        Breakpoint(name="sm", min_width_px=640, occurrences=259),
        Breakpoint(name="md", min_width_px=940, occurrences=142),
        Breakpoint(name="lg", min_width_px=1264, occurrences=5),
    ]
    [change] = responsive_changes(snapshots, breakpoints)
    assert change.kind == Kind.NAV_COLLAPSES_TO_HAMBURGER
    assert change.params == {"below_px": 940.0, "links": 7.0}


def test_nav_collapse_without_consistent_breakpoint_names_no_threshold() -> None:
    snapshots = [
        _snap(1440, nav_visible=True, visible_nav_links=4),
        _snap(768, hamburger_visible=True),
    ]
    [change] = responsive_changes(snapshots)
    assert change.params == {"links": 4.0}  # "between 1440px and 768px", no invented threshold
    assert "no @media breakpoint consistent with both widths" in change.evidence
    # A breakpoint outside (768, 1440] cannot explain the change either.
    [change] = responsive_changes(snapshots, [Breakpoint(name="sm", min_width_px=640)])
    assert "below_px" not in change.params


def test_nav_collapse_at_desktop_first_max_width_boundary() -> None:
    # linear.app: links at 1440 and 1024, hamburger from (max-width: 768px) down.
    snapshots = [
        _snap(1440, nav_visible=True, visible_nav_links=6),
        _snap(768, hamburger_visible=True),
    ]
    breakpoints = [
        Breakpoint(name="sm", max_width_px=640, occurrences=367),
        Breakpoint(name="md", max_width_px=768, occurrences=212),
        Breakpoint(name="lg", max_width_px=1024, occurrences=136),
        Breakpoint(name="xl", max_width_px=1280, occurrences=45),
    ]
    [change] = responsive_changes(snapshots, breakpoints)
    assert change.params == {"below_px": 769.0, "max_px": 768.0, "links": 6.0}
    assert "@media (max-width: 768px)" in change.evidence


def test_nav_collapse_ignores_max_width_that_misses_the_narrow_capture() -> None:
    # (max-width: 767.98px) no longer matches at 768px, so it cannot explain the 768 menu.
    snapshots = [
        _snap(1440, nav_visible=True, visible_nav_links=5),
        _snap(768, hamburger_visible=True),
    ]
    breakpoints = [
        Breakpoint(name="md", max_width_px=767.98, occurrences=50),
        Breakpoint(name="lg", min_width_px=1024, occurrences=10),
    ]
    [change] = responsive_changes(snapshots, breakpoints)
    assert change.params == {"below_px": 1024.0, "links": 5.0}
    assert "@media (min-width: 1024px)" in change.evidence


def test_menu_button_already_visible_still_counts_as_collapse() -> None:
    snapshots = [
        _snap(1440, nav_visible=True, visible_nav_links=5, hamburger_visible=True),
        _snap(390, hamburger_visible=True),
    ]
    assert [c.kind for c in responsive_changes(snapshots)] == [Kind.NAV_COLLAPSES_TO_HAMBURGER]


def test_nav_hidden_without_menu_button() -> None:
    snapshots = [_snap(1440, nav_visible=True, visible_nav_links=3), _snap(390)]
    [change] = responsive_changes(snapshots)
    assert change.kind == Kind.NAV_HIDDEN and change.params == {"links": 3.0}


def test_all_change_kinds_in_declaration_order() -> None:
    snapshots = [
        _snap(
            1440,
            nav_visible=True,
            visible_nav_links=6,
            sidebar_visible=True,
            main_columns=4,
            max_heading_px=72,
            container_width_px=1200,
            gutter_px=32,
        ),
        _snap(
            390,
            hamburger_visible=True,
            main_columns=1,
            max_heading_px=40,
            container_width_px=390,
            gutter_px=16,
            horizontal_scrollers=2,
            min_tap_target_px=18,
        ),
    ]
    changes = responsive_changes(snapshots, container_px=1180)
    assert [c.kind for c in changes] == [
        Kind.NAV_COLLAPSES_TO_HAMBURGER,
        Kind.SIDEBAR_HIDDEN,
        Kind.COLUMNS_REDUCE,
        Kind.HEADING_SCALES_DOWN,
        Kind.CONTAINER_BECOMES_FLUID,
        Kind.GUTTER_SHRINKS,
        Kind.HORIZONTAL_SCROLL_APPEARS,
        Kind.TAP_TARGETS_SMALL,
    ]
    params = {c.kind: c.params for c in changes}
    assert params[Kind.COLUMNS_REDUCE] == {"from": 4.0, "to": 1.0}
    assert params[Kind.HEADING_SCALES_DOWN] == {"from_px": 72.0, "to_px": 40.0}
    assert params[Kind.CONTAINER_BECOMES_FLUID] == {"max_width_px": 1180.0}
    assert params[Kind.GUTTER_SHRINKS] == {"from_px": 32.0, "to_px": 16.0}
    assert params[Kind.HORIZONTAL_SCROLL_APPEARS] == {"count": 2.0}
    assert params[Kind.TAP_TARGETS_SMALL] == {"min_px": 18.0}
    assert all(c.from_width == 1440 and c.to_width == 390 for c in changes)


def test_small_differences_are_not_changes() -> None:
    snapshots = [
        _snap(1440, max_heading_px=48, gutter_px=24, min_tap_target_px=20),
        _snap(1024, max_heading_px=44, gutter_px=23, min_tap_target_px=20),
    ]
    assert responsive_changes(snapshots) == []  # 8% smaller heading, 1px gutter, not a phone


def test_container_becomes_fluid_only_once() -> None:
    snapshots = [
        _snap(1440, container_width_px=1200),
        _snap(768, container_width_px=768),
        _snap(390, container_width_px=390),
    ]
    changes = responsive_changes(snapshots)
    assert [(c.from_width, c.kind) for c in changes] == [(1440, Kind.CONTAINER_BECOMES_FLUID)]
    assert changes[0].params == {"max_width_px": 1200.0}


def test_missing_measurements_are_skipped() -> None:
    snapshots = [_snap(1440), _snap(768), _snap(390)]
    assert responsive_changes(snapshots) == []


def test_off_canvas_or_low_asides_are_not_sidebars() -> None:
    drawer = _el(1, "aside", (1440, 0, 400, 900), landmark="aside")
    low = _el(2, "aside", (0, 1200, 300, 900), landmark="aside")
    assert analyze_layout([_page([HEADER, drawer, low])]).shell == "top-nav"


# ─── Tap targets ─────────────────────────────────────────────────────────────


def test_scaled_down_tap_targets_are_not_claimed() -> None:
    # linear.app: a hero app mockup scaled with the page yields 14px / 12px "buttons".
    snapshots = [
        _snap(1440, min_tap_target_px=24),
        _snap(768, min_tap_target_px=14),
        _snap(390, min_tap_target_px=12),
    ]
    assert responsive_changes(snapshots) == []
    cleaned = discount_scaled_tap_targets(snapshots)
    assert [s.min_tap_target_px for s in cleaned] == [24.0, None, None]
    info = analyze_layout([_page(layouts=snapshots)])
    assert [s.min_tap_target_px for s in info.snapshots] == [24.0, None, None]


def test_small_tap_targets_seen_at_every_width_are_claimed() -> None:
    snapshots = [_snap(1440, min_tap_target_px=20), _snap(390, min_tap_target_px=18)]
    [change] = responsive_changes(snapshots)
    assert change.kind == Kind.TAP_TARGETS_SMALL and change.params == {"min_px": 18.0}
    assert discount_scaled_tap_targets(snapshots) == snapshots
