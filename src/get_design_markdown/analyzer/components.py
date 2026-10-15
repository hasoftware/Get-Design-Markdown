"""Components by heuristics: buttons, links, inputs, cards, navigation, badges, footer,
their variants, hover states and token references (stream: analyzer-components, phase GĐ5).

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_components(pages, palette=..., typography=..., spacing=..., radii=..., shadows=...)
        -> list[ComponentSpec]
"""

from __future__ import annotations

from collections.abc import Sequence

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import (
    ColorPalette,
    ComponentSpec,
    ScaleStep,
    ShadowLevel,
    SpacingSystem,
    Typography,
)
from get_design_markdown.models.raw import PageCapture


def analyze_components(
    pages: Sequence[PageCapture],
    *,
    palette: ColorPalette,
    typography: Typography,
    spacing: SpacingSystem,
    radii: Sequence[ScaleStep],
    shadows: Sequence[ShadowLevel],
) -> list[ComponentSpec]:
    """Component variants in naming.COMPONENT_NAME_ORDER order (pure).

    The other analyses may be empty (their stream not merged yet): then token_refs stay empty
    for that group, the raw `properties` are still filled.
    """
    raise NotImplementedYetError("Nhận diện component chưa được xây dựng (giai đoạn 5).")
