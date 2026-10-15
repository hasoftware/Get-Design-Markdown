"""Breakpoints measured from @media queries (stream: analyzer-foundations, phase GĐ4).

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_breakpoints(stylesheets) -> list[Breakpoint]
"""

from __future__ import annotations

from collections.abc import Sequence

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import Breakpoint
from get_design_markdown.models.raw import StylesheetInfo


def analyze_breakpoints(stylesheets: Sequence[StylesheetInfo]) -> list[Breakpoint]:
    """Real width breakpoints only (ascending), named from naming.BREAKPOINT_NAMES (pure)."""
    raise NotImplementedYetError("Phân tích breakpoint chưa được xây dựng (giai đoạn 4).")
