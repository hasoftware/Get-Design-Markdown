"""preview.html in a real Chromium (file://, network blocked): theme toggle, hover, mobile."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, Route, sync_playwright

from get_design_markdown.exporters.preview import render_preview
from tests.fixture_data import load_analysis_sample

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def preview_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("preview") / "preview.html"
    path.write_text(render_preview(load_analysis_sample()), encoding="utf-8")
    return path.resolve().as_uri()


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        try:
            yield instance
        finally:
            instance.close()


class Opener:
    """Loads a URI offline (only file:// requests pass) and records page errors."""

    def __init__(self, browser: Browser) -> None:
        self.browser = browser
        self.errors: list[str] = []
        self.pages: list[Page] = []

    def __call__(self, uri: str, width: int = 1440) -> Page:
        context = self.browser.new_context(viewport={"width": width, "height": 900})
        context.route("**/*", _offline)
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(uri)
        self.pages.append(page)
        return page


def _offline(route: Route) -> None:
    if route.request.url.startswith("file:"):
        route.continue_()
    else:
        route.abort()


@pytest.fixture
def open_page(browser: Browser) -> Iterator[Opener]:
    opener = Opener(browser)
    yield opener
    for page in opener.pages:
        page.context.close()


def _background(page: Page, selector: str) -> str:
    return str(
        page.evaluate(
            "selector => getComputedStyle(document.querySelector(selector)).backgroundColor",
            selector,
        )
    )


def test_theme_toggle_switches_and_persists(open_page: Opener, preview_uri: str) -> None:
    page = open_page(preview_uri)
    assert _background(page, "body") == "rgb(255, 255, 255)"
    toggle = page.locator("#theme-toggle")
    assert toggle.get_attribute("aria-pressed") == "false"
    toggle.click()
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    assert _background(page, "body") == "rgb(11, 16, 32)"
    assert toggle.get_attribute("aria-pressed") == "true"
    # Component stages keep the default capture's colors.
    assert _background(page, ".pv-stage") == "rgb(255, 255, 255)"
    page.reload()
    assert _background(page, "body") == "rgb(11, 16, 32)"
    page.locator("#theme-toggle").click()
    assert _background(page, "body") == "rgb(255, 255, 255)"
    assert open_page.errors == []


def test_components_have_real_hover_states(open_page: Opener, preview_uri: str) -> None:
    page = open_page(preview_uri)
    button = page.locator("#cmp-button-primary button").first
    assert button.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(79, 70, 229)"
    assert button.evaluate("el => getComputedStyle(el).borderTopLeftRadius") == "8px"
    button.hover()
    page.wait_for_timeout(250)
    assert button.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(67, 56, 202)"
    twin = page.locator("#cmp-button-primary .is-hover")
    assert twin.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(67, 56, 202)"
    placeholder = page.locator("#cmp-text-input input").first.evaluate(
        "el => getComputedStyle(el, '::placeholder').color"
    )
    assert placeholder == "rgb(148, 163, 184)"
    assert open_page.errors == []


@pytest.mark.parametrize("width", [390, 768])
def test_no_horizontal_scroll_on_small_screens(
    open_page: Opener, preview_uri: str, width: int
) -> None:
    page = open_page(preview_uri, width)
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert open_page.errors == []


def test_page_renders_without_dark_data(open_page: Opener, tmp_path: Path) -> None:
    analysis = load_analysis_sample()
    analysis.dark = None
    path = tmp_path / "preview.html"
    path.write_text(render_preview(analysis), encoding="utf-8")
    page = open_page(path.resolve().as_uri())
    assert page.locator("#theme-toggle").count() == 0
    assert _background(page, "body") == "rgb(255, 255, 255)"
    assert open_page.errors == []
