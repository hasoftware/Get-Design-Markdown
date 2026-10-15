"""Collector tests with Playwright Chromium on the local fixture site (marker: browser).

One module-scoped capture of the Acme Cloud fixture site (dark mode + sub-pages) feeds most
tests; the special pages (challenge, login, never-idle, lazy, timeouts) get small captures
without screenshots or hover.
"""

from __future__ import annotations

import asyncio
import socket
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from get_design_markdown.collector import browser, robots, viewports
from get_design_markdown.collector.collect import collect_site
from get_design_markdown.errors import (
    BotBlockedError,
    LoginRequiredError,
    NavigationError,
    PageTimeoutError,
)
from get_design_markdown.models.raw import (
    CaptureOptions,
    ElementSample,
    LayoutSnapshot,
    PageCapture,
    RawCapture,
    RobotsCheck,
    Screenshot,
)
from tests.fixture_data import load_site_contract

pytestmark = pytest.mark.browser

QUICK = CaptureOptions(screenshot_widths=[], hover_buttons=0, hover_links=0, timeout_s=20)
#: Left in screenshots/ before the module capture: removed (stale) or kept (not ours).
STALE_FILES = ["contact-sales-1440.png", "contact-sales-390.png"]
KEPT_FILES = ["notes.txt"]


def _capture(url: str, out_dir: Path, options: CaptureOptions = QUICK) -> RawCapture:
    return asyncio.run(collect_site(url, out_dir=out_dir, options=options))


@pytest.fixture(scope="module")
def full(
    site_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[RawCapture, Path]]:
    """The fixture site with --dark --pages 3; robots.txt 'forbids' about.html.

    The output folder already holds screenshots of an earlier run (a sub-page this run will
    not capture) plus a file that is not a collector screenshot.
    """
    out_dir = tmp_path_factory.mktemp("acme")
    (out_dir / "screenshots").mkdir()
    for leftover in STALE_FILES + KEPT_FILES:
        (out_dir / "screenshots" / leftover).write_bytes(b"old")
    messages: list[str] = []

    async def fake_robots(url: str, **_: object) -> RobotsCheck:
        allowed = not url.endswith("/about.html")
        note = "ok" if allowed else "robots.txt không cho phép /about.html."
        return RobotsCheck(robots_url=url, allowed=allowed, note=note)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(robots, "check_robots", fake_robots)
        raw = asyncio.run(
            collect_site(
                site_url,
                out_dir=out_dir,
                options=CaptureOptions(dark=True, pages=3),
                progress=messages.append,
            )
        )
    assert messages[0] == "Mở trình duyệt…"
    yield raw, out_dir


@pytest.fixture(scope="module")
def home(full: tuple[RawCapture, Path]) -> PageCapture:
    return full[0].pages[0]


def _by_class(page: PageCapture, name: str) -> list[ElementSample]:
    return [element for element in page.elements if name in element.classes]


def _first(page: PageCapture, name: str) -> ElementSample:
    found = _by_class(page, name)
    assert found, f"no element with class {name}"
    return found[0]


# ─── Main page ───────────────────────────────────────────────────────────────


def test_main_page_basics(full: tuple[RawCapture, Path], home: PageCapture, site_url: str) -> None:
    raw = full[0]
    assert raw.source_url == site_url
    assert raw.domain.startswith("127.0.0.1_")
    assert raw.options is not None and raw.options.dark and raw.options.pages == 3
    assert home.name == "home" and home.status_code == 200
    assert home.load_state == "networkidle"
    assert home.title == "Acme Cloud — Ship cloud apps without the ops"
    assert home.meta_description is not None and home.meta_description.startswith("Acme Cloud")
    assert home.lang == "en"
    assert home.root_font_size_px == 16
    assert home.document_height > 1500


def test_element_tree_is_consistent(home: PageCapture) -> None:
    elements = home.elements
    assert 60 <= len(elements) <= 3000
    assert home.elements_total == len(elements)
    assert [elements[0].tag, elements[1].tag] == ["html", "body"]
    assert elements[0].parent_index is None
    children: dict[int, int] = {}
    for position, element in enumerate(elements):
        assert element.index == position
        if position == 0:
            continue
        parent = element.parent_index
        assert parent is not None and parent < position
        assert element.depth == elements[parent].depth + 1
        children[parent] = children.get(parent, 0) + 1
    for element in elements:
        assert element.child_count == children.get(element.index, 0)


