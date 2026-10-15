"""Run js/extract.js in the page: meta, CSS variables, computed styles of visible elements
(stream: collector — internal module, signatures may be refined by the collector stream).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from playwright.async_api import Page

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.raw import CssVariable, ElementSample


@dataclass
class DomExtraction:
    title: str = ""
    meta_description: str | None = None
    lang: str | None = None
    document_height: float = 0.0
    root_font_size_px: float = 16.0
    css_variables: list[CssVariable] = field(default_factory=list)
    elements_total: int = 0
    elements: list[ElementSample] = field(default_factory=list)


async def extract_dom(page: Page, *, max_elements: int) -> DomExtraction:
    """Evaluate js/extract.js and validate its result into models."""
    raise NotImplementedYetError("Trích xuất DOM chưa được xây dựng (giai đoạn 1).")


def parse_extraction(payload: dict[str, object]) -> DomExtraction:
    """Validate the JSON payload returned by extract.js (pure, unit-testable)."""
    raise NotImplementedYetError("Trích xuất DOM chưa được xây dựng (giai đoạn 1).")
