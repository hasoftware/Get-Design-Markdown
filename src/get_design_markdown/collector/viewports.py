"""Screenshots and layout snapshots at 1440 / 768 / 390
(stream: collector — internal module, signatures may be refined by the collector stream).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from playwright.async_api import Page

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.raw import ColorScheme, LayoutSnapshot, Screenshot


async def measure_layout(page: Page, *, viewport_width: int) -> LayoutSnapshot:
    """Nav/hamburger/sidebar visibility, columns, scrollers, tap targets, container, heading."""
    raise NotImplementedYetError("Đo layout chưa được xây dựng (giai đoạn 1).")


async def capture_viewports(
    page: Page,
    *,
    page_name: str,
    out_dir: Path,
    widths: Sequence[int],
    color_scheme: ColorScheme = "light",
    max_screenshot_height: int = 8000,
) -> tuple[list[LayoutSnapshot], list[Screenshot]]:
    """Resize to each width, measure the layout and save a (height-capped) screenshot."""
    raise NotImplementedYetError("Chụp ảnh 3 viewport chưa được xây dựng (giai đoạn 1).")
