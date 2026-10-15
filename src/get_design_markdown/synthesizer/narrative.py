"""Rule-based prose for each DESIGN.md section (stream: synthesizer, phase GĐ6)."""

from __future__ import annotations

from dataclasses import dataclass

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import DesignAnalysis
from get_design_markdown.synthesizer.catalog import Phrases


@dataclass(frozen=True)
class Section:
    #: One of render.DESIGN_SECTIONS.
    title: str
    #: Markdown body without the "## " heading line.
    body: str


def build_sections(
    analysis: DesignAnalysis, phrases: Phrases, *, examples: bool = False
) -> list[Section]:
    """Sections in render.DESIGN_SECTIONS order; sections without data are omitted."""
    raise NotImplementedYetError("Phần mô tả chưa được xây dựng (giai đoạn 6).")
