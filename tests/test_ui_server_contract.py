"""The HTTP API contract of ui/server.py, checked on the real server AND on the fake API.

The frontend is developed against tests/ui_fake_api.py; running the same checks on both keeps
the two implementations in step. Server-only behaviour (lifecycle, idle shutdown, settings
file, symlinks...) is tested in test_ui_server.py.
"""

from __future__ import annotations

import http.client
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest

from get_design_markdown.ui.jobs import FINAL_SUCCESS_MESSAGE, JobSnapshot
from get_design_markdown.ui.server import (
    FILES_HTML_CSP,
    INDEX_CSP,
    TOKEN_HEADER,
    ErrorResponse,
    HealthResponse,
    HistoryResponse,
    JobResponse,
    ResultsResponse,
    SettingsResponse,
)
from tests.test_ui_server import (  # noqa: F401 - fixtures used by `target`
    Harness,
    harness,
    output_root,
    ui_output_template,
)
from tests.ui_fake_api import ACME_SITE, BETA_SITE, FakeApi

WAIT_S = 15.0

#: Sorted by path, case-insensitively (as Windows lists them).
ACME_FILES = [
    "analysis.json",
    "DESIGN.md",
    "preview.html",
    "prompt-images/desktop.png",
    "prompt-images/mobile.png",
    "prompt.md",
    "raw.json",
    "screenshots/home-1440.png",
    "screenshots/home-390.png",
    "screenshots/home-768.png",
    "screenshots/home-dark-1440.png",
    "synthesis.json",
    "tokens.json",
]


@dataclass
class Target:
    """One implementation of the API, as the tests see it."""

    kind: Literal["real", "fake"]
    url: str
    port: int
    token: str
    output_root: Path
    opened_folders: Callable[[], list[str]]


@pytest.fixture(params=["real", "fake"])
def target(request: pytest.FixtureRequest) -> Target:
    if request.param == "fake":
        api: FakeApi = request.getfixturevalue("ui_fake_api")
        return Target(
            "fake",
            api.url,
            api.port,
            api.token,
            api.output_root,
            lambda: list(api.state.opened_folders),
        )
    real: Harness = request.getfixturevalue("harness")
    server = real.make(mode="browser")  # like the fake: idle shutdown after 120 s
    return Target(
        "real",
        server.url,
        server.port,
        server.token,
        real.output_root.resolve(),
        lambda: [str(folder) for folder in real.opened],
    )


@pytest.fixture
def client(target: Target) -> Iterator[httpx.Client]:
    with httpx.Client(
        base_url=target.url,
        headers={TOKEN_HEADER: target.token},
        trust_env=False,
        timeout=WAIT_S,
    ) as session:
        yield session


def _raw_status(target: Target, path: str) -> int:
    """GET `path` exactly as written (httpx would normalise "..")."""
    connection = http.client.HTTPConnection("127.0.0.1", target.port, timeout=WAIT_S)
    try:
        connection.request("GET", path)
        return connection.getresponse().status
    finally:
        connection.close()


def _error(response: httpx.Response) -> ErrorResponse:
    return ErrorResponse.model_validate_json(response.content)


def _start(client: httpx.Client, url: str, **body: Any) -> JobSnapshot:
    response = client.post("/api/jobs", json={"url": url, **body})
    assert response.status_code == 202, response.text
    return JobResponse.model_validate_json(response.content).job


def _wait_finished(client: httpx.Client, job_id: str, timeout: float = WAIT_S) -> JobSnapshot:
    deadline = time.monotonic() + timeout
    events: list[Any] = []
    since = 0
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}", params={"since": since})
        job = JobResponse.model_validate_json(response.content).job
        events.extend(job.events)
        if job.events:
            since = job.events[-1].seq
        if job.finished and since == job.last_seq:
            return job.model_copy(update={"events": events})
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


# ─── Security rules ──────────────────────────────────────────────────────────


def test_api_requires_the_token(target: Target) -> None:
    with httpx.Client(base_url=target.url, trust_env=False, timeout=WAIT_S) as anonymous:
        for path in ("/api/health", "/api/settings", "/api/history", "/api/jobs/current"):
            response = anonymous.get(path)
            assert response.status_code == 403
            assert _error(response).error.code == "forbidden"
            assert _error(response).error.hint
        assert anonymous.get("/api/health", headers={TOKEN_HEADER: "nope"}).status_code == 403
        assert anonymous.post("/api/jobs", json={"url": "stripe.com"}).status_code == 403
        assert anonymous.put("/api/settings", json={"pages": 2}).status_code == 403
        # The page, its static files and the result files need no token.
        assert anonymous.get("/").status_code == 200
        assert anonymous.get(f"/files/{ACME_SITE}/DESIGN.md").status_code == 200