def test_elements_carry_computed_styles_and_context(home: PageCapture) -> None:
    title = next(element for element in home.elements if element.tag == "h1")
    assert title.text == "Ship cloud apps without the ops"
    assert title.has_own_text
    assert title.styles.font_size == "64px" and title.styles.font_weight == "700"
    assert '"Space Grotesk"' in title.styles.font_family
    assert title.landmark == "main"

    primary = _first(home, "btn-primary")
    assert primary.href is not None and primary.href.endswith("/signup.html")
    assert primary.styles.background_color == "rgb(79, 70, 229)"
    assert primary.styles.border_radius == ("8px", "8px", "8px", "8px")
    assert primary.landmark == "header"

    header = _first(home, "site-header")
    assert header.styles.position == "sticky"
    assert header.styles.backdrop_filter == "blur(12px)"
    assert header.styles.border_width == ("0px", "0px", "1px", "0px")

    email = next(element for element in home.elements if element.tag == "input")
    assert email.input_type == "email"
    assert email.placeholder_color == "rgb(148, 163, 184)"
    assert email.landmark == "form"

    secondary = _first(home, "btn-secondary")
    assert secondary.input_type == "submit" and secondary.text == "Get updates"

    nav_links = [e for e in _by_class(home, "nav-link") if e.landmark == "nav"]
    assert [link.text for link in nav_links] == ["Product", "Pricing", "Docs", "Company"]
    footer_links = _by_class(home, "footer-link")
    assert footer_links and all(link.landmark == "footer" for link in footer_links)
    cards = _by_class(home, "card")
    assert len(cards) == 3
    assert all(card.styles.box_shadow.count("rgba(15, 23, 42") == 2 for card in cards)


def test_css_variables_are_resolved(home: PageCapture) -> None:
    variables = {variable.name: variable for variable in home.css_variables}
    assert variables["--color-primary"].declared == "#4f46e5"
    assert variables["--color-primary"].resolved == "rgb(79, 70, 229)"
    assert variables["--space-4"].resolved == "16px"
    assert variables["--nav-bg"].resolved == "rgba(255, 255, 255, 0.8)"
    assert variables["--shadow-card"].resolved == (
        "rgba(15, 23, 42, 0.12) 0px 8px 24px -4px, rgba(15, 23, 42, 0.06) 0px 2px 6px 0px"
    )
    assert variables["--brand-gradient"].resolved == (
        "linear-gradient(135deg, rgb(99, 102, 241), rgb(236, 72, 153))"
    )
    assert variables["--gutter"].resolved == "24px"  # @media (min-width: 640px) applies
    assert variables["--header-height"].resolved == "64px"  # inline <style>
    assert all(variable.source == "root" for variable in variables.values())


def test_cookie_banner_is_an_overlay(home: PageCapture) -> None:
    banner = next(element for element in home.elements if element.id == "cookie-consent")
    assert banner.in_overlay and banner.landmark == "dialog"
    inside = [e for e in home.elements if e.parent_index == banner.index]
    assert inside and all(element.in_overlay for element in inside)
    assert sum(element.in_overlay for element in home.elements) == 1 + len(inside)
    assert any("in_overlay" in warning for warning in home.warnings)
    overlay_indexes = {element.index for element in home.elements if element.in_overlay}
    assert not overlay_indexes & {state.element_index for state in home.hover_states}


