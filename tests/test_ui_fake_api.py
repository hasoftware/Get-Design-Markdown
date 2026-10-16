"""The fake UI API (tests/ui_fake_api.py) follows the contract of ui/server.py.

The frontend is built against the fake, so it must enforce the same rules as the real server:
token, Host and Origin checks, no CORS, error envelope, response models, /files confinement.
"""

from __future__ import annotations

import http.client
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from get_design_markdown.ui.jobs import FINAL_SUCCESS_MESSAGE, JobSnapshot
from get_design_markdown.ui.server import (
    FILES_HTML_CSP,
    INDEX_CSP,
    TOKEN_HEADER,
    BrowserStatusResponse,
    ErrorResponse,
    HealthResponse,
    HistoryResponse,
    InstallResponse,
    JobResponse,
    ResultsResponse,
    SettingsResponse,
)
from tests.ui_fake_api import ACME_SITE, BETA_SITE, FakeApi


@pytest.fixture
def client(ui_fake_api: FakeApi) -> Iterator[httpx.Client]:
    with httpx.Client(
        base_url=ui_fake_api.url,
        headers={TOKEN_HEADER: ui_fake_api.token},
        trust_env=False,
        timeout=10,
    ) as session:
        yield session


def _raw_status(api: FakeApi, path: str) -> int:
    """GET `path` exactly as written (httpx would normalise "..")."""
    connection = http.client.HTTPConnection("127.0.0.1", api.port, timeout=10)
    try:
        connection.request("GET", path)
        return connection.getresponse().status
    finally:
        connection.close()


def _error(response: httpx.Response) -> ErrorResponse:
    return ErrorResponse.model_validate_json(response.content)


def _wait_finished(client: httpx.Client, job_id: str, timeout: float = 10.0) -> JobSnapshot:
    deadline = time.monotonic() + timeout
    events: list[Any] = []
    since = 0
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}", params={"since": since})
        job = JobResponse.model_validate_json(response.content).job
        events.extend(job.events)
        if job.events:
            since = job.events[-1].seq
        if job.finished:
            return job.model_copy(update={"events": events})
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


# ─── Security rules ──────────────────────────────────────────────────────────


def test_api_requires_the_token(ui_fake_api: FakeApi) -> None:
    with httpx.Client(base_url=ui_fake_api.url, trust_env=False) as anonymous:
        response = anonymous.get("/api/health")
        assert response.status_code == 403
        assert _error(response).error.code == "forbidden"
        wrong = anonymous.get("/api/health", headers={TOKEN_HEADER: "nope"})
        assert wrong.status_code == 403
        # Static files and the page itself need no token.
        assert anonymous.get("/").status_code == 200


