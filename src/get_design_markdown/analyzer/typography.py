"""Typography: font roles, semantic type levels, scale ratio
(stream: analyzer-foundations, phase GĐ4).

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_typography(pages, stylesheets) -> Typography
"""

from __future__ import annotations

from collections.abc import Sequence

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import Typography
from get_design_markdown.models.raw import PageCapture, StylesheetInfo


def analyze_typography(
    pages: Sequence[PageCapture], stylesheets: Sequence[StylesheetInfo] = ()
) -> Typography:
    """Families (primary/heading/mono/display + Google-font lookup), levels largest-first
    named from naming.TYPE_LEVEL_ORDER, weights used, base size and scale ratio (pure)."""
    raise NotImplementedYetError("Phân tích typography chưa được xây dựng (giai đoạn 4).")
