"""Capture CSS responses and parse them with tinycss2
(stream: collector — internal module, signatures may be refined by the collector stream).
"""

from __future__ import annotations

from playwright.async_api import Page

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.raw import MediaQuery, StylesheetInfo


class StylesheetRecorder:
    """Attach before navigation (page.on("response")) and collect every text/css response,
    including cross-origin ones whose cssRules are unreadable from JavaScript."""

    def __init__(self, page: Page) -> None:
        self.page = page

    async def finish(self) -> list[StylesheetInfo]:
        """Parse all recorded responses (+ inline <style> blocks) into StylesheetInfo."""
        raise NotImplementedYetError("Bắt stylesheet chưa được xây dựng (giai đoạn 2).")


def parse_stylesheet(
    url: str, css_text: str, *, status: int | None = None, inline: bool = False
) -> StylesheetInfo:
    """Parse CSS with tinycss2: :root/html variables, @media (nested), @font-face, rule count."""
    raise NotImplementedYetError("Phân tích stylesheet chưa được xây dựng (giai đoạn 2).")


def media_query_from_prelude(prelude: str) -> MediaQuery:
    """MediaQuery from an @media prelude (uses css_values.parse_media_query)."""
    raise NotImplementedYetError("Phân tích @media chưa được xây dựng (giai đoạn 2).")
