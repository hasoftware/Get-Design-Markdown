"""Colors: normalization, CIEDE2000 clustering per usage channel, roles, semantic tokens,
WCAG contrast pairs (stream: analyzer-colors, phase GĐ3).

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_colors(pages, stylesheets, threshold=...) -> ColorPalette
"""

from __future__ import annotations

from collections.abc import Sequence

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import ColorPalette
from get_design_markdown.models.raw import PageCapture, StylesheetInfo

#: Default CIEDE2000 merge threshold (spec: 3).
DEFAULT_DELTA_E = 3.0


def analyze_colors(
    pages: Sequence[PageCapture],
    stylesheets: Sequence[StylesheetInfo] = (),
    *,
    threshold: float = DEFAULT_DELTA_E,
) -> ColorPalette:
    """Build the palette of one color scheme from the captured pages (pure function).

    Called once with RawCapture.pages (light, pages[0] = main page) and once with
    [RawCapture.dark] for the dark theme. ColorPalette.scheme must reflect the measured canvas
    lightness ("dark" when the canvas OKLCH lightness < 0.5), not the emulated media.
    """
    raise NotImplementedYetError("Phân tích màu sắc chưa được xây dựng (giai đoạn 3).")
