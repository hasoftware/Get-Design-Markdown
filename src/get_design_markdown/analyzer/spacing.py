"""Spacing: base unit (4 or 8px), spacing scale, container width, section rhythm
(stream: analyzer-foundations, phase GĐ4).

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_spacing(pages) -> SpacingSystem
"""

from __future__ import annotations

from collections.abc import Sequence

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import SpacingSystem
from get_design_markdown.models.raw import PageCapture


def analyze_spacing(pages: Sequence[PageCapture]) -> SpacingSystem:
    """Spacing system from paddings, margins and gaps (pure)."""
    raise NotImplementedYetError("Phân tích spacing chưa được xây dựng (giai đoạn 4).")
