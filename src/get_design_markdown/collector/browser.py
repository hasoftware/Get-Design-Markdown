"""Playwright session, navigation, lazy-load scrolling, focus release and blocker detection.

The collector only *detects* bot challenges and sign-in walls and reports them; it never
tries to solve captchas or evade challenges.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Response,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from get_design_markdown.errors import (
    BROWSER_INSTALL_HINT,
    BotBlockedError,
    BrowserNotInstalledError,
    CollectError,
    LoginRequiredError,
    NavigationError,
    PageTimeoutError,
)
from get_design_markdown.models.raw import BrowserChoice, CaptureOptions, ColorScheme, LoadState
from get_design_markdown.resources import resource_text

INSTALL_HINT = "Chạy: python -m uv run playwright install chromium"
TIMEOUT_HINT = (
    "Tăng thời gian chờ bằng biến môi trường GDM_TIMEOUT (ví dụ GDM_TIMEOUT=90) "
    "hoặc kiểm tra kết nối mạng."
)
OPEN_FAILED_HINT = (
    "Kiểm tra lại URL hoặc thử mở trang trong trình duyệt; nếu trang mở được, hãy chạy lại lệnh."
)
REDIRECT_LOOP_HINT = (
    "Thử mở trang trong trình duyệt hoặc dùng địa chỉ cuối cùng sau chuyển hướng "
    "(ví dụ thêm hoặc bỏ 'www.')."
)
UNSAFE_PORT_HINT = "Dùng cổng web thông thường (80, 443, 8000, 8080...) hoặc bỏ số cổng khỏi URL."

#: Upper bound for the networkidle wait after the load event (seconds).
NETWORK_IDLE_TIMEOUT_S = 15.0
#: networkidle wait after scrolling through the page (seconds, timeout ignored).
SCROLL_IDLE_TIMEOUT_S = 3.0
#: Pause between two scroll steps (milliseconds).
SCROLL_STEP_DELAY_MS = 250
#: Stop scrolling after this many CSS pixels.
SCROLL_MAX_DISTANCE_PX = 30_000

#: Visible-text length below which an error status (403/429/503) is treated as a block page.
BLOCK_PAGE_MAX_TEXT = 500
#: Challenge markers only count on short pages (Cloudflare injects challenge-platform
#: scripts into normal pages too).
MARKER_PAGE_MAX_TEXT = 1500
#: Title matches only count on pages shorter than this (avoids articles about "access denied").
TITLE_PAGE_MAX_TEXT = 3000
#: A page with a visible password field and fewer text elements than this is a sign-in wall.
LOGIN_MAX_TEXT_ELEMENTS = 40

BOT_TITLE_RE = re.compile(
    r"just a moment|attention required|access denied|verify you are human|"
    r"checking your browser|security check|are you a robot|pardon our interruption|"
    r"request blocked|robot check|captcha",
    re.IGNORECASE,
)
#: Strings looked for in the page HTML (lowercase) by js/probe.js.
BOT_MARKERS: tuple[str, ...] = (
    "cf-chl",
    "cf_chl_opt",
    "challenge-platform",
    "g-recaptcha",
    "hcaptcha",
    "h-captcha",
    "px-captcha",
    "captcha-delivery",
    "_incapsula_resource",
    "are you a robot",
)
BOT_TEXT_RE = re.compile(r"are you a robot|verify (that )?you are (a )?human", re.IGNORECASE)
BOT_STATUSES = frozenset({403, 429, 503})
LOGIN_PATH_RE = re.compile(
    r"(?:^|/)(?:log[-_]?in|sign[-_]?in|auth|oauth2?|sso|sessions?)(?:[/._-]|$)",
    re.IGNORECASE,
)
LOGIN_HOST_RE = re.compile(r"^(?:login|signin|auth|sso|accounts?|id)\.", re.IGNORECASE)

_NET_ERROR_RE = re.compile(r"net::(ERR_[A-Z0-9_]+)")
_LOAD_SETTLE_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class LoadResult:
    final_url: str
    status_code: int | None
    load_state: LoadState
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PageProbe:
    """Facts js/probe.js reads from a freshly loaded page (input of detect_blocker)."""

    title: str = ""
    text: str = ""
    markers: tuple[str, ...] = ()
    password_inputs: int = 0
    text_elements: int = 0


@dataclass
class BrowserSession:
    """A launched browser plus the user agent every context uses.

    `label` names the browser actually launched and its version, e.g.
    "chromium 141.0.7390.37" or "msedge 141.0.3537.71"; collect_site() stores it in
    RawCapture.browser so a capture can be reproduced with the same engine.
    """

    browser: Browser
    user_agent: str
    label: str
    contexts: list[BrowserContext] = field(default_factory=list)


# ─── Launch & contexts ───────────────────────────────────────────────────────


def _launch_error(error: Exception) -> CollectError:
    text = str(error)
    if "Executable doesn't exist" in text or "playwright install" in text:
        return BrowserNotInstalledError(
            "Chưa cài trình duyệt Chromium cho Playwright.", hint=BROWSER_INSTALL_HINT
        )
    first_line = text.strip().splitlines()[0] if text.strip() else type(error).__name__
    return CollectError(
        f"Không khởi động được Chromium: {first_line}",
        hint=f"Thử cài lại trình duyệt. {INSTALL_HINT}",
    )


def browser_user_agent(default: str) -> str:
    """Playwright's default user agent without the 'HeadlessChrome' token."""
    return default.replace("HeadlessChrome", "Chrome")