def test_stylesheets_include_cross_origin_css(full: tuple[RawCapture, Path]) -> None:
    raw = full[0]
    urls = [sheet.url for sheet in raw.stylesheets]
    assert len(urls) == len(set(urls))
    theme = next(sheet for sheet in raw.stylesheets if sheet.url.endswith("/theme.css"))
    assert theme.url.startswith("http://localhost:")  # cross-origin: cssRules unreadable
    assert theme.status == 200 and theme.parse_errors == 1
    assert any(media.prefers_color_scheme == "dark" for media in theme.media_queries)
    widths = {
        media.min_width_px
        for sheet in raw.stylesheets
        for media in sheet.media_queries
        if media.min_width_px is not None
    }
    assert {640, 768, 1024, 1280} <= widths
    styles = next(sheet for sheet in raw.stylesheets if sheet.url.endswith("/styles.css"))
    assert len(styles.font_faces) == 3
    inline = [sheet for sheet in raw.stylesheets if sheet.inline]
    assert inline and inline[0].url == "inline:home#1"
    assert any(variable.name == "--header-height" for variable in inline[0].variables)
    assert not any("gdm" in sheet.url for sheet in raw.stylesheets)


def test_hover_states(home: PageCapture) -> None:
    contract = load_site_contract()
    expected_hex = contract.hover["button-primary"]["backgroundColor"]
    assert expected_hex == "#4338ca"
    by_index = {element.index: element for element in home.elements}
    states = {state.element_index: state for state in home.hover_states}
    primary = [i for i in states if "btn-primary" in by_index[i].classes]
    assert primary
    for index in primary:
        assert states[index].kind == "button"
        assert states[index].changed["background_color"] == (
            "rgb(79, 70, 229)",
            "rgb(67, 56, 202)",
        )
    outline = next(i for i in states if "btn-outline" in by_index[i].classes)
    assert states[outline].changed["border_color"] == ("rgb(226, 232, 240)", "rgb(203, 213, 225)")
    ghost = next(i for i in states if "btn-ghost" in by_index[i].classes)
    assert states[ghost].changed["background_color"][1] == "rgb(238, 242, 255)"
    text_link = next(i for i in states if "text-link" in by_index[i].classes)
    assert states[text_link].kind == "link"
    assert states[text_link].changed["text_decoration_line"] == ("none", "underline")
    assert "border_color" not in states[text_link].changed  # currentColor echo filtered
    assert len(home.hover_states) >= 8


def test_layout_snapshots(home: PageCapture) -> None:
    layouts = {layout.viewport_width: layout for layout in home.layouts}
    assert sorted(layouts) == [390, 768, 1440]
    desktop, tablet, phone = layouts[1440], layouts[768], layouts[390]
    assert desktop.viewport_height == 900 and tablet.viewport_height == 1024
    assert phone.viewport_height == 844
    assert desktop.nav_visible and not desktop.hamburger_visible
    assert desktop.visible_nav_links == 5  # 4 nav links + "Sign in"
    assert tablet.hamburger_visible and not tablet.nav_visible
    assert phone.hamburger_visible and not phone.nav_visible
    assert [desktop.main_columns, tablet.main_columns, phone.main_columns] == [3, 2, 1]
    assert [desktop.max_heading_px, tablet.max_heading_px, phone.max_heading_px] == [64, 48, 40]
    assert desktop.container_width_px == 1200 and desktop.gutter_px == 24
    assert tablet.container_width_px == 768 and tablet.gutter_px == 24
    assert phone.container_width_px == 390 and phone.gutter_px == 20
    assert not any(layout.sidebar_visible for layout in layouts.values())
    assert all(layout.horizontal_scrollers == 0 for layout in layouts.values())
    assert desktop.min_tap_target_px is not None and desktop.min_tap_target_px >= 16
    assert phone.document_height > desktop.document_height


def _png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def test_screenshots(full: tuple[RawCapture, Path], home: PageCapture) -> None:
    out_dir = full[1]
    names = [shot.name for shot in home.screenshots]
    assert names == ["home-1440", "home-768", "home-390"]
    for shot in home.screenshots:
        assert shot.path == f"screenshots/{shot.name}.png" and shot.full_page
        width, height = _png_size(out_dir / shot.path)
        assert width == shot.viewport_width
        assert height >= shot.viewport_height or height >= home.document_height - 1
    dark = full[0].dark
    assert dark is not None
    assert [shot.path for shot in dark.screenshots] == ["screenshots/home-dark-1440.png"]
    assert (out_dir / "screenshots" / "home-dark-1440.png").is_file()


