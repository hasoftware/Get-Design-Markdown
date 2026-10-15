"""Rich terminal summary of an analysis (stream: analyzer-components, phase GĐ5).

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    build_report(analysis) -> RenderableType
"""

from __future__ import annotations

from rich.console import RenderableType

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import DesignAnalysis


def build_report(analysis: DesignAnalysis) -> RenderableType:
    """Tables with Vietnamese headings: palette (swatch + hex + role + share), contrast
    warnings, type levels, spacing/radius/shadow scales, breakpoints, components (pure)."""
    raise NotImplementedYetError("Bảng tóm tắt chưa được xây dựng (giai đoạn 5).")
