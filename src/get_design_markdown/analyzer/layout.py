"""Layout shell, container and responsive changes from the 1440/768/390 LayoutSnapshots
(stream: analyzer-foundations, phase GĐ4).

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_layout(pages, breakpoints) -> LayoutInfo
"""

from __future__ import annotations

from collections.abc import Sequence

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import Breakpoint, LayoutInfo
from get_design_markdown.models.raw import PageCapture


def analyze_layout(
    pages: Sequence[PageCapture], breakpoints: Sequence[Breakpoint] = ()
) -> LayoutInfo:
    """Shell type, container max width, gutter, snapshots of the main page and the
    ResponsiveChange list between consecutive widths (pure)."""
    raise NotImplementedYetError("Phân tích layout chưa được xây dựng (giai đoạn 4).")
