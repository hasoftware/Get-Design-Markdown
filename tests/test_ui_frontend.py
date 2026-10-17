"""The local UI frontend (ui/static) in a real Chromium, against the fake API (ui_fake_api).

Every page opened through the `ui` fixture is checked when the test ends: no console errors,
no uncaught exceptions, no Content-Security-Policy violations and no request leaving
127.0.0.1 (preview.html's own Google Fonts stylesheet is answered locally and allowed, the
FILES_HTML_CSP permits it inside the sandboxed preview).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest
from playwright.sync_api import (
    Browser,
    BrowserContext,
    ConsoleMessage,
    Dialog,
    Page,
    Request,
    Route,
    expect,
    sync_playwright,
)

from get_design_markdown.collector.robots import check_robots, evaluate_robots, robots_url_for
from get_design_markdown.console import ConsoleEvent, use_sink
from get_design_markdown.models.raw import RobotsCheck
from get_design_markdown.pipeline import report_robots
from get_design_markdown.ui.jobs import JobError, JobEvent, JobRequest, JobSnapshot
from tests.ui_fake_api import ACME_SITE, BETA_SITE, FAKE_TOKEN, FakeApi

pytestmark = pytest.mark.browser

expect.set_options(timeout=10_000)

#: Optional browser channel for a manual run, e.g. GDM_UI_TEST_CHANNEL=msedge (the engine of
#: the Windows window, WebView2). Unset: Playwright's Chromium, as in CI.
CHANNEL_ENV = "GDM_UI_TEST_CHANNEL"

#: A request the OS itself refused (Windows WSAENOBUFS: the CI runner ran short of sockets late in
#: a long test run). Not a frontend defect: the UI already copes with a failed request (job poll
#: retried, heartbeat repeated, resume skipped), and a test that needed the reply still fails.
TRANSIENT_SOCKET_ERROR = re.compile(r"net::ERR_NO_BUFFER_SPACE")

#: Collects CSP violations in every frame the page opens.
CSP_PROBE = """
window.__gdmViolations = [];
document.addEventListener("securitypolicyviolation", (event) => {
  window.__gdmViolations.push(`${event.violatedDirective} ${event.blockedURI}`);
});
"""

#: WCAG contrast of every visible text node against its composed background.
CONTRAST_CHECK = """
() => {
  const parse = (value) => {
    const match = /rgba?\\(([^)]+)\\)/.exec(value);
    if (!match) return null;
    const parts = match[1].split(/[\\s,/]+/).filter(Boolean).map(Number);
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  };
  const blend = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });
  const channel = (v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  const luminance = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const background = (element) => {
    const layers = [];
    for (let node = element; node; node = node.parentElement) {
      const colour = parse(getComputedStyle(node).backgroundColor);
      if (colour && colour.a > 0) {
        layers.push(colour);
        if (colour.a >= 1) break;
      }
    }
    let base = { r: 255, g: 255, b: 255, a: 1 };
    for (let i = layers.length - 1; i >= 0; i -= 1) base = blend(layers[i], base);
    return base;
  };
  const failures = [];
  for (const element of document.querySelectorAll("body *")) {
    if (["SCRIPT", "STYLE", "OPTION", "svg", "use"].includes(element.tagName)) continue;
    const skipped = ".sr-only, #toasts, [disabled], [aria-disabled='true'], noscript";
    if (element.closest(skipped)) continue;
    const ownText = Array.from(element.childNodes).some(
      (node) => node.nodeType === Node.TEXT_NODE && node.nodeValue.trim(),
    );
    if (!ownText || element.getClientRects().length === 0) continue;
    const style = getComputedStyle(element);
    if (style.visibility === "hidden") continue;
    const back = background(element);
    let fore = parse(style.color);
    if (fore.a < 1) fore = blend(fore, back);
    const [l1, l2] = [luminance(fore), luminance(back)].sort((a, b) => b - a);
    const ratio = (l1 + 0.05) / (l2 + 0.05);
    const size = parseFloat(style.fontSize);
    const bold = parseInt(style.fontWeight, 10) >= 700;
    const minimum = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
    if (ratio < minimum) {
      const text = element.textContent.trim().slice(0, 40);
      failures.push(`${element.tagName}.${element.className} "${text}" ${ratio.toFixed(2)}`);
    }
  }
  return failures;
}
"""

#: Interactive elements without an accessible name.
UNNAMED_CONTROLS = """
() => {
  const missing = [];
  const selector = "button, a[href], input:not([type=hidden]), select, textarea, [role=tab]";
  for (const element of document.querySelectorAll(selector)) {
    if (element.getClientRects().length === 0 && element.type !== "radio") continue;
    if (element.closest("[hidden]")) continue;
    const labelled = element.getAttribute("aria-labelledby");
    const name =
      element.getAttribute("aria-label") ||
      (labelled &&
        labelled
          .split(" ")
          .map((id) => document.getElementById(id)?.textContent || "")
          .join(" ")) ||
      (element.labels && Array.from(element.labels).map((label) => label.textContent).join(" ")) ||
      element.textContent ||
      element.getAttribute("title") ||
      "";
    if (!name.trim()) missing.push(element.outerHTML.slice(0, 80));
  }
  return missing;
}
"""


# ─── Session plumbing ────────────────────────────────────────────────────────


@dataclass
class UiSession:
    """One browser context showing the UI, plus what it did on the wire and in the console."""

    api: FakeApi
    context: BrowserContext
    page: Page
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    external: list[str] = field(default_factory=list)
    preview_external: list[str] = field(default_factory=list)
    api_requests: list[tuple[str, str, str | None]] = field(default_factory=list)
    allowed_console: list[re.Pattern[str]] = field(default_factory=lambda: [TRANSIENT_SOCKET_ERROR])
    allowed_csp: list[re.Pattern[str]] = field(default_factory=list)

    def open(self, *, token: bool = True, wait: bool = True) -> UiSession:
        self.page.goto(self.api.launch_url if token else self.api.url)
        if wait:
            self.wait_ready()
        return self

    def wait_ready(self) -> None:
        # Locator assertions only: a string page.wait_for_function() would poll with eval(),
        # which the page's CSP (script-src 'self') rightly refuses.
        try:
            self.page.wait_for_selector("#app:not([hidden])")
        except Exception as error:
            raise AssertionError(f"the UI did not start: {self.page_errors}") from error
        expect(self.page.locator("#opt-pages")).not_to_have_value("")
        self.page.wait_for_selector("#history-loading", state="hidden")

    def allow_console(self, pattern: str) -> None:
        self.allowed_console.append(re.compile(pattern))

    def allow_csp(self, pattern: str) -> None:
        """Expect the page CSP to block something matching `pattern` (console line and
        securitypolicyviolation event)."""
        self.allowed_csp.append(re.compile(pattern))
        self.allowed_console.append(re.compile(pattern))

    def start_job(self, url: str) -> None:
        if not self.page.is_visible("#view-start"):
            self.page.click("#new-analysis")
        self.page.fill("#url-input", url)
        self.page.press("#url-input", "Enter")

    def wait_results(self) -> None:
        self.page.wait_for_selector("#view-results:not([hidden])", timeout=15_000)

    def problems(self) -> list[str]:
        found: list[str] = []
        for message in self.console_errors:
            if not any(pattern.search(message) for pattern in self.allowed_console):
                found.append(f"console: {message}")
        found += [f"pageerror: {error}" for error in self.page_errors]
        found += [f"external request: {url}" for url in self.external]
        if not self.page.is_closed():
            for frame in self.page.frames:
                try:
                    violations = frame.evaluate("window.__gdmViolations || []")
                except Exception:
                    continue
                found += [
                    f"csp ({frame.url[:60]}): {item}"
                    for item in violations
                    if not any(pattern.search(item) for pattern in self.allowed_csp)
                ]
        return found


def _is_preview_frame(url: str) -> bool:
    return "/files/" in url and url.split("?")[0].endswith("/preview.html")


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(channel=os.environ.get(CHANNEL_ENV) or None)
        try:
            yield instance
        finally:
            instance.close()


OpenUi = Callable[..., UiSession]


@pytest.fixture
def ui(browser: Browser, ui_fake_api: FakeApi) -> Iterator[OpenUi]:
    """Factory: ui(width=1280, height=860, color_scheme="light") -> UiSession (not opened)."""
    sessions: list[UiSession] = []
    ui_fake_api.event_delay_s = 0.01

    def factory(width: int = 1280, height: int = 860, color_scheme: str = "light") -> UiSession:
        context = browser.new_context(
            viewport={"width": width, "height": height},
            color_scheme=color_scheme,  # type: ignore[arg-type]
        )
        context.add_init_script(CSP_PROBE)
        context.set_default_timeout(10_000)
        page = context.new_page()
        session = UiSession(api=ui_fake_api, context=context, page=page)

        def guard(route: Route) -> None:
            url = route.request.url
            if url.startswith((ui_fake_api.url, "data:", "blob:", "about:")):
                route.continue_()
                return
            try:
                frame_url = route.request.frame.url
            except Exception:
                frame_url = ""
            if _is_preview_frame(frame_url):
                session.preview_external.append(url)
                route.fulfill(status=200, content_type="text/css", body="")
            else:
                session.external.append(url)
                route.abort()

        def on_console(message: ConsoleMessage) -> None:
            if message.type == "error":
                location = message.location.get("url", "") if message.location else ""
                session.console_errors.append(f"{message.text} @ {location}")

        def on_request(request: Request) -> None:
            if "/api/" in request.url:
                session.api_requests.append(
                    (request.method, request.url, request.headers.get("x-gdm-token"))
                )

        context.route("**/*", guard)
        page.on("console", on_console)
        page.on("pageerror", lambda error: session.page_errors.append(str(error)))
        page.on("request", on_request)
        sessions.append(session)
        return session

    yield factory
    problems = [problem for session in sessions for problem in session.problems()]
    for session in sessions:
        session.context.close()
    assert problems == []


def wait_until(page: Page, check: Callable[[], bool], timeout: float = 10.0) -> None:
    """Poll `check` while letting Playwright work: the context routes every request through
    this process, so a plain time.sleep() would stall the page's requests."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError("condition not reached in time")


