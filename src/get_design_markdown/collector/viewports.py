"""Screenshots and layout snapshots at 1440 / 768 / 390."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from get_design_markdown.collector.browser import scroll_through, wait_quietly
from get_design_markdown.models.raw import ColorScheme, LayoutSnapshot, Screenshot
from get_design_markdown.resources import resource_text
from get_design_markdown.storage import screenshot_filename

#: Viewport heights of the reference devices (desktop, tablet portrait, phone).
VIEWPORT_HEIGHTS = {1440: 900, 768: 1024, 390: 844}
#: Pause after resizing before measuring (milliseconds).
RESIZE_SETTLE_MS = 400
#: networkidle cap after resizing (seconds; a timeout is not an error).
RESIZE_IDLE_S = 2.0
SCREENSHOT_TIMEOUT_MS = 30_000

_DOCUMENT_HEIGHT_JS = (
    "() => Math.max(document.documentElement.scrollHeight, "
    "document.body ? document.body.scrollHeight : 0)"
)


def viewport_height_for(width: int) -> int:
    """Height paired with a capture width (900 / 1024 / 844 for 1440 / 768 / 390); pure."""
    if width in VIEWPORT_HEIGHTS:
        return VIEWPORT_HEIGHTS[width]
    if width >= 1024:
        return 900
    if width >= 600:
        return 1024
    return 844


def screenshot_name(page_name: str, width: int, color_scheme: ColorScheme = "light") -> str:
    """ "home-1440", "home-dark-1440"; pure."""
    scheme = "-dark" if color_scheme == "dark" else ""
    return f"{page_name}{scheme}-{width}"


def measurement_order(widths: Sequence[int]) -> list[tuple[int, int]]:
    """(position in `widths`, width) pairs, widest first (stable for equal widths); pure."""
    return sorted(enumerate(widths), key=lambda item: (-item[1], item[0]))


def layout_from_payload(payload: dict[str, Any], *, viewport_width: int) -> LayoutSnapshot:
    """LayoutSnapshot from the js/layout.js result; pure."""
    data = {key: value for key, value in payload.items() if key in LayoutSnapshot.model_fields}
    data["viewport_width"] = viewport_width
    return LayoutSnapshot.model_validate(data)


async def measure_layout(page: Page, *, viewport_width: int) -> LayoutSnapshot:
    """Nav/hamburger/sidebar visibility, columns, scrollers, tap targets, container, heading."""
    payload = await page.evaluate(resource_text("collector", "js", "layout.js"))
    return layout_from_payload(dict(payload), viewport_width=viewport_width)


async def take_screenshot(
    page: Page,
    *,
    path: Path,
    width: int,
    max_height: int,
) -> float:
    """Full-page PNG capped at max_height (clip); returns the document height."""
    height = float(await page.evaluate(_DOCUMENT_HEIGHT_JS))
    path.parent.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "path": str(path),
        "full_page": True,
        "animations": "disabled",
        "timeout": SCREENSHOT_TIMEOUT_MS,
    }
    if height > max_height:
        options["clip"] = {"x": 0, "y": 0, "width": width, "height": max_height}
    await page.screenshot(**options)
    return height


async def capture_viewports(
    page: Page,
    *,
    page_name: str,
    out_dir: Path,
    widths: Sequence[int],
    color_scheme: ColorScheme = "light",
    max_screenshot_height: int = 8000,
    measure: bool = True,
    restore: tuple[int, int] = (1440, 900),
    warnings: list[str] | None = None,
) -> tuple[list[LayoutSnapshot], list[Screenshot]]:
    """Resize to each width, measure the layout and save a (height-capped) screenshot.

    Widths are processed from the widest down (js/layout.js follows the column grids it
    found at a wider width as they stack); results keep the order of `widths`. Lazy content
    is re-triggered at each width with a quick scroll pass limited to the screenshot height.
    Failures at one width become warnings, not errors.
    """
    layouts: list[tuple[int, LayoutSnapshot]] = []
    shots: list[tuple[int, Screenshot]] = []
    notes = warnings if warnings is not None else []
    truncated: list[tuple[int, str]] = []
    current = page.viewport_size
    for position, width in measurement_order(widths):
        height = viewport_height_for(width)
        try:
            if current is None or current["width"] != width or current["height"] != height:
                await page.set_viewport_size({"width": width, "height": height})
                current = {"width": width, "height": height}
                await page.wait_for_timeout(RESIZE_SETTLE_MS)
            await scroll_through(
                page,
                max_steps=max(int(max_screenshot_height / (height * 0.9)) + 1, 2),
                step_fraction=0.9,
                step_delay_ms=120,
                max_distance_px=max_screenshot_height,
                idle_timeout_s=0,
                settle_ms=150,
                bottom_rechecks=0,
            )
            await wait_quietly(page, timeout_s=RESIZE_IDLE_S)
            if measure:
                layouts.append((position, await measure_layout(page, viewport_width=width)))
            relative = screenshot_filename(page_name, width, color_scheme)
            document_height = await take_screenshot(
                page, path=out_dir / relative, width=width, max_height=max_screenshot_height
            )
        except PlaywrightError as error:
            first_line = str(error).strip().splitlines()[0] if str(error).strip() else ""
            notes.append(f"Không chụp được ảnh ở bề rộng {width}px: {first_line}")
            continue
        if document_height > max_screenshot_height:
            truncated.append((position, f"{document_height:.0f}px ở {width}px"))
        shots.append(
            (
                position,
                Screenshot(
                    name=screenshot_name(page_name, width, color_scheme),
                    viewport_width=width,
                    viewport_height=height,
                    color_scheme=color_scheme,
                    path=relative,
                    full_page=True,
                ),
            )
        )
    if truncated:
        notes.append(
            f"Trang dài ({', '.join(note for _, note in sorted(truncated))}) — ảnh chụp chỉ lấy "
            f"{max_screenshot_height}px đầu trang."
        )
    if current is not None and (current["width"], current["height"]) != restore:
        with suppress(PlaywrightError):
            await page.set_viewport_size({"width": restore[0], "height": restore[1]})
    layouts.sort(key=lambda pair: pair[0])
    shots.sort(key=lambda pair: pair[0])
    return [layout for _, layout in layouts], [shot for _, shot in shots]
