"""Collector unit tests that need no browser (pure functions and payload parsing)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from get_design_markdown.collector import collect
from get_design_markdown.collector.browser import (
    LoadResult,
    PageProbe,
    _launch_error,
    access_error,
    browser_user_agent,
    detect_blocker,
    navigation_error,
)
from get_design_markdown.collector.discovery import (
    candidate_url,
    discover_pages,
    is_excluded_path,
    unique_page_name,
    url_key,
)
from get_design_markdown.collector.dom import parse_extraction
from get_design_markdown.collector.hover import (
    diff_styles,
    execution_order,
    hover_kind,
    hover_wait_ms,
    select_hover_candidates,
)
from get_design_markdown.collector.stylesheets import (
    StylesheetRegistry,
    media_query_from_prelude,
    normalize_media_query,
    parse_stylesheet,
)
from get_design_markdown.collector.viewports import (
    layout_from_payload,
    measurement_order,
    screenshot_name,
    viewport_height_for,
)
from get_design_markdown.errors import (
    BotBlockedError,
    BrowserNotInstalledError,
    CollectError,
    LoginRequiredError,
    NavigationError,
    PageTimeoutError,
)
from get_design_markdown.models.raw import (
    BoundingBox,
    ColorScheme,
    ComputedStyle,
    ElementSample,
    PageCapture,
    RawCapture,
    Screenshot,
    StylesheetInfo,
)
from get_design_markdown.storage import screenshot_filename
from tests.fixture_data import SITE_DIR, load_raw_sample

LONG_TEXT = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 80


# ─── Blocker detection ───────────────────────────────────────────────────────


def _blocker(**overrides: Any) -> str | None:
    arguments: dict[str, Any] = {
        "title": "Acme",
        "body_text": LONG_TEXT,
        "status_code": 200,
        "final_url": "https://acme.example/",
        "requested_url": "https://acme.example/",
    }
    arguments.update(overrides)
    return detect_blocker(**arguments)


def test_normal_page_is_not_blocked() -> None:
    assert _blocker() is None


@pytest.mark.parametrize(
    "title",
    ["Just a moment...", "Attention Required! | Cloudflare", "Access denied", "Security check"],
)
def test_challenge_titles_on_short_pages_are_bot_walls(title: str) -> None:
    assert _blocker(title=title, body_text="Checking your browser.", status_code=403) == "bot"
    assert _blocker(title=title, body_text="Please wait.") == "bot"


def test_challenge_title_on_a_long_article_is_not_a_wall() -> None:
    assert _blocker(title="Access denied errors explained", body_text=LONG_TEXT) is None


def test_markers_count_only_on_short_or_error_pages() -> None:
    # Cloudflare injects challenge-platform scripts and forms embed reCAPTCHA on normal pages.
    assert _blocker(markers=["challenge-platform"]) is None
    assert _blocker(markers=["g-recaptcha"]) is None
    assert _blocker(markers=["cf_chl_opt"], body_text="One moment please.") == "bot"
    assert _blocker(markers=["g-recaptcha"], status_code=429) == "bot"


def test_error_status_with_almost_no_text_is_a_bot_wall() -> None:
    assert _blocker(status_code=403, body_text="Forbidden") == "bot"
    assert _blocker(status_code=503, body_text="") == "bot"
    assert _blocker(status_code=403, body_text=LONG_TEXT) is None


def test_robot_question_in_text_is_a_bot_wall() -> None:
    assert _blocker(body_text="Are you a robot? Tick the box below.") == "bot"


def test_redirect_to_sign_in_path_is_a_login_wall() -> None:
    assert _blocker(final_url="https://acme.example/login?next=/app") == "login"
    assert _blocker(final_url="https://acme.example/users/sign_in") == "login"
    assert _blocker(final_url="https://acme.example/auth/start") == "login"


def test_sign_in_host_after_redirect_is_a_login_wall() -> None:
    assert (
        _blocker(
            requested_url="https://www.acme.example/app",
            final_url="https://accounts.acme.example/signin/v2",
        )
        == "login"
    )


def test_requested_login_page_is_judged_by_its_form() -> None:
    url = "https://acme.example/login"
    assert _blocker(requested_url=url, final_url=url) is None
    assert _blocker(requested_url=url, final_url=url, password_inputs=1, text_elements=9) == "login"


def test_login_form_inside_a_big_page_is_not_a_wall() -> None:
    assert _blocker(password_inputs=1, text_elements=180) is None


def test_author_pages_are_not_mistaken_for_auth() -> None:
    assert _blocker(final_url="https://acme.example/author/jane") is None


def _load(status: int | None, final_url: str = "https://acme.example/") -> LoadResult:
    return LoadResult(final_url=final_url, status_code=status, load_state="networkidle")


def test_access_error_mapping() -> None:
    url = "https://acme.example/"
    page = PageProbe(title="Acme", text=LONG_TEXT, text_elements=200)
    assert access_error(requested_url=url, load=_load(200), probe=page) is None
    assert access_error(requested_url=url, load=_load(None), probe=page) is None

    challenge = PageProbe(title="Just a moment...", text="Checking your browser")
    assert isinstance(
        access_error(requested_url=url, load=_load(403), probe=challenge), BotBlockedError
    )

    not_found = access_error(requested_url=url, load=_load(404), probe=page)
    assert isinstance(not_found, NavigationError) and "404" in not_found.message
    server = access_error(requested_url=url, load=_load(502), probe=page)
    assert isinstance(server, NavigationError) and "502" in server.message

    unauthorized = access_error(requested_url=url, load=_load(401), probe=page)
    assert isinstance(unauthorized, LoginRequiredError)
    assert "đăng nhập" in unauthorized.message

    redirected = access_error(
        requested_url=url, load=_load(200, "https://acme.example/login"), probe=page
    )
    assert isinstance(redirected, LoginRequiredError)

    teapot = access_error(requested_url=url, load=_load(418), probe=page)
    assert isinstance(teapot, NavigationError) and "418" in teapot.message


@pytest.mark.parametrize(
    ("message", "kind", "fragment"),
    [
        ("net::ERR_NAME_NOT_RESOLVED at https://nope.example/", NavigationError, "DNS"),
        ("net::ERR_CONNECTION_REFUSED at http://127.0.0.1:1/", NavigationError, "ERR_CONNECTION"),
        ("net::ERR_CERT_AUTHORITY_INVALID", NavigationError, "Chứng chỉ"),
        ("net::ERR_TIMED_OUT", PageTimeoutError, "ERR_TIMED_OUT"),
        ("net::ERR_ABORTED", NavigationError, "ERR_ABORTED"),
        ("Something odd", NavigationError, "Something odd"),
    ],
)
def test_navigation_error_mapping(message: str, kind: type[CollectError], fragment: str) -> None:
    error = navigation_error("https://acme.example/", Exception(message))
    assert type(error) is kind
    assert fragment in error.message


@pytest.mark.parametrize(
    "message",
    [
        "net::ERR_NAME_NOT_RESOLVED at https://nope.example/",
        "net::ERR_CONNECTION_REFUSED at http://127.0.0.1:1/",
        "net::ERR_CERT_AUTHORITY_INVALID",
        "net::ERR_TIMED_OUT",
        "net::ERR_INTERNET_DISCONNECTED",
        "net::ERR_TOO_MANY_REDIRECTS at https://acme.example/",
        "net::ERR_UNSAFE_PORT at http://127.0.0.1:9/",
        "net::ERR_ABORTED",
        "net::ERR_SOMETHING_NEW",
        "Something odd",
        "",
    ],
)
def test_every_navigation_error_has_a_vietnamese_hint(message: str) -> None:
    error = navigation_error("http://127.0.0.1:9/", Exception(message))
    assert error.hint, f"no hint for {message!r}"
    assert any(char in error.hint for char in "ạảãáàâăêôơưđểịọ"), error.hint


def test_unsafe_port_names_the_cause() -> None:
    error = navigation_error("http://127.0.0.1:9/", Exception("net::ERR_UNSAFE_PORT"))
    assert isinstance(error, NavigationError)
    assert "ERR_UNSAFE_PORT" in error.message and "cổng" in error.message
    assert error.hint is not None and "cổng" in error.hint


def test_access_errors_have_hints() -> None:
    url = "https://acme.example/"
    page = PageProbe(title="Acme", text=LONG_TEXT, text_elements=200)
    challenge = PageProbe(title="Just a moment...", text="Checking your browser")
    for load, probe in (
        (_load(403), challenge),
        (_load(404), page),
        (_load(502), page),
        (_load(401), page),
        (_load(418), page),
    ):
        error = access_error(requested_url=url, load=load, probe=probe)
        assert error is not None and error.hint, (load, probe)


def test_missing_chromium_has_install_hint() -> None:
    error = _launch_error(Exception("Executable doesn't exist at C:/ms-playwright/chrome.exe"))
    assert isinstance(error, BrowserNotInstalledError)
    assert error.hint is not None and "playwright install chromium" in error.hint
    other = _launch_error(Exception("spawn failed\nmore"))
    assert not isinstance(other, BrowserNotInstalledError)
    assert "spawn failed" in other.message


def test_user_agent_drops_headless_token() -> None:
    agent = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 HeadlessChrome/156.0 Safari/537.36"
    assert browser_user_agent(agent) == agent.replace("HeadlessChrome", "Chrome")


# ─── Stylesheets ─────────────────────────────────────────────────────────────

CSS = """
@charset "utf-8";
@import url(reset.css);
:root { --brand: #4f46e5; --Space-4: 1rem; color: red; }
html[data-theme="light"] { --surface: #f8fafc; }
:root .card { --not-root: 1px; }
.dark :root { --nested: 2px; }
@media (prefers-color-scheme: dark) { :root { --brand: #818cf8; --only-dark: #000; } }
@media (min-width:768px) { .a { color: red } .b { color: blue } }
@supports (display: grid) {
  @media (min-width: 768px) { .c { display: grid } }
  @layer components { @media (min-width: 1024px) { .d { gap: 8px } } }
}
.e { color: red; @media (min-width: 640px) { color: blue } }
@font-face {
  font-family: "Space Grotesk";
  src: local("Space Grotesk"), url(/f.woff2) format("woff2");
  font-weight: 300 700;
  font-style: normal;
  font-display: swap;
}
@font-face { src: url(/nameless.woff2); }
@keyframes spin { from { transform: rotate(0) } to { transform: rotate(1turn) } }
@property --angle { syntax: "<angle>"; inherits: false; initial-value: 0deg; }
.unterminated
"""


def test_parse_stylesheet_counts_rules_and_errors() -> None:
    info = parse_stylesheet("https://cdn.example/app.css", CSS, status=200)
    assert info.url == "https://cdn.example/app.css"
    assert info.status == 200 and info.inline is False
    assert info.size_bytes == len(CSS.encode("utf-8"))
    # :root, html[...], :root .card, .dark :root, dark :root, .a, .b, .c, .d, .e
    assert info.rule_count == 10
    assert info.parse_errors == 1


def test_parse_stylesheet_root_variables() -> None:
    variables = {v.name: v for v in parse_stylesheet("a.css", CSS).variables}
    assert variables["--brand"].declared == "#4f46e5"  # top-level light value wins over dark
    assert variables["--Space-4"].declared == "1rem"  # custom property names keep their case
    assert variables["--surface"].declared == "#f8fafc"
    assert variables["--only-dark"].declared == "#000"
    assert variables["--angle"].declared == "0deg"
    assert "--not-root" not in variables and "--nested" not in variables
    assert all(v.source == "stylesheet" and v.resolved is None for v in variables.values())


def test_parse_stylesheet_media_queries_nested_and_aggregated() -> None:
    media = {m.query: m for m in parse_stylesheet("a.css", CSS).media_queries}
    assert media["(min-width: 768px)"].occurrences == 2  # top level + inside @supports
    assert media["(min-width: 768px)"].min_width_px == 768
    assert media["(min-width: 1024px)"].occurrences == 1  # inside @supports > @layer
    assert media["(min-width: 640px)"].min_width_px == 640  # CSS nesting
    assert media["(prefers-color-scheme: dark)"].prefers_color_scheme == "dark"


def test_parse_stylesheet_font_faces() -> None:
    faces = parse_stylesheet("https://cdn.example/app.css", CSS).font_faces
    assert len(faces) == 1
    face = faces[0]
    assert face.family == "Space Grotesk"
    assert face.src == ['local("Space Grotesk")', 'url(/f.woff2) format("woff2")']
    assert (face.weight, face.style, face.display) == ("300 700", "normal", "swap")
    assert face.stylesheet_url == "https://cdn.example/app.css"


def test_parse_inline_stylesheet_flag() -> None:
    info = parse_stylesheet("inline:home#1", ":root{--x:1px}", inline=True)
    assert info.inline is True and info.status is None
    assert [v.name for v in info.variables] == ["--x"]


def test_fixture_site_stylesheets_match_the_contract() -> None:
    styles = parse_stylesheet("styles.css", (SITE_DIR / "styles.css").read_text("utf-8"))
    widths = {m.min_width_px for m in styles.media_queries if m.min_width_px}
    assert widths == {640, 768, 1024, 1280}
    assert {face.family for face in styles.font_faces} == {
        "Inter",
        "Space Grotesk",
        "JetBrains Mono",
    }
    theme = parse_stylesheet("theme.css", (SITE_DIR / "theme.css").read_text("utf-8"))
    assert theme.parse_errors == 1
    assert any(m.prefers_color_scheme == "dark" for m in theme.media_queries)


@pytest.mark.parametrize(
    ("prelude", "query", "low", "high"),
    [
        (
            "(min-width:768px)and (max-width:1023.98px)",
            "(min-width: 768px) and (max-width: 1023.98px)",
            768,
            1023.98,
        ),
        ("screen AND (MIN-WIDTH : 48em)", "screen and (min-width: 48em)", 768, None),
        ("(width>=640px)", "(width >= 640px)", 640, None),
        ("  print  ", "print", None, None),
    ],
)
def test_media_query_from_prelude(
    prelude: str, query: str, low: float | None, high: float | None
) -> None:
    media = media_query_from_prelude(prelude)
    assert media.query == query
    assert (media.min_width_px, media.max_width_px) == (low, high)
    assert media.occurrences == 1


def test_media_query_color_scheme() -> None:
    assert media_query_from_prelude("(prefers-color-scheme:dark)").prefers_color_scheme == "dark"
    assert normalize_media_query("(PREFERS-COLOR-SCHEME:  light)") == (
        "(prefers-color-scheme: light)"
    )


def test_stylesheet_registry_deduplicates_by_key() -> None:
    registry = StylesheetRegistry()
    first = StylesheetInfo(url="https://a.example/x.css")
    inline = StylesheetInfo(url="inline:home#1", inline=True)
    assert registry.add([("https://a.example/x.css", first), ("inline:abc", inline)]) == 2
    again = StylesheetInfo(url="inline:pricing#1", inline=True)
    assert registry.add([("https://a.example/x.css", first), ("inline:abc", again)]) == 0
    assert [sheet.url for sheet in registry.sheets] == ["https://a.example/x.css", "inline:home#1"]


# ─── Hover ───────────────────────────────────────────────────────────────────


def _sample_elements() -> dict[str, ElementSample]:
    page = load_raw_sample().pages[0]
    found: dict[str, ElementSample] = {}
    for element in page.elements:
        for name in ("btn-primary", "btn-outline", "btn-ghost", "btn-secondary", "text-link"):
            if name in element.classes and name not in found:
                found[name] = element
        if "cookie-accept" in element.classes:
            found["cookie"] = element
    return found


def test_hover_kinds_on_sample_page() -> None:
    found = _sample_elements()
    for name in ("btn-primary", "btn-outline", "btn-ghost", "btn-secondary"):
        assert hover_kind(found[name]) == "button", name
    assert hover_kind(found["text-link"]) == "link"
    assert hover_kind(found["cookie"]) is None  # overlay


def test_select_hover_candidates_on_sample_page() -> None:
    page = load_raw_sample().pages[0]
    by_index = {element.index: element for element in page.elements}
    chosen = select_hover_candidates(page.elements)
    buttons = [index for index, kind in chosen if kind == "button"]
    links = [index for index, kind in chosen if kind == "link"]
    assert chosen == [(i, "button") for i in buttons] + [(i, "link") for i in links]

    classes = {name for index in buttons for name in by_index[index].classes}
    assert {"btn-primary", "btn-outline", "btn-ghost", "btn-secondary"} <= classes
    assert all(not by_index[index].in_overlay for index, _ in chosen)

    # At most two elements per identical style: 5 identical nav links -> 2.
    nav_links = [i for i in links if "nav-link" in by_index[i].classes]
    assert len(nav_links) == 2
    # Footer links come last.
    landmarks = [by_index[i].landmark for i in links]
    first_footer = landmarks.index("footer")
    assert all(landmark == "footer" for landmark in landmarks[first_footer:])
    assert "text-link" in by_index[links[0]].classes or by_index[links[0]].landmark == "nav"


def test_select_hover_candidates_respects_limits() -> None:
    elements = load_raw_sample().pages[0].elements
    chosen = select_hover_candidates(elements, max_buttons=2, max_links=1)
    assert [kind for _, kind in chosen] == ["button", "button", "link"]
    assert select_hover_candidates(elements, max_buttons=0, max_links=0) == []


def test_execution_order_puts_header_items_last() -> None:
    elements = load_raw_sample().pages[0].elements
    chosen = select_hover_candidates(elements)
    by_index = {element.index: element for element in elements}
    ordered = execution_order(chosen, elements)
    assert sorted(ordered) == sorted(chosen)
    flags = [by_index[index].landmark in ("header", "nav") for index, _ in ordered]
    assert flags == sorted(flags)


def test_diff_styles_reports_changed_fields_with_joined_sides() -> None:
    before = ComputedStyle(
        background_color="rgba(0, 0, 0, 0)",
        border_width=("1px", "1px", "1px", "1px"),
        border_style=("solid", "solid", "solid", "solid"),
        border_color=("rgb(226, 232, 240)",) * 4,
        padding=("8px", "12px", "8px", "12px"),
    )
    after = before.model_copy(
        update={
            "background_color": "rgb(248, 250, 252)",
            "border_color": ("rgb(203, 213, 225)",) * 4,
        }
    )
    assert diff_styles(before, after) == {
        "background_color": ("rgba(0, 0, 0, 0)", "rgb(248, 250, 252)"),
        "border_color": ("rgb(226, 232, 240)", "rgb(203, 213, 225)"),
    }
    assert diff_styles(before, before) == {}


def test_diff_styles_joins_unequal_sides() -> None:
    before = ComputedStyle(padding=("8px", "12px", "8px", "12px"))
    after = ComputedStyle(padding=("8px", "16px", "8px", "16px"))
    assert diff_styles(before, after) == {
        "padding": ("8px | 12px | 8px | 12px", "8px | 16px | 8px | 16px")
    }


def test_diff_styles_ignores_current_color_echo_without_border() -> None:
    before = ComputedStyle(color="rgb(71, 85, 105)", border_color=("rgb(71, 85, 105)",) * 4)
    after = ComputedStyle(color="rgb(15, 23, 42)", border_color=("rgb(15, 23, 42)",) * 4)
    assert diff_styles(before, after) == {"color": ("rgb(71, 85, 105)", "rgb(15, 23, 42)")}


def test_hover_wait_bounds() -> None:
    assert hover_wait_ms(0) == 150
    assert hover_wait_ms(240) == 240
    assert hover_wait_ms(5000) == 1000


# ─── Discovery ───────────────────────────────────────────────────────────────


def test_discover_pages_on_sample_page() -> None:
    page = load_raw_sample().pages[0]
    top = discover_pages(page, limit=4)
    assert set(top) == {
        "https://acme.example/product",
        "https://acme.example/solutions",
        "https://acme.example/pricing",
        "https://acme.example/docs",
    }
    # Pricing is linked from the nav, the body and the footer: duplicates add weight.
    assert top[0] == "https://acme.example/pricing"
    assert discover_pages(page, limit=2) == top[:2]
    assert discover_pages(page, limit=0) == []


def test_discover_pages_exclusions() -> None:
    every = discover_pages(load_raw_sample().pages[0], limit=50)
    joined = " ".join(every)
    for fragment in ("login", "signup", "legal", "careers", "status.acme.example"):
        assert fragment not in joined
    assert "https://acme.example/" not in every  # the page itself
    assert len(every) == len(set(every))
    assert {"https://acme.example/demo", "https://acme.example/about"} <= set(every)


def _link(index: int, href: str, *, landmark: str | None = None, y: float = 500) -> ElementSample:
    return ElementSample(
        index=index,
        tag="a",
        href=href,
        text="link",
        landmark=landmark,
        box=BoundingBox(x=0, y=y, width=80, height=20),
    )


def test_discover_pages_ranking_and_normalization() -> None:
    page = PageCapture(
        url="https://www.acme.example/",
        final_url="https://www.acme.example/",
        elements=[
            _link(0, "https://acme.example/blog#latest", landmark="footer", y=3000),
            _link(1, "/features/", landmark="nav", y=20),
            _link(2, "/features?utm_source=x", landmark="main"),
            _link(3, "/docs/api/v2", landmark="nav", y=900),
            _link(4, "/brochure.pdf", landmark="nav", y=20),
            _link(5, "mailto:hi@acme.example", landmark="nav", y=20),
            _link(6, "https://other.example/x", landmark="nav", y=20),
            _link(7, "#top", landmark="nav", y=20),
            _link(8, "/", landmark="header", y=20),
        ],
    )
    assert discover_pages(page, limit=10) == [
        "https://www.acme.example/features/",
        "https://www.acme.example/docs/api/v2",
        "https://acme.example/blog",
    ]


def test_candidate_url_normalization() -> None:
    base = "https://acme.example/products/"
    assert candidate_url("team", base_url=base) == "https://acme.example/products/team"
    assert candidate_url("/a?utm_medium=x&id=3#f", base_url=base) == "https://acme.example/a?id=3"
    for href in ("mailto:a@b.c", "tel:123", "javascript:void(0)", "#x", "/f.PDF", "ftp://x/y"):
        assert candidate_url(href, base_url=base) is None
    assert url_key("https://www.acme.example/a/") == url_key("https://acme.example/a")
    assert is_excluded_path("https://acme.example/sign-up") is True
    assert is_excluded_path("https://acme.example/pricing") is False


def test_unique_page_name() -> None:
    assert unique_page_name("https://acme.example/pricing", ["home"]) == "pricing"
    assert unique_page_name("https://acme.example/pricing/", ["home", "pricing"]) == "pricing-2"
    assert unique_page_name("https://acme.example/pricing", ["pricing", "pricing-2"]) == (
        "pricing-3"
    )
    assert unique_page_name("https://acme.example/", []) == "home"


# ─── DOM payload ─────────────────────────────────────────────────────────────


def _payload() -> dict[str, Any]:
    return {
        "title": "  Acme  ",
        "meta_description": "",
        "lang": "en",
        "document_height": 2116,
        "root_font_size_px": 16,
        "css_variables": [
            {
                "name": "--brand",
                "declared": "#4f46e5",
                "resolved": "rgb(79, 70, 229)",
                "source": "root",
            },
            {"name": "", "declared": "x", "resolved": None, "source": "root"},
        ],
        "elements_total": 3,
        "elements": [
            {
                "index": 0,
                "tag": "html",
                "box": {"x": 0, "y": 0, "width": 1440, "height": 2116},
                "styles": {"color": "rgb(15, 23, 42)", "padding": ["0px", "0px", "0px", "0px"]},
            },
            {
                "index": 1,
                "tag": "a",
                "classes": ["btn"],
                "text": "  Start   building   free and keep going until the text is too long  ",
                "has_own_text": True,
                "href": "https://acme.example/signup",
                "box": {"x": 10, "y": 20, "width": 190, "height": 44},
                "depth": 1,
                "parent_index": 0,
                "landmark": "main",
                "styles": {
                    "border_radius": ["8px", "8px", "8px", "8px"],
                    "future_property": "ignored",
                },
                "unknown_field": True,
            },
        ],
        "timing_ms": {"walk": 3, "elements": 4},
    }


def test_parse_extraction_validates_payload() -> None:
    result = parse_extraction(_payload())
    assert result.title == "Acme"
    assert result.meta_description is None
    assert result.lang == "en"
    assert result.document_height == 2116
    assert [variable.name for variable in result.css_variables] == ["--brand"]
    assert result.elements_total == 3 and result.truncated
    link = result.elements[1]
    assert link.styles.border_radius == ("8px", "8px", "8px", "8px")
    assert link.text == "Start building free and keep going until the text "[:50]
    assert len(link.text) <= 50
    assert link.parent_index == 0 and link.landmark == "main"
    assert result.timing_ms == {"walk": 3.0, "elements": 4.0}


def test_parse_extraction_rejects_broken_elements() -> None:
    payload = _payload()
    del payload["elements"][1]["box"]
    with pytest.raises(CollectError, match="không hợp lệ"):
        parse_extraction(payload)


# ─── Viewports & orchestration helpers ───────────────────────────────────────


def test_viewport_heights_and_names() -> None:
    assert [viewport_height_for(width) for width in (1440, 768, 390)] == [900, 1024, 844]
    assert viewport_height_for(1280) == 900
    assert viewport_height_for(600) == 1024
    assert viewport_height_for(320) == 844
    assert screenshot_name("home", 1440) == "home-1440"
    assert screenshot_name("home", 1440, "dark") == "home-dark-1440"


def test_measurement_order_is_widest_first() -> None:
    assert measurement_order([1440, 768, 390]) == [(0, 1440), (1, 768), (2, 390)]
    assert measurement_order([390, 1440, 768]) == [(1, 1440), (2, 768), (0, 390)]
    assert measurement_order([768, 768]) == [(0, 768), (1, 768)]
    assert measurement_order([]) == []


def test_layout_from_payload() -> None:
    snapshot = layout_from_payload(
        {"viewport_height": 844, "main_columns": 1, "hamburger_visible": True, "extra": 1},
        viewport_width=390,
    )
    assert snapshot.viewport_width == 390
    assert snapshot.hamburger_visible is True and snapshot.main_columns == 1


def _shot(page: str, width: int, scheme: ColorScheme = "light") -> Screenshot:
    return Screenshot(
        name=screenshot_name(page, width, scheme),
        viewport_width=width,
        viewport_height=viewport_height_for(width),
        color_scheme=scheme,
        path=screenshot_filename(page, width, scheme),
        full_page=True,
    )


def test_referenced_screenshots_cover_pages_and_dark() -> None:
    home = "https://acme.example/"
    pricing = "https://acme.example/pricing"
    capture = RawCapture(
        tool_version="0",
        source_url=home,
        domain="acme.example",
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        pages=[
            PageCapture(url=home, final_url=home, screenshots=[_shot("home", 1440)]),
            PageCapture(
                url=pricing, final_url=pricing, name="pricing", screenshots=[_shot("pricing", 390)]
            ),
        ],
        dark=PageCapture(
            url=home,
            final_url=home,
            color_scheme="dark",
            screenshots=[_shot("home", 1440, "dark")],
        ),
    )
    assert collect.referenced_screenshots(capture) == {
        "home-1440.png",
        "pricing-390.png",
        "home-dark-1440.png",
    }


def test_remove_stale_screenshots(tmp_path: Path) -> None:
    folder = tmp_path / "screenshots"
    folder.mkdir()
    names = [
        "home-1440.png",
        "home-dark-1440.png",
        "contact-sales-1440.png",
        "contact-sales-390.png",
        "pricing-2-768.png",
        "notes.png",  # not a collector name
        "Logo-1440.png",  # uppercase: never written by the collector
        "home-1440.png.tmp",
    ]
    for name in names:
        (folder / name).write_bytes(b"x")
    (folder / "old-1440.png").mkdir()  # a folder, even with a matching name, is kept
    (tmp_path / "prompt-images").mkdir()
    (tmp_path / "prompt-images" / "desktop.png").write_bytes(b"x")

    removed = collect.remove_stale_screenshots(tmp_path, {"home-1440.png", "home-dark-1440.png"})

    assert removed == ["contact-sales-1440.png", "contact-sales-390.png", "pricing-2-768.png"]
    left = sorted(path.name for path in folder.iterdir())
    kept = ["home-1440.png", "home-dark-1440.png", "notes.png", "Logo-1440.png"]
    assert left == sorted([*kept, "home-1440.png.tmp", "old-1440.png"])
    assert (tmp_path / "prompt-images" / "desktop.png").is_file()


def test_remove_stale_screenshots_without_folder(tmp_path: Path) -> None:
    assert collect.remove_stale_screenshots(tmp_path, set()) == []
    assert collect.remove_stale_screenshots(tmp_path / "missing", set()) == []


def test_collect_warnings_are_vietnamese() -> None:
    assert collect._truncation_warning(10, 10) is None
    assert collect._truncation_warning(3199, 3000) == (
        "Trang có 3199 phần tử hiển thị — chỉ lấy mẫu 3000."
    )
    assert collect._overlay_warning([]) is None
    note = collect._overlay_warning([{"kind": "cookie"}, {"kind": "backdrop"}])
    assert note is not None and "2 lớp phủ" in note and "banner cookie" in note
