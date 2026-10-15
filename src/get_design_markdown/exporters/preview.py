"""preview.html: static page showing swatches, type specimens and rebuilt components
(stream: exporters, phase GĐ8).

PUBLIC ENTRY POINT (frozen signature, called by exporters/export.py):
    render_preview(analysis) -> str
"""

from __future__ import annotations

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import DesignAnalysis


def render_preview(analysis: DesignAnalysis) -> str:
    """Self-contained HTML (inline CSS/JS, no network): color swatches with hex + role +
    contrast, typography specimens per level, buttons/inputs/cards/badges/nav rebuilt from
    ComponentSpec with hover styles, spacing/radius/shadow scales, light/dark toggle when
    analysis.dark exists (pure; templates/preview.html.j2)."""
    raise NotImplementedYetError("Xuất preview.html chưa được xây dựng (giai đoạn 8).")
