"""Stage 1 orchestration: capture a site into a RawCapture (stream: collector).

PUBLIC ENTRY POINT (frozen signature, called by pipeline.run_collect):
    collect_site(url, out_dir=..., options=..., progress=...) -> RawCapture

Everything else in the collector package is internal to the collector stream.
"""

from __future__ import annotations

from pathlib import Path

from get_design_markdown.console import ProgressCallback
from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.raw import CaptureOptions, RawCapture


async def collect_site(
    url: str,
    *,
    out_dir: Path,
    options: CaptureOptions,
    progress: ProgressCallback | None = None,
) -> RawCapture:
    """Capture `url` with Playwright Chromium and return the RawCapture.

    - `url` is already normalized (storage.normalize_url) and robots.txt for it was already
      checked by the caller; the caller also fills RawCapture.robots and writes raw.json.
    - Writes screenshots under out_dir/screenshots/ (storage.screenshot_filename()).
    - pages[0] is the main page; options.pages > 0 adds sub-pages found by
      discovery.discover_pages(); options.dark adds RawCapture.dark (main page re-captured with
      prefers-color-scheme: dark).
    - Calls progress(message) with short Vietnamese status texts.
    - Raises CollectError subclasses (errors.py) with Vietnamese messages for timeouts,
      bot blocks, login walls, navigation failures and a missing Chromium install.
    """
    raise NotImplementedYetError(
        "Phần thu thập bằng trình duyệt chưa được xây dựng (giai đoạn 1–2).",
        hint="Hiện mới có bước kiểm tra robots.txt.",
    )