def test_foreign_host_and_origin_are_refused(client: httpx.Client, target: Target) -> None:
    response = client.get("/api/health", headers={"Host": "evil.example"})
    assert response.status_code == 403 and _error(response).error.code == "bad_host"
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403
    files = client.get(f"/files/{ACME_SITE}/DESIGN.md", headers={"Host": f"evil:{target.port}"})
    assert files.status_code == 403
    assert client.get("/api/health", headers={"Host": f"localhost:{target.port}"}).is_success
    response = client.post("/api/heartbeat", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403 and _error(response).error.code == "bad_origin"
    response = client.put("/api/settings", json={}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403 and _error(response).error.code == "bad_origin"
    assert client.post("/api/heartbeat", headers={"Origin": target.url.rstrip("/")}).is_success


@pytest.mark.parametrize("path", ["/api/jobs", "/api/settings", "/", f"/files/{ACME_SITE}/x"])
def test_preflight_is_refused_without_cors_headers(client: httpx.Client, path: str) -> None:
    response = client.request(
        "OPTIONS",
        path,
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": TOKEN_HEADER,
        },
    )
    assert response.status_code == 405
    assert _error(response).error.code == "method_not_allowed"
    assert not [name for name in response.headers if name.lower().startswith("access-control")]


def test_files_are_confined_to_the_site_folder(target: Target) -> None:
    assert _raw_status(target, f"/files/{ACME_SITE}/DESIGN.md") == 200
    assert _raw_status(target, f"/files/{ACME_SITE}/screenshots/home-1440.png") == 200
    for path in (
        f"/files/{ACME_SITE}/../{BETA_SITE}/DESIGN.md",
        f"/files/{ACME_SITE}/%2e%2e/{BETA_SITE}/DESIGN.md",
        f"/files/{ACME_SITE}/..%5c{BETA_SITE}%5cDESIGN.md",
        f"/files/{ACME_SITE}/screenshots/../DESIGN.md",
        f"/files/{ACME_SITE}/C:/Windows/win.ini",
        f"/files/{ACME_SITE}/DESIGN.md%00.png",
        f"/files/{ACME_SITE}/screenshots",
        f"/files/{ACME_SITE}/missing.md",
        "/files/../ui-settings.json",
        "/files/nope.example/DESIGN.md",
        "/static/../index.html",
        "/static/%2e%2e/server.py",
    ):
        assert _raw_status(target, path) == 404, path


def test_headers_and_content_security_policy(client: httpx.Client) -> None:
    index = client.get("/")
    assert index.headers["content-security-policy"] == INDEX_CSP
    assert index.headers["x-content-type-options"] == "nosniff"
    assert index.headers["referrer-policy"] == "no-referrer"
    preview = client.get(f"/files/{ACME_SITE}/preview.html")
    assert preview.headers["content-type"].startswith("text/html")
    assert preview.headers["content-security-policy"] == FILES_HTML_CSP
    design = client.get(f"/files/{ACME_SITE}/DESIGN.md")
    assert design.headers["content-type"] == "text/markdown; charset=utf-8"
    assert "content-disposition" not in design.headers
    download = client.get(f"/files/{ACME_SITE}/DESIGN.md", params={"download": "1"})
    assert download.headers["content-disposition"] == 'attachment; filename="DESIGN.md"'
    assert client.get(f"/files/{ACME_SITE}/tokens.json").headers["content-type"] == (
        "application/json; charset=utf-8"
    )
    health = client.get("/api/health")
    assert health.headers["cache-control"] == "no-store"
    assert health.headers["content-type"] == "application/json; charset=utf-8"
    assert health.headers["cross-origin-resource-policy"] == "same-origin"


def test_body_rules(client: httpx.Client) -> None:
    response = client.post("/api/jobs", content=b"not json", headers={"Content-Type": "text/plain"})
    assert response.status_code == 415
    assert _error(response).error.code == "unsupported_media_type"
    response = client.post(
        "/api/jobs", content=b"{broken", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400 and _error(response).error.code == "bad_json"
    response = client.post(
        "/api/jobs", content=b"[1, 2]", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400 and _error(response).error.code == "bad_json"
    response = client.post(
        "/api/jobs", content=b"x" * (70 * 1024), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413 and _error(response).error.code == "too_large"


def test_unknown_api_paths(client: httpx.Client) -> None:
    response = client.get("/api/nothing-here")
    assert response.status_code == 404 and _error(response).error.code == "not_found"
    assert client.get("/api/jobs/0123456789ab").status_code == 404
    assert _error(client.get("/api/jobs/0123456789ab")).error.code == "job_not_found"
    assert client.post("/api/jobs/0123456789ab/cancel").status_code == 404
    assert client.delete("/api/settings").status_code == 405


# ─── Endpoints ───────────────────────────────────────────────────────────────


def test_health(client: httpx.Client, target: Target) -> None:
    health = HealthResponse.model_validate_json(client.get("/api/health").content)
    assert health.ok and health.api == 1 and health.app == "get-design-markdown"
    assert not health.busy and health.job_id is None and health.mode == "browser"
    assert health.output_root == str(target.output_root)


def test_settings_round_trip_and_validation(client: httpx.Client, target: Target) -> None:
    current = SettingsResponse.model_validate_json(client.get("/api/settings").content)
    assert current.settings == current.defaults
    assert current.browsers == ["auto", "chromium", "msedge", "chrome"]
    assert current.limits.pages == (0, 10)
    assert current.limits.max_elements == (100, 20000)
    assert current.limits.timeout_s == (5, 600)
    assert current.limits.delta_e == (0.5, 20)
    assert current.output_root == str(target.output_root)
    updated = client.put("/api/settings", json={"pages": 3, "lang": "vi", "browser": "msedge"})
    assert updated.status_code == 200
    settings = SettingsResponse.model_validate_json(updated.content).settings
    assert (settings.pages, settings.lang, settings.browser) == (3, "vi", "msedge")
    again = SettingsResponse.model_validate_json(client.get("/api/settings").content).settings
    assert again == settings
    bad = client.put("/api/settings", json={"pages": 11})
    assert bad.status_code == 422
    assert _error(bad).error.code == "invalid" and _error(bad).error.details == {"field": "pages"}
    unknown = client.put("/api/settings", json={"colour": "red"})
    assert unknown.status_code == 422 and _error(unknown).error.details == {"field": "colour"}


def test_successful_job_streams_events(client: httpx.Client) -> None:
    job = _start(client, "stripe.com", pages=2)
    assert job.status in ("queued", "running") and job.events == []
    assert job.site == "stripe.com" and job.settings.pages == 2
    health = HealthResponse.model_validate_json(client.get("/api/health").content)
    assert health.job_id in (job.id, None)
    finished = _wait_finished(client, job.id)
    assert finished.status == "succeeded" and finished.error is None
    assert [event.seq for event in finished.events] == list(range(1, len(finished.events) + 1))
    assert [event.step for event in finished.events if event.kind == "step"] == [1, 2, 3, 4]
    assert {event.total for event in finished.events if event.kind == "step"} == {4}
    assert finished.events[-1].kind == "success"
    assert finished.events[-1].message == FINAL_SUCCESS_MESSAGE
    assert finished.files[0] == "DESIGN.md" and "preview.html" in finished.files
    results = ResultsResponse.model_validate_json(client.get("/api/results/stripe.com").content)
    assert results.design is not None and results.site == "stripe.com"


def test_robots_warning_is_an_event(client: httpx.Client) -> None:
    job = _start(client, "https://robots.example")
    finished = _wait_finished(client, job.id)
    assert finished.status == "succeeded"
    warnings = [event.message for event in finished.events if event.kind == "warning"]
    assert any("robots.txt" in message for message in warnings)


def test_busy_failing_and_cancelled_jobs(client: httpx.Client) -> None:
    slow = _start(client, "https://slow.example")
    busy = client.post("/api/jobs", json={"url": "stripe.com"})
    assert busy.status_code == 409
    assert _error(busy).error.code == "busy" and _error(busy).error.details == {"job_id": slow.id}
    assert HealthResponse.model_validate_json(client.get("/api/health").content).busy
    cancel = client.post(f"/api/jobs/{slow.id}/cancel")
    assert cancel.status_code == 202
    assert JobResponse.model_validate_json(cancel.content).job.cancel_requested
    cancelled = _wait_finished(client, slow.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error is not None and cancelled.error.code == "cancelled"
    assert cancelled.events[-1].kind == "warning"
    assert client.post(f"/api/jobs/{slow.id}/cancel").status_code == 200

    failed = _start(client, "fail.example")
    finished = _wait_finished(client, failed.id)
    assert finished.status == "failed"
    assert finished.error is not None and finished.error.code == "bot_blocked"
    assert finished.events[-1].kind == "error" and finished.events[-1].hint
    assert finished.events[-1].message == finished.error.message

    missing = _start(client, "no-browser.example")
    finished = _wait_finished(client, missing.id)
    assert finished.error is not None and finished.error.code == "browser_missing"
    assert finished.error.hint

    invalid = client.post("/api/jobs", json={"url": "ftp://x.example"})
    assert invalid.status_code == 422 and _error(invalid).error.code == "invalid_url"
    extra = client.post("/api/jobs", json={"url": "x.example", "output_dir": "/tmp"})
    assert extra.status_code == 422 and _error(extra).error.details == {"field": "output_dir"}
    pages = client.post("/api/jobs", json={"url": "x.example", "pages": 11})
    assert pages.status_code == 422 and _error(pages).error.details == {"field": "pages"}
    current = client.get("/api/jobs/current").json()["job"]
    assert current["id"] == missing.id


def test_history_and_results(client: httpx.Client, target: Target) -> None:
    history = HistoryResponse.model_validate_json(client.get("/api/history").content)
    assert history.output_root == str(target.output_root)
    assert [entry.site for entry in history.entries] == [ACME_SITE, BETA_SITE]
    acme, beta = history.entries
    assert acme.name == "Acme Cloud" and acme.url == "https://acme.example/"
    assert acme.has_design and acme.has_prompt and acme.has_raw and acme.has_tokens
    assert acme.thumbnail == "screenshots/home-1440.png" and acme.pages == ["home"]
    assert beta.name == "Beta Studio" and not beta.has_prompt and beta.thumbnail is None

    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    assert results.site == ACME_SITE and results.entry == acme
    assert results.folder == str(target.output_root / ACME_SITE)
    paths = [file.path for file in results.files]
    assert sorted(paths) == sorted(ACME_FILES)
    if target.kind == "real" or sys.platform == "win32":  # the fake sorts Path objects
        assert paths == ACME_FILES
    kinds = {file.path: file.kind for file in results.files}
    assert kinds["DESIGN.md"] == "design" and kinds["screenshots/home-1440.png"] == "image"
    assert results.design is not None and results.design.front_matter is not None
    assert results.design.front_matter.startswith("version: alpha")
    assert results.design.markdown.startswith("---\nversion: alpha")
    assert "<table>" in results.design.html and "<script" not in results.design.html
    assert "version: alpha" not in results.design.html
    assert results.prompt is not None
    assert results.preview_url == f"/files/{ACME_SITE}/preview.html"
    assert [
        (shot.page, shot.viewport_width, shot.color_scheme) for shot in results.screenshots
    ] == [
        ("home", 1440, "light"),
        ("home", 768, "light"),
        ("home", 390, "light"),
        ("home", 1440, "dark"),
    ]
    assert results.prompt_images == [
        f"/files/{ACME_SITE}/prompt-images/desktop.png",
        f"/files/{ACME_SITE}/prompt-images/mobile.png",
    ]
    assert results.token_counts is not None and results.token_counts["colors"] > 0
    assert results.warnings
    for file in results.files:
        response = client.get(file.url)
        assert response.status_code == 200, file.url
        assert len(response.content) == file.size
    for shot in results.screenshots:
        assert client.get(shot.url).headers["content-type"] == "image/png"

    beta_results = ResultsResponse.model_validate_json(
        client.get(f"/api/results/{BETA_SITE}").content
    )
    assert beta_results.prompt is None and beta_results.screenshots == []
    assert beta_results.warnings == ["Trang có lớp phủ cookie — đã ẩn trước khi đo."]
    assert client.get("/api/results/missing.example").status_code == 404
    assert _error(client.get("/api/results/missing.example")).error.code == "site_not_found"


def test_open_folder_and_heartbeat(client: httpx.Client, target: Target) -> None:
    response = client.post("/api/open-folder", json={"site": ACME_SITE})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "folder": str(target.output_root / ACME_SITE)}
    assert client.post("/api/open-folder", json={}).status_code == 200
    bad = client.post("/api/open-folder", json={"site": "../x"})
    assert bad.status_code == 404 and _error(bad).error.code == "site_not_found"
    assert target.opened_folders() == [
        str(target.output_root / ACME_SITE),
        str(target.output_root),
    ]
    assert client.post("/api/heartbeat").json() == {"ok": True, "idle_shutdown_s": 120.0}
