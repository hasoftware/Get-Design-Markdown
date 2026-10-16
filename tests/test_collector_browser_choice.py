"""Browser choice of the collector: auto / chromium / msedge / chrome with fallback.

The launch chain is tested with a fake launcher (no browser); the tests marked `browser` launch
the real browsers that exist on this machine (Edge / Chrome tests skip when they are missing).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.async_api import Error as PlaywrightError

from get_design_markdown.collector import browser
from get_design_markdown.collector.browser import (
    AUTO_BROWSER_ORDER,
    LaunchFailure,
    LaunchTarget,
    classify_launch_error,
    launch_browser,
    launch_candidates,
    open_browser,
)
from get_design_markdown.collector.collect import collect_site
from get_design_markdown.console import ConsoleEvent, use_sink
from get_design_markdown.errors import (
    BROWSER_INSTALL_HINT,
    BrowserNotInstalledError,
    CollectError,
    JobCancelledError,
)
from get_design_markdown.models.raw import BrowserChoice, CaptureOptions
from get_design_markdown.ui import browser_install

MISSING_CHROMIUM = (
    "BrowserType.launch: Executable doesn't exist at C:\\Users\\me\\AppData\\Local\\"
    "ms-playwright\\chromium_headless_shell-1248\\chrome-headless-shell-win64\\"
    "chrome-headless-shell.exe\n╔════╗\n║ Looks like Playwright was just installed or updated. ║\n"
    "║ Please run the following command to download new browsers: ║\n"
    "║     playwright install ║"
)
MISSING_EDGE = (
    "BrowserType.launch: Chromium distribution 'msedge' is not found at "
    "C:\\Users\\me\\AppData\\Local\\Microsoft\\Edge\\Application\\msedge.exe\n"
    'Run "playwright install msedge"'
)
MISSING_CHROME = (
    "BrowserType.launch: Chromium distribution 'chrome' is not found at "
    '/opt/google/chrome/chrome\nRun "playwright install chrome"'
)
CRASHED = (
    "BrowserType.launch: Target page, context or browser has been closed\n"
    "Browser logs:\n[pid=123][err] crashed"
)


# ─── Pure parts ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        (MISSING_CHROMIUM, "missing"),
        (MISSING_EDGE, "missing"),
        (MISSING_CHROME, "missing"),
        ("Chromium distribution 'msedge' is not supported on linux-arm64", "missing"),
        ("Please run: playwright install chromium", "missing"),
        (CRASHED, "failed"),
        ("spawn UNKNOWN", "failed"),
        ("Timeout 180000ms exceeded.", "failed"),
        ("", "failed"),
    ],
)
def test_classify_launch_error(message: str, kind: str) -> None:
    assert classify_launch_error(PlaywrightError(message)) == kind


def test_launch_candidates() -> None:
    assert launch_candidates("auto") == ("chromium", "msedge", "chrome")
    assert launch_candidates("chromium") == ("chromium",)
    assert launch_candidates("msedge") == ("msedge",)
    assert launch_candidates("chrome") == ("chrome",)


def test_auto_order_matches_the_ui_status() -> None:
    assert AUTO_BROWSER_ORDER == browser_install.AUTO_ORDER


# ─── Fallback chain with a fake launcher ─────────────────────────────────────


class FakeBrowser:
    """The parts of playwright's Browser that open_browser() uses."""

    def __init__(self, name: str, version: str = "150.0.1.2") -> None:
        self.name = name
        self.version = version
        self.closed = False
        self.pages = 0

    async def new_page(self) -> FakePage:
        self.pages += 1
        return FakePage()

    async def close(self) -> None:
        self.closed = True


class FakePage:
    async def evaluate(self, expression: str) -> str:
        assert expression == "navigator.userAgent"
        return "Mozilla/5.0 (Windows NT 10.0) HeadlessChrome/150.0.0.0 Safari/537.36"

    async def close(self) -> None:
        return None


class FakeLauncher:
    """Launches the browsers in `available`; the others raise their configured error."""

    def __init__(self, errors: dict[str, str], available: tuple[str, ...] = ()) -> None:
        self.errors = errors
        self.available = available
        self.calls: list[str] = []
        self.launched: list[FakeBrowser] = []

    async def __call__(self, name: LaunchTarget) -> Any:
        self.calls.append(name)
        if name in self.available:
            fake = FakeBrowser(name)
            self.launched.append(fake)
            return fake
        raise PlaywrightError(self.errors.get(name, f"no fake for {name}"))


