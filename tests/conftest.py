from __future__ import annotations

import functools
import os
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from get_design_markdown.models.analysis import (
    ColorCluster,
    ColorPalette,
    ColorRole,
    ColorToken,
    ComponentKind,
    ComponentSpec,
    ContrastPair,
    DesignAnalysis,
    FontFamilyInfo,
    FontRole,
    ScaleStep,
    SiteInfo,
    TypeLevel,
    Typography,
)
from get_design_markdown.models.raw import (
    BoundingBox,
    ComputedStyle,
    CssVariable,
    ElementSample,
    HoverState,
    LayoutSnapshot,
    PageCapture,
    RawCapture,
    RobotsCheck,
    Screenshot,
)
from tests.fixture_data import SITE_DIR

FIXED_TIME = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)

#: Set to 1 in CI so that a missing Chromium fails browser tests instead of skipping them.
REQUIRE_BROWSER_ENV = "GDM_REQUIRE_BROWSER"


# ─── Browser availability (marker "browser") ─────────────────────────────────


@functools.cache
def chromium_problem() -> str | None:
    """None when Playwright's Chromium can be launched, else the reason."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            browser.close()
    except Exception as error:  # pragma: no cover - depends on the machine
        return f"{type(error).__name__}: {str(error).splitlines()[0] if str(error) else ''}"
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    browser_items = [item for item in items if item.get_closest_marker("browser")]
    if not browser_items or os.environ.get(REQUIRE_BROWSER_ENV) == "1":
        return
    problem = chromium_problem()
    if problem is None:
        return
    skip = pytest.mark.skip(
        reason=f"Chromium chưa sẵn sàng ({problem}) — chạy 'python -m uv run playwright "
        "install chromium'."
    )
    for item in browser_items:
        item.add_marker(skip)


# ─── Static fixture site server ──────────────────────────────────────────────


class _FixtureSiteHandler(SimpleHTTPRequestHandler):
    """Serves tests/fixtures/site. Query parameters for collector tests:
    ?status=403 answers with that status (body still the file), ?delay=2.5 waits first
    (seconds, capped at 15)."""

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_response(self, code: int, message: str | None = None) -> None:
        query = parse_qs(urlsplit(self.path).query)
        forced = query.get("status", [""])[0]
        if code == 200 and forced.isdigit():
            code = int(forced)
        super().send_response(code, message)

    def do_GET(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        delay = query.get("delay", [""])[0]
        try:
            seconds = min(float(delay), 15.0) if delay else 0.0
        except ValueError:
            seconds = 0.0
        if seconds > 0:
            time.sleep(seconds)
        super().do_GET()


def serve_directory(directory: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = functools.partial(_FixtureSiteHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host!s}:{port}/"


@pytest.fixture(scope="session")
def site_url() -> Iterator[str]:
    """Base URL ("http://127.0.0.1:<port>/") of tests/fixtures/site served over HTTP.

    The same files are reachable on "http://localhost:<port>/" — a different origin, useful
    for cross-origin stylesheet tests. Skips when the site does not exist yet.
    """
    if not (SITE_DIR / "index.html").is_file():
        pytest.skip(
            "Trang fixture tests/fixtures/site/index.html chưa được tạo (stream collector)."
        )
    server, url = serve_directory(SITE_DIR)
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()


# ─── Small hand-made models ──────────────────────────────────────────────────


@pytest.fixture
def raw_capture() -> RawCapture:
    button = ElementSample(
        index=0,
        tag="a",
        classes=["btn", "btn-primary"],
        text="Start now",
        has_own_text=True,
        href="/start",
        box=BoundingBox(x=100, y=40, width=120, height=40),
        styles=ComputedStyle(
            color="rgb(255, 255, 255)",
            background_color="rgb(99, 91, 255)",
            font_family='"Sohne", sans-serif',
            font_size="15px",
            font_weight="500",
            padding=("8px", "16px", "8px", "16px"),
            border_radius=("999px", "999px", "999px", "999px"),
            display="inline-flex",
            cursor="pointer",
        ),
    )
    page = PageCapture(
        url="https://stripe.com/",
        final_url="https://stripe.com/",
        title="Stripe",
        meta_description="Financial infrastructure to grow your revenue.",
        lang="en",
        document_height=9000,
        css_variables=[
            CssVariable(name="--accent", declared="#635bff", resolved="#635bff", source="root")
        ],
        elements=[button],
        hover_states=[
            HoverState(
                element_index=0,
                kind="button",
                changed={"background_color": ("rgb(99, 91, 255)", "rgb(10, 37, 64)")},
            )
        ],
        layouts=[LayoutSnapshot(viewport_width=390, hamburger_visible=True, min_tap_target_px=40)],
        screenshots=[
            Screenshot(
                name="home-1440",
                viewport_width=1440,
                viewport_height=900,
                path="screenshots/home-1440.png",
            )
        ],
    )
    return RawCapture(
        tool_version="0.1.0",
        source_url="https://stripe.com/",
        domain="stripe.com",
        collected_at=FIXED_TIME,
        robots=RobotsCheck(robots_url="https://stripe.com/robots.txt", allowed=True, note="ok"),
        pages=[page],
    )


@pytest.fixture
def design_analysis() -> DesignAnalysis:
    return DesignAnalysis(
        tool_version="0.1.0",
        analyzed_at=FIXED_TIME,
        site=SiteInfo(url="https://stripe.com/", name="Stripe", pages_analyzed=["home"]),
        colors=ColorPalette(
            clusters=[
                ColorCluster(
                    id="c1",
                    channel="background",
                    hex="#635bff",
                    weight=1200.0,
                    share=0.04,
                    chroma=0.2,
                    hue=280,
                )
            ],
            tokens=[
                ColorToken(name="primary", value="#635bff", role=ColorRole.BRAND, cluster_id="c1")
            ],
            contrast_pairs=[
                ContrastPair(
                    foreground="#ffffff",
                    background="#635bff",
                    ratio=4.63,
                    passes_aa=True,
                    passes_aa_large=True,
                    passes_aaa=False,
                    occurrences=3,
                )
            ],
        ),
        typography=Typography(
            families=[FontFamilyInfo(name="Sohne", role=FontRole.PRIMARY, share=0.9)],
            levels=[TypeLevel(name="body", size_px=16, font_family="Sohne", weight=400)],
        ),
        radii=[ScaleStep(name="full", value_px=999, count=12)],
        components=[
            ComponentSpec(
                name="button-primary",
                kind=ComponentKind.BUTTON,
                count=3,
                properties={"backgroundColor": "#635bff"},
                token_refs={"backgroundColor": "{colors.primary}"},
                states={"hover": {"backgroundColor": "#0a2540"}},
            )
        ],
    )