def session_label(name: str, version: str) -> str:
    """RawCapture.browser value: "<name> <version>" ("chromium 141.0.7390.37")."""
    return f"{name} {version}".strip()


@asynccontextmanager
async def open_browser(choice: BrowserChoice = "chromium") -> AsyncIterator[BrowserSession]:
    """Launch a headless browser for `choice`; always closes it (and its contexts) on exit.

    Contract (CaptureOptions.browser, `--browser`, GDM_BROWSER):
    - "chromium": Playwright's bundled Chromium.
    - "msedge" / "chrome": the installed Microsoft Edge / Google Chrome (Playwright channel).
    - "auto": the first that launches of Playwright's Chromium -> Edge -> Chrome.
    - Nothing usable: BrowserNotInstalledError (hint: Edge/Chrome, the UI button or
      `playwright install chromium`); any other launch failure: CollectError.
    The yielded session's `label` names the browser really used ("msedge 141.0.3537.71").

    Current behaviour: every choice launches Playwright's Chromium (the Edge / Chrome
    fallback chain is not implemented yet).
    """
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except PlaywrightError as error:
            raise _launch_error(error) from None
        try:
            probe = await browser.new_page()
            try:
                default_agent = str(await probe.evaluate("navigator.userAgent"))
            finally:
                await probe.close()
            yield BrowserSession(
                browser=browser,
                user_agent=browser_user_agent(default_agent),
                label=session_label("chromium", browser.version),
            )
        finally:
            with suppress(PlaywrightError):
                await browser.close()


async def new_context(
    session: BrowserSession, options: CaptureOptions, *, color_scheme: ColorScheme = "light"
) -> BrowserContext:
    """A context with the capture viewport, en-US locale, explicit color scheme and
    reduced motion (stable screenshots, short hover transitions)."""
    context = await session.browser.new_context(
        viewport={"width": options.viewport_width, "height": options.viewport_height},
        device_scale_factor=1,
        locale="en-US",
        color_scheme=color_scheme,
        reduced_motion="reduce",
        user_agent=session.user_agent,
        service_workers="block",
    )
    session.contexts.append(context)
    return context


@asynccontextmanager
async def open_context(
    options: CaptureOptions, *, color_scheme: ColorScheme = "light"
) -> AsyncIterator[BrowserContext]:
    """Launch headless Chromium and yield a context (viewport, UA, locale, color scheme)."""
    async with open_browser() as session:
        context = await new_context(session, options, color_scheme=color_scheme)
        try:
            yield context
        finally:
            with suppress(PlaywrightError):
                await context.close()


# ─── Navigation ──────────────────────────────────────────────────────────────