def _launch(
    choice: BrowserChoice, launcher: FakeLauncher
) -> tuple[LaunchTarget, Any, list[LaunchFailure]]:
    return asyncio.run(launch_browser(choice, launcher))


ALL_MISSING = {"chromium": MISSING_CHROMIUM, "msedge": MISSING_EDGE, "chrome": MISSING_CHROME}


def test_auto_uses_chromium_first() -> None:
    launcher = FakeLauncher(ALL_MISSING, available=("chromium", "msedge", "chrome"))
    name, launched, failures = _launch("auto", launcher)
    assert (name, launcher.calls, failures) == ("chromium", ["chromium"], [])
    assert launched is launcher.launched[0]


def test_auto_falls_back_to_edge_then_chrome() -> None:
    launcher = FakeLauncher(ALL_MISSING, available=("msedge",))
    name, _, failures = _launch("auto", launcher)
    assert name == "msedge" and launcher.calls == ["chromium", "msedge"]
    assert [(failure.name, failure.kind) for failure in failures] == [("chromium", "missing")]
    assert failures[0].reason.startswith("BrowserType.launch: Executable doesn't exist")

    launcher = FakeLauncher(ALL_MISSING, available=("chrome",))
    name, _, failures = _launch("auto", launcher)
    assert name == "chrome" and launcher.calls == ["chromium", "msedge", "chrome"]
    assert [failure.kind for failure in failures] == ["missing", "missing"]


def test_auto_without_any_browser_raises_browser_not_installed() -> None:
    launcher = FakeLauncher(ALL_MISSING)
    with pytest.raises(BrowserNotInstalledError) as caught:
        _launch("auto", launcher)
    assert launcher.calls == ["chromium", "msedge", "chrome"]
    assert caught.value.message == BrowserNotInstalledError().message
    assert caught.value.hint == BROWSER_INSTALL_HINT


def test_auto_skips_a_broken_browser_and_warns() -> None:
    launcher = FakeLauncher({**ALL_MISSING, "chromium": CRASHED}, available=("msedge",))
    name, _, failures = _launch("auto", launcher)
    assert name == "msedge"
    assert [(failure.name, failure.kind) for failure in failures] == [("chromium", "failed")]
    warning = browser._fallback_warning(name, failures)
    assert warning is not None
    assert "Không khởi động được Chromium" in warning and "Microsoft Edge" in warning
    # A browser that is only missing is skipped silently.
    assert browser._fallback_warning("msedge", [LaunchFailure("chromium", "missing", "x")]) is None


def test_auto_with_a_broken_browser_and_nothing_else_reports_it() -> None:
    launcher = FakeLauncher({**ALL_MISSING, "chromium": CRASHED})
    with pytest.raises(CollectError) as caught:
        _launch("auto", launcher)
    assert not isinstance(caught.value, BrowserNotInstalledError)
    assert caught.value.message == (
        "Không khởi động được Chromium: BrowserType.launch: Target page, context or browser "
        "has been closed"
    )
    assert caught.value.hint is not None and "playwright install chromium" in caught.value.hint


def test_auto_with_several_broken_browsers_lists_them() -> None:
    launcher = FakeLauncher(
        {"chromium": CRASHED, "msedge": "spawn EACCES", "chrome": MISSING_CHROME}
    )
    with pytest.raises(CollectError) as caught:
        _launch("auto", launcher)
    message = caught.value.message
    assert message.startswith("Không khởi động được trình duyệt nào (")
    assert "Microsoft Edge: spawn EACCES" in message and "Google Chrome: chưa cài" in message


@pytest.mark.parametrize(
    ("choice", "error", "named"),
    [
        ("chromium", MISSING_CHROMIUM, "Chromium của Playwright"),
        ("msedge", MISSING_EDGE, "Microsoft Edge"),
        ("chrome", MISSING_CHROME, "Google Chrome"),
    ],
)
def test_explicit_choice_that_is_missing(choice: BrowserChoice, error: str, named: str) -> None:
    launcher = FakeLauncher({choice: error}, available=tuple(n for n in ALL_MISSING if n != choice))
    with pytest.raises(BrowserNotInstalledError) as caught:
        _launch(choice, launcher)
    assert launcher.calls == [choice]  # never falls back to another browser
    assert (
        named in caught.value.message and f"trình duyệt đã chọn: {choice}" in caught.value.message
    )
    assert caught.value.hint is not None and "--browser auto" in caught.value.hint


