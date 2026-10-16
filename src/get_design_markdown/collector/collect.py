"""Stage 1 orchestration: capture a site into a RawCapture.

PUBLIC ENTRY POINT (frozen signature, called by pipeline.run_collect):
    collect_site(url, out_dir=..., options=..., progress=...) -> RawCapture

Everything else in the collector package is internal.

Per page: load (load + networkidle, with fallbacks) -> blocker check -> scroll for lazy
content -> blur the focused element (autofocus fields are measured at rest) -> mark overlays
-> extract DOM styles + CSS variables -> hide overlays -> hover states -> layout snapshots and
screenshots at each width -> stylesheets from the network.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page
from playwright.async_api import Error as PlaywrightError

from get_design_markdown import __version__
from get_design_markdown.collector import robots
from get_design_markdown.collector.browser import (
    ensure_accessible,
    load_page,
    new_context,
    open_browser,
    release_focus,
    scroll_through,
)
from get_design_markdown.collector.discovery import discover_pages, unique_page_name
from get_design_markdown.collector.dom import extract_dom
from get_design_markdown.collector.hover import capture_hover_states
from get_design_markdown.collector.stylesheets import StylesheetRecorder, StylesheetRegistry
from get_design_markdown.collector.viewports import capture_viewports
from get_design_markdown.console import ProgressCallback, silent_progress
from get_design_markdown.errors import CollectError
from get_design_markdown.models.raw import (
    CaptureOptions,
    ColorScheme,
    HoverState,
    LayoutSnapshot,
    PageCapture,
    RawCapture,
    Screenshot,
)
from get_design_markdown.resources import resource_text
from get_design_markdown.storage import SCREENSHOTS_DIR, domain_of, page_slug

ROBOTS_TIMEOUT_S = 15.0
OVERLAY_KINDS = {"cookie": "banner cookie", "dialog": "hộp thoại", "backdrop": "lớp nền mờ"}
#: Names the collector gives its screenshots (storage.screenshot_filename): a page slug,
#: an optional "-dark", then the viewport width — "home-1440.png", "home-dark-1440.png".
SCREENSHOT_FILE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-\d{2,5}\.png$")


@dataclass(frozen=True)
class _PagePlan:
    """What to capture for one page."""

    url: str
    name: str
    color_scheme: ColorScheme = "light"
    hover: bool = True
    widths: tuple[int, ...] = ()
    measure: bool = True
    record_stylesheets: bool = True


def _overlay_warning(overlays: Sequence[dict[str, Any]]) -> str | None:
    if not overlays:
        return None
    kinds: list[str] = []
    for item in overlays:
        label = OVERLAY_KINDS.get(str(item.get("kind")), "lớp phủ")
        if label not in kinds:
            kinds.append(label)
    return (
        f"Phát hiện {len(overlays)} lớp phủ ({', '.join(kinds)}) — các phần tử của nó được "
        "đánh dấu in_overlay và được ẩn trước khi thu hover/chụp ảnh."
    )


def _truncation_warning(total: int, kept: int) -> str | None:
    if total <= kept:
        return None
    return f"Trang có {total} phần tử hiển thị — chỉ lấy mẫu {kept}."


def _prefixed(notify: ProgressCallback, prefix: str) -> ProgressCallback:
    def update(message: str) -> None:
        notify(prefix + message)

    return update


def _widths_label(widths: Sequence[int]) -> str:
    return "/".join(str(width) for width in widths)


async def _detect_overlays(page: Page) -> list[dict[str, Any]]:
    try:
        result = await page.evaluate(
            resource_text("collector", "js", "overlays.js"), {"mode": "detect"}
        )
    except PlaywrightError:
        return []
    return [dict(item) for item in result or [] if isinstance(item, dict)]


async def _hide_overlays(page: Page, overlays: Sequence[dict[str, Any]]) -> None:
    if not overlays:
        return
    unlock = any(item.get("kind") in ("dialog", "backdrop") for item in overlays)
    with suppress(PlaywrightError):
        await page.evaluate(
            resource_text("collector", "js", "overlays.js"),
            {"mode": "hide", "unlockScroll": unlock},
        )


async def capture_page(
    context: BrowserContext,
    plan: _PagePlan,
    *,
    out_dir: Path,
    options: CaptureOptions,
    registry: StylesheetRegistry | None,
    notify: ProgressCallback,
) -> PageCapture:
    """Capture one page in `context`; raises CollectError subclasses."""
    page = await context.new_page()
    recorder = StylesheetRecorder(page, page_name=plan.name) if plan.record_stylesheets else None
    warnings: list[str] = []
    try:
        notify(f"Tải trang {urlsplit(plan.url).path or '/'}…")
        load = await load_page(page, plan.url, timeout_s=options.timeout_s)
        warnings.extend(load.warnings)
        await ensure_accessible(page, load, requested_url=plan.url)

        notify("Cuộn để tải nội dung lazy-load…")
        await scroll_through(page)
        # Measure controls at rest: an autofocused field would report its focus ring.
        await release_focus(page)

        overlays = await _detect_overlays(page)
        notify("Trích xuất style…")
        extraction = await extract_dom(page, max_elements=options.max_elements)
        notify(f"Trích xuất style ({len(extraction.elements)} phần tử)…")
        for note in (
            _truncation_warning(extraction.elements_total, len(extraction.elements)),
            _overlay_warning(overlays),
        ):
            if note:
                warnings.append(note)
        await _hide_overlays(page, overlays)

        hover_states: list[HoverState] = []
        if plan.hover and (options.hover_buttons > 0 or options.hover_links > 0):
            notify("Thu trạng thái hover…")
            hover_states = await capture_hover_states(
                page,
                extraction.elements,
                max_buttons=options.hover_buttons,
                max_links=options.hover_links,
                warnings=warnings,
            )

        layouts: list[LayoutSnapshot] = []
        screenshots: list[Screenshot] = []
        if plan.widths:
            notify(f"Chụp ảnh {_widths_label(plan.widths)}…")
            await release_focus(page)  # a script may have focused a field since extraction
            layouts, screenshots = await capture_viewports(
                page,
                page_name=plan.name,
                out_dir=out_dir,
                widths=plan.widths,
                color_scheme=plan.color_scheme,
                measure=plan.measure,
                restore=(options.viewport_width, options.viewport_height),
                warnings=warnings,
            )

        if recorder is not None and registry is not None:
            registry.add(await recorder.finish_keyed())
            warnings.extend(recorder.warnings)

        return PageCapture(
            url=plan.url,
            final_url=load.final_url,
            name=plan.name,
            status_code=load.status_code,
            load_state=load.load_state,
            title=extraction.title,
            meta_description=extraction.meta_description,
            lang=extraction.lang,
            color_scheme=plan.color_scheme,
            document_height=extraction.document_height,
            root_font_size_px=extraction.root_font_size_px,
            css_variables=extraction.css_variables,
            elements_total=extraction.elements_total,
            elements=extraction.elements,
            hover_states=hover_states,
            layouts=layouts,
            screenshots=screenshots,
            warnings=warnings,
        )
    finally:
        with suppress(PlaywrightError):
            await page.close()


def referenced_screenshots(capture: RawCapture) -> set[str]:
    """File names (inside screenshots/) of every screenshot the capture lists; pure."""
    pages = [*capture.pages, *([capture.dark] if capture.dark is not None else [])]
    return {PurePosixPath(shot.path).name for page in pages for shot in page.screenshots}


def remove_stale_screenshots(out_dir: Path, keep: Collection[str]) -> list[str]:
    """Delete collector screenshots in out_dir/screenshots/ that `keep` does not name.

    A re-collect with other sub-pages (or a width that failed this time) would otherwise
    leave PNGs from an earlier run next to a raw.json that no longer references them. Only
    files named like collector screenshots are touched; returns the removed names.
    """
    folder = out_dir / SCREENSHOTS_DIR
    if not folder.is_dir():
        return []
    removed: list[str] = []
    for path in sorted(folder.iterdir()):
        if path.name in keep or not SCREENSHOT_FILE_RE.match(path.name):
            continue
        try:
            if path.is_file():
                path.unlink()
                removed.append(path.name)
        except OSError:
            continue
    return removed


async def _robots_allows(url: str, *, timeout_s: float) -> tuple[bool, str | None]:
    check = await robots.check_robots(url, timeout_s=min(timeout_s, ROBOTS_TIMEOUT_S))
    if check.allowed is False:
        return False, check.note
    return True, None


async def _capture_subpages(
    context: BrowserContext,
    main: PageCapture,
    *,
    out_dir: Path,
    options: CaptureOptions,
    registry: StylesheetRegistry,
    notify: ProgressCallback,
    warnings: list[str],
) -> list[PageCapture]:
    urls = discover_pages(main, limit=options.pages)
    if len(urls) < options.pages:
        warnings.append(
            f"Chỉ tìm thấy {len(urls)} trang con phù hợp trong navigation "
            f"(yêu cầu {options.pages})."
        )
    pages: list[PageCapture] = []
    taken = [main.name]
    for position, url in enumerate(urls, start=1):
        path = urlsplit(url).path or "/"
        notify(f"Trang con {position}/{len(urls)}: {path}…")
        allowed, note = await _robots_allows(url, timeout_s=options.timeout_s)
        if not allowed:
            warnings.append(f"Bỏ qua trang con {path}: {note}")
            continue
        name = unique_page_name(url, taken)
        plan = _PagePlan(url=url, name=name, widths=tuple(options.screenshot_widths))
        try:
            capture = await capture_page(
                context,
                plan,
                out_dir=out_dir,
                options=options,
                registry=registry,
                notify=_prefixed(notify, f"Trang con {position}/{len(urls)}: "),
            )
        except CollectError as error:
            warnings.append(f"Không thu thập được trang con {path}: {error.message}")
            continue
        if urlsplit(capture.final_url).hostname != urlsplit(main.final_url).hostname:
            warnings.append(
                f"Trang con {path} chuyển hướng sang {capture.final_url} (tên miền khác) — "
                "đã bỏ qua."
            )
            continue
        taken.append(name)
        pages.append(capture)
    return pages


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
    - Writes screenshots under out_dir/screenshots/ (storage.screenshot_filename()) and, once
      the capture succeeded, removes screenshots of earlier runs that it does not reference.
    - pages[0] is the main page; options.pages > 0 adds sub-pages found by
      discovery.discover_pages(); options.dark adds RawCapture.dark (main page re-captured with
      prefers-color-scheme: dark).
    - Calls progress(message) with short Vietnamese status texts.
    - Raises CollectError subclasses (errors.py) with Vietnamese messages for timeouts,
      bot blocks, login walls, navigation failures and a missing Chromium install.
    """
    notify = progress or silent_progress
    warnings: list[str] = []
    registry = StylesheetRegistry()
    main_name = page_slug(url)
    notify("Mở trình duyệt…")
    async with open_browser() as session:
        context = await new_context(session, options, color_scheme="light")
        try:
            main = await capture_page(
                context,
                _PagePlan(url=url, name=main_name, widths=tuple(options.screenshot_widths)),
                out_dir=out_dir,
                options=options,
                registry=registry,
                notify=notify,
            )
            subpages: list[PageCapture] = []
            if options.pages > 0:
                subpages = await _capture_subpages(
                    context,
                    main,
                    out_dir=out_dir,
                    options=options,
                    registry=registry,
                    notify=notify,
                    warnings=warnings,
                )
        finally:
            with suppress(PlaywrightError):
                await context.close()

        dark: PageCapture | None = None
        if options.dark:
            notify("Thu bản dark mode…")
            dark_context = await new_context(session, options, color_scheme="dark")
            try:
                dark = await capture_page(
                    dark_context,
                    _PagePlan(
                        url=url,
                        name=main_name,
                        color_scheme="dark",
                        hover=False,
                        widths=(options.viewport_width,),
                        measure=False,
                        record_stylesheets=False,
                    ),
                    out_dir=out_dir,
                    options=options,
                    registry=None,
                    notify=_prefixed(notify, "Dark mode: "),
                )
            except CollectError as error:
                warnings.append(f"Không thu được bản dark mode: {error.message}")
            finally:
                with suppress(PlaywrightError):
                    await dark_context.close()

    capture = RawCapture(
        tool_version=__version__,
        source_url=url,
        domain=domain_of(url),
        collected_at=datetime.now(UTC),
        options=options,
        pages=[main, *subpages],
        dark=dark,
        stylesheets=registry.sheets,
        warnings=warnings,
    )
    removed = remove_stale_screenshots(out_dir, referenced_screenshots(capture))
    if removed:
        notify(f"Đã xóa {len(removed)} ảnh chụp cũ không còn dùng…")
    return capture
