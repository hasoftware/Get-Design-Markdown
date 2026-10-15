"""Border-radius scale and shadow (elevation) levels
(stream: analyzer-foundations, phase GĐ4).

PUBLIC ENTRY POINTS (frozen signatures, called by analyzer/analyze.py):
    analyze_radii(pages) -> list[ScaleStep]
    analyze_shadows(pages) -> list[ShadowLevel]
"""

from __future__ import annotations

from collections.abc import Sequence

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import ScaleStep, ShadowLevel
from get_design_markdown.models.raw import PageCapture


def analyze_radii(pages: Sequence[PageCapture]) -> list[ScaleStep]:
    """Radius steps ascending, named from naming.RADIUS_STEP_NAMES (+ "full" for pills)."""
    raise NotImplementedYetError("Phân tích bo góc chưa được xây dựng (giai đoạn 4).")


def analyze_shadows(pages: Sequence[PageCapture]) -> list[ShadowLevel]:
    """Shadow levels from subtlest to strongest, named from naming.SHADOW_LEVEL_NAMES."""
    raise NotImplementedYetError("Phân tích đổ bóng chưa được xây dựng (giai đoạn 4).")