def test_explicit_choice_that_fails_keeps_the_previous_message() -> None:
    launcher = FakeLauncher({"chromium": "spawn failed\nmore"})
    with pytest.raises(CollectError) as caught:
        _launch("chromium", launcher)
    assert not isinstance(caught.value, BrowserNotInstalledError)
    assert caught.value.message == "Không khởi động được Chromium: spawn failed"
    launcher = FakeLauncher({"msedge": "spawn EPERM"})
    with pytest.raises(CollectError) as caught:
        _launch("msedge", launcher)
    assert caught.value.message == "Không khởi động được Microsoft Edge: spawn EPERM"


@pytest.mark.parametrize("choice", ["chromium", "msedge", "chrome"])
def test_explicit_choice_launches_exactly_that_browser(choice: BrowserChoice) -> None:
    launcher = FakeLauncher({}, available=("chromium", "msedge", "chrome"))
    name, _, failures = _launch(choice, launcher)
    assert (name, launcher.calls, failures) == (choice, [choice], [])


def test_unexpected_exceptions_are_not_swallowed() -> None:
    class Boom(RuntimeError):
        pass

    async def launcher(name: LaunchTarget) -> Any:
        raise Boom(name)

    with pytest.raises(Boom):
        asyncio.run(launch_browser("auto", launcher))


class LauncherPlan:
    """What the fake launchers of open_browser() do; keeps every launcher it made."""

    def __init__(self) -> None:
        self.errors: dict[str, str] = {}
        self.available: tuple[str, ...] = ()
        self.made: list[FakeLauncher] = []

    def factory(self, playwright: object) -> FakeLauncher:
        launcher = FakeLauncher(self.errors, self.available)
        self.made.append(launcher)
        return launcher


@pytest.fixture
def plan(monkeypatch: pytest.MonkeyPatch) -> LauncherPlan:
    """open_browser() with Playwright's launch replaced (the driver itself still starts)."""
    plan = LauncherPlan()
    monkeypatch.setattr(browser, "playwright_launcher", plan.factory)
    return plan


def test_open_browser_labels_the_browser_really_used(plan: LauncherPlan) -> None:
    plan.errors, plan.available = ALL_MISSING, ("msedge",)

    async def run() -> tuple[str, str]:
        async with open_browser("auto") as session:
            return session.label, session.user_agent

    label, agent = asyncio.run(run())
    assert label == "msedge 150.0.1.2"
    assert "HeadlessChrome" not in agent and "Chrome/150" in agent
    assert plan.made[0].launched[0].closed


def test_open_browser_warns_about_a_skipped_broken_browser(plan: LauncherPlan) -> None:
    plan.errors, plan.available = {**ALL_MISSING, "chromium": CRASHED}, ("chrome",)
    events: list[ConsoleEvent] = []

    async def run() -> str:
        with use_sink(events.append):
            async with open_browser("auto") as session:
                return session.label

    assert asyncio.run(run()) == "chrome 150.0.1.2"
    assert [event.kind for event in events] == ["warning"]
    assert "dùng Google Chrome thay thế" in events[0].message


def test_open_browser_closes_the_browser_when_the_warning_cancels(plan: LauncherPlan) -> None:
    plan.errors, plan.available = {**ALL_MISSING, "chromium": CRASHED}, ("msedge",)

    def cancel(event: ConsoleEvent) -> None:
        raise JobCancelledError()

    async def run() -> None:
        with use_sink(cancel):
            async with open_browser("auto"):
                pytest.fail("the session must not be yielded")

    with pytest.raises(JobCancelledError):
        asyncio.run(run())
    assert plan.made[0].launched[0].closed


def test_open_browser_without_any_browser(plan: LauncherPlan) -> None:
    plan.errors = ALL_MISSING

    async def run() -> None:
        async with open_browser("auto"):
            pytest.fail("the session must not be yielded")

    with pytest.raises(BrowserNotInstalledError):
        asyncio.run(run())
    assert plan.made[0].calls == ["chromium", "msedge", "chrome"]


