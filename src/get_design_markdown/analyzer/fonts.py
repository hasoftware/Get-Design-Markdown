"""Offline font knowledge base: Google Fonts membership, category, free substitutes
(stream: analyzer-foundations, phase GĐ4; used by typography.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import FontCategory


@dataclass(frozen=True)
class FontLookup:
    name: str
    is_google_font: bool = False
    category: FontCategory = "unknown"
    #: Freely available look-alikes, Google Fonts first (empty when the font itself is free).
    substitutes: tuple[str, ...] = field(default_factory=tuple)


def lookup_font(family: str) -> FontLookup:
    """Case-insensitive lookup of a family name; unknown fonts get category from the name
    (mono/serif hints) and generic substitutes for that category."""
    raise NotImplementedYetError("Bảng tra font chưa được xây dựng (giai đoạn 4).")