def navigation_error(url: str, error: Exception) -> CollectError:
    """Map a Playwright navigation failure (net::ERR_...) to a user-facing error; pure."""
    text = str(error)
    match = _NET_ERROR_RE.search(text)
    code = match.group(1) if match else ""
    if code in ("ERR_NAME_NOT_RESOLVED", "ERR_NAME_RESOLUTION_FAILED"):
        return NavigationError(
            f"Không phân giải được tên miền của {url} (lỗi DNS).",
            hint="Kiểm tra lại địa chỉ URL và kết nối Internet.",
        )
    if code in ("ERR_CONNECTION_REFUSED", "ERR_ADDRESS_UNREACHABLE", "ERR_CONNECTION_RESET"):
        return NavigationError(
            f"Không kết nối được tới {url} ({code}).",
            hint="Máy chủ có thể đang tắt hoặc chặn kết nối — kiểm tra lại URL.",
        )
    if code.startswith(("ERR_CERT", "ERR_SSL", "ERR_BAD_SSL")):
        return NavigationError(
            f"Chứng chỉ HTTPS của {url} không hợp lệ ({code}).",
            hint="Công cụ không bỏ qua lỗi chứng chỉ; hãy kiểm tra lại địa chỉ trang.",
        )
    if code in ("ERR_TIMED_OUT", "ERR_CONNECTION_TIMED_OUT"):
        return PageTimeoutError(f"Hết thời gian kết nối tới {url} ({code}).", hint=TIMEOUT_HINT)
    if code == "ERR_INTERNET_DISCONNECTED":
        return NavigationError("Máy không có kết nối Internet.", hint="Kiểm tra kết nối mạng.")
    if code in ("ERR_TOO_MANY_REDIRECTS",):
        return NavigationError(
            f"Trang {url} chuyển hướng vòng lặp ({code}).", hint=REDIRECT_LOOP_HINT
        )
    if code == "ERR_UNSAFE_PORT":
        return NavigationError(
            f"Không mở được {url} ({code}): Chromium chặn cổng này vì lý do an toàn.",
            hint=UNSAFE_PORT_HINT,
        )
    if code:
        return NavigationError(f"Không mở được {url} ({code}).", hint=OPEN_FAILED_HINT)
    first_line = text.strip().splitlines()[0] if text.strip() else type(error).__name__
    return NavigationError(f"Không mở được {url}: {first_line}", hint=OPEN_FAILED_HINT)