def test_screenshots_folder_matches_the_capture(full: tuple[RawCapture, Path]) -> None:
    raw, out_dir = full
    pages = [*raw.pages, *([raw.dark] if raw.dark else [])]
    listed = {shot.path.removeprefix("screenshots/") for page in pages for shot in page.screenshots}
    on_disk = {path.name for path in (out_dir / "screenshots").iterdir()}
    assert on_disk == listed | set(KEPT_FILES)
    assert not set(STALE_FILES) & on_disk


def test_dark_capture(full: tuple[RawCapture, Path]) -> None:
    dark = full[0].dark
    assert dark is not None and dark.color_scheme == "dark"
    contract = load_site_contract()
    assert contract.dark_palette["canvas"] == "#0b1020"
    body = dark.elements[1]
    assert body.tag == "body" and body.styles.background_color == "rgb(11, 16, 32)"
    variables = {variable.name: variable for variable in dark.css_variables}
    assert variables["--color-primary"].resolved == "rgb(129, 140, 248)"
    assert dark.hover_states == [] and dark.layouts == []


def test_sub_pages(full: tuple[RawCapture, Path]) -> None:
    raw = full[0]
    names = [page.name for page in raw.pages]
    assert names == ["home", "pricing", "docs"]
    assert len(set(names)) == len(names)
    assert any("about.html" in warning and "robots" in warning for warning in raw.warnings)
    pricing = raw.pages[1]
    assert pricing.final_url.endswith("/pricing.html") and pricing.title.startswith("Pricing")
    assert [shot.name for shot in pricing.screenshots] == [
        "pricing-1440",
        "pricing-768",
        "pricing-390",
    ]
    docs = raw.pages[2]
    assert docs.layouts[0].sidebar_visible is False  # the docs aside is not pinned/tall
    # Shared stylesheets are recorded once.
    assert sum(sheet.url.endswith("/styles.css") for sheet in raw.stylesheets) == 1


# ─── Limits and special pages ────────────────────────────────────────────────


def test_max_elements_keeps_ancestors(site_url: str, tmp_path: Path) -> None:
    raw = _capture(site_url, tmp_path, QUICK.model_copy(update={"max_elements": 20}))
    page = raw.pages[0]
    assert page.elements_total > 20
    assert len(page.elements) <= 20
    assert [page.elements[0].tag, page.elements[1].tag] == ["html", "body"]
    for element in page.elements[1:]:
        assert element.parent_index is not None and element.parent_index < element.index
    tags = {element.tag for element in page.elements}
    assert {"header", "nav", "main", "footer"} <= tags
    assert any("chỉ lấy mẫu" in warning for warning in page.warnings)
    assert page.screenshots == [] and page.hover_states == []