def test_foreign_host_and_origin_are_refused(client: httpx.Client, ui_fake_api: FakeApi) -> None:
    response = client.get("/api/health", headers={"Host": "evil.example"})
    assert response.status_code == 403 and _error(response).error.code == "bad_host"
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/health", headers={"Host": f"localhost:{ui_fake_api.port}"}).is_success
    response = client.post("/api/heartbeat", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403 and _error(response).error.code == "bad_origin"
    assert client.post("/api/heartbeat", headers={"Origin": ui_fake_api.url.rstrip("/")}).is_success


def test_preflight_is_refused_without_cors_headers(client: httpx.Client) -> None:
    response = client.request(
        "OPTIONS",
        "/api/jobs",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 405
    assert not [name for name in response.headers if name.lower().startswith("access-control")]


def test_files_are_confined_to_the_site_folder(ui_fake_api: FakeApi) -> None:
    assert _raw_status(ui_fake_api, f"/files/{ACME_SITE}/DESIGN.md") == 200
    for path in (
        f"/files/{ACME_SITE}/../{BETA_SITE}/DESIGN.md",
        f"/files/{ACME_SITE}/%2e%2e/{BETA_SITE}/DESIGN.md",
        f"/files/{ACME_SITE}/..%5c{BETA_SITE}%5cDESIGN.md",
        "/files/../ui-settings.json",
        f"/files/{ACME_SITE}/screenshots",
        f"/files/{ACME_SITE}/missing.md",
        "/files/nope.example/DESIGN.md",
        "/static/../index.html",
    ):
        assert _raw_status(ui_fake_api, path) == 404, path


def test_headers_and_content_security_policy(client: httpx.Client) -> None:
    index = client.get("/")
    assert index.headers["content-security-policy"] == INDEX_CSP
    assert index.headers["x-content-type-options"] == "nosniff"
    preview = client.get(f"/files/{ACME_SITE}/preview.html")
    assert preview.headers["content-type"].startswith("text/html")
    assert preview.headers["content-security-policy"] == FILES_HTML_CSP
    download = client.get(f"/files/{ACME_SITE}/DESIGN.md", params={"download": "1"})
    assert download.headers["content-disposition"] == 'attachment; filename="DESIGN.md"'
    health = client.get("/api/health")
    assert health.headers["cache-control"] == "no-store"
    assert health.headers["content-type"] == "application/json; charset=utf-8"


def test_body_rules(client: httpx.Client) -> None:
    response = client.post("/api/jobs", content=b"not json", headers={"Content-Type": "text/plain"})
    assert response.status_code == 415
    response = client.post(
        "/api/jobs", content=b"{broken", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400 and _error(response).error.code == "bad_json"
    response = client.post(
        "/api/jobs", content=b"x" * (70 * 1024), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


# ─── Endpoints ───────────────────────────────────────────────────────────────


def test_health(client: httpx.Client) -> None:
    health = HealthResponse.model_validate_json(client.get("/api/health").content)
    assert health.ok and health.api == 1 and not health.busy and health.mode == "browser"


def test_settings_round_trip_and_validation(client: httpx.Client, ui_fake_api: FakeApi) -> None:
    current = SettingsResponse.model_validate_json(client.get("/api/settings").content)
    assert current.settings == current.defaults
    assert current.browsers == ["auto", "chromium", "msedge", "chrome"]
    assert current.limits.pages == (0, 10)
    updated = client.put("/api/settings", json={"pages": 3, "lang": "vi", "browser": "msedge"})
    assert updated.status_code == 200
    settings = SettingsResponse.model_validate_json(updated.content).settings
    assert (settings.pages, settings.lang, settings.browser) == (3, "vi", "msedge")
    assert ui_fake_api.state.settings_saves == 1
    bad = client.put("/api/settings", json={"pages": 11})
    assert bad.status_code == 422
    assert _error(bad).error.details == {"field": "pages"}
    unknown = client.put("/api/settings", json={"colour": "red"})
    assert unknown.status_code == 422 and _error(unknown).error.code == "invalid"


def test_successful_job_streams_scripted_events(client: httpx.Client) -> None:
    response = client.post("/api/jobs", json={"url": "stripe.com", "pages": 2})
    assert response.status_code == 202
    job = JobResponse.model_validate_json(response.content).job
    assert job.site == "stripe.com" and job.settings.pages == 2
    finished = _wait_finished(client, job.id)
    assert finished.status == "succeeded" and finished.error is None
    assert [event.seq for event in finished.events] == list(range(1, len(finished.events) + 1))
    assert [event.step for event in finished.events if event.kind == "step"] == [1, 2, 3, 4]
    assert finished.events[-1].message == FINAL_SUCCESS_MESSAGE
    assert "DESIGN.md" in finished.files
    results = ResultsResponse.model_validate_json(client.get("/api/results/stripe.com").content)
    assert results.design is not None


def test_busy_failing_and_cancelled_jobs(client: httpx.Client, ui_fake_api: FakeApi) -> None:
    slow = JobResponse.model_validate_json(
        client.post("/api/jobs", json={"url": "https://slow.example"}).content
    ).job
    busy = client.post("/api/jobs", json={"url": "stripe.com"})
    assert busy.status_code == 409
    assert _error(busy).error.details == {"job_id": slow.id}
    cancel = client.post(f"/api/jobs/{slow.id}/cancel")
    assert cancel.status_code == 202
    assert JobResponse.model_validate_json(cancel.content).job.cancel_requested
    cancelled = _wait_finished(client, slow.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error is not None and cancelled.error.code == "cancelled"
    assert client.post(f"/api/jobs/{slow.id}/cancel").status_code == 200

    failed = JobResponse.model_validate_json(
        client.post("/api/jobs", json={"url": "fail.example"}).content
    ).job
    finished = _wait_finished(client, failed.id)
    assert finished.status == "failed"
    assert finished.error is not None and finished.error.code == "bot_blocked"
    assert finished.events[-1].kind == "error" and finished.events[-1].hint

    missing = JobResponse.model_validate_json(
        client.post("/api/jobs", json={"url": "no-browser.example"}).content
    ).job
    finished = _wait_finished(client, missing.id)
    assert finished.error is not None and finished.error.code == "browser_missing"

    invalid = client.post("/api/jobs", json={"url": "ftp://x.example"})
    assert invalid.status_code == 422 and _error(invalid).error.code == "invalid_url"
    assert client.get("/api/jobs/0123456789ab").status_code == 404
    current = client.get("/api/jobs/current").json()["job"]
    assert current["id"] == missing.id


def test_history_and_results(client: httpx.Client) -> None:
    history = HistoryResponse.model_validate_json(client.get("/api/history").content)
    assert [entry.site for entry in history.entries] == [ACME_SITE, BETA_SITE]
    acme, beta = history.entries
    assert acme.has_design and acme.has_prompt and acme.thumbnail == "screenshots/home-1440.png"
    assert beta.name == "Beta Studio" and not beta.has_prompt and beta.thumbnail is None
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    assert results.design is not None and results.design.front_matter is not None
    assert results.design.front_matter.startswith("version: alpha")
    assert "<table>" in results.design.html and "<script" not in results.design.html
    assert results.prompt is not None
    assert results.preview_url == f"/files/{ACME_SITE}/preview.html"
    assert {shot.color_scheme for shot in results.screenshots} == {"light", "dark"}
    assert results.prompt_images == [
        f"/files/{ACME_SITE}/prompt-images/desktop.png",
        f"/files/{ACME_SITE}/prompt-images/mobile.png",
    ]
    for file in results.files:
        assert client.get(file.url).status_code == 200, file.url
    assert client.get("/api/results/..").status_code == 404
    assert client.get("/api/results/missing.example").status_code == 404


def test_open_folder_heartbeat_and_shutdown(client: httpx.Client, ui_fake_api: FakeApi) -> None:
    assert client.post("/api/open-folder", json={"site": ACME_SITE}).status_code == 200
    assert client.post("/api/open-folder", json={}).status_code == 200
    assert client.post("/api/open-folder", json={"site": "../x"}).status_code == 404
    assert ui_fake_api.state.opened_folders == [
        str(ui_fake_api.output_root / ACME_SITE),
        str(ui_fake_api.output_root),
    ]
    assert client.post("/api/heartbeat").json() == {"ok": True, "idle_shutdown_s": 120.0}
    assert ui_fake_api.state.heartbeats == 1
    assert client.post("/api/shutdown", json={}).status_code == 202
    assert ui_fake_api.state.shutdown_requests == [False]


def test_browser_status_and_install(client: httpx.Client, ui_fake_api: FakeApi) -> None:
    status = BrowserStatusResponse.model_validate_json(client.get("/api/browser/status").content)
    assert [info.name for info in status.browsers] == ["chromium", "msedge", "chrome"]
    assert status.auto == "msedge" and status.install.state == "idle"
    ui_fake_api.state.browsers_available = False
    status = BrowserStatusResponse.model_validate_json(client.get("/api/browser/status").content)
    assert status.auto is None
    started = client.post("/api/browser/install")
    assert started.status_code == 202
    assert client.post("/api/browser/install").status_code == 409
    deadline = time.monotonic() + 10
    install = InstallResponse.model_validate_json(started.content).install
    while install.state == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
        install = InstallResponse.model_validate_json(
            client.get("/api/browser/install").content
        ).install
    assert install.state == "succeeded" and install.percent == 100
    status = BrowserStatusResponse.model_validate_json(client.get("/api/browser/status").content)
    assert status.auto == "chromium"
