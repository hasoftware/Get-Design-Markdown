"""Playwright session, navigation, lazy-load scrolling and blocker detection
(stream: collector — internal module, signatures may be refined by the collector stream).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from playwright.async_api import BrowserContext, Page

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.raw import CaptureOptions, ColorScheme, LoadState


@dataclass(frozen=True)
class LoadResult:
    final_url: str
    status_code: int | None
    load_state: LoadState
    warnings: tuple[str, ...] = ()


@asynccontextmanager
async def open_context(
    options: CaptureOptions, *, color_scheme: ColorScheme = "light"
) -> AsyncIterator[BrowserContext]:
    """Launch headless Chromium and yield a context (viewport, UA, locale, color scheme)."""
    raise NotImplementedYetError("Trình duyệt chưa được xây dựng (giai đoạn 1).")
    yield  # pragma: no cover


async def load_page(page: Page, url: str, *, timeout_s: float) -> LoadResult:
    """goto + networkidle with fallback to load/domcontentloaded; raises CollectError."""
    raise NotImplementedYetError("Tải trang chưa được xây dựng (giai đoạn 1).")


async def scroll_through(page: Page, *, max_steps: int = 40) -> None:
    """Scroll to the bottom in viewport steps to trigger lazy-loading, then back to the top."""
    raise NotImplementedYetError("Cuộn trang chưa được xây dựng (giai đoạn 1).")


def detect_blocker(
    *, title: str, body_text: str, status_code: int | None, final_url: str
) -> str | None:
    """Return "bot" / "login" when the page is a bot challenge or sign-in wall, else None."""
    raise NotImplementedYetError("Phát hiện chặn bot chưa được xây dựng (giai đoạn 1).")
