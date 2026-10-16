"""End-to-end: the real pipeline through the real local UI server on the fixture site.

ui.server.UiServer(mode="none") runs with its default JobManager, whose runner is
jobs.run_pipeline (the same pipeline as `gdm get`), on a temporary output root and settings file.
One job goes through the HTTP API exactly as the frontend drives it: token header,
POST /api/jobs, then GET /api/jobs/{id}?since=<last seq> until the job finished. Its DESIGN.md,
tokens.json and preview.html must be identical (timestamps aside) to what `gdm get` writes for
the same URL, pass the independent verifier (scripts/verify_output.py) and be served safely
under /files. A Playwright test then runs one job through the real frontend.

A part of the UI that raises NotImplementedYetError skips these tests with the reason, so a
checkout where the UI is incomplete stays green; once it is built they all run.
"""

from __future__ import annotations

import http.client
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from playwright.sync_api import ConsoleMessage, Page, expect, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from scripts.verify_output import verify_output

from get_design_markdown.console import MessageSink
from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.resources import resource_path
from get_design_markdown.storage import (
    ANALYSIS_FILE,
    DESIGN_FILE,
    PREVIEW_FILE,
    PROMPT_FILE,
    RAW_FILE,
    SYNTHESIS_FILE,
    TOKENS_FILE,
    domain_of,
)
from get_design_markdown.ui import history, render_md
from get_design_markdown.ui.jobs import (
    FINAL_SUCCESS_MESSAGE,
    PIPELINE_STEPS,
    JobManager,
    JobRequest,
    JobSnapshot,
)
from get_design_markdown.ui.server import (
    FILES_HTML_CSP,
    HOST,
    INDEX_CSP,
    TOKEN_HEADER,
    CurrentJobResponse,
    ErrorResponse,
    HealthResponse,
    HistoryResponse,
    JobResponse,
    ResultsResponse,
    UiServer,
)
from get_design_markdown.ui.settings import SETTINGS_FILE, UiSettings, load_ui_settings

pytestmark = [pytest.mark.e2e, pytest.mark.browser]

#: Upper bound for one pipeline run on the fixture site (about 10 s on a desktop PC).
JOB_TIMEOUT_S = 300.0
POLL_S = 0.2
#: Files a successful job reports and writes (prompt.md only with prompt_only).
JOB_FILES = frozenset(
    {DESIGN_FILE, PREVIEW_FILE, TOKENS_FILE, ANALYSIS_FILE, SYNTHESIS_FILE, RAW_FILE}
)
#: Outputs that must match `gdm get` byte for byte once timestamps are masked.
COMPARED_FILES = (DESIGN_FILE, TOKENS_FILE, PREVIEW_FILE)
SCREENSHOT_WIDTHS = {1440, 768, 390}
#: ISO dates and date-times ("2026-10-16", "2026-10-16T09:47:17.183483+00:00", "...Z").
TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)
#: Placed next to the output root: no /files URL may ever reach it.
OUTSIDE_FILE = "outside-secret.txt"
OUTSIDE_TEXT = "must never be served"
#: Accessible name of the frontend's URL field ("Địa chỉ trang web", "URL"...).
URL_FIELD_NAME = re.compile(r"địa chỉ|url|trang web", re.IGNORECASE)


def _skip_when_not_built(error: NotImplementedYetError) -> None:
    pytest.skip(f"Giao diện chưa được xây dựng đầy đủ: {error.message}")