class StuckBrowser(FakeBrowser):
    """A browser that launched but never opens a page (no renderer starts)."""

    async def new_page(self) -> FakePage:
        self.pages += 1
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def test_probe_user_agent_gives_up_on_a_browser_that_opens_no_page() -> None:
    stuck = StuckBrowser("chromium")

    async def run() -> str:
        return await browser.probe_user_agent(cast(Any, stuck), "msedge", timeout_s=0.05)

    with pytest.raises(CollectError) as caught:
        asyncio.run(run())
    assert "Microsoft Edge đã chạy nhưng không mở được trang nào trong 0.05 giây" in str(
        caught.value
    )
    assert caught.value.hint is not None and "--browser" in caught.value.hint
    assert stuck.pages == 1


def test_open_browser_closes_a_browser_that_opens_no_page(
    plan: LauncherPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan.errors, plan.available = ALL_MISSING, ("chromium",)
    monkeypatch.setattr(browser, "BROWSER_START_TIMEOUT_S", 0.05)
    launched: list[FakeBrowser] = []
    original = FakeLauncher.__call__

    async def stuck_launch(self: FakeLauncher, name: LaunchTarget) -> Any:
        await original(self, name)
        fake = StuckBrowser(name)
        self.launched[-1] = fake
        launched.append(fake)
        return fake

    monkeypatch.setattr(FakeLauncher, "__call__", stuck_launch)

    async def run() -> None:
        async with open_browser("chromium"):
            pytest.fail("the session must not be yielded")

    with pytest.raises(CollectError):
        asyncio.run(run())
    assert launched and launched[0].closed


# ─── Real browsers (marker: browser) ─────────────────────────────────────────


def _installed(name: str) -> bool:
    status = browser_install.browser_status()
    return any(info.name == name and info.available for info in status.browsers)


async def _label(choice: BrowserChoice) -> str:
    async with open_browser(choice) as session:
        page = await session.browser.new_page()
        await page.goto("about:blank")
        await page.close()
        return session.label


@pytest.mark.browser
def test_real_chromium_and_auto() -> None:
    label = asyncio.run(_label("chromium"))
    assert label.startswith("chromium ") and label.split(" ", 1)[1][:1].isdigit()
    # Playwright's Chromium is installed wherever the browser tests run, so auto picks it.
    assert asyncio.run(_label("auto")) == label


@pytest.mark.browser
@pytest.mark.parametrize("choice", ["msedge", "chrome"])
def test_real_installed_channel(choice: BrowserChoice) -> None:
    if not _installed(choice):
        pytest.skip(f"{choice} chưa được cài trên máy này.")
    label = asyncio.run(_label(choice))
    assert label.startswith(f"{choice} ") and label.split(" ", 1)[1][:1].isdigit()


@pytest.mark.browser
def test_fixture_site_with_edge_matches_chromium(site_url: str, tmp_path: Path) -> None:
    if not _installed("msedge"):
        pytest.skip("Microsoft Edge chưa được cài trên máy này.")
    quick = CaptureOptions(screenshot_widths=[], hover_buttons=0, hover_links=0, timeout_s=30)
    edge = asyncio.run(
        collect_site(
            site_url,
            out_dir=tmp_path / "edge",
            options=quick.model_copy(update={"browser": "msedge"}),
        )
    )
    chromium = asyncio.run(
        collect_site(
            site_url,
            out_dir=tmp_path / "chromium",
            options=quick.model_copy(update={"browser": "chromium"}),
        )
    )
    assert edge.browser is not None and edge.browser.startswith("msedge ")
    assert chromium.browser is not None and chromium.browser.startswith("chromium ")
    assert edge.options is not None and edge.options.browser == "msedge"
    edge_page, chromium_page = edge.pages[0], chromium.pages[0]
    assert edge_page.title == chromium_page.title
    assert {variable.name for variable in edge_page.css_variables} == {
        variable.name for variable in chromium_page.css_variables
    }
    # Same engine family: the same elements, give or take what another Chromium release
    # renders differently (the installed Edge is rarely the exact build Playwright pins).
    edge_tags = Counter(element.tag for element in edge_page.elements)
    chromium_tags = Counter(element.tag for element in chromium_page.elements)
    difference = (edge_tags - chromium_tags) + (chromium_tags - edge_tags)
    assert sum(difference.values()) <= max(2, len(chromium_page.elements) // 50), difference
    edge_classes = {name for element in edge_page.elements for name in element.classes}
    chromium_classes = {name for element in chromium_page.elements for name in element.classes}
    assert {"btn-primary", "card", "main-nav"} <= edge_classes & chromium_classes