def active_id(page: Page) -> str:
    return str(page.evaluate("document.activeElement ? document.activeElement.id : ''"))


def show_no_history(page: Page) -> None:
    """Answer GET /api/history with an empty list: a first launch, nothing analysed yet."""

    def empty(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"output_root": "C:/empty", "entries": []}',
        )

    page.route("**/api/history", empty)


# ─── Session token, heartbeat, expiry ────────────────────────────────────────


def test_token_is_removed_from_the_address_bar_and_sent_on_every_call(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    assert "token" not in page.url
    assert page.evaluate("sessionStorage.getItem('gdm.token')") == FAKE_TOKEN
    wait_until(page, lambda: session.api.state.heartbeats >= 1)
    paths = {urlpath(url) for _method, url, _token in session.api_requests}
    assert {"/api/settings", "/api/history", "/api/browser/status", "/api/heartbeat"} <= paths
    assert all(token == FAKE_TOKEN for _m, _u, token in session.api_requests)

    # A reload (no fragment any more) keeps working with the token kept for this tab.
    page.reload()
    session.wait_ready()
    assert all(token == FAKE_TOKEN for _m, _u, token in session.api_requests)

    # The page sends a heartbeat when it becomes visible again.
    before = session.api.state.heartbeats
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    wait_until(page, lambda: session.api.state.heartbeats > before)


def urlpath(url: str) -> str:
    return re.sub(r"^https?://[^/]+", "", url).split("?")[0]


def test_missing_token_shows_the_expired_page(ui: OpenUi) -> None:
    session = ui().open(token=False, wait=False)
    page = session.page
    expect(page.locator("#expired")).to_be_visible()
    expect(page.locator("#expired-title")).to_have_text("Phiên làm việc đã hết hạn")
    expect(page.locator("#expired")).to_contain_text("gdm ui")
    expect(page.locator("#app")).to_be_hidden()
    assert session.api_requests == []


def test_wrong_token_shows_the_expired_page(ui: OpenUi) -> None:
    session = ui()
    session.allow_console(r"status of 403")
    session.page.goto(f"{session.api.url}#token=not-the-token")
    expect(session.page.locator("#expired")).to_be_visible()
    assert session.page.evaluate("sessionStorage.getItem('gdm.token')") is None


def test_session_rotated_mid_session(ui: OpenUi) -> None:
    session = ui().open()
    session.allow_console(r"status of 403")
    try:
        session.api.token = "rotated"
        session.page.click(f".history-item[data-site='{BETA_SITE}']")
        expect(session.page.locator("#expired")).to_be_visible()
    finally:
        session.api.token = FAKE_TOKEN


def test_lost_connection_banner_and_reconnect(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    session.allow_console(r"ERR_FAILED|net::")
    page.route("**/api/**", lambda route: route.abort())
    page.click(f".history-item[data-site='{BETA_SITE}']")
    expect(page.locator("#connection-banner")).to_be_visible()
    expect(page.locator("#connection-banner")).to_contain_text("Mất kết nối")
    page.unroute("**/api/**")
    page.click("#connection-retry")
    expect(page.locator("#connection-banner")).to_be_hidden()


# ─── First run ───────────────────────────────────────────────────────────────


def test_first_run_guide_is_dismissed_and_reopened(ui: OpenUi) -> None:
    session = ui()
    page = session.page
    show_no_history(page)
    session.open()
    guide = page.locator("#guide")
    expect(guide).to_be_visible()
    expect(guide.locator(".guide-steps > li")).to_have_count(3)
    expect(guide).to_contain_text("Dán địa chỉ trang web")
    expect(guide).to_contain_text("Xem và sao chép DESIGN.md cho AI")
    expect(guide).to_contain_text(
        "Công cụ chỉ để tham khảo và học hỏi phong cách; không sao chép logo hay tài sản có "
        "bản quyền."
    )
    page.click("#guide-close")
    expect(guide).to_be_hidden()
    assert active_id(page) == "url-input"
    assert "gdm.guideDone=1" in page.evaluate("document.cookie")
    page.reload()
    session.wait_ready()
    expect(guide).to_be_hidden()
    page.click("#help-button")
    expect(guide).to_be_visible()
    assert active_id(page) == "guide"


def test_guide_and_theme_are_kept_when_the_server_port_changes(ui: OpenUi) -> None:
    """Every launch serves the UI on a new port, a new origin with an empty localStorage: the
    dismissed guide and a manual theme are still remembered there."""
    session = ui()
    page = session.page
    show_no_history(page)
    session.open()
    expect(page.locator("#guide")).to_be_visible()
    page.click("#guide-close")
    page.locator(".theme-option", has=page.locator("input[value='dark']")).click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")

    second = FakeApi(session.api.output_root).start()
    try:
        assert second.port != session.api.port
        page.route(f"{second.url}**", lambda route: route.continue_())
        page.goto(second.launch_url)
        session.wait_ready()
        assert page.evaluate("localStorage.getItem('gdm.guideDone')") is None
        page.wait_for_timeout(300)
        expect(page.locator("#guide")).to_be_hidden()
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        assert page.is_checked("input[name='theme'][value='dark']")
        page.click("#help-button")
        expect(page.locator("#guide")).to_be_visible()
    finally:
        second.stop()


def test_guide_stays_closed_once_sites_were_analysed(ui: OpenUi) -> None:
    """Browser data can be cleared, blocked or belong to another browser; a history with
    analysed sites already says this is not the first run. The help button still opens it."""
    session = ui().open()
    page = session.page
    expect(page.locator(".history-item")).to_have_count(2)
    page.wait_for_timeout(300)
    expect(page.locator("#guide")).to_be_hidden()
    assert "gdm.guideDone" not in page.evaluate("document.cookie")
    page.click("#help-button")
    expect(page.locator("#guide")).to_be_visible()


# ─── Settings ────────────────────────────────────────────────────────────────


def test_settings_round_trip(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    api = session.api
    page.click('.stepper [data-step="1"]')
    page.check("#opt-dark")
    page.check('input[name="opt-lang"][value="vi"]')
    page.click("#advanced > summary")
    page.check("#opt-prompt")
    page.uncheck("#opt-open")
    page.fill("#opt-timeout", "90")
    page.press("#opt-timeout", "Tab")
    page.fill("#opt-delta-e", "2.5")
    page.press("#opt-delta-e", "Tab")
    page.fill("#opt-max-elements", "5000")
    page.press("#opt-max-elements", "Tab")
    page.select_option("#opt-browser", "msedge")

    def saved() -> bool:
        settings = api.state.settings
        return (
            settings.pages == 1
            and settings.dark
            and settings.lang == "vi"
            and settings.prompt_only
            and not settings.open_after
            and settings.timeout_s == 90
            and settings.delta_e == 2.5
            and settings.max_elements == 5000
            and settings.browser == "msedge"
        )

    wait_until(page, saved)
    expect(page.locator("#opt-browser-status")).to_contain_text("Đã tìm thấy Microsoft Edge")

    page.reload()
    session.wait_ready()
    assert page.input_value("#opt-pages") == "1"
    assert page.is_checked("#opt-dark")
    assert page.is_checked('input[name="opt-lang"][value="vi"]')
    assert page.is_checked("#opt-prompt")
    assert not page.is_checked("#opt-open")
    assert page.input_value("#opt-timeout") == "90"
    assert page.input_value("#opt-delta-e") == "2.5"
    assert page.input_value("#opt-max-elements") == "5000"
    assert page.input_value("#opt-browser") == "msedge"

    # "Đặt lại" restores the defaults, the output folder stays.
    page.click("#advanced > summary")
    page.click("#settings-reset")
    wait_until(
        page,
        lambda: (
            api.state.settings.model_dump(exclude={"output_dir"})
            == api.state.defaults.model_dump(exclude={"output_dir"})
        ),
    )
    expect(page.locator("#opt-pages")).to_have_value("0")
    expect(page.locator("#opt-browser")).to_have_value("auto")


def test_invalid_setting_is_shown_next_to_its_field(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    session.allow_console(r"status of 422")
    page.click("#advanced > summary")
    page.fill("#opt-timeout", "3")
    page.press("#opt-timeout", "Tab")
    error = page.locator("#opt-timeout_s-error")
    expect(error).to_be_visible()
    expect(error).to_have_text("Nhập một số từ 5 đến 600.")
    expect(page.locator("#opt-timeout")).to_have_attribute("aria-invalid", "true")
    assert session.api.state.settings.timeout_s == 45

    # A job is not started while an option is invalid; focus goes to the field.
    page.fill("#url-input", "demo.example")
    page.press("#url-input", "Enter")
    expect(page.locator("#toasts")).to_contain_text("Một tùy chọn chưa hợp lệ")
    assert active_id(page) == "opt-timeout"
    assert session.api.state.jobs == {}

    page.fill("#opt-timeout", "30")
    page.press("#opt-timeout", "Tab")
    expect(error).to_be_hidden()
    wait_until(page, lambda: session.api.state.settings.timeout_s == 30)
    expect(page.locator("#opt-timeout")).not_to_have_attribute("aria-invalid", "true")


def test_locked_output_folder_is_read_only(ui: OpenUi) -> None:
    """`gdm ui --out X` locks the output folder: shown read-only with an explanation."""
    session = ui()
    page = session.page

    def locked(route: Route) -> None:
        if route.request.method != "GET":
            route.fallback()
            return
        response = route.fetch()
        data = response.json()
        data["output_root_locked"] = True
        data["output_root"] = "D:/Ket-qua-co-dinh"
        route.fulfill(response=response, json=data)

    page.route("**/api/settings", locked)
    session.open()
    page.click("#advanced > summary")
    output = page.locator("#opt-output")
    expect(output).to_have_value("D:/Ket-qua-co-dinh")
    expect(output).to_have_attribute("readonly", "")
    expect(page.locator("#opt-output-locked")).to_be_visible()
    expect(page.locator("#opt-output-locked")).to_contain_text("--out")
    expect(page.locator("#opt-output-help")).to_be_hidden()
    expect(page.locator("#app-version")).to_have_text(re.compile(r"^Phiên bản \d+\.\d+"))


def test_stepper_respects_the_limits(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    minus = page.locator('.stepper [data-step="-1"]')
    plus = page.locator('.stepper [data-step="1"]')
    expect(minus).to_be_disabled()
    for _ in range(12):
        if plus.is_enabled():
            plus.click()
    expect(page.locator("#opt-pages")).to_have_value("10")
    expect(plus).to_be_disabled()
    wait_until(page, lambda: session.api.state.settings.pages == 10)


# ─── Jobs ────────────────────────────────────────────────────────────────────


def test_successful_job_shows_steps_log_and_results(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    session.start_job("demo.example")
    session.wait_results()

    job = next(iter(session.api.state.jobs.values()))
    assert job.request.url == "demo.example"
    assert job.snapshot.status == "succeeded"
    steps = page.locator("#job-steps > li")
    expect(steps).to_have_count(4)
    assert [steps.nth(i).get_attribute("data-state") for i in range(4)] == ["done"] * 4
    log = page.locator("#job-log")
    assert log.get_attribute("role") == "log"
    assert log.get_attribute("aria-live") == "polite"
    expect(log.locator(".log-line")).to_have_count(14)
    expect(log).to_contain_text("Bước 4/4")
    expect(log).to_contain_text("Hoàn tất: đã tạo DESIGN.md")
    # "Đã ghi <absolute path>" lines show the file name; the full path is the tooltip.
    wrote = log.locator(".log-text", has_text="Đã ghi DESIGN.md")
    expect(wrote).to_have_count(1)
    folder = session.api.output_root / "demo.example"
    expect(wrote).to_have_attribute("title", str(folder / "DESIGN.md"))
    assert str(folder) not in log.inner_text()
    assert page.evaluate("sessionStorage.getItem('gdm.ackJob')") == job.snapshot.id

    # Results of the new site, also selected in the history.
    expect(page.locator("#results-title")).to_have_text("Acme Cloud")
    expect(page.locator("#results-stats li")).to_have_count(5)
    expect(page.locator("#results-stats li").first).to_have_text("10 màu")
    expect(page.locator(".history-item[data-site='demo.example']")).to_have_attribute(
        "aria-current", "true"
    )
    assert page.title().startswith("Acme Cloud")

    preview = page.locator("#preview-wrap iframe")
    assert preview.get_attribute("sandbox") == "allow-scripts"
    assert preview.get_attribute("src") == "/files/demo.example/preview.html"

    page.click("#tab-design")
    design = page.locator("#design-wrap iframe")
    assert design.get_attribute("sandbox") == ""
    srcdoc = design.get_attribute("srcdoc") or ""
    assert "Design Reference" in srcdoc
    assert "<script" not in srcdoc.lower()
    assert 'href="https://' not in srcdoc
    assert 'class="sw"' in srcdoc
    assert '<html lang="en">' in srcdoc
    page.click("#front-matter > summary")
    expect(page.locator("#front-matter-code")).to_contain_text("version: alpha")
    expect(page.locator("#front-matter-code .swatch").first).to_be_visible()
    assert (
        page.get_attribute("#download-design", "href") == "/files/demo.example/DESIGN.md?download=1"
    )

    page.click("#tab-ai")
    expect(page.locator("#prompt-present")).to_be_visible()
    expect(page.locator("#prompt-images li")).to_have_count(2)
    assert page.locator("#prompt-wrap iframe").get_attribute("sandbox") == ""

    page.click("#tab-shots")
    shots = page.locator("#shots .shot")
    expect(shots).to_have_count(4)
    shots.first.click()
    expect(page.locator("#lightbox")).to_be_visible()
    expect(page.locator("#lightbox-img")).to_have_attribute(
        "src", "/files/demo.example/screenshots/home-1440.png"
    )
    page.keyboard.press("Escape")
    expect(page.locator("#lightbox")).to_be_hidden()

    page.click("#tab-files")
    expect(page.locator("#files-body tr")).to_have_count(13)
    expect(page.locator("#folder-path")).to_contain_text("demo.example")


def test_progress_stays_when_open_after_is_off_and_robots_warning_shows(ui: OpenUi) -> None:
    session = ui()
    api = session.api
    api.state.settings = api.state.settings.model_copy(update={"open_after": False})
    session.open()
    page = session.page
    session.start_job("robots.example")
    outcome = page.locator("#job-outcome [data-outcome='ok']")
    expect(outcome).to_be_visible()
    robots = page.locator("#job-robots")
    expect(robots).to_be_visible()
    expect(robots).to_contain_text("robots.txt không cho phép truy cập")
    expect(page.locator("#job-title")).to_have_text("Đã phân tích xong robots.example")
    expect(page.locator("#job-ruler")).to_have_attribute("data-state", "succeeded")
    expect(page.locator("#cancel-button")).to_be_hidden()
    outcome.get_by_role("button", name="Xem kết quả").click()
    session.wait_results()
    expect(page.locator(".history-item[data-site='robots.example']")).to_be_visible()


ROBOTS_PAGE = "https://scripted.example/"


def _robots_report(check: RobotsCheck) -> ConsoleEvent:
    """The message the pipeline shows for a robots.txt check (its real wording)."""
    events: list[ConsoleEvent] = []
    with use_sink(events.append):
        report_robots(check)
    assert len(events) == 1
    return events[0]


def _fetched_robots(handler: Callable[[httpx.Request], httpx.Response]) -> RobotsCheck:
    """check_robots() through a fake transport, in a thread of its own: the sync Playwright
    API owns the event loop of this one."""

    async def run() -> RobotsCheck:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await check_robots(ROBOTS_PAGE, client=client, timeout_s=15.0)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(run())).result()


def _refuse_connection(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=request)


def _time_out(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


ROBOTS_CHECKS: dict[str, Callable[[], RobotsCheck]] = {
    "disallowed": lambda: evaluate_robots(
        robots_url_for(ROBOTS_PAGE), 200, "User-agent: *\nDisallow: /\n", ROBOTS_PAGE
    ),
    "server-error": lambda: evaluate_robots(robots_url_for(ROBOTS_PAGE), 503, "", ROBOTS_PAGE),
    "unreachable": lambda: _fetched_robots(_refuse_connection),
    "timeout": lambda: _fetched_robots(_time_out),
    "allowed": lambda: evaluate_robots(
        robots_url_for(ROBOTS_PAGE), 200, "User-agent: *\nAllow: /\n", ROBOTS_PAGE
    ),
}


def _scripted_job(api: FakeApi, events: list[ConsoleEvent], error: JobError) -> str:
    """GET /api/jobs/{id} body of a finished, failed job that sent `events`."""
    now = datetime.now(UTC)
    snapshot = JobSnapshot(
        id="abcdefabcdef",
        url="scripted.example",
        status="failed",
        created_at=now,
        started_at=now,
        finished_at=now,
        step=1,
        total=4,
        step_message="Thu thập dữ liệu trang",
        events=[JobEvent.from_console(event, seq=i, at=now) for i, event in enumerate(events, 1)],
        last_seq=len(events),
        settings=api.state.settings,
        site="scripted.example",
        error=error,
    )
    return '{"job": ' + snapshot.model_dump_json() + "}"


def _serve_scripted_job(page: Page, body: str) -> None:
    def scripted(route: Route) -> None:
        if route.request.method == "POST" and urlpath(route.request.url) == "/api/jobs":
            route.fulfill(status=202, content_type="application/json", body=body)
        elif urlpath(route.request.url) == "/api/jobs/abcdefabcdef":
            route.fulfill(status=200, content_type="application/json", body=body)
        else:
            route.fallback()

    page.route("**/api/jobs**", scripted)


@pytest.mark.parametrize(
    ("case", "verdict"),
    [
        ("disallowed", "disallow"),
        ("server-error", "disallow"),
        ("unreachable", "unchecked"),
        ("timeout", "unchecked"),
        ("allowed", None),
    ],
)
def test_robots_callout_only_for_a_real_refusal(ui: OpenUi, case: str, verdict: str | None) -> None:
    """The callout says the site does not want automated readers: only when robots.txt says
    so. A robots.txt that could not be read (mistyped or unreachable domain, slow server) is a
    neutral log line without network jargon."""
    session = ui().open()
    page = session.page
    robots = _robots_report(ROBOTS_CHECKS[case]())
    error = JobError(
        code="navigation",
        message="Không mở được https://scripted.example/ (net::ERR_NAME_NOT_RESOLVED).",
        hint="Kiểm tra lại địa chỉ và kết nối mạng.",
    )
    events = [
        ConsoleEvent(kind="step", message="Thu thập dữ liệu trang", step=1, total=4),
        robots,
        ConsoleEvent(kind="error", message=error.message, hint=error.hint),
    ]
    _serve_scripted_job(page, _scripted_job(session.api, events, error))
    session.start_job("scripted.example")
    expect(page.locator("#job-outcome [data-code='navigation']")).to_be_visible()
    log = page.locator("#job-log")
    expect(log.locator(".log-line")).to_have_count(3)
    callout = page.locator("#job-robots")
    if verdict == "disallow":
        assert robots.kind == "warning"
        expect(callout).to_be_visible()
        expect(callout).to_contain_text("Trang này không muốn bị công cụ tự động đọc")
        expect(page.locator("#job-robots-text")).to_have_text(robots.message)
        expect(log.locator(".log-line[data-kind='warning']")).to_have_text(re.compile("robots"))
        return
    expect(callout).to_be_hidden()
    expect(log.locator(".log-line[data-kind='warning']")).to_have_count(0)
    if verdict == "unchecked":
        assert robots.kind == "info"  # nothing is known either way: not a warning
        line = log.locator(".log-line[data-kind='info']")
        expect(line).to_have_count(1)
        reason = "tải tệp này quá lâu" if case == "timeout" else "không tải được tệp này"
        expect(line).to_contain_text(
            f"Không kiểm tra được robots.txt của trang ({reason}). Công cụ vẫn tiếp tục."
        )
        text = log.inner_text()
        assert robots.message not in text
        assert "ConnectError" not in text and "Timeout" not in text
    else:
        assert robots.kind == "success"
        expect(log.locator(".log-line[data-kind='success']")).to_have_text(re.compile("robots"))


def test_failing_job_shows_message_hint_and_retry(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    session.start_job("fail.example")
    outcome = page.locator("#job-outcome [data-outcome='danger']")
    expect(outcome).to_be_visible()
    assert outcome.get_attribute("role") == "alert"
    assert outcome.get_attribute("data-code") == "bot_blocked"
    expect(outcome).to_contain_text("chặn trình duyệt tự động")
    expect(outcome).to_contain_text("Công cụ không vượt qua cơ chế chống bot")
    expect(outcome).to_contain_text("Captcha và trang kiểm tra trình duyệt")
    expect(page.locator("#job-steps > li").first).to_have_attribute("data-state", "failed")
    expect(page.locator("#job-ruler")).to_have_attribute("data-state", "failed")
    expect(page.locator("#job-log .log-line[data-kind='error']")).to_have_count(1)

    outcome.get_by_role("button", name="Thử lại").click()
    wait_until(page, lambda: len(session.api.state.jobs) == 2)
    expect(page.locator("#job-outcome [data-outcome='danger']")).to_be_visible()

    page.locator("#job-outcome").get_by_role("button", name="Sửa địa chỉ").click()
    expect(page.locator("#view-start")).to_be_visible()
    expect(page.locator("#url-input")).to_have_value("fail.example")
    assert active_id(page) == "url-input"


def test_invalid_url_is_reported_under_the_field(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    session.allow_console(r"status of 422")
    page.press("#url-input", "Enter")
    expect(page.locator("#url-error")).to_have_text("Nhập địa chỉ trang web, ví dụ stripe.com.")
    page.fill("#url-input", "ftp://example.com")
    page.press("#url-input", "Enter")
    expect(page.locator("#url-error")).to_contain_text("Chỉ hỗ trợ URL http/https")
    expect(page.locator("#url-input")).to_have_attribute("aria-invalid", "true")
    page.fill("#url-input", "stripe.com")
    expect(page.locator("#url-error")).to_be_hidden()
    assert session.api.state.jobs == {}


def test_missing_browser_flow_downloads_chromium(ui: OpenUi) -> None:
    session = ui()
    api = session.api
    api.state.browsers_available = False
    session.open()
    page = session.page
    expect(page.locator("#browser-pill-text")).to_have_text("Chưa có trình duyệt")
    expect(page.locator("#browser-pill")).to_have_attribute("data-state", "missing")
    expect(page.locator("#browser-banner")).to_be_visible()

    session.start_job("no-browser.example")
    outcome = page.locator("#job-outcome [data-code='browser_missing']")
    expect(outcome).to_be_visible()
    expect(outcome).to_contain_text("Cài Microsoft Edge hoặc Google Chrome")
    expect(outcome).not_to_contain_text("playwright install")
    install = outcome.get_by_role("button", name="Tải Chromium")
    expect(install).to_have_class(re.compile(r"btn-primary"))
    expect(outcome.get_by_role("button", name="Thử lại")).to_have_class(
        re.compile(r"btn-secondary")
    )

    api.event_delay_s = 0.15
    install.click()
    expect(outcome.locator("progress")).to_be_visible()
    expect(outcome).to_contain_text("Đã tải xong Chromium.")
    assert api.state.chromium_installed
    expect(page.locator("#browser-pill-text")).to_have_text("Trình duyệt: Chromium (Playwright)")
    expect(page.locator("#browser-banner")).to_be_hidden()


def test_missing_chosen_browser_offers_the_automatic_choice(ui: OpenUi) -> None:
    """Chromium is chosen in the options but missing, Edge is installed: the help names the
    choice and switches to "Tự động" instead of saying the machine has no browser."""
    session = ui()
    api = session.api
    api.state.settings = api.state.settings.model_copy(update={"browser": "chromium"})
    session.open()
    page = session.page
    session.start_job("no-browser.example")
    outcome = page.locator("#job-outcome [data-code='browser_missing']")
    expect(outcome).to_be_visible()
    expect(outcome.locator("[data-browser-help]")).to_have_attribute("data-browser-help", "switch")
    expect(outcome).to_contain_text(
        "Bạn đang chọn Chromium (Playwright) trong Tùy chọn nâng cao, nhưng máy chưa có trình "
        "duyệt này."
    )
    expect(outcome).to_contain_text("lúc này là Microsoft Edge")
    expect(outcome).not_to_contain_text("Máy chưa có trình duyệt mà công cụ dùng được")
    expect(outcome).not_to_contain_text("Cài Microsoft Edge hoặc Google Chrome")
    switch = outcome.get_by_role("button", name="Chuyển sang Tự động và thử lại")
    expect(switch).to_have_class(re.compile(r"btn-primary"))
    expect(outcome.get_by_role("button", name="Thử lại", exact=True)).to_have_class(
        re.compile(r"btn-secondary")
    )
    # Downloading Chromium stays possible, as the second choice.
    expect(outcome.get_by_role("button", name="Tải Chromium")).to_have_class(
        re.compile(r"btn-secondary")
    )

    switch.click()
    wait_until(page, lambda: api.state.settings.browser == "auto" and len(api.state.jobs) == 2)
    expect(page.locator("#opt-browser")).to_have_value("auto")
    # The scripted job fails again although Edge is there now: the help says so plainly.
    retried = page.locator("#job-outcome [data-browser-help='ready']")
    expect(retried).to_be_visible()
    expect(retried).to_have_text("Lúc này máy đã có Microsoft Edge. Bấm Thử lại để phân tích tiếp.")
    expect(
        page.locator("#job-outcome").get_by_role("button", name="Thử lại", exact=True)
    ).to_have_class(re.compile(r"btn-primary"))


def test_missing_chosen_browser_with_nothing_installed(ui: OpenUi) -> None:
    """Edge is chosen and nothing is installed: install Edge, or go back to "Tự động", which
    then offers the Chromium download."""
    session = ui()
    api = session.api
    api.state.browsers_available = False
    api.state.settings = api.state.settings.model_copy(update={"browser": "msedge"})
    session.open()
    page = session.page
    session.start_job("no-browser.example")
    outcome = page.locator("#job-outcome [data-code='browser_missing']")
    help_block = outcome.locator("[data-browser-help]")
    expect(help_block).to_have_attribute("data-browser-help", "pinned")
    expect(outcome).to_contain_text("Bạn đang chọn Microsoft Edge")
    expect(outcome).to_contain_text("Cài Microsoft Edge, rồi bấm Thử lại.")
    expect(outcome.get_by_role("button", name="Tải Chromium")).to_have_count(0)
    outcome.get_by_role("button", name="Chuyển sang Tự động", exact=True).click()
    expect(help_block).to_have_attribute("data-browser-help", "none")
    wait_until(page, lambda: api.state.settings.browser == "auto")
    expect(outcome).to_contain_text("Máy chưa có trình duyệt mà công cụ dùng được")
    expect(outcome.get_by_role("button", name="Tải Chromium")).to_have_class(
        re.compile(r"btn-primary")
    )
    expect(outcome.locator(".browser-help-lead")).to_be_focused()


def test_install_from_the_start_banner(ui: OpenUi) -> None:
    session = ui()
    session.api.state.browsers_available = False
    session.api.event_delay_s = 0.15
    session.open()
    page = session.page
    banner = page.locator("#browser-banner")
    banner.get_by_role("button", name="Tải Chromium").click()
    expect(banner.locator("progress")).to_be_visible()
    expect(banner.locator(".install-message")).to_contain_text("Đang tải Chromium")
    expect(banner).to_be_hidden(timeout=15_000)
    expect(page.locator("#browser-list")).to_contain_text("Chromium (Playwright): có sẵn")


def test_cancel_a_slow_job(ui: OpenUi) -> None:
    session = ui()
    session.api.event_delay_s = 0.05
    session.open()
    page = session.page
    session.start_job("slow.example")
    expect(page.locator("#job-title")).to_have_text("Đang phân tích slow.example")
    expect(page.locator("#job-progress")).to_contain_text("chờ trang")
    expect(page.locator("#job-elapsed")).to_contain_text("Đã chạy")
    page.click("#cancel-button")
    expect(page.locator("#cancel-label")).to_have_text("Đang hủy…")
    outcome = page.locator("#job-outcome [data-outcome='info']")
    expect(outcome).to_be_visible()
    expect(outcome).to_contain_text("Đã hủy phân tích")
    expect(page.locator("#job-ruler")).to_have_attribute("data-state", "cancelled")
    expect(page.locator("#cancel-button")).to_be_hidden()
    job = next(iter(session.api.state.jobs.values()))
    assert job.snapshot.status == "cancelled"


def test_running_job_is_resumed_after_a_reload(ui: OpenUi) -> None:
    session = ui()
    session.api.event_delay_s = 0.05
    session.open()
    page = session.page
    session.start_job("slow.example")
    expect(page.locator("#job-progress")).to_contain_text("chờ trang")
    page.reload()
    session.wait_ready()
    expect(page.locator("#view-job")).to_be_visible()
    expect(page.locator("#job-title")).to_have_text("Đang phân tích slow.example")
    expect(page.locator("#job-log")).to_contain_text("Bước 1/4")
    # The start view says a job is running and leads back to it.
    page.click("#new-analysis")
    expect(page.locator("#busy-banner")).to_contain_text("Đang phân tích slow.example")
    page.click("#busy-show")
    expect(page.locator("#view-job")).to_be_visible()
    page.click("#cancel-button")
    expect(page.locator("#job-outcome [data-outcome='info']")).to_be_visible()


def test_busy_server_attaches_to_the_running_job(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    session.allow_console(r"status of 409")
    session.api.event_delay_s = 0.05
    running = session.api.start_job(JobRequest(url="slow.example"))
    session.start_job("other.example")
    expect(page.locator("#job-title")).to_have_text("Đang phân tích slow.example")
    assert list(session.api.state.jobs) == [running.id]
    page.click("#cancel-button")
    expect(page.locator("#job-outcome [data-outcome='info']")).to_be_visible()


def _failed_snapshot(api: FakeApi, code: str, message: str, hint: str | None) -> str:
    now = datetime.now(UTC)
    snapshot = JobSnapshot(
        id="abcdefabcdef",
        url="scripted.example",
        status="failed",
        created_at=now,
        started_at=now,
        finished_at=now,
        step=1,
        total=4,
        step_message="Thu thập dữ liệu trang",
        last_seq=0,
        settings=api.state.settings,
        site="scripted.example",
        error=JobError(code=code, message=message, hint=hint),
    )
    return '{"job": ' + snapshot.model_dump_json() + "}"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("timeout", "Tăng thời gian chờ lên 90 giây rồi thử lại"),
        ("login_required", "Công cụ chỉ đọc được trang công khai"),
        ("navigation", "máy có kết nối Internet không"),
        ("invalid_url", "bắt đầu bằng https://"),
        ("unexpected", "ui.log"),
        ("config", "Mở Tùy chọn nâng cao"),
        ("analysis", None),
    ],
)
def test_error_help_depends_on_the_error_code(ui: OpenUi, code: str, expected: str | None) -> None:
    session = ui().open()
    page = session.page
    body = _failed_snapshot(session.api, code, f"Lỗi kiểu {code}.", "Gợi ý của máy chủ.")

    def scripted(route: Route) -> None:
        if route.request.method == "POST" and urlpath(route.request.url) == "/api/jobs":
            route.fulfill(status=202, content_type="application/json", body=body)
        elif urlpath(route.request.url) == "/api/jobs/abcdefabcdef":
            route.fulfill(status=200, content_type="application/json", body=body)
        else:
            route.fallback()

    page.route("**/api/jobs**", scripted)
    session.start_job("scripted.example")
    outcome = page.locator(f"#job-outcome [data-code='{code}']")
    expect(outcome).to_be_visible()
    expect(outcome).to_contain_text(f"Lỗi kiểu {code}.")
    expect(outcome).to_contain_text("Gợi ý của máy chủ.")
    extra = outcome.locator(".extra")
    if expected is None:
        expect(extra).to_have_count(0)
    else:
        expect(extra).to_contain_text(expected)
    if code == "timeout":
        outcome.get_by_role("button", name=re.compile("Tăng thời gian chờ")).click()
        wait_until(page, lambda: session.api.state.settings.timeout_s == 90)
    if code == "config":
        # An unusable results folder: the button leads to the field that fixes it.
        outcome.get_by_role("button", name="Mở Tùy chọn nâng cao").click()
        expect(page.locator("#view-start")).to_be_visible()
        assert page.evaluate("document.getElementById('advanced').open") is True
        assert active_id(page) == "opt-output"


# ─── History and results ─────────────────────────────────────────────────────


def test_history_lists_sites_and_opens_results(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    items = page.locator(".history-item")
    expect(items).to_have_count(2)
    expect(items.first).to_contain_text("Acme Cloud")
    expect(items.first.locator("img")).to_have_attribute(
        "src", f"/files/{ACME_SITE}/screenshots/home-1440.png"
    )
    beta = page.locator(f".history-item[data-site='{BETA_SITE}']")
    expect(beta).to_contain_text("Beta Studio")
    expect(beta).to_contain_text("beta.example")
    expect(beta).to_contain_text("13/10/2026")
    beta.focus()
    page.keyboard.press("Enter")
    session.wait_results()
    expect(page.locator("#results-title")).to_have_text("Beta Studio")
    expect(beta).to_have_attribute("aria-current", "true")
    expect(page.locator("#results-warnings")).to_be_visible()
    expect(page.locator("#results-warnings-title")).to_have_text("Ghi chú khi phân tích (1)")

    page.click("#tab-design")
    srcdoc = page.locator("#design-wrap iframe").get_attribute("srcdoc") or ""
    assert '<html lang="vi">' in srcdoc
    page.click("#tab-ai")
    expect(page.locator("#prompt-absent")).to_be_visible()
    expect(page.locator("#prompt-present")).to_be_hidden()
    page.click("#goto-prompt-option")
    expect(page.locator("#view-start")).to_be_visible()
    assert page.evaluate("document.getElementById('advanced').open") is True
    assert active_id(page) == "opt-prompt"

    # The open site is remembered for a reload of this tab.
    page.click(f".history-item[data-site='{BETA_SITE}']")
    session.wait_results()
    page.click("#tab-shots")
    expect(page.locator("#shots .empty")).to_contain_text("Không có ảnh chụp")
    page.reload()
    session.wait_ready()
    session.wait_results()
    expect(page.locator("#results-title")).to_have_text("Beta Studio")
    expect(page.locator("#tab-shots")).to_have_attribute("aria-selected", "true")


def test_results_from_two_runs_are_flagged_up_front(ui: OpenUi, ui_fake_api: FakeApi) -> None:
    """Screenshots newer than DESIGN.md (a re-run that stopped half-way): a callout above the
    tabs says so and offers a new analysis; the note is not repeated in the folded notes."""
    folder = ui_fake_api.output_root / "mixed.example"
    shutil.copytree(ui_fake_api.output_root / ACME_SITE, folder)
    later = time.time() + 30
    for shot in (folder / "screenshots").glob("*.png"):
        os.utime(shot, (later, later))
    session = ui().open()
    page = session.page
    page.click(f".history-item[data-site='{ACME_SITE}']")
    session.wait_results()
    expect(page.locator("#results-mixed")).to_be_hidden()

    page.click(".history-item[data-site='mixed.example']")
    session.wait_results()
    callout = page.locator("#results-mixed")
    expect(callout).to_be_visible()
    expect(callout).to_contain_text("ảnh chụp mới hơn DESIGN.md")
    notes = page.locator("#results-warnings-list li").all_text_contents()
    assert not any(note.startswith("Lượt chạy gần nhất") for note in notes)
    page.click("#results-mixed-rerun")
    expect(page.locator("#job-url")).to_contain_text("acme.example")


def test_results_keep_a_robots_refusal_visible(ui: OpenUi, ui_fake_api: FakeApi) -> None:
    """A site whose robots.txt refused automated access says so above the tabs whenever its
    results are opened, not only while the job runs."""
    folder = ui_fake_api.output_root / "robots.example"
    shutil.copytree(ui_fake_api.output_root / ACME_SITE, folder)
    raw_path = folder / "raw.json"
    stat = raw_path.stat()
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    data["robots"] = {"robots_url": "https://robots.example/robots.txt", "allowed": False}
    raw_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.utime(raw_path, (stat.st_atime, stat.st_mtime))
    session = ui().open()
    page = session.page
    page.click(f".history-item[data-site='{ACME_SITE}']")
    session.wait_results()
    expect(page.locator("#results-robots")).to_be_hidden()

    page.click(".history-item[data-site='robots.example']")
    session.wait_results()
    callout = page.locator("#results-robots")
    expect(callout).to_be_visible()
    expect(callout).to_contain_text("không muốn bị công cụ tự động đọc")
    expect(callout).to_contain_text("https://robots.example/robots.txt")
    expect(page.locator("#results-mixed")).to_be_hidden()
    notes = page.locator("#results-warnings-list li").all_text_contents()
    assert not any("robots.txt của trang này" in note for note in notes)


def test_files_from_an_earlier_prompt_only_run_are_marked_old(
    ui: OpenUi, ui_fake_api: FakeApi
) -> None:
    """prompt.md and prompt-images/ left by an earlier "prompt-only" run do not match the
    latest DESIGN.md: the Files tab labels them, the AI tab does not offer them."""
    folder = ui_fake_api.output_root / "oldprompt.example"
    shutil.copytree(ui_fake_api.output_root / ACME_SITE, folder)
    (folder / "prompt.md").write_text("# Old prompt\n", encoding="utf-8")
    (folder / "prompt-images").mkdir(exist_ok=True)
    shutil.copyfile(folder / "screenshots" / "home-1440.png", folder / "prompt-images" / "d.png")
    synthesis_path = folder / "synthesis.json"
    synthesis = json.loads(synthesis_path.read_text(encoding="utf-8"))
    synthesis.update(prompt_path=None, image_paths=[])
    synthesis_path.write_text(json.dumps(synthesis), encoding="utf-8")
    session = ui().open()
    page = session.page
    page.click(".history-item[data-site='oldprompt.example']")
    session.wait_results()
    page.click("#tab-ai")
    expect(page.locator("#prompt-present")).to_be_hidden()
    page.click("#tab-files")
    stale = page.locator("#files-body tr.file-stale")
    expect(stale).to_have_count(len(list((folder / "prompt-images").iterdir())) + 1)
    expect(stale.first).to_contain_text("prompt-images")
    expect(stale.last).to_contain_text("prompt.md")
    expect(stale.last).to_contain_text("Từ một lượt chạy trước")
    current = page.locator("#files-body tr:not(.file-stale) .file-base")
    expect(current.filter(has_text=re.compile(r"^DESIGN\.md$"))).to_have_count(1)


def test_empty_history_invites_a_first_analysis(ui: OpenUi) -> None:
    session = ui()
    page = session.page
    show_no_history(page)
    session.open()
    expect(page.locator("#history-empty")).to_be_visible()
    expect(page.locator("#history-empty")).to_contain_text("Chưa có trang nào.")
    expect(page.locator("#history-list")).to_be_hidden()


def test_copy_design_and_open_folder(ui: OpenUi) -> None:
    session = ui()
    origin = session.api.url.rstrip("/")
    session.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
    session.open()
    page = session.page
    page.click(f".history-item[data-site='{ACME_SITE}']")
    session.wait_results()
    page.click("#copy-design-top")
    expect(page.locator("#toasts")).to_contain_text("Đã sao chép DESIGN.md")
    expect(page.locator("#copy-design-top")).to_have_attribute("data-copied", "true")
    copied = str(page.evaluate("navigator.clipboard.readText()"))
    expected = (session.api.output_root / ACME_SITE / "DESIGN.md").read_text(encoding="utf-8")
    # The Windows clipboard hands text back with CRLF line breaks.
    assert copied.replace("\r\n", "\n") == expected

    page.click("#open-site-folder")
    wait_until(page, lambda: bool(session.api.state.opened_folders))
    assert session.api.state.opened_folders[-1].endswith(ACME_SITE)
    page.click("#open-root")
    wait_until(page, lambda: len(session.api.state.opened_folders) == 2)
    assert session.api.state.opened_folders[-1] == str(session.api.output_root)


HOSTILE = '<img src=x onerror="window.top.__pwned=1">'


def test_site_derived_strings_never_become_markup(ui: OpenUi) -> None:
    """Names, URLs, warnings, front matter and rendered Markdown come from an analysed site:
    they are shown as text, and the Markdown frame cannot run anything."""
    session = ui()
    page = session.page
    dialogs: list[str] = []

    def on_dialog(dialog: Dialog) -> None:
        dialogs.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", on_dialog)

    def hostile(route: Route) -> None:
        response = route.fetch()
        data = response.json()
        data["entry"]["name"] = f"Acme {HOSTILE}"
        data["entry"]["url"] = "javascript:alert(1)"
        data["entry"]["pages"] = [HOSTILE]
        data["warnings"] = [f"Cảnh báo {HOSTILE}"]
        data["design"]["front_matter"] = f"name: {HOSTILE}\ncolors:\n  primary: '#4f46e5'\n"
        data["design"]["html"] = (
            f"<h1>Tiêu đề</h1>{HOSTILE}<script>window.top.__pwned=2</script>"
            '<p><a href="javascript:alert(1)">bấm</a> <a href="https://evil.example/">ra ngoài</a>'
            ' <span onclick="alert(1)">x</span> #ff0000</p>'
        )
        route.fulfill(response=response, json=data)

    page.route(f"**/api/results/{ACME_SITE}", hostile)
    # Parsing the hostile fragment in an inert DOMParser document makes Chrome check its
    # inline handlers against the page CSP, which refuses them: blocked, never run.
    session.allow_csp(r"script-src-attr|Executing inline event handler violates")
    session.open()
    page.click(f".history-item[data-site='{ACME_SITE}']")
    session.wait_results()
    expect(page.locator("#results-title")).to_have_text(f"Acme {HOSTILE}")
    assert page.locator("#results-title img, #results-meta img").count() == 0
    expect(page.locator("#results-url")).to_have_text("javascript:alert(1)")
    assert page.locator("#results-url a").count() == 0
    expect(page.locator("#results-meta")).to_contain_text(HOSTILE)
    page.click("#results-warnings > summary")
    expect(page.locator("#results-warnings-list li")).to_have_text(f"Cảnh báo {HOSTILE}")
    assert page.locator("#results-warnings-list img").count() == 0

    page.click("#tab-design")
    page.click("#front-matter > summary")
    expect(page.locator("#front-matter-code")).to_contain_text(HOSTILE)
    assert page.locator("#front-matter-code img").count() == 0
    srcdoc = page.locator("#design-wrap iframe").get_attribute("srcdoc") or ""
    head, body = srcdoc.split("</head>", 1)
    for forbidden in ("<script", "onerror", "onclick", "javascript:", "<img", "<a "):
        assert forbidden not in body.lower(), forbidden
    # The frame title repeats the site name, escaped.
    assert "&lt;img src=x onerror=" in head
    assert 'title="https://evil.example/"' in srcdoc
    assert 'class="sw"' in srcdoc
    page.wait_for_timeout(300)
    assert page.evaluate("window.__pwned") is None
    assert dialogs == []


def test_missing_site_returns_to_the_start_view(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    session.allow_console(r"status of 404")
    page.evaluate("sessionStorage.setItem('gdm.openSite', 'gone.example')")
    page.reload()
    session.wait_ready()
    expect(page.locator("#toasts")).to_contain_text("Không còn kết quả cho gone.example")
    expect(page.locator("#view-start")).to_be_visible()
    assert page.evaluate("sessionStorage.getItem('gdm.openSite')") is None


# ─── Keyboard, accessibility, theme, layout ──────────────────────────────────


def test_keyboard_only_flow(ui: OpenUi) -> None:
    session = ui()
    page = session.page
    show_no_history(page)
    session.open()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "skip-link"
    page.keyboard.press("Enter")
    assert active_id(page) == "main"
    page.keyboard.press("Tab")
    assert active_id(page) == "guide-close"
    page.keyboard.press("Enter")
    assert active_id(page) == "url-input"
    page.keyboard.type("keys.example")
    page.keyboard.press("Enter")
    session.wait_results()
    expect(page.locator("#results-title")).to_be_focused()

    for _ in range(20):
        if active_id(page) == "tab-preview":
            break
        page.keyboard.press("Tab")
    assert active_id(page) == "tab-preview"
    outline = page.evaluate(
        "(() => { const s = getComputedStyle(document.activeElement);"
        " return [s.outlineStyle, parseFloat(s.outlineWidth)]; })()"
    )
    assert outline[0] == "solid" and outline[1] >= 2

    page.keyboard.press("ArrowRight")
    assert active_id(page) == "tab-design"
    expect(page.locator("#tab-design")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#panel-design")).to_be_visible()
    expect(page.locator("#panel-preview")).to_be_hidden()
    page.keyboard.press("End")
    assert active_id(page) == "tab-files"
    page.keyboard.press("Home")
    assert active_id(page) == "tab-preview"
    page.keyboard.press("ArrowLeft")
    assert active_id(page) == "tab-files"
    expect(page.locator("#tab-files")).to_have_attribute("tabindex", "0")
    expect(page.locator("#tab-preview")).to_have_attribute("tabindex", "-1")


def test_url_error_is_announced_also_when_repeated(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    error = page.locator("#url-error")
    assert error.get_attribute("role") == "alert"
    page.evaluate(
        """() => {
          const box = document.getElementById('url-error');
          window.__urlErrorStates = [];
          new MutationObserver(() => window.__urlErrorStates.push(box.hidden))
            .observe(box, { attributes: true, attributeFilter: ['hidden'] });
        }"""
    )
    page.focus("#url-input")
    page.keyboard.press("Enter")
    expect(error).to_have_text("Nhập địa chỉ trang web, ví dụ stripe.com.")
    # Pressing Enter again with the same mistake hides the alert for a moment and shows it
    # again, so that a screen reader reads it a second time; focus stays in the field.
    page.keyboard.press("Enter")
    wait_until(page, lambda: page.evaluate("window.__urlErrorStates.length") >= 3)
    assert page.evaluate("window.__urlErrorStates") == [False, True, False]
    expect(error).to_be_visible()
    assert active_id(page) == "url-input"


def test_progress_of_a_long_step_is_announced_politely(ui: OpenUi) -> None:
    session = ui()
    session.api.event_delay_s = 0.05
    session.open()
    page = session.page
    live = page.locator("#job-progress-live")
    assert live.get_attribute("role") == "status"
    assert live.get_attribute("aria-live") == "polite"
    session.start_job("slow.example")
    # The first progress text of the step is read at once...
    expect(live).to_have_text("Đang thu thập — chờ trang… 0s")
    # ...the next ones (5 a second here) at most every 10 s, while the page shows them all.
    # (one event per 0.2 s: 5 s to 39 s of the fake clock lie well inside the first 10 s)
    expect(page.locator("#job-progress")).to_have_text(re.compile(r"chờ trang… ([5-9]|[1-3]\d)s$"))
    expect(live).to_have_text("Đang thu thập — chờ trang… 0s")
    page.click("#cancel-button")
    expect(live).to_have_text(re.compile(r"^Đang hủy… Thường chỉ mất vài giây"))
    expect(page.locator("#job-outcome [data-outcome='info']")).to_be_visible()
    expect(live).to_have_text("")


def test_throttled_announcer_keeps_the_latest_text(ui: OpenUi) -> None:
    session = ui().open()
    seen = session.page.evaluate(
        """async () => {
          const { ThrottledAnnouncer } = await import('/static/dom.js');
          const region = document.createElement('p');
          const said = [];
          new MutationObserver(() => said.push(region.textContent))
            .observe(region, { childList: true, characterData: true, subtree: true });
          const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
          const announcer = new ThrottledAnnouncer(region, 300);
          announcer.say('A');
          announcer.say('B');
          announcer.say('C');
          await pause(450);
          announcer.say('C');
          announcer.say('D');
          announcer.restart();
          announcer.say('E');
          await pause(400);
          announcer.say('F');
          announcer.announce('G');
          await pause(400);
          announcer.reset();
          await pause(20);
          return said;
        }"""
    )
    # A at once, B dropped for the newer C (after 300 ms), C again skipped, D dropped by
    # restart(), E at once, F replaced by the immediate G, then the region is cleared.
    assert seen == ["A", "C", "E", "G", ""]


def test_tab_pattern_roles(ui: OpenUi) -> None:
    session = ui().open()
    page = session.page
    page.click(f".history-item[data-site='{ACME_SITE}']")
    session.wait_results()
    tabs = page.locator("[role=tablist] [role=tab]")
    expect(tabs).to_have_count(5)
    for index in range(5):
        tab = tabs.nth(index)
        panel_id = tab.get_attribute("aria-controls")
        assert panel_id
        panel = page.locator(f"#{panel_id}")
        expect(panel).to_have_attribute("role", "tabpanel")
        expect(panel).to_have_attribute("aria-labelledby", tab.get_attribute("id") or "")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_text_contrast_and_control_names(ui: OpenUi, scheme: str) -> None:
    session = ui(color_scheme=scheme).open()
    page = session.page
    page.click("#advanced > summary")
    assert page.evaluate(CONTRAST_CHECK) == []
    assert page.evaluate(UNNAMED_CONTROLS) == []

    session.start_job("fail.example")
    expect(page.locator("#job-outcome [data-outcome='danger']")).to_be_visible()
    assert page.evaluate(CONTRAST_CHECK) == []

    page.click(f".history-item[data-site='{ACME_SITE}']")
    session.wait_results()
    page.click("#results-warnings > summary")
    for tab in ("preview", "design", "ai", "shots", "files"):
        page.click(f"#tab-{tab}")
        assert page.evaluate(CONTRAST_CHECK) == [], tab
        assert page.evaluate(UNNAMED_CONTROLS) == [], tab


def test_contrast_probe_flags_low_contrast(ui: OpenUi) -> None:
    """The probe used above is not vacuous: a pale grey line on white is reported."""
    session = ui().open()
    page = session.page
    page.evaluate(
        """() => {
          const probe = document.createElement('p');
          probe.id = 'low-contrast-probe';
          probe.textContent = 'Chữ quá nhạt';
          probe.style.color = '#c8c8c8';
          probe.style.background = '#ffffff';
          document.getElementById('view-start').prepend(probe);
        }"""
    )
    failures = page.evaluate(CONTRAST_CHECK)
    assert len(failures) == 1
    assert "Chữ quá nhạt" in failures[0]
    page.evaluate("document.getElementById('low-contrast-probe').remove()")


def test_dark_theme_follows_the_system_and_the_toggle(ui: OpenUi) -> None:
    session = ui(color_scheme="dark").open()
    page = session.page
    background = "getComputedStyle(document.body).backgroundColor"
    assert page.evaluate(background) == "rgb(10, 10, 10)"
    page.click(f".history-item[data-site='{BETA_SITE}']")
    session.wait_results()
    page.click("#tab-design")
    frame = page.locator("#design-wrap iframe")
    assert "color-scheme: dark" in (frame.get_attribute("srcdoc") or "")

    page.locator(".theme-option", has=page.locator("input[value='light']")).click()
    assert page.evaluate(background) == "rgb(255, 255, 255)"
    assert "color-scheme: light" in (frame.get_attribute("srcdoc") or "")
    assert "gdm.theme=light" in page.evaluate("document.cookie")
    page.reload()
    session.wait_ready()
    assert page.evaluate(background) == "rgb(255, 255, 255)"
    assert page.is_checked("input[name='theme'][value='light']")
    page.locator(".theme-option", has=page.locator("input[value='auto']")).click()
    assert page.evaluate(background) == "rgb(10, 10, 10)"
    assert page.evaluate("localStorage.getItem('gdm.theme')") is None
    assert "gdm.theme" not in page.evaluate("document.cookie")


def _no_horizontal_scroll(page: Page) -> bool:
    return bool(
        page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
    )


@pytest.mark.parametrize("width", [1024, 1280, 1920])
def test_layout_has_no_horizontal_scroll(ui: OpenUi, width: int) -> None:
    session = ui(width=width, height=768).open()
    page = session.page
    # The sidebar footer (open-folder button + version) stays inside the sidebar.
    expect(page.locator("#app-version")).to_have_text(re.compile(r"^Phiên bản \d+\.\d+"))
    sidebar = page.locator("#sidebar").bounding_box()
    assert sidebar is not None
    for selector in ("#open-root", "#app-version"):
        box = page.locator(selector).bounding_box()
        assert box is not None and box["x"] + box["width"] <= sidebar["x"] + sidebar["width"], (
            selector
        )
    page.click("#advanced > summary")
    assert _no_horizontal_scroll(page)
    session.start_job("fail.example")
    expect(page.locator("#job-outcome [data-outcome='danger']")).to_be_visible()
    assert _no_horizontal_scroll(page)
    page.click(f".history-item[data-site='{ACME_SITE}']")
    session.wait_results()
    for tab in ("preview", "design", "ai", "shots", "files"):
        page.click(f"#tab-{tab}")
        assert _no_horizontal_scroll(page), tab
    expect(page.locator("#sidebar")).to_be_visible()
    expect(page.locator("#history-toggle")).to_be_hidden()


#: Topbar elements whose right edge passes the viewport.
TOPBAR_OVERFLOW = """
() => Array.from(document.querySelectorAll('.topbar *'))
  .filter((element) => {
    const box = element.getBoundingClientRect();
    return box.width > 0 && box.right > window.innerWidth + 0.5;
  })
  .map((element) => `${element.tagName}#${element.id}.${element.getAttribute('class') || ''}`)
"""


@pytest.mark.parametrize("width", [320, 400, 560, 720, 760, 800, 860, 900])
def test_topbar_fits_narrow_windows(ui: OpenUi, width: int) -> None:
    """`gdm ui` runs in browser tabs of any width: the topbar shrinks its browser pill and
    hides button labels instead of scrolling sideways; every control keeps its name."""
    session = ui(width=width, height=700)
    session.api.state.chromium_installed = True  # the longest label: "Chromium (Playwright)"
    session.open()
    page = session.page
    pill = page.locator("#browser-pill")
    expect(pill).to_have_attribute("data-state", "ok")
    expect(page.locator("#browser-pill-text")).to_have_text("Trình duyệt: Chromium (Playwright)")
    assert page.evaluate(TOPBAR_OVERFLOW) == []
    assert _no_horizontal_scroll(page)
    expect(
        page.get_by_role("button", name="Trình duyệt: Chromium (Playwright)", exact=True)
    ).to_be_visible()
    expect(page.get_by_role("button", name="Hướng dẫn", exact=True)).to_be_visible()
    expect(page.get_by_role("radio", name="Tối", exact=True)).to_be_attached()
    if width < 900:
        expect(page.get_by_role("button", name="Lịch sử", exact=True)).to_be_visible()
    for scheme in ("dark", "light"):
        page.emulate_media(color_scheme=scheme)
        assert page.evaluate(TOPBAR_OVERFLOW) == []
        assert _no_horizontal_scroll(page)


def test_narrow_window_uses_a_history_drawer(ui: OpenUi) -> None:
    session = ui(width=800, height=700).open()
    page = session.page
    assert _no_horizontal_scroll(page)
    toggle = page.locator("#history-toggle")
    expect(toggle).to_be_visible()
    expect(page.locator("#sidebar")).to_be_hidden()
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#sidebar")).to_be_visible()
    expect(page.locator(".history-item").first).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#sidebar")).to_be_hidden()
    expect(toggle).to_be_focused()
    toggle.click()
    page.click(f".history-item[data-site='{ACME_SITE}']")
    session.wait_results()
    expect(page.locator("#sidebar")).to_be_hidden()
    for tab in ("preview", "design", "ai", "shots", "files"):
        page.click(f"#tab-{tab}")
        assert _no_horizontal_scroll(page), tab