def _require_backend(probe_dir: Path) -> None:
    """Skip when a backend piece the job flow needs still raises NotImplementedYetError."""

    def runner(request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        return probe_dir

    try:
        render_md.render_markdown("# probe\n")
        history.list_history(probe_dir)
        load_ui_settings(probe_dir / SETTINGS_FILE, UiSettings(output_dir=probe_dir))
        _ = JobManager(runner).busy
    except NotImplementedYetError as error:
        _skip_when_not_built(error)


@dataclass
class UiSession:
    server: UiServer
    client: httpx.Client
    out_root: Path
    site_url: str

    @property
    def site(self) -> str:
        return domain_of(self.site_url)

    @property
    def folder(self) -> Path:
        return self.out_root / self.site


@dataclass(frozen=True)
class FinishedJob:
    #: The final snapshot, with every event the polls returned.
    snapshot: JobSnapshot
    seconds: float
    polls: int


@contextmanager
def serve_ui(site_url: str, base: Path) -> Iterator[UiSession]:
    """The real UiServer (mode "none", default JobManager) with its output root, settings
    file and a file outside the output root under `base`."""
    _require_backend(base / "probe")
    out_root = base / "output"
    out_root.mkdir(parents=True)
    (base / OUTSIDE_FILE).write_text(OUTSIDE_TEXT, encoding="utf-8")
    server = UiServer(
        mode="none",
        out_root=out_root,
        settings_path=base / "config" / SETTINGS_FILE,
        port=0,
        idle_timeout_s=None,
    )
    try:
        server.start()
    except NotImplementedYetError as error:
        _skip_when_not_built(error)
    try:
        with httpx.Client(
            base_url=server.url,
            headers={TOKEN_HEADER: server.token},
            trust_env=False,
            timeout=30,
        ) as client:
            yield UiSession(server=server, client=client, out_root=out_root, site_url=site_url)
    finally:
        server.stop()


@pytest.fixture(scope="module")
def ui_session(site_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[UiSession]:
    """One server for the HTTP API tests (they share its job)."""
    with serve_ui(site_url, tmp_path_factory.mktemp("ui-e2e")) as session:
        yield session


@pytest.fixture
def fresh_ui_session(site_url: str, tmp_path: Path) -> Iterator[UiSession]:
    """A server that never ran a job: what a first-time user opens."""
    with serve_ui(site_url, tmp_path) as session:
        yield session


def _job(response: httpx.Response) -> JobSnapshot:
    return JobResponse.model_validate_json(response.content).job


def _error(response: httpx.Response) -> ErrorResponse:
    return ErrorResponse.model_validate_json(response.content)


def wait_finished(client: httpx.Client, job_id: str, timeout: float = JOB_TIMEOUT_S) -> FinishedJob:
    """Poll like the frontend (since = last seq seen) until the job finished."""
    started = time.monotonic()
    events: list[object] = []
    since = 0
    polls = 0
    while time.monotonic() - started < timeout:
        response = client.get(f"/api/jobs/{job_id}", params={"since": since})
        assert response.status_code == 200, response.text
        job = _job(response)
        polls += 1
        assert all(event.seq > since for event in job.events), "events at or before since"
        events.extend(job.events)
        if job.events:
            since = job.events[-1].seq
        if job.finished and since >= job.last_seq:
            snapshot = job.model_copy(update={"events": events})
            return FinishedJob(snapshot=snapshot, seconds=time.monotonic() - started, polls=polls)
        if not job.events:
            time.sleep(POLL_S)
    raise AssertionError(f"Lượt phân tích {job_id} chưa xong sau {timeout:g} giây.")


def describe(job: JobSnapshot) -> str:
    """Status, error and the last log lines, for assertion messages."""
    lines = [f"status={job.status}", f"error={job.error}"]
    lines += [f"  #{event.seq} {event.kind}: {event.message}" for event in job.events[-15:]]
    return "\n".join(lines)


@pytest.fixture(scope="module")
def ui_job(ui_session: UiSession) -> FinishedJob:
    """One job for the fixture site through the HTTP API (saved settings, 0 sub-pages)."""
    response = ui_session.client.post("/api/jobs", json={"url": ui_session.site_url, "pages": 0})
    assert response.status_code == 202, response.text
    created = _job(response)
    assert created.status in ("queued", "running") and created.events == []
    finished = wait_finished(ui_session.client, created.id)
    assert finished.snapshot.status == "succeeded", describe(finished.snapshot)
    return finished


@pytest.fixture(scope="module")
def cli_folder(site_url: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """What `gdm get <same url> --out <other folder>` writes (a separate process)."""
    out_root = tmp_path_factory.mktemp("ui-e2e-cli")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", COLUMNS="200")
    command = [sys.executable, "-m", "get_design_markdown", "get", site_url, "--out", str(out_root)]
    completed = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=JOB_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]
    return out_root / domain_of(site_url)


def mask_timestamps(text: str) -> str:
    return TIMESTAMP_RE.sub("<timestamp>", text)


def raw_request(
    server: UiServer, method: str, path: str, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    """One request with the path sent exactly as written (httpx would normalise "..")."""
    connection = http.client.HTTPConnection(HOST, server.port, timeout=30)
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        connection.close()


# ─── The job through the HTTP API ────────────────────────────────────────────


def test_mask_timestamps() -> None:
    text = (
        "**Extracted:** 2026-10-16 · "
        '"collectedAt": "2026-10-16T09:47:17.183483+00:00", "at": "2026-10-16T09:47:26Z"'
    )
    assert mask_timestamps(text) == (
        '**Extracted:** <timestamp> · "collectedAt": "<timestamp>", "at": "<timestamp>"'
    )
    assert mask_timestamps("#4f46e5 16px 1.5") == "#4f46e5 16px 1.5"


def test_job_runs_the_whole_pipeline(ui_session: UiSession, ui_job: FinishedJob) -> None:
    job = ui_job.snapshot
    assert job.url == ui_session.site_url
    assert job.site == ui_session.site
    assert job.error is None and not job.cancel_requested
    assert job.settings.output_dir.resolve() == ui_session.out_root.resolve()
    assert job.settings.pages == 0
    assert job.started_at is not None and job.finished_at is not None
    assert job.created_at <= job.started_at <= job.finished_at
    seqs = [event.seq for event in job.events]
    assert seqs == list(range(1, len(seqs) + 1)), "events lost or duplicated while polling"
    assert job.last_seq == seqs[-1]
    steps = [event.step for event in job.events if event.kind == "step"]
    assert steps == list(range(1, PIPELINE_STEPS + 1)), describe(job)
    assert {event.total for event in job.events if event.kind == "step"} == {PIPELINE_STEPS}
    assert (job.step, job.total) == (PIPELINE_STEPS, PIPELINE_STEPS)
    final = job.events[-1]
    assert (final.kind, final.message) == ("success", FINAL_SUCCESS_MESSAGE)
    assert not [event for event in job.events if event.kind == "error"], describe(job)
    assert set(job.files) == JOB_FILES, job.files
    for name in JOB_FILES:
        assert (ui_session.folder / name).is_file(), name
    assert not (ui_session.folder / PROMPT_FILE).exists()


def test_job_writes_what_gdm_get_writes(
    ui_session: UiSession, ui_job: FinishedJob, cli_folder: Path
) -> None:
    for name in COMPARED_FILES:
        ui_text = (ui_session.folder / name).read_text(encoding="utf-8")
        cli_text = (cli_folder / name).read_text(encoding="utf-8")
        assert mask_timestamps(ui_text) == mask_timestamps(cli_text), name


def test_job_output_passes_the_verifier(ui_session: UiSession, ui_job: FinishedJob) -> None:
    findings = verify_output(ui_session.folder)
    errors = [f"[{f.check}] {f.message}" for f in findings if f.severity == "error"]
    assert errors == [], "\n".join(errors)


def test_finished_job_state(ui_session: UiSession, ui_job: FinishedJob) -> None:
    client = ui_session.client
    job = ui_job.snapshot
    health = HealthResponse.model_validate_json(client.get("/api/health").content)
    assert health.ok and health.mode == "none" and not health.busy and health.job_id is None
    assert Path(health.output_root).resolve() == ui_session.out_root.resolve()
    current = CurrentJobResponse.model_validate_json(client.get("/api/jobs/current").content)
    assert current.job is not None and current.job.id == job.id
    assert current.job.status == "succeeded"
    caught_up = _job(client.get(f"/api/jobs/{job.id}", params={"since": job.last_seq}))
    assert caught_up.events == [] and caught_up.last_seq == job.last_seq
    replay = _job(client.get(f"/api/jobs/{job.id}"))
    assert [event.seq for event in replay.events] == [event.seq for event in job.events]
    # Cancelling a finished job changes nothing.
    cancel = client.post(f"/api/jobs/{job.id}/cancel")
    assert cancel.status_code == 200 and _job(cancel).status == "succeeded"


def test_results_and_history(ui_session: UiSession, ui_job: FinishedJob) -> None:
    client = ui_session.client
    site, folder = ui_session.site, ui_session.folder
    response = client.get(f"/api/results/{quote(site)}")
    assert response.status_code == 200, response.text
    results = ResultsResponse.model_validate_json(response.content)
    assert results.site == site
    assert Path(results.folder).resolve() == folder.resolve()
    design = results.design
    assert design is not None
    assert design.markdown == (folder / DESIGN_FILE).read_text(encoding="utf-8")
    assert design.front_matter is not None and design.front_matter.startswith("version: alpha")
    assert "<h1" in design.html and "<table>" in design.html
    assert "<script" not in design.html.lower()
    assert results.prompt is None and results.prompt_images == []
    assert results.preview_url == f"/files/{site}/{PREVIEW_FILE}"
    assert {shot.viewport_width for shot in results.screenshots} == SCREENSHOT_WIDTHS
    assert {shot.color_scheme for shot in results.screenshots} == {"light"}
    assert results.token_counts is not None and results.token_counts.get("colors", 0) > 0
    kinds = {file.path: file.kind for file in results.files}
    assert kinds[DESIGN_FILE] == "design" and kinds[PREVIEW_FILE] == "preview"
    assert kinds[TOKENS_FILE] == "tokens" and kinds[RAW_FILE] == "raw"
    assert kinds["screenshots/home-1440.png"] == "image"
    for file in results.files:
        served = client.get(file.url)
        assert served.status_code == 200, file.url
        assert len(served.content) == file.size == (folder / file.path).stat().st_size, file.url
    for shot in results.screenshots:
        assert client.get(shot.url).headers["content-type"] == "image/png", shot.url

    entries = HistoryResponse.model_validate_json(client.get("/api/history").content).entries
    entry = next(entry for entry in entries if entry.site == site)
    assert entry == results.entry
    assert entry.has_raw and entry.has_analysis and entry.has_design
    assert entry.has_preview and entry.has_tokens and not entry.has_prompt
    assert entry.pages == ["home"]
    assert entry.thumbnail == "screenshots/home-1440.png"
    assert entry.url is not None and entry.url.startswith(ui_session.site_url.rstrip("/"))


# ─── Security of the running server ──────────────────────────────────────────


def test_files_are_served_with_safe_headers(ui_session: UiSession, ui_job: FinishedJob) -> None:
    server, site = ui_session.server, ui_session.site
    status, headers, body = raw_request(server, "GET", f"/files/{site}/{DESIGN_FILE}")
    assert status == 200
    assert body == (ui_session.folder / DESIGN_FILE).read_bytes()
    assert headers["content-type"] == "text/markdown; charset=utf-8"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cross-origin-resource-policy"] == "same-origin"
    assert headers["cache-control"] in ("no-store", "no-cache")
    assert not [name for name in headers if name.startswith("access-control")]

    status, headers, _ = raw_request(server, "GET", f"/files/{site}/{PREVIEW_FILE}")
    assert status == 200 and headers["content-type"].startswith("text/html")
    assert headers["content-security-policy"] == FILES_HTML_CSP

    status, headers, _ = raw_request(server, "GET", f"/files/{site}/{DESIGN_FILE}?download=1")
    assert status == 200
    assert headers["content-disposition"] == f'attachment; filename="{DESIGN_FILE}"'

    status, headers, body = raw_request(server, "HEAD", f"/files/{site}/{TOKENS_FILE}")
    assert status == 200 and body == b""
    assert headers["content-type"].startswith("application/json")

    status, headers, _ = raw_request(server, "GET", "/")
    assert status == 200 and headers["content-security-policy"] == INDEX_CSP


def test_files_and_static_are_confined(ui_session: UiSession, ui_job: FinishedJob) -> None:
    server, site, folder = ui_session.server, ui_session.site, ui_session.folder
    (folder / ".hidden.txt").write_text("hidden", encoding="utf-8")
    outside = ui_session.out_root.parent / OUTSIDE_FILE
    attempts = [
        f"/files/{site}/../../{OUTSIDE_FILE}",
        f"/files/{site}/%2e%2e/%2e%2e/{OUTSIDE_FILE}",
        f"/files/{site}/%2E%2E/%2E%2E/{OUTSIDE_FILE}",
        f"/files/{site}/..%2f..%2f{OUTSIDE_FILE}",
        f"/files/{site}/..%5c..%5c{OUTSIDE_FILE}",
        f"/files/{site}/%2e%2e%5c%2e%2e%5c{OUTSIDE_FILE}",
        f"/files/../{OUTSIDE_FILE}",
        f"/files/%2e%2e/{OUTSIDE_FILE}",
        f"/files/{site}/./{DESIGN_FILE}",
        f"/files/{site}//{DESIGN_FILE}",
        f"/files/{site}/" + quote(str(outside), safe=""),
        f"/files/{site}/C:%5cWindows%5cwin.ini",
        f"/files/{site}/{DESIGN_FILE}%00.png",
        f"/files/{site}/.hidden.txt",
        f"/files/{site}/screenshots",
        f"/files/{site}/screenshots/",
        f"/files/{site}/",
        f"/files/{site}",
        "/files/",
        "/static/../server.py",
        "/static/%2e%2e/server.py",
        "/static/..%5cserver.py",
    ]
    for path in attempts:
        status, _, body = raw_request(server, "GET", path)
        assert status == 404, path
        text = body.decode("utf-8", errors="replace")
        assert OUTSIDE_TEXT not in text and str(ui_session.out_root) not in text, path
        assert ErrorResponse.model_validate_json(body).error.code == "not_found", path


def test_symlink_out_of_the_site_folder_is_refused(
    ui_session: UiSession, ui_job: FinishedJob
) -> None:
    link = ui_session.folder / "linked-secret.txt"
    try:
        link.symlink_to(ui_session.out_root.parent / OUTSIDE_FILE)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"Hệ điều hành không cho tạo symlink ở đây: {error}")
    try:
        status, _, body = raw_request(
            ui_session.server, "GET", f"/files/{ui_session.site}/{link.name}"
        )
        assert status == 404 and OUTSIDE_TEXT.encode() not in body
    finally:
        link.unlink()


def test_api_refuses_foreign_callers(ui_session: UiSession, ui_job: FinishedJob) -> None:
    server, client = ui_session.server, ui_session.client
    for headers in ({}, {TOKEN_HEADER: "wrong-token"}):
        status, _, body = raw_request(server, "GET", "/api/health", headers)
        assert status == 403, headers
        assert ErrorResponse.model_validate_json(body).error.code == "forbidden"
    for path in ("/", f"/files/{ui_session.site}/{DESIGN_FILE}", "/api/health"):
        status, _, body = raw_request(
            server, "GET", path, {"Host": "evil.example", TOKEN_HEADER: server.token}
        )
        assert status == 403, path
        assert ErrorResponse.model_validate_json(body).error.code == "bad_host", path
    # A page on another origin cannot start a job, even with the token.
    before = CurrentJobResponse.model_validate_json(client.get("/api/jobs/current").content).job
    response = client.post(
        "/api/jobs", json={"url": ui_session.site_url}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403 and _error(response).error.code == "bad_origin"
    after = CurrentJobResponse.model_validate_json(client.get("/api/jobs/current").content).job
    assert before is not None and after is not None
    assert (after.id, after.status) == (before.id, before.status)
    status, headers, _ = raw_request(
        server,
        "OPTIONS",
        "/api/jobs",
        {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert status == 405
    assert not [name for name in headers if name.startswith("access-control")]


def test_invalid_url_is_refused_before_any_job(ui_session: UiSession, ui_job: FinishedJob) -> None:
    response = ui_session.client.post("/api/jobs", json={"url": "ftp://files.example"})
    assert response.status_code == 422
    error = _error(response).error
    assert error.code == "invalid_url" and error.message


# ─── The real frontend ───────────────────────────────────────────────────────


def _require_frontend() -> None:
    try:
        resource_path("ui", "static", "app.js")
    except FileNotFoundError:
        pytest.skip("Giao diện web (ui/static/app.js) chưa được xây dựng.")


def _dismiss_first_run_guide(page: Page) -> None:
    """Close the first-run guide when it covers the page as a modal dialog."""
    dialog = page.get_by_role("dialog").first
    try:
        dialog.wait_for(state="visible", timeout=2000)
    except PlaywrightTimeoutError:
        return
    page.keyboard.press("Escape")
    if dialog.is_visible():
        dialog.get_by_role("button").last.click()
    expect(dialog).to_be_hidden()


def test_frontend_runs_a_job(fresh_ui_session: UiSession) -> None:
    """A first-time user: open the page, paste the URL, press "Phân tích", get the results."""
    _require_frontend()
    session = fresh_ui_session
    server, site = session.server, session.site
    page_errors: list[str] = []
    csp_violations: list[str] = []

    def on_console(message: ConsoleMessage) -> None:
        if message.type == "error" and "Content Security Policy" in message.text:
            csp_violations.append(message.text)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 860})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("console", on_console)
            page.goto(server.launch_url)
            # The token is taken out of the address bar once the page read it.
            expect(page).not_to_have_url(re.compile(r"token="))
            _dismiss_first_run_guide(page)

            url_field = page.get_by_role("textbox", name=URL_FIELD_NAME).first
            expect(url_field).to_be_visible()
            url_field.fill(session.site_url)
            page.get_by_role("button", name="Phân tích", exact=True).click()

            design_tab = page.get_by_role("tab", name=re.compile(r"DESIGN\.md"))
            expect(design_tab).to_be_visible(timeout=JOB_TIMEOUT_S * 1000)
            expect(page.get_by_role("tab", name=re.compile("Xem trước"))).to_be_visible()
            preview = page.locator(f'iframe[src*="/files/{site}/{PREVIEW_FILE}"]')
            expect(preview).to_have_count(1)
            assert preview.get_attribute("sandbox") == "allow-scripts"
            design_tab.click()
            rendered = page.locator("iframe[srcdoc]")
            expect(rendered.first).to_be_attached()
            # Rendered Markdown sits in a sandbox without scripts (sandbox="" is the strictest).
            sandboxes = [frame.get_attribute("sandbox") for frame in rendered.all()]
            assert sandboxes and all(value == "" for value in sandboxes), sandboxes
        finally:
            browser.close()

    assert page_errors == []
    assert csp_violations == []
    current = CurrentJobResponse.model_validate_json(
        session.client.get("/api/jobs/current").content
    ).job
    assert current is not None and current.url == session.site_url
    assert current.status == "succeeded", describe(current)
    assert (session.folder / DESIGN_FILE).is_file()
