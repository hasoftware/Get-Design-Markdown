"""Assemble the full DESIGN.md text (stream: synthesizer, phase GĐ6).

PUBLIC ENTRY POINT (frozen signature, called by synthesizer/synthesize.py):
    render_design_md(analysis, lang=..., examples=...) -> str
"""

from __future__ import annotations

from typing import Final

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import DesignAnalysis

#: "## " sections in the mandatory order (Google Labs DESIGN.md spec + Agent Prompt Guide last).
DESIGN_SECTIONS: Final = (
    "Overview",
    "Colors",
    "Typography",
    "Layout",
    "Elevation & Depth",
    "Shapes",
    "Components",
    "Do's and Don'ts",
    "Agent Prompt Guide",
)
SUPPORTED_LANGS: Final = ("en", "vi")


def render_design_md(analysis: DesignAnalysis, *, lang: str = "en", examples: bool = False) -> str:
    """Front matter (YAML between "---" lines) + "# <Site> — Design Reference" + reference note
    (source URL, extraction date = analysis.site.collected_at, pages analyzed, "reference only,
    not an official design system") + the sections of DESIGN_SECTIONS in order.

    Deterministic: the same analysis gives byte-identical output (no clock, no randomness).
    Section titles and token names stay English; prose follows `lang`.
    """
    raise NotImplementedYetError("Dựng DESIGN.md chưa được xây dựng (giai đoạn 6).")