async def load_page(page: Page, url: str, *, timeout_s: float) -> LoadResult:
    """goto(load) + networkidle (capped) with fallback to domcontentloaded; raises CollectError.

    Status codes are not judged here (see ensure_accessible()).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    timeout_ms = timeout_s * 1000
    warnings: list[str] = []
    state: LoadState
    navigations: list[Response] = []

    def on_response(response: Response) -> None:
        with suppress(PlaywrightError):
            if response.request.is_navigation_request() and response.frame == page.main_frame:
                navigations.append(response)

    page.on("response", on_response)
    response: Response | None = None
    try:
        response = await page.goto(url, wait_until="load", timeout=timeout_ms)
        state = "load"
    except PlaywrightTimeoutError:
        # Keep waiting for the same navigation (a second goto would abort it) until
        # DOMContentLoaded, for at most another timeout_s.
        try:
            await page.wait_for_url(
                lambda current: current != "about:blank",
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
        except PlaywrightTimeoutError:
            raise PageTimeoutError(
                f"Trang {url} không tải xong trong {timeout_s:g} giây.", hint=TIMEOUT_HINT
            ) from None
        except PlaywrightError as error:
            raise navigation_error(url, error) from None
        state = "domcontentloaded"
        warnings.append(
            f"Trang không phát sự kiện 'load' trong {timeout_s:g} giây — "
            "dùng trạng thái domcontentloaded (một số nội dung có thể chưa tải)."
        )
        try:
            await page.wait_for_load_state("load", timeout=_LOAD_SETTLE_TIMEOUT_S * 1000)
            state = "load"
        except PlaywrightError:
            pass
    except PlaywrightError as error:
        raise navigation_error(url, error) from None
    finally:
        page.remove_listener("response", on_response)
    if response is None and navigations:
        response = navigations[-1]

    if state == "load":
        idle_s = min(NETWORK_IDLE_TIMEOUT_S, max(deadline - loop.time(), 1.0))
        try:
            await page.wait_for_load_state("networkidle", timeout=idle_s * 1000)
            state = "networkidle"
        except PlaywrightTimeoutError:
            warnings.append(
                f"Trang không đạt trạng thái mạng rảnh (networkidle) sau {idle_s:.0f} giây "
                "— tiếp tục sau sự kiện load."
            )
        except PlaywrightError:
            pass
    status = response.status if response is not None else None
    return LoadResult(
        final_url=page.url, status_code=status, load_state=state, warnings=tuple(warnings)
    )


# ─── Blockers ────────────────────────────────────────────────────────────────


def _path(url: str) -> str:
    return urlsplit(url).path or "/"


def _is_login_url(final_url: str, requested_url: str | None) -> bool:
    final = urlsplit(final_url)
    if LOGIN_PATH_RE.search(final.path or "/"):
        return requested_url is None or not LOGIN_PATH_RE.search(_path(requested_url))
    if requested_url is not None:
        requested_host = (urlsplit(requested_url).hostname or "").removeprefix("www.")
        final_host = (final.hostname or "").removeprefix("www.")
        if final_host != requested_host and LOGIN_HOST_RE.match(final_host):
            return True
    return False


def detect_blocker(
    *,
    title: str,
    body_text: str,
    status_code: int | None,
    final_url: str,
    requested_url: str | None = None,
    markers: Sequence[str] = (),
    password_inputs: int = 0,
    text_elements: int | None = None,
) -> str | None:
    """Return "bot" / "login" when the page is a bot challenge or sign-in wall, else None; pure.

    - "bot": a challenge title or "are you a robot" text on a short page, challenge markers
      (cf-chl, g-recaptcha...) on a short or error page, or 403/429/503 with almost no text.
    - "login": the final URL (but not the requested one) is a sign-in path/host, or the page
      is essentially only a sign-in form (visible password field, < 40 text elements).
    A big page that merely contains a login form or a reCAPTCHA widget is not a blocker.
    """
    text = " ".join(body_text.split())
    text_length = len(text)
    error_status = status_code in BOT_STATUSES
    if BOT_TITLE_RE.search(title or "") and text_length < TITLE_PAGE_MAX_TEXT:
        return "bot"
    if BOT_TEXT_RE.search(text) and text_length < TITLE_PAGE_MAX_TEXT:
        return "bot"
    if markers and (text_length < MARKER_PAGE_MAX_TEXT or error_status):
        return "bot"
    if error_status and text_length < BLOCK_PAGE_MAX_TEXT:
        return "bot"
    if _is_login_url(final_url, requested_url):
        return "login"
    if (
        password_inputs > 0
        and text_elements is not None
        and text_elements < LOGIN_MAX_TEXT_ELEMENTS
    ):
        return "login"
    return None


async def probe_page(page: Page) -> PageProbe:
    """Title, visible text, challenge markers and sign-in form facts of the current page."""
    payload: dict[str, Any] = await page.evaluate(
        resource_text("collector", "js", "probe.js"), {"markers": list(BOT_MARKERS)}
    )
    return PageProbe(
        title=str(payload.get("title") or ""),
        text=str(payload.get("text") or ""),
        markers=tuple(str(marker) for marker in payload.get("markers") or ()),
        password_inputs=int(payload.get("password_inputs") or 0),
        text_elements=int(payload.get("text_elements") or 0),
    )


def access_error(
    *,
    requested_url: str,
    load: LoadResult,
    probe: PageProbe,
) -> CollectError | None:
    """The error to raise for a loaded page (bot wall, login wall, HTTP error), or None; pure."""
    status = load.status_code
    blocker = detect_blocker(
        title=probe.title,
        body_text=probe.text,
        status_code=status,
        final_url=load.final_url,
        requested_url=requested_url,
        markers=probe.markers,
        password_inputs=probe.password_inputs,
        text_elements=probe.text_elements,
    )
    status_note = f", HTTP {status}" if status else ""
    if blocker == "bot":
        return BotBlockedError(
            f"Trang {requested_url} chặn trình duyệt tự động "
            f"(trang kiểm tra bot/captcha{status_note}).",
            hint=(
                "Công cụ không vượt qua cơ chế chống bot. Hãy thử lại sau, "
                "hoặc chọn một trang công khai khác của site."
            ),
        )
    if status is not None and (status in (404, 410) or status >= 500):
        hint = (
            "Kiểm tra lại đường dẫn trang."
            if status < 500
            else "Máy chủ đang gặp lỗi — hãy thử lại sau."
        )
        return NavigationError(f"Máy chủ trả về HTTP {status} cho {requested_url}.", hint=hint)
    if blocker == "login" or status == 401:
        return LoginRequiredError(
            "Trang yêu cầu đăng nhập — công cụ chỉ phân tích trang công khai.",
            hint=(
                f"URL cuối cùng: {load.final_url}. Hãy dùng một trang công khai "
                "(trang chủ, trang giới thiệu, bảng giá...)."
            ),
        )
    if status is not None and status >= 400:
        return NavigationError(
            f"Máy chủ trả về HTTP {status} cho {requested_url}.",
            hint="Trang có thể yêu cầu quyền truy cập hoặc đã bị gỡ.",
        )
    return None


async def ensure_accessible(page: Page, load: LoadResult, *, requested_url: str) -> PageProbe:
    """Raise BotBlockedError / LoginRequiredError / NavigationError when the loaded page is
    not the public page we asked for."""
    try:
        probe = await probe_page(page)
    except PlaywrightError:
        probe = PageProbe()
    error = access_error(requested_url=requested_url, load=load, probe=probe)
    if error is not None:
        raise error
    return probe


# ─── Scrolling ───────────────────────────────────────────────────────────────

_SCROLL_STEP_JS = """
(fraction) => {
  const doc = document.scrollingElement || document.documentElement;
  const step = Math.max(Math.round(window.innerHeight * fraction), 200);
  window.scrollTo({ top: window.scrollY + step, left: 0, behavior: "instant" });
  return {
    y: window.scrollY,
    viewport: window.innerHeight,
    height: Math.max(doc.scrollHeight, document.body ? document.body.scrollHeight : 0),
  };
}
"""
_SCROLL_TOP_JS = "() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' })"
_DOCUMENT_HEIGHT_JS = (
    "() => Math.max(document.documentElement.scrollHeight, "
    "document.body ? document.body.scrollHeight : 0)"
)
#: At the bottom, wait this long for lazy loaders (milliseconds), at most BOTTOM_RECHECKS times.
BOTTOM_WAIT_MS = 600
BOTTOM_RECHECKS = 3


async def scroll_through(
    page: Page,
    *,
    max_steps: int = 40,
    step_fraction: float = 0.8,
    step_delay_ms: int = SCROLL_STEP_DELAY_MS,
    max_distance_px: int = SCROLL_MAX_DISTANCE_PX,
    idle_timeout_s: float | None = None,
    settle_ms: int = 500,
    bottom_rechecks: int = BOTTOM_RECHECKS,
) -> None:
    """Scroll to the bottom in viewport steps to trigger lazy-loading, then back to the top.

    Re-reads the document height after each step (infinite/lazy sections grow it) and stops
    at the bottom, after `max_steps` steps or `max_distance_px` pixels. `idle_timeout_s`
    defaults to SCROLL_IDLE_TIMEOUT_S (0 skips the networkidle wait).
    """
    idle_s = SCROLL_IDLE_TIMEOUT_S if idle_timeout_s is None else idle_timeout_s
    try:
        bottom_checks = 0
        for _ in range(max_steps):
            state: dict[str, float] = await page.evaluate(_SCROLL_STEP_JS, step_fraction)
            await page.wait_for_timeout(step_delay_ms)
            if state["y"] >= max_distance_px:
                break
            if state["y"] + state["viewport"] >= state["height"] - 2:
                # At the bottom: give lazy loaders a moment to append more content.
                if bottom_checks >= bottom_rechecks:
                    break
                bottom_checks += 1
                await page.wait_for_timeout(BOTTOM_WAIT_MS)
                height = float(await page.evaluate(_DOCUMENT_HEIGHT_JS))
                if height <= state["height"] + 2:
                    break
        if idle_s > 0:
            with suppress(PlaywrightError):
                await page.wait_for_load_state("networkidle", timeout=idle_s * 1000)
        await page.evaluate(_SCROLL_TOP_JS)
        await page.wait_for_timeout(settle_ms)
    except PlaywrightError:
        # The page navigated or closed while scrolling; capture what is there.
        with suppress(PlaywrightError):
            await page.evaluate(_SCROLL_TOP_JS)


# ─── Focus ───────────────────────────────────────────────────────────────────

#: Extra wait after blurring, on top of two animation frames (milliseconds).
FOCUS_SETTLE_MS = 50


@dataclass(frozen=True)
class FocusRelease:
    """What js/focus.js did: the tag it blurred (None when nothing was focused)."""

    tag: str | None = None
    still_focused: bool = False
    finished_transitions: int = 0

    @property
    def blurred(self) -> bool:
        return self.tag is not None


async def release_focus(page: Page, *, settle_ms: int = FOCUS_SETTLE_MS) -> FocusRelease:
    """Blur the focused element so styles and screenshots show controls at rest.

    Autofocused fields (or a search box focused by script) would otherwise be measured with
    their :focus / :focus-visible / :focus-within styles — a focus-ring border recorded as the
    field's resting border. Waits for JS-tracked focus classes to re-render and finishes the
    CSS transitions the blur started. Errors (page navigated or closed) are swallowed.
    """
    try:
        result = await page.evaluate(
            resource_text("collector", "js", "focus.js"), {"settleMs": settle_ms}
        )
    except PlaywrightError:
        return FocusRelease()
    if not isinstance(result, dict) or not result.get("blurred"):
        return FocusRelease()
    return FocusRelease(
        tag=str(result.get("tag") or "?"),
        still_focused=bool(result.get("still_focused")),
        finished_transitions=int(result.get("finished_transitions") or 0),
    )


async def wait_quietly(page: Page, *, timeout_s: float) -> None:
    """networkidle with a short cap; a timeout is not an error."""
    with suppress(PlaywrightError):
        await page.wait_for_load_state("networkidle", timeout=timeout_s * 1000)
