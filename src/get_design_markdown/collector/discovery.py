"""Find important sub-pages from the navigation of a captured page (--pages N)
(stream: collector).
"""

from __future__ import annotations

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.raw import PageCapture


def discover_pages(page: PageCapture, *, limit: int) -> list[str]:
    """Up to `limit` absolute same-site URLs ranked by importance (header nav first); pure.

    Excludes the page itself, fragments, mailto/tel/javascript links, files (pdf, zip, images),
    login/sign-up/legal pages and duplicates after normalization.
    """
    raise NotImplementedYetError("Tìm trang con chưa được xây dựng (giai đoạn 7).")