def test_never_idle_page_falls_back_to_load(
    site_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(browser, "NETWORK_IDLE_TIMEOUT_S", 1.5)
    monkeypatch.setattr(browser, "SCROLL_IDLE_TIMEOUT_S", 0.5)
    page = _capture(site_url + "never-idle.html", tmp_path).pages[0]
    assert page.load_state == "load"
    assert any("networkidle" in warning for warning in page.warnings)
    assert page.title.startswith("Live status")


def test_lazy_sections_are_captured_after_scrolling(site_url: str, tmp_path: Path) -> None:
    page = _capture(site_url + "lazy.html", tmp_path).pages[0]
    texts = {element.text for element in page.elements if element.tag == "h2"}
    assert {"Lazy section 1", "Lazy section 2", "Lazy section 3"} <= texts


def test_bot_challenge_raises(site_url: str, tmp_path: Path) -> None:
    with pytest.raises(BotBlockedError) as caught:
        _capture(site_url + "challenge.html?status=403", tmp_path)
    assert "403" in caught.value.message


def test_login_wall_raises(site_url: str, tmp_path: Path) -> None:
    with pytest.raises(LoginRequiredError, match="đăng nhập"):
        _capture(site_url + "login.html", tmp_path)


def test_missing_page_raises(site_url: str, tmp_path: Path) -> None:
    with pytest.raises(NavigationError, match="404"):
        _capture(site_url + "missing-page.html", tmp_path)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def test_unreachable_host_raises(tmp_path: Path) -> None:
    with pytest.raises(NavigationError) as caught:
        _capture(f"http://127.0.0.1:{_free_port()}/", tmp_path)
    assert "ERR_CONNECTION_REFUSED" in caught.value.message
    assert caught.value.hint


def test_unsafe_port_raises_with_a_hint(tmp_path: Path) -> None:
    with pytest.raises(NavigationError) as caught:
        _capture("http://127.0.0.1:9/", tmp_path)
    assert "ERR_UNSAFE_PORT" in caught.value.message
    assert caught.value.hint is not None and "cổng" in caught.value.hint


# A card grid (3 / 2 / 1 columns) whose cards hold 2-column internals, a 4-up stats row that
# stays 2 columns on phones, and a big aria-hidden 2-column mock-up. At 390 px the nested
# internals and the mock-up each outweigh the stacked cards once the stats are added, so both
# exclusions are needed; at 1440 px the widest row (4 stats) is not the main grid.
GRID_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><style>
  * { box-sizing: border-box; }
  body { margin: 0; font: 16px/1.5 sans-serif; }
  main { max-width: 1232px; margin: 0 auto; padding: 0 16px; }
  section { margin: 0 0 48px; }
  .cards, .stats, .mock { display: grid; gap: 16px; }
  .cards { grid-template-columns: 1fr; }
  .card { padding: 8px; border: 0; }
  .inner { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .inner > div { height: 220px; }
  .stats { grid-template-columns: 1fr 1fr; }
  .stats > div { height: 80px; }
  .mock { grid-template-columns: 1fr 1fr; }
  .mock > div { height: 700px; }
  @media (min-width: 600px) { .cards { grid-template-columns: 1fr 1fr; } }
  @media (min-width: 1000px) {
    .cards { grid-template-columns: repeat(3, 1fr); }
    .stats { grid-template-columns: repeat(4, 1fr); }
  }
</style></head><body><main>
  <section class="cards">
    <article class="card"><div class="inner"><div>Deploy</div><div>Fast</div></div></article>
    <article class="card"><div class="inner"><div>Scale</div><div>Wide</div></div></article>
    <article class="card"><div class="inner"><div>Observe</div><div>Deep</div></div></article>
  </section>
  <section class="stats"><div>99.99%</div><div>120ms</div><div>35+</div><div>24/7</div></section>
  <section class="mock" aria-hidden="true"><div>Inbox</div><div>Issue</div></section>
</main></body></html>"""


def test_main_columns_follow_the_dominant_grid(tmp_path: Path) -> None:
    async def run() -> tuple[list[LayoutSnapshot], list[Screenshot]]:
        async with browser.open_context(QUICK) as context:
            page = await context.new_page()
            await page.set_content(GRID_PAGE)
            # Narrowest first on purpose: the widest width must still be measured first.
            return await viewports.capture_viewports(
                page, page_name="grid", out_dir=tmp_path, widths=[390, 768, 1440]
            )

    layouts, shots = asyncio.run(run())
    assert [layout.viewport_width for layout in layouts] == [390, 768, 1440]
    assert [shot.viewport_width for shot in shots] == [390, 768, 1440]
    assert [layout.main_columns for layout in layouts] == [1, 2, 3]


def test_slow_page_times_out(site_url: str, tmp_path: Path) -> None:
    options = QUICK.model_copy(update={"timeout_s": 1.0})
    with pytest.raises(PageTimeoutError) as caught:
        _capture(site_url + "index.html?delay=5", tmp_path, options)
    assert caught.value.hint is not None and "GDM_TIMEOUT" in caught.value.hint


def test_probe_reports_password_fields(site_url: str) -> None:
    async def run() -> dict[str, Any]:
        async with browser.open_context(QUICK) as context:
            page = await context.new_page()
            await page.goto(site_url + "login.html")
            probe = await browser.probe_page(page)
            return {
                "title": probe.title,
                "passwords": probe.password_inputs,
                "texts": probe.text_elements,
            }

    result = asyncio.run(run())
    assert result["title"].startswith("Sign in")
    assert result["passwords"] == 1 and 0 < result["texts"] < 40
