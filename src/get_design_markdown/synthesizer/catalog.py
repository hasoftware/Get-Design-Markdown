"""Phrase catalogs (synthesizer/phrases/en.yaml, vi.yaml) used by the narrative rules
(stream: synthesizer, phase GĐ6).
"""

from __future__ import annotations

from collections.abc import Mapping

from get_design_markdown.errors import NotImplementedYetError


class Phrases:
    """Lookup of phrase templates by dotted key ("overview.canvas.near_black")."""

    def __init__(self, lang: str, catalog: Mapping[str, object]) -> None:
        self.lang = lang
        self.catalog = catalog

    def get(self, key: str, **values: object) -> str:
        """Format the phrase `key` with str.format(**values); missing key -> KeyError."""
        raise NotImplementedYetError("Câu mẫu chưa được xây dựng (giai đoạn 6).")


def load_phrases(lang: str) -> Phrases:
    """Load synthesizer/phrases/<lang>.yaml (resources.resource_text); unknown lang ->
    GdmError subclass with a Vietnamese message listing en, vi."""
    raise NotImplementedYetError("Câu mẫu chưa được xây dựng (giai đoạn 6).")
