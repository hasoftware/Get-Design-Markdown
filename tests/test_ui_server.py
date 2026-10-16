"""The real local UI server (ui/server.py) over real HTTP on 127.0.0.1, port 0.

Jobs run scripted fake runners (no browser); the Chromium installer, browser detection and the
file manager are faked; every test gets its own copy of an output root built by the real
synthesizer and exporters (tests/ui_fake_api.build_fake_output). The contract checks shared
with the fake API live in test_ui_server_contract.py.
"""

from __future__ import annotations

import contextlib
import errno
import http.client
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from get_design_markdown.console import (
    MessageSink,
    print_step,
    print_success,
    print_warning,
    progress_status,
    use_sink,
)
from get_design_markdown.errors import (
    BotBlockedError,
    BrowserNotInstalledError,
    GdmError,
    NotImplementedYetError,
)
from get_design_markdown.storage import domain_of
from get_design_markdown.synthesizer.synthesize import run_synthesize
from get_design_markdown.ui import browser_install
from get_design_markdown.ui import server as server_module
from get_design_markdown.ui.browser_install import (
    AUTO_ORDER,
    BROWSER_LABELS,
    BrowserInfo,
    BrowserInstaller,
    BrowserStatus,
    InstallStatus,
)
from get_design_markdown.ui.jobs import JobManager, JobRequest, JobSnapshot
from get_design_markdown.ui.server import (
    FILES_HTML_CSP,
    IDLE_SHUTDOWN_S,
    INDEX_CSP,
    MAX_BODY_BYTES,
    TOKEN_HEADER,
    BrowserStatusResponse,
    ErrorResponse,
    HealthResponse,
    HeartbeatResponse,
    InstallResponse,
    JobResponse,
    ResultsResponse,
    SettingsResponse,
    UiMode,
    UiServer,
)
from get_design_markdown.ui.settings import UiSettings, load_ui_settings
from tests.ui_fake_api import ACME_SITE, BETA_SITE, build_fake_output

UI_LOGGER = "get_design_markdown.ui"
WAIT_S = 10.0


# ─── Scripted jobs, fake installer, fake browsers ────────────────────────────


class ScriptedRunner:
    """Real-server counterpart of the fake API's scripted jobs, chosen by the URL:

    - "slow"        progress every 10 ms for a minute (cancel tests)
    - "hold"        waits for `release` without any message (a cancel cannot take effect)
    - "fail"        bot wall in step 1 (code "bot_blocked")
    - "no-browser"  no browser in step 1 (code "browser_missing")
    - "crash"       an unexpected RuntimeError
    - "robots"      succeeds with a robots.txt warning
    - anything else succeeds: the site folder is a copy of `template` (acme.example)
    """

    def __init__(self, template: Path) -> None:
        self.template = template
        self.release = threading.Event()
        self.started = threading.Event()
        self.calls: list[tuple[JobRequest, UiSettings]] = []

    def __call__(self, request: JobRequest, settings: UiSettings, sink: MessageSink) -> Path:
        self.calls.append((request, settings))
        self.started.set()
        url = request.url.lower()
        folder = settings.output_dir / domain_of(request.url)
        print_step(1, 4, "Thu thập dữ liệu trang")
        if "hold" in url:
            self.release.wait(WAIT_S * 3)
            return folder
        if "slow" in url:
            with progress_status("Đang thu thập") as progress:
                for index in range(6000):
                    progress(f"chờ trang… {index}")
                    time.sleep(0.01)
            return folder
        if "robots" in url:
            print_warning("robots.txt không cho phép truy cập /. Công cụ vẫn tiếp tục.")
        else:
            print_success("robots.txt cho phép truy cập /.")
        with progress_status("Đang thu thập") as progress:
            progress("Mở trình duyệt…")
        if "no-browser" in url:
            raise BrowserNotInstalledError()
        if "fail" in url:
            raise BotBlockedError(
                "Trang chặn trình duyệt tự động (HTTP 403).",
                hint="Hãy thử lại sau, hoặc chọn một trang công khai khác của site.",
            )
        if "crash" in url:
            raise RuntimeError("boom")
        print_step(2, 4, "Phân tích thiết kế")
        print_step(3, 4, "Dựng DESIGN.md")
        print_step(4, 4, "Xuất tokens.json và preview.html")
        if not folder.exists():
            shutil.copytree(self.template, folder)
        return folder


class FakeInstaller(BrowserInstaller):
    """Chromium download that only changes state when the test says so."""

    def __init__(self) -> None:
        super().__init__(command=["fake-install"])
        self._status = InstallStatus()
        self.starts = 0

    def start(self) -> InstallStatus:
        self.starts += 1
        self._status = InstallStatus(
            state="running",
            percent=0.0,
            message="Đang tải Chromium… 0%",
            started_at=datetime.now(UTC),
        )
        return self._status

    def status(self) -> InstallStatus:
        return self._status

    def wait(self, timeout: float | None = None) -> InstallStatus:
        return self._status

    def finish(self) -> None:
        self._status = self._status.model_copy(
            update={
                "state": "succeeded",
                "percent": 100.0,
                "message": "Đã tải xong Chromium.",
                "finished_at": datetime.now(UTC),
            }
        )


def fake_browser_status(available: tuple[str, ...] = ("msedge",)) -> BrowserStatus:
    infos = [
        BrowserInfo(
            name=name,
            label=BROWSER_LABELS[name],
            available=name in available,
            version="141.0.3537.71" if name in available else None,
            path=f"C:/fake/{name}.exe" if name in available else None,
            detail=None if name in available else "Không tìm thấy trên máy.",
        )
        for name in AUTO_ORDER
    ]
    auto = next((info.name for info in infos if info.available), None)
    return BrowserStatus(browsers=infos, auto=auto)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def ui_output_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """acme.example + beta.example, built once by the real synthesizer and exporters."""
    root = tmp_path_factory.mktemp("ui-server-template")
    build_fake_output(root)
    return root


@pytest.fixture
def output_root(ui_output_template: Path, tmp_path: Path) -> Path:
    root = tmp_path / "output"
    shutil.copytree(ui_output_template, root)
    return root


@dataclass
class Harness:
    """What a test needs around a real UiServer."""

    output_root: Path
    settings_path: Path
    runner: ScriptedRunner
    installer: FakeInstaller
    opened: list[Path] = field(default_factory=list)
    open_error: BaseException | None = None
    browsers: Callable[[], BrowserStatus] = fake_browser_status
    servers: list[UiServer] = field(default_factory=list)

    def make(self, **overrides: Any) -> UiServer:
        """A started server with test doubles; overrides go to UiServer(...)."""
        options: dict[str, Any] = {
            "mode": "none",
            "out_root": self.output_root,
            "settings_path": self.settings_path,
            "jobs": JobManager(self.runner),
            "installer": self.installer,
            "defaults": UiSettings(output_dir=self.output_root),
        }
        options.update(overrides)
        server = UiServer(**options)
        self.servers.append(server)
        server.start()
        return server


@pytest.fixture
def harness(
    output_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Harness]:
    harness = Harness(
        output_root=output_root,
        settings_path=tmp_path / "config" / "ui-settings.json",
        runner=ScriptedRunner(output_root / ACME_SITE),
        installer=FakeInstaller(),
    )

    def record_open(folder: Path) -> None:
        if harness.open_error is not None:
            raise harness.open_error
        harness.opened.append(folder)

    monkeypatch.setattr(server_module, "open_folder", record_open)
    monkeypatch.setattr(browser_install, "browser_status", lambda: harness.browsers())
    yield harness
    harness.runner.release.set()
    for server in harness.servers:
        server.stop()


@pytest.fixture
def server(harness: Harness) -> UiServer:
    return harness.make()


def api_client(server: UiServer, *, token: bool = True) -> httpx.Client:
    headers = {TOKEN_HEADER: server.token} if token else {}
    return httpx.Client(base_url=server.url, headers=headers, trust_env=False, timeout=WAIT_S)


@pytest.fixture
def client(server: UiServer) -> Iterator[httpx.Client]:
    with api_client(server) as session:
        yield session


@dataclass
class RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def code(self) -> str:
        return str(ErrorResponse.model_validate_json(self.body).error.code)


def raw_request(
    port: int,
    method: str,
    path: str,
    headers: list[tuple[str, str]] | None = None,
    body: bytes | None = None,
    *,
    host: str | None = "default",
) -> RawResponse:
    """One request sent exactly as written (no path normalisation, explicit Host)."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=WAIT_S)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host == "default":
            connection.putheader("Host", f"127.0.0.1:{port}")
        elif host is not None:
            connection.putheader("Host", host)
        for name, value in headers or []:
            connection.putheader(name, value)
        if body is not None and not any(
            name.lower() == "content-length" for name, _ in headers or []
        ):
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        return RawResponse(
            status=response.status,
            headers={name.lower(): value for name, value in response.getheaders()},
            body=response.read(),
        )
    finally:
        connection.close()


def error_of(response: httpx.Response) -> ErrorResponse:
    return ErrorResponse.model_validate_json(response.content)


def wait_job(client: httpx.Client, job_id: str, timeout: float = WAIT_S) -> JobSnapshot:
    """Poll like the frontend (since = last seq) until the job finished; all events."""
    deadline = time.monotonic() + timeout
    events: list[Any] = []
    since = 0
    while time.monotonic() < deadline:
        job = JobResponse.model_validate_json(
            client.get(f"/api/jobs/{job_id}", params={"since": since}).content
        ).job
        events.extend(job.events)
        if job.events:
            since = job.events[-1].seq
        if job.finished and since == job.last_seq:
            return job.model_copy(update={"events": events})
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


def start_job(client: httpx.Client, url: str, **body: Any) -> JobSnapshot:
    response = client.post("/api/jobs", json={"url": url, **body})
    assert response.status_code == 202, response.text
    return JobResponse.model_validate_json(response.content).job


def captured_before_the_results(folder: Path, seconds: float = 60.0) -> None:
    """Date every screenshot `seconds` before DESIGN.md, as a complete run leaves them (a test
    that adds screenshots must not look like an interrupted re-run)."""
    reference = (folder / "DESIGN.md").stat().st_mtime - seconds
    for shot in (folder / "screenshots").iterdir():
        os.utime(shot, (reference, reference))


def no_cors(headers: httpx.Headers | dict[str, str]) -> bool:
    return not [name for name in headers if name.lower().startswith("access-control")]


# ─── Lifecycle ───────────────────────────────────────────────────────────────


def test_start_binds_localhost_and_builds_the_launch_url(server: UiServer) -> None:
    assert server.port > 0
    assert server.url == f"http://127.0.0.1:{server.port}/"
    assert server.launch_url == f"{server.url}#token={server.token}"
    assert len(server.token) >= 43 and server.token.replace("-", "").replace("_", "").isalnum()
    with socket.create_connection(("127.0.0.1", server.port), timeout=WAIT_S):
        pass


def test_each_session_has_its_own_token(harness: Harness) -> None:
    assert harness.make().token != harness.make().token
    assert harness.make(token="given").token == "given"


def test_port_property_before_start(harness: Harness) -> None:
    server = UiServer(
        mode="none", out_root=harness.output_root, settings_path=harness.settings_path
    )
    with pytest.raises(RuntimeError):
        _ = server.port
    assert server.wait(0) is True  # nothing started: nothing to wait for
    server.stop()  # harmless


def test_port_in_use_is_a_vietnamese_error(harness: Harness) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        server = UiServer(
            mode="none",
            port=port,
            out_root=harness.output_root,
            settings_path=harness.settings_path,
            installer=harness.installer,
        )
        with pytest.raises(GdmError) as caught:
            server.start()
    assert caught.value.message == f"Cổng {port} đang được chương trình khác dùng."
    assert caught.value.hint and "--port" in caught.value.hint


def test_fixed_port_is_used(harness: Harness) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = harness.make(port=port)
    assert server.port == port


def test_stop_is_idempotent_and_closes_the_socket(harness: Harness) -> None:
    server = harness.make()
    port = server.port
    with api_client(server) as client:
        assert client.get("/api/health").status_code == 200
    server.stop()
    server.stop()
    assert server.wait(0) is True
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()


def test_context_manager(harness: Harness) -> None:
    with (
        UiServer(
            mode="none",
            out_root=harness.output_root,
            settings_path=harness.settings_path,
            installer=harness.installer,
            defaults=UiSettings(output_dir=harness.output_root),
        ) as server,
        api_client(server) as client,
    ):
        assert client.get("/api/health").json()["ok"] is True
    assert server.wait(0)


def test_stop_cancels_the_running_job_first(harness: Harness) -> None:
    jobs = JobManager(harness.runner)
    server = harness.make(jobs=jobs)
    with api_client(server) as client:
        job = start_job(client, "https://slow.example")
    assert harness.runner.started.wait(WAIT_S)
    begin = time.monotonic()
    server.stop()
    assert time.monotonic() - begin < WAIT_S
    assert jobs.get(job.id).status == "cancelled"
    assert not [thread for thread in threading.enumerate() if thread.name == f"gdm-job-{job.id}"]


def test_wait_returns_false_while_running(server: UiServer) -> None:
    begin = time.monotonic()
    assert server.wait(0.3) is False
    assert time.monotonic() - begin >= 0.25


def test_missing_frontend_folder_fails_at_start(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*parts: str) -> Path:
        raise FileNotFoundError("/".join(parts))

    monkeypatch.setattr(server_module, "resource_path", missing)
    server = UiServer(
        mode="none", out_root=harness.output_root, settings_path=harness.settings_path
    )
    with pytest.raises(GdmError) as caught:
        server.start()
    assert "ui/static" in caught.value.message and caught.value.hint


def test_a_bad_environment_fails_at_start(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GDM_BROWSER", "firefox")
    server = UiServer(mode="none", settings_path=harness.settings_path)  # defaults from env
    with pytest.raises(GdmError):
        server.start()


# ─── Idle shutdown and /api/shutdown ─────────────────────────────────────────


# The idle clock runs from server.start(): a test that must reach a server with a short idle
# timeout holds the shutdown off with the (fake) Chromium download or a held job until it is
# connected, so it never depends on how fast a client starts on a busy machine.


def test_browser_mode_stops_after_the_idle_timeout(harness: Harness) -> None:
    server = harness.make(mode="browser", idle_timeout_s=0.3)
    assert server.wait(WAIT_S) is True  # no page ever connected: the clock started at start()


def test_heartbeat_reports_the_idle_timeout(harness: Harness) -> None:
    harness.installer.start()  # holds the idle shutdown off while the client connects
    server = harness.make(mode="browser", idle_timeout_s=0.3)
    with api_client(server) as client:
        heartbeat = HeartbeatResponse.model_validate_json(client.post("/api/heartbeat").content)
    assert heartbeat.idle_shutdown_s == pytest.approx(0.3)
    assert server.wait(0.6) is False  # the download still runs
    harness.installer.finish()
    assert server.wait(WAIT_S) is True


def test_heartbeats_keep_the_server_alive(harness: Harness) -> None:
    harness.installer.start()
    server = harness.make(mode="browser", idle_timeout_s=1.5)
    with api_client(server) as client:
        assert client.post("/api/heartbeat").status_code == 200
        harness.installer.finish()  # from now on only the heartbeats keep it alive
        begin = time.monotonic()
        while time.monotonic() - begin < 2.0:  # longer than the timeout
            assert client.post("/api/heartbeat").status_code == 200
            time.sleep(0.1)
        assert client.get("/api/health").status_code == 200
    assert server.wait(WAIT_S) is True  # once the heartbeats stop


def test_a_running_job_or_install_postpones_the_idle_shutdown(harness: Harness) -> None:
    jobs = JobManager(harness.runner)
    job = jobs.start(
        JobRequest(url="https://hold.example"), UiSettings(output_dir=harness.output_root)
    )
    assert harness.runner.started.wait(WAIT_S)
    server = harness.make(mode="browser", idle_timeout_s=0.2, jobs=jobs)
    with api_client(server) as client:
        assert client.get("/api/health").json()["job_id"] == job.id
    assert server.wait(1.0) is False  # the job holds it
    harness.installer.start()
    harness.runner.release.set()
    assert jobs.wait(job.id, WAIT_S).status == "succeeded"
    assert server.wait(0.8) is False  # the download still runs
    harness.installer.finish()
    assert server.wait(WAIT_S) is True


@pytest.mark.parametrize("mode", ["window", "none"])
def test_no_idle_shutdown_outside_browser_mode(harness: Harness, mode: UiMode) -> None:
    server = harness.make(mode=mode, idle_timeout_s=0.1)
    with api_client(server) as client:
        assert client.post("/api/heartbeat").json() == {"ok": True, "idle_shutdown_s": None}
    assert server.wait(0.5) is False


def test_idle_timeout_none_disables_it(harness: Harness) -> None:
    server = harness.make(mode="browser", idle_timeout_s=None)
    with api_client(server) as client:
        assert client.post("/api/heartbeat").json()["idle_shutdown_s"] is None
    assert server.wait(0.3) is False


def test_default_idle_timeout_is_two_minutes(harness: Harness) -> None:
    server = harness.make(mode="browser")
    with api_client(server) as client:
        assert client.post("/api/heartbeat").json()["idle_shutdown_s"] == IDLE_SHUTDOWN_S == 120


def test_shutdown_endpoint_stops_the_server(server: UiServer, client: httpx.Client) -> None:
    response = client.post("/api/shutdown", json={})
    assert response.status_code == 202 and response.json() == {"ok": True}
    assert server.wait(WAIT_S) is True


def test_shutdown_refuses_while_a_job_runs_unless_forced(
    harness: Harness, server: UiServer, client: httpx.Client
) -> None:
    job = start_job(client, "https://slow.example")
    assert harness.runner.started.wait(WAIT_S)
    busy = client.post("/api/shutdown", json={"force": False})
    assert busy.status_code == 409
    assert error_of(busy).error.code == "busy"
    assert error_of(busy).error.details == {"job_id": job.id}
    assert server.wait(0.2) is False
    forced = client.post("/api/shutdown", json={"force": True})
    assert forced.status_code == 202
    assert server.wait(WAIT_S) is True
    jobs = server.jobs
    assert jobs.get(job.id).status == "cancelled"


def test_shutdown_body_is_validated(client: httpx.Client) -> None:
    response = client.post("/api/shutdown", json={"force": "later"})
    assert response.status_code == 422 and error_of(response).error.details == {"field": "force"}
    response = client.post("/api/shutdown", json={"now": True})
    assert response.status_code == 422 and error_of(response).error.details == {"field": "now"}


# ─── Security details ────────────────────────────────────────────────────────

API_CALLS: list[tuple[str, str]] = [
    ("GET", "/api/health"),
    ("GET", "/api/settings"),
    ("PUT", "/api/settings"),
    ("POST", "/api/jobs"),
    ("GET", "/api/jobs/current"),
    ("GET", "/api/jobs/0123456789ab"),
    ("POST", "/api/jobs/0123456789ab/cancel"),
    ("GET", "/api/history"),
    ("GET", f"/api/results/{ACME_SITE}"),
    ("POST", "/api/open-folder"),
    ("GET", "/api/browser/status"),
    ("GET", "/api/browser/install"),
    ("POST", "/api/browser/install"),
    ("POST", "/api/heartbeat"),
    ("POST", "/api/shutdown"),
    ("GET", "/api/unknown"),
]


@pytest.mark.parametrize(("method", "path"), API_CALLS)
@pytest.mark.parametrize("token", [None, "", "wrong", "TOKEN-UPPER"])
def test_every_api_call_needs_the_token(
    harness: Harness, server: UiServer, method: str, path: str, token: str | None
) -> None:
    headers = [("Content-Type", "application/json")]
    if token is not None:
        headers.append((TOKEN_HEADER, server.token.upper() if token == "TOKEN-UPPER" else token))
    body = json.dumps({"url": "stripe.com", "pages": 1, "force": True}).encode()
    response = raw_request(server.port, method, path, headers, body if method != "GET" else None)
    assert response.status == 403 and response.code == "forbidden"
    assert no_cors(response.headers)
    # Nothing happened behind the refusal.
    assert harness.runner.calls == [] and harness.installer.starts == 0
    assert harness.opened == [] and not harness.settings_path.exists()
    assert server.wait(0) is False


def test_token_is_only_accepted_in_its_header(server: UiServer) -> None:
    for path in (f"/api/health?token={server.token}", f"/api/health#token={server.token}"):
        assert raw_request(server.port, "GET", path).status == 403
    response = raw_request(
        server.port, "GET", "/api/health", [(TOKEN_HEADER, server.token), (TOKEN_HEADER, "x")]
    )
    assert response.status == 403  # two token headers
    ok = raw_request(server.port, "GET", "/api/health", [(TOKEN_HEADER, f" {server.token} ")])
    assert ok.status == 200


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "evil.example",
        "evil.example:{port}",
        "127.0.0.1",
        "127.0.0.1:1",
        "localhost",
        "[::1]:{port}",
        "0.0.0.0:{port}",
        "127.0.0.1:{port}.evil.example",
        "127.0.0.2:{port}",
    ],
)
@pytest.mark.parametrize(
    "path",
    ["/", "/static/index.html", f"/files/{ACME_SITE}/DESIGN.md", "/api/health"],
)
def test_dns_rebinding_hosts_are_refused(server: UiServer, host: str | None, path: str) -> None:
    value = None if host is None else host.format(port=server.port)
    response = raw_request(server.port, "GET", path, [(TOKEN_HEADER, server.token)], host=value)
    assert response.status == 403 and response.code == "bad_host"
    assert no_cors(response.headers)


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "LOCALHOST:{port}"])
def test_local_hosts_are_accepted(server: UiServer, host: str) -> None:
    response = raw_request(
        server.port,
        "GET",
        "/api/health",
        [(TOKEN_HEADER, server.token)],
        host=host.format(port=server.port),
    )
    assert response.status == 200


def test_two_host_headers_are_refused(server: UiServer) -> None:
    response = raw_request(
        server.port,
        "GET",
        "/",
        [("Host", "evil.example")],
        host=f"127.0.0.1:{server.port}",
    )
    assert response.status == 403 and response.code == "bad_host"


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "null",
        "http://127.0.0.1",
        "http://127.0.0.1:1",
        "https://127.0.0.1:{port}",
        "http://localhost.evil.example:{port}",
    ],
)
@pytest.mark.parametrize(("method", "path"), [("POST", "/api/heartbeat"), ("PUT", "/api/settings")])
def test_foreign_origins_cannot_write(
    harness: Harness, server: UiServer, origin: str, method: str, path: str
) -> None:
    response = raw_request(
        server.port,
        method,
        path,
        [
            (TOKEN_HEADER, server.token),
            ("Origin", origin.format(port=server.port)),
            ("Content-Type", "application/json"),
        ],
        b'{"pages": 3}',
    )
    assert response.status == 403 and response.code == "bad_origin"
    assert not harness.settings_path.exists()


def test_same_origin_writes_and_foreign_origin_reads(
    server: UiServer, client: httpx.Client
) -> None:
    for origin in (f"http://127.0.0.1:{server.port}", f"http://localhost:{server.port}"):
        assert client.post("/api/heartbeat", headers={"Origin": origin}).status_code == 200
    # The Origin rule is for writes; reads still need the token.
    assert client.get("/api/health", headers={"Origin": "https://evil.example"}).status_code == 200


@pytest.mark.parametrize("method", ["OPTIONS", "DELETE", "PATCH", "TRACE", "CONNECT", "FOO"])
@pytest.mark.parametrize("path", ["/", "/api/jobs", "/api/settings", f"/files/{ACME_SITE}/x"])
def test_other_methods_are_refused_without_cors(server: UiServer, method: str, path: str) -> None:
    response = raw_request(
        server.port,
        method,
        path,
        [
            ("Origin", "https://evil.example"),
            ("Access-Control-Request-Method", "POST"),
            ("Access-Control-Request-Headers", TOKEN_HEADER),
        ],
    )
    assert response.status == 405 and response.code == "method_not_allowed"
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert no_cors(response.headers)


def test_no_response_ever_carries_cors_headers(server: UiServer, client: httpx.Client) -> None:
    responses = [
        client.get("/"),
        client.get("/api/health"),
        client.get(f"/files/{ACME_SITE}/preview.html"),
        client.get("/api/results/missing.example"),
        client.post("/api/jobs", json={"url": "ftp://x"}),
        client.get("/api/health", headers={TOKEN_HEADER: "wrong", "Origin": "https://e.example"}),
        client.request("OPTIONS", "/api/jobs", headers={"Origin": "https://evil.example"}),
    ]
    for response in responses:
        assert no_cors(response.headers), response.url


@pytest.mark.parametrize(
    "path",
    [
        f"/files/{ACME_SITE}/../{BETA_SITE}/DESIGN.md",
        f"/files/{ACME_SITE}/%2e%2e/{BETA_SITE}/DESIGN.md",
        f"/files/{ACME_SITE}/%2E%2E%2F{BETA_SITE}%2FDESIGN.md",
        f"/files/{ACME_SITE}/..%5c{BETA_SITE}%5cDESIGN.md",
        f"/files/{ACME_SITE}/screenshots/..%2f..%2f{BETA_SITE}%2fDESIGN.md",
        f"/files/{ACME_SITE}/screenshots/../DESIGN.md",
        f"/files/{ACME_SITE}/./DESIGN.md",
        f"/files/{ACME_SITE}//DESIGN.md",
        f"/files/{ACME_SITE}/DESIGN.md/",
        f"/files/{ACME_SITE}/DESIGN.md%00.png",
        f"/files/{ACME_SITE}/C:/Windows/win.ini",
        f"/files/{ACME_SITE}/C:%5CWindows%5Cwin.ini",
        f"/files/{ACME_SITE}/%2Fetc%2Fpasswd",
        f"/files/{ACME_SITE}/%5C%5Cserver%5Cshare%5Cfile",
        f"/files/{ACME_SITE}/DESIGN.md:stream",
        f"/files/{ACME_SITE}/.hidden",
        f"/files/{ACME_SITE}/screenshots",
        f"/files/{ACME_SITE}/missing.md",
        f"/files/{ACME_SITE}",
        f"/files/{ACME_SITE}/",
        "/files//etc/passwd",
        "/files/../ui-settings.json",
        "/files/%2e%2e/ui-settings.json",
        "/files/..%2fconfig/ui-settings.json",
        "/files/.hidden-site/DESIGN.md",
        "/files/C:/Windows/win.ini",
        "/files/CON/x",
        "/files/nope.example/DESIGN.md",
        "/files",
        "/static/../ui-settings.json",
        "/static/%2e%2e/server.py",
        "/static/..%5cserver.py",
        "/static/",
        "/static",
        "/static/missing.js",
        "/index.html",
        "/etc/passwd",
        "//etc/passwd",
        "http://evil.example/api/health",
    ],
)
def test_paths_outside_the_folders_are_not_found(
    server: UiServer, output_root: Path, path: str
) -> None:
    (output_root / ACME_SITE / ".hidden").write_text("secret", encoding="utf-8")
    hidden = output_root / ".hidden-site"
    hidden.mkdir()
    (hidden / "DESIGN.md").write_text("secret", encoding="utf-8")
    response = raw_request(server.port, "GET", path)
    assert response.status == 404, (path, response.body)
    assert response.code == "not_found"
    assert b"secret" not in response.body


@pytest.mark.parametrize(
    "site",
    ["..", "%2e%2e", "..%2f..", ".hidden", "C:", "a%5cb", "%00", "missing.example", "x" * 300],
)
def test_results_of_invalid_or_missing_sites(server: UiServer, site: str) -> None:
    response = raw_request(
        server.port, "GET", f"/api/results/{site}", [(TOKEN_HEADER, server.token)]
    )
    assert response.status == 404 and response.code == "site_not_found"


def _link_dir(link: Path, target: Path) -> None:
    """A directory symlink, or a junction on Windows without symlink rights."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if sys.platform != "win32":
            pytest.skip("symlinks are not available")
    import _winapi

    try:
        _winapi.CreateJunction(str(target), str(link))
    except OSError:
        pytest.skip("neither symlinks nor junctions are available")


def test_links_cannot_escape_the_site_folder(
    server: UiServer, output_root: Path, tmp_path: Path, client: httpx.Client
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    _link_dir(output_root / ACME_SITE / "escape", outside)
    assert raw_request(server.port, "GET", f"/files/{ACME_SITE}/escape/secret.md").status == 404
    try:
        (output_root / ACME_SITE / "link.md").symlink_to(outside / "secret.md")
    except OSError:
        pass
    else:
        assert raw_request(server.port, "GET", f"/files/{ACME_SITE}/link.md").status == 404
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    assert not [file for file in results.files if "secret" in file.path or file.name == "link.md"]
    # A link that stays inside the folder is fine.
    _link_dir(output_root / ACME_SITE / "shots", output_root / ACME_SITE / "screenshots")
    assert raw_request(server.port, "GET", f"/files/{ACME_SITE}/shots/home-1440.png").status == 200


# ─── Request bodies ──────────────────────────────────────────────────────────


def test_oversized_body_gets_a_413_answer(harness: Harness, server: UiServer) -> None:
    body = b"{" + b" " * MAX_BODY_BYTES + b"}"
    response = raw_request(
        server.port,
        "POST",
        "/api/jobs",
        [(TOKEN_HEADER, server.token), ("Content-Type", "application/json")],
        body,
    )
    assert response.status == 413 and response.code == "too_large"
    assert harness.runner.calls == []
    # A body of exactly the limit is read (and judged on its content).
    padded = b'{"url": "' + b"a" * (MAX_BODY_BYTES - 20) + b'"}'
    padded += b" " * (MAX_BODY_BYTES - len(padded))
    assert len(padded) == MAX_BODY_BYTES
    response = raw_request(
        server.port,
        "POST",
        "/api/jobs",
        [(TOKEN_HEADER, server.token), ("Content-Type", "application/json")],
        padded,
    )
    assert response.status == 422 and response.code == "invalid"


def test_huge_body_does_not_hurt_the_server(server: UiServer) -> None:
    # Past MAX_DRAIN_BYTES the server closes the connection: the client may see a reset.
    with contextlib.suppress(OSError):
        response = raw_request(
            server.port,
            "PUT",
            "/api/settings",
            [(TOKEN_HEADER, server.token), ("Content-Type", "application/json")],
            b"x" * (4 * 1024 * 1024),
        )
        assert response.status == 413
    health = raw_request(server.port, "GET", "/api/health", [(TOKEN_HEADER, server.token)])
    assert health.status == 200


@pytest.mark.parametrize(
    ("headers", "body", "status", "code"),
    [
        ([("Content-Type", "text/plain")], b'{"url": "x.example"}', 415, "unsupported_media_type"),
        ([], b'{"url": "x.example"}', 415, "unsupported_media_type"),
        ([("Content-Type", "application/json")], b"{broken", 400, "bad_json"),
        ([("Content-Type", "application/json")], b"[1, 2]", 400, "bad_json"),
        ([("Content-Type", "application/json")], b'"text"', 400, "bad_json"),
        ([("Content-Type", "application/json")], b"\xff\xfe{}", 400, "bad_json"),
        ([("Content-Type", "application/json")], b"[" * 30000 + b"]" * 30000, 400, "bad_json"),
        (
            [("Content-Type", "application/json")],
            b'{"url": 1' + b"0" * 6000 + b"}",
            400,
            "bad_json",
        ),
        ([("Content-Type", "APPLICATION/JSON; charset=UTF-8")], b'{"url": ""}', 422, "invalid"),
        ([("Content-Type", "application/json"), ("Content-Length", "abc")], b"{}", 400, "bad_json"),
        ([("Content-Type", "application/json"), ("Content-Length", "-1")], b"{}", 400, "bad_json"),
    ],
    ids=[
        "text-plain",
        "no-content-type",
        "broken-json",
        "array",
        "string",
        "not-utf8",
        "deep-nesting",
        "huge-number",
        "media-type-case",
        "content-length-text",
        "content-length-negative",
    ],
)
def test_body_rules(
    server: UiServer, headers: list[tuple[str, str]], body: bytes, status: int, code: str
) -> None:
    response = raw_request(
        server.port, "POST", "/api/jobs", [(TOKEN_HEADER, server.token), *headers], body
    )
    assert (response.status, response.code) == (status, code)


def test_chunked_bodies_are_refused(server: UiServer) -> None:
    response = raw_request(
        server.port,
        "POST",
        "/api/heartbeat",
        [
            (TOKEN_HEADER, server.token),
            ("Content-Type", "application/json"),
            ("Transfer-Encoding", "chunked"),
        ],
        b"2\r\n{}\r\n0\r\n\r\n",
    )
    assert response.status == 400 and response.code == "bad_json"


def test_empty_body_counts_as_an_empty_object(server: UiServer) -> None:
    response = raw_request(server.port, "POST", "/api/heartbeat", [(TOKEN_HEADER, server.token)])
    assert response.status == 200
    response = raw_request(
        server.port, "POST", "/api/open-folder", [(TOKEN_HEADER, server.token)], b"  \r\n "
    )
    assert response.status == 200


# ─── Errors ──────────────────────────────────────────────────────────────────


def test_unexpected_errors_are_logged_but_not_shown(
    server: UiServer,
    client: httpx.Client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def explode(root: Path) -> list[Any]:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(server_module, "list_history", explode)
    with caplog.at_level(logging.ERROR, logger=UI_LOGGER):
        response = client.get("/api/history")
    assert response.status_code == 500
    error = error_of(response).error
    assert error.code == "internal" and error.message == server_module.UNEXPECTED_MESSAGE
    assert error.hint and "ui.log" in error.hint
    assert b"secret" not in response.content and b"Traceback" not in response.content
    assert "secret internal detail" in caplog.text and "Traceback" in caplog.text
    # The server keeps serving.
    assert client.get("/api/health").status_code == 200


def test_requests_are_not_logged_to_stderr(
    server: UiServer, client: httpx.Client, capfd: pytest.CaptureFixture[str]
) -> None:
    capfd.readouterr()
    client.get("/")
    client.get("/api/health")
    client.get("/api/results/missing.example")
    client.post("/api/jobs", content=b"{", headers={"Content-Type": "application/json"})
    raw_request(server.port, "FOO", "/")
    raw_request(server.port, "GET", "/" + "a" * 70000)  # request line too long
    captured = capfd.readouterr()
    assert captured.err == "" and captured.out == "", captured


def test_protocol_errors_use_the_json_envelope(server: UiServer) -> None:
    response = raw_request(server.port, "GET", "/" + "a" * 70000)
    assert response.status == 414 and response.code == "too_large"
    for request in (b"GET / extra HTTP/1.1\r\n\r\n", b"GET / HTTP/2.0\r\n\r\n"):
        with socket.create_connection(("127.0.0.1", server.port), timeout=WAIT_S) as connection:
            connection.sendall(request)
            answer = b""
            while chunk := connection.recv(65536):
                answer += chunk
        head, _, body = answer.partition(b"\r\n\r\n")
        assert head.startswith((b"HTTP/1.0 400", b"HTTP/1.0 505")), answer
        assert b"Content-Type: application/json; charset=utf-8" in head
        assert b"\r\nServer: gdm-ui\r\n" in head
        assert ErrorResponse.model_validate_json(body).error.code == "invalid"


# ─── Settings ────────────────────────────────────────────────────────────────


def test_settings_are_saved_to_the_config_file(
    harness: Harness, server: UiServer, client: httpx.Client
) -> None:
    response = client.put("/api/settings", json={"pages": 3, "lang": "vi", "dark": True})
    assert response.status_code == 200
    settings = SettingsResponse.model_validate_json(response.content)
    assert settings.config_path == str(harness.settings_path)
    saved = load_ui_settings(harness.settings_path, UiSettings(output_dir=harness.output_root))
    assert saved == settings.settings
    assert (saved.pages, saved.lang, saved.dark) == (3, "vi", True)
    # A new server reads them back.
    again = harness.make()
    with api_client(again) as other:
        reloaded = SettingsResponse.model_validate_json(other.get("/api/settings").content)
    assert reloaded.settings == saved


def test_schema_version_in_a_put_is_ignored(client: httpx.Client) -> None:
    current = client.get("/api/settings").json()["settings"]
    current["schema_version"] = 77
    current["pages"] = 2
    response = client.put("/api/settings", json=current)
    assert response.status_code == 200
    assert response.json()["settings"]["schema_version"] == 1


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"pages": 11}, "pages"),
        ({"pages": "two"}, "pages"),
        ({"max_elements": 50}, "max_elements"),
        ({"timeout_s": 1000}, "timeout_s"),
        ({"delta_e": 0}, "delta_e"),
        ({"lang": "fr"}, "lang"),
        ({"browser": "firefox"}, "browser"),
        ({"dark": "maybe"}, "dark"),
        ({"output_dir": ""}, "output_dir"),
        ({"output_dir": None}, "output_dir"),
        ({"output_dir": 5}, "output_dir"),
        ({"colour": "red"}, "colour"),
    ],
)
def test_invalid_settings_are_refused_in_vietnamese(
    harness: Harness, client: httpx.Client, change: dict[str, Any], field: str
) -> None:
    response = client.put("/api/settings", json=change)
    assert response.status_code == 422
    error = error_of(response).error
    assert error.code == "invalid" and error.details == {"field": field}
    assert error.message and error.message.isascii() is False
    assert not harness.settings_path.exists()


def test_output_dir_is_expanded_and_made_absolute(
    harness: Harness, client: httpx.Client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    from get_design_markdown.ui import settings as ui_settings

    monkeypatch.setattr(ui_settings, "_home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for value, expected in (
        ("~/Designs", home / "Designs"),
        ("relative/out", home / "relative" / "out"),
    ):
        response = client.put("/api/settings", json={"output_dir": value})
        assert response.status_code == 200, response.text
        assert Path(response.json()["settings"]["output_dir"]) == expected
    assert not (home / "Designs").exists()  # created only when a job writes there


def test_output_dir_must_not_be_a_file(client: httpx.Client, tmp_path: Path) -> None:
    file = tmp_path / "a-file.txt"
    file.write_text("x", encoding="utf-8")
    response = client.put("/api/settings", json={"output_dir": str(file)})
    assert response.status_code == 422
    assert error_of(response).error.details == {"field": "output_dir"}
    assert str(file) in error_of(response).error.message


def test_locked_output_root_does_not_move(
    harness: Harness, server: UiServer, client: httpx.Client, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    response = SettingsResponse.model_validate_json(
        client.put("/api/settings", json={"output_dir": str(elsewhere)}).content
    )
    assert response.output_root_locked
    assert response.settings.output_dir == elsewhere
    assert response.output_root == str(harness.output_root.resolve())
    assert client.get("/api/health").json()["output_root"] == str(harness.output_root.resolve())


def test_unlocked_output_root_follows_the_settings(harness: Harness, tmp_path: Path) -> None:
    server = harness.make(out_root=None)
    with api_client(server) as client:
        first = SettingsResponse.model_validate_json(client.get("/api/settings").content)
        assert not first.output_root_locked
        assert first.output_root == str(harness.output_root)
        assert [entry["site"] for entry in client.get("/api/history").json()["entries"]] == [
            ACME_SITE,
            BETA_SITE,
        ]
        elsewhere = tmp_path / "elsewhere"
        changed = SettingsResponse.model_validate_json(
            client.put("/api/settings", json={"output_dir": str(elsewhere)}).content
        )
        assert changed.output_root == str(elsewhere)
        assert client.get("/api/history").json() == {"output_root": str(elsewhere), "entries": []}
        job = start_job(client, "https://stripe.com")
        wait_job(client, job.id)
        assert (elsewhere / "stripe.com" / "DESIGN.md").is_file()


def test_settings_save_failure_is_reported(harness: Harness, tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    server = harness.make(settings_path=blocker / "ui-settings.json")
    with api_client(server) as client:
        response = client.put("/api/settings", json={"pages": 2})
        assert response.status_code == 500
        error = error_of(response).error
        assert error.code == "internal"
        assert error.message.startswith("Không lưu được cấu hình giao diện")
        # Nothing changed in memory.
        assert client.get("/api/settings").json()["settings"]["pages"] == 0


def test_a_running_job_keeps_its_settings(harness: Harness, client: httpx.Client) -> None:
    job = start_job(client, "https://hold.example")
    assert harness.runner.started.wait(WAIT_S)
    assert client.put("/api/settings", json={"pages": 7, "lang": "vi"}).status_code == 200
    harness.runner.release.set()
    done = wait_job(client, job.id)
    assert (done.settings.pages, done.settings.lang) == (0, "en")
    _request, settings = harness.runner.calls[0]
    assert (settings.pages, settings.lang) == (0, "en")


def test_job_settings_combine_saved_settings_request_and_output_root(
    harness: Harness, client: httpx.Client
) -> None:
    client.put("/api/settings", json={"pages": 4, "lang": "vi", "dark": True, "browser": "msedge"})
    job = start_job(client, "stripe.com", pages=1, browser="chrome")
    done = wait_job(client, job.id)
    request, settings = harness.runner.calls[0]
    assert request.url == "stripe.com"
    assert (settings.pages, settings.lang, settings.dark, settings.browser) == (
        1,
        "vi",
        True,
        "chrome",
    )
    assert settings.output_dir == harness.output_root.resolve()
    assert done.settings == settings


# ─── Jobs over HTTP ──────────────────────────────────────────────────────────


def test_job_events_are_paged_with_since(client: httpx.Client) -> None:
    job = start_job(client, "https://stripe.com")
    done = wait_job(client, job.id)
    total = done.last_seq
    assert total >= 8
    page = JobResponse.model_validate_json(
        client.get(f"/api/jobs/{job.id}", params={"since": total - 2}).content
    ).job
    assert [event.seq for event in page.events] == [total - 1, total]
    empty = client.get(f"/api/jobs/{job.id}", params={"since": total + 50}).json()["job"]
    assert empty["events"] == [] and empty["last_seq"] == total
    negative = client.get(f"/api/jobs/{job.id}", params={"since": "-3"}).json()["job"]
    assert negative["events"][0]["seq"] == 1
    bad = client.get(f"/api/jobs/{job.id}", params={"since": "abc"})
    assert bad.status_code == 422 and error_of(bad).error.details == {"field": "since"}
    current = client.get("/api/jobs/current", params={"since": total - 1}).json()["job"]
    assert current["id"] == job.id and [event["seq"] for event in current["events"]] == [total]


def test_job_snapshot_json_shape(client: httpx.Client) -> None:
    job = start_job(client, "https://www.Stripe.com/")
    data = client.get(f"/api/jobs/{job.id}").json()["job"]
    assert data["site"] == "stripe.com" and data["url"] == "https://www.Stripe.com/"
    assert data["created_at"].endswith("Z")
    wait_job(client, job.id)
    data = client.get(f"/api/jobs/{job.id}").json()["job"]
    assert data["status"] == "succeeded" and data["finished_at"].endswith("Z")
    assert data["files"][0] == "DESIGN.md"
    assert all(event["at"].endswith("Z") for event in data["events"])


def test_unexpected_job_error_shows_a_vietnamese_message(
    client: httpx.Client, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=UI_LOGGER):
        job = start_job(client, "https://crash.example")
        done = wait_job(client, job.id)
    assert done.status == "failed" and done.error is not None
    assert done.error.code == "unexpected"
    assert done.error.message == "Lỗi không mong muốn: RuntimeError: boom"
    assert "Traceback" not in done.model_dump_json()
    assert "Traceback" in caplog.text


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/jobs/0123456789ab/cancel", "GET"),
        ("/api/jobs/0123456789ab", "POST"),
        ("/api/jobs", "GET"),
        ("/api/jobs/current", "POST"),
        ("/api/health", "POST"),
        ("/api/health", "HEAD"),
        ("/api/settings", "POST"),
        ("/api/history", "PUT"),
        ("/api/heartbeat", "GET"),
    ],
)
def test_wrong_method_on_an_api_path(server: UiServer, path: str, method: str) -> None:
    response = raw_request(
        server.port,
        method,
        path,
        [(TOKEN_HEADER, server.token), ("Content-Type", "application/json")],
        b"{}" if method in ("POST", "PUT") else None,
    )
    assert response.status == 405
    assert response.headers.get("allow")
    if method != "HEAD":
        assert response.code == "method_not_allowed"


@pytest.mark.parametrize(
    "path", ["/api/jobs/XYZ", "/api/jobs/0123456789AB", "/api/jobs/0123456789abc", "/api/x/y"]
)
def test_malformed_api_paths_are_not_found(server: UiServer, path: str) -> None:
    response = raw_request(server.port, "GET", path, [(TOKEN_HEADER, server.token)])
    assert response.status == 404


# ─── Results ─────────────────────────────────────────────────────────────────


def test_results_fallback_to_screenshot_files(
    client: httpx.Client, output_root: Path, ui_output_template: Path
) -> None:
    shots = output_root / BETA_SITE / "screenshots"
    shots.mkdir()
    for name in ("home-1440.png", "home-dark-1440.png", "pricing-390.png", "odd.png"):
        shutil.copyfile(
            ui_output_template / ACME_SITE / "screenshots" / "home-1440.png", shots / name
        )
    (shots / "notes.txt").write_text("x", encoding="utf-8")
    captured_before_the_results(output_root / BETA_SITE)
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{BETA_SITE}").content)
    assert [
        (shot.page, shot.viewport_width, shot.color_scheme) for shot in results.screenshots
    ] == [
        ("home", 1440, "light"),
        ("home", 1440, "dark"),
        ("odd", 1440, "light"),  # width read from the image itself
        ("pricing", 390, "light"),
    ]
    assert results.screenshots[0].url == f"/files/{BETA_SITE}/screenshots/home-1440.png"
    assert results.prompt is None and results.prompt_images == []
    assert results.warnings == ["Trang có lớp phủ cookie — đã ẩn trước khi đo."]
    assert results.entry.name == "Beta Studio"


def test_results_ignore_screenshot_paths_outside_the_folder(
    client: httpx.Client, output_root: Path
) -> None:
    analysis_path = output_root / ACME_SITE / "analysis.json"
    data = json.loads(analysis_path.read_text(encoding="utf-8"))
    data["screenshots"] = [
        *data["screenshots"],
        {
            "name": "x",
            "viewport_width": 1440,
            "viewport_height": 900,
            "path": "../beta.example/DESIGN.md",
        },
        {"name": "y", "viewport_width": 1440, "viewport_height": 900, "path": "C:/Windows/win.ini"},
        {
            "name": "z",
            "viewport_width": 1440,
            "viewport_height": 900,
            "path": "screenshots/gone.png",
        },
        "not an object",
    ]
    analysis_path.write_text(json.dumps(data), encoding="utf-8")
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    assert [shot.path for shot in results.screenshots] == [
        "screenshots/home-1440.png",
        "screenshots/home-768.png",
        "screenshots/home-390.png",
        "screenshots/home-dark-1440.png",
    ]


def test_results_survive_damaged_json_files(client: httpx.Client, output_root: Path) -> None:
    folder = output_root / ACME_SITE
    (folder / "analysis.json").write_text("{broken", encoding="utf-8")
    (folder / "synthesis.json").write_text('{"token_counts": "many"}', encoding="utf-8")
    written = (folder / "DESIGN.md").stat().st_mtime
    os.utime(folder / "analysis.json", (written, written))  # not a newer, unfinished run
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    assert results.entry.name == ACME_SITE
    assert results.token_counts is None and results.warnings == []
    assert len(results.screenshots) == 4  # from the screenshots folder
    assert results.design is not None


def test_results_urls_are_percent_encoded(client: httpx.Client, output_root: Path) -> None:
    folder = output_root / ACME_SITE
    (folder / "ghi chú #1 ü.txt").write_text("xin chào", encoding="utf-8")
    (folder / "screenshots" / "home (2)-1440.png").write_bytes(b"png")
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    files = {file.name: file for file in results.files}
    note = files["ghi chú #1 ü.txt"]
    assert note.url == f"/files/{ACME_SITE}/ghi%20ch%C3%BA%20%231%20%C3%BC.txt"
    assert note.kind == "other" and note.size == len("xin chào".encode())
    assert client.get(note.url).content.decode("utf-8") == "xin chào"
    shot = files["home (2)-1440.png"]
    assert shot.url == f"/files/{ACME_SITE}/screenshots/home%20%282%29-1440.png"
    assert shot.kind == "image" and client.get(shot.url).status_code == 200
    paths = [file.path for file in results.files]
    assert paths == sorted(paths, key=lambda path: (path.casefold(), path))


def test_results_list_only_the_site_folder_and_its_two_image_folders(
    client: httpx.Client, output_root: Path
) -> None:
    folder = output_root / ACME_SITE
    (folder / "nested" / "deeper").mkdir(parents=True)
    (folder / "nested" / "deeper" / "x.txt").write_text("x", encoding="utf-8")
    (folder / ".DS_Store").write_text("x", encoding="utf-8")
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    paths = [file.path for file in results.files]
    assert "nested/deeper/x.txt" not in paths and ".DS_Store" not in paths
    assert {file.kind for file in results.files if "/" not in file.path} >= {
        "design",
        "preview",
        "tokens",
        "prompt",
        "analysis",
        "synthesis",
        "raw",
    }


# ─── Static and result files ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "content_type", "attachment"),
    [
        ("DESIGN.md", "text/markdown; charset=utf-8", False),
        ("tokens.json", "application/json; charset=utf-8", False),
        ("screenshots/home-1440.png", "image/png", False),
        ("notes.txt", "text/plain; charset=utf-8", False),
        ("style.css", "text/css; charset=utf-8", False),
        ("photo.jpg", "image/jpeg", False),
        ("photo.jpeg", "image/jpeg", False),
        ("photo.webp", "image/webp", False),
        ("drawing.svg", "application/octet-stream", True),
        ("script.js", "application/octet-stream", True),
        ("archive.zip", "application/octet-stream", True),
        ("noext", "application/octet-stream", True),
    ],
)
def test_result_file_content_types(
    client: httpx.Client, output_root: Path, name: str, content_type: str, attachment: bool
) -> None:
    path = output_root / ACME_SITE / name
    if not path.exists():
        path.write_bytes(b"<svg onload=alert(1)>" if name.endswith(".svg") else b"data")
    response = client.get(f"/files/{ACME_SITE}/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert ("content-disposition" in response.headers) is attachment
    assert "content-security-policy" not in response.headers
    assert response.content == path.read_bytes()


def test_html_files_are_sandboxed(client: httpx.Client, output_root: Path) -> None:
    (output_root / ACME_SITE / "other.html").write_text("<p>x</p>", encoding="utf-8")
    for name in ("preview.html", "other.html"):
        response = client.get(f"/files/{ACME_SITE}/{name}")
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["content-security-policy"] == FILES_HTML_CSP


def test_download_and_head(server: UiServer, client: httpx.Client, output_root: Path) -> None:
    response = client.get(f"/files/{ACME_SITE}/DESIGN.md", params={"download": "1"})
    assert response.headers["content-disposition"] == 'attachment; filename="DESIGN.md"'
    plain = client.get(f"/files/{ACME_SITE}/DESIGN.md", params={"download": "0"})
    assert "content-disposition" not in plain.headers
    (output_root / ACME_SITE / "bản thiết kế.md").write_text("x", encoding="utf-8")
    named = client.get(f"/files/{ACME_SITE}/b%E1%BA%A3n%20thi%E1%BA%BFt%20k%E1%BA%BF.md?download=1")
    assert named.headers["content-disposition"] == (
        'attachment; filename="b_n thi_t k_.md"; '
        "filename*=UTF-8''b%E1%BA%A3n%20thi%E1%BA%BFt%20k%E1%BA%BF.md"
    )
    head = raw_request(server.port, "HEAD", f"/files/{ACME_SITE}/DESIGN.md")
    size = (output_root / ACME_SITE / "DESIGN.md").stat().st_size
    assert head.status == 200 and head.body == b""
    assert head.headers["content-length"] == str(size)
    assert raw_request(server.port, "HEAD", "/").status == 200


def test_large_files_are_streamed(
    client: httpx.Client, output_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "IN_MEMORY_FILE_BYTES", 1024)
    data = os.urandom(300_000)
    (output_root / ACME_SITE / "big.json").write_bytes(data)
    response = client.get(f"/files/{ACME_SITE}/big.json")
    assert response.status_code == 200 and response.content == data
    assert response.headers["content-length"] == str(len(data))


def test_index_and_static_files(harness: Harness, tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>UI</title>", encoding="utf-8")
    (static / "app.js").write_text("console.log(1)", encoding="utf-8")
    (static / "app.css").write_text("body{}", encoding="utf-8")
    (static / "icon.svg").write_text("<svg/>", encoding="utf-8")
    (static / "font.woff2").write_bytes(b"wOF2")
    (static / "data.bin").write_bytes(b"\x00")
    server = harness.make(static_dir=static)
    with api_client(server, token=False) as client:
        index = client.get("/")
        assert index.text == "<!doctype html><title>UI</title>"
        assert index.headers["content-security-policy"] == INDEX_CSP
        assert index.headers["content-type"] == "text/html; charset=utf-8"
        assert index.headers["cache-control"] == "no-cache"
        assert index.headers["referrer-policy"] == "no-referrer"
        assert index.headers["cross-origin-resource-policy"] == "same-origin"
        expected = {
            "app.js": "text/javascript; charset=utf-8",
            "app.css": "text/css; charset=utf-8",
            "icon.svg": "image/svg+xml",
            "font.woff2": "font/woff2",
            "index.html": "text/html; charset=utf-8",
            "data.bin": "application/octet-stream",
        }
        for name, content_type in expected.items():
            response = client.get(f"/static/{name}")
            assert response.status_code == 200 and response.headers["content-type"] == content_type
            assert "content-security-policy" not in response.headers
        assert client.get("/static/data.bin").headers["content-disposition"]


def test_packaged_frontend_is_served(client: httpx.Client) -> None:
    index = client.get("/")
    assert index.status_code == 200 and '<html lang="vi">' in index.text


# ─── Open folder, browsers ───────────────────────────────────────────────────


def test_open_folder(harness: Harness, client: httpx.Client, output_root: Path) -> None:
    response = client.post("/api/open-folder", json={"site": ACME_SITE})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "folder": str(output_root / ACME_SITE)}
    assert client.post("/api/open-folder", json={"site": None}).status_code == 200
    assert harness.opened == [output_root / ACME_SITE, output_root.resolve()]
    for site in ("../x", "missing.example", ".hidden", "C:\\Windows"):
        response = client.post("/api/open-folder", json={"site": site})
        assert response.status_code == 404 and error_of(response).error.code == "site_not_found"
    extra = client.post("/api/open-folder", json={"path": "C:/Windows"})
    assert extra.status_code == 422
    assert len(harness.opened) == 2


def test_open_folder_creates_the_output_root(harness: Harness, tmp_path: Path) -> None:
    root = tmp_path / "new" / "root"
    server = harness.make(out_root=root)
    with api_client(server) as client:
        assert client.post("/api/open-folder", json={}).status_code == 200
    assert root.is_dir() and harness.opened == [root.resolve()]


def test_open_folder_failure_is_501(harness: Harness, client: httpx.Client) -> None:
    harness.open_error = FileNotFoundError(2, "xdg-open not found")
    response = client.post("/api/open-folder", json={"site": ACME_SITE})
    assert response.status_code == 501
    error = error_of(response).error
    assert error.code == "unsupported" and error.hint and ACME_SITE in error.hint


def test_browser_status_and_install(harness: Harness, client: httpx.Client) -> None:
    status = BrowserStatusResponse.model_validate_json(client.get("/api/browser/status").content)
    assert [info.name for info in status.browsers] == ["chromium", "msedge", "chrome"]
    assert status.auto == "msedge" and status.install.state == "idle"
    harness.browsers = lambda: fake_browser_status(())
    status = BrowserStatusResponse.model_validate_json(client.get("/api/browser/status").content)
    assert status.auto is None

    started = client.post("/api/browser/install")
    assert started.status_code == 202
    assert InstallResponse.model_validate_json(started.content).install.state == "running"
    again = client.post("/api/browser/install", json={})
    assert again.status_code == 409
    error = error_of(again).error
    assert error.code == "install_running"
    assert error.details is not None and error.details["install"]["state"] == "running"
    assert harness.installer.starts == 1
    polled = InstallResponse.model_validate_json(client.get("/api/browser/install").content)
    assert polled.install.state == "running"
    harness.installer.finish()
    polled = InstallResponse.model_validate_json(client.get("/api/browser/install").content)
    assert polled.install.state == "succeeded" and polled.install.percent == 100
    assert client.post("/api/browser/install").status_code == 202  # a new one may start
    assert harness.installer.starts == 2


def test_browser_status_errors(
    harness: Harness, client: httpx.Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def not_built() -> BrowserStatus:
        raise NotImplementedYetError("Chưa kiểm tra được trình duyệt.")

    harness.browsers = not_built
    response = client.get("/api/browser/status")
    assert response.status_code == 501 and error_of(response).error.code == "unsupported"

    def broken() -> BrowserStatus:
        raise GdmError("Không đọc được danh sách trình duyệt.", hint="Thử lại.")

    harness.browsers = broken
    response = client.get("/api/browser/status")
    assert response.status_code == 500
    assert error_of(response).error.message == "Không đọc được danh sách trình duyệt."


def test_health_reports_the_running_job(harness: Harness, client: httpx.Client) -> None:
    job = start_job(client, "https://hold.example")
    health = HealthResponse.model_validate_json(client.get("/api/health").content)
    assert health.busy and health.job_id == job.id
    harness.runner.release.set()
    wait_job(client, job.id)
    health = HealthResponse.model_validate_json(client.get("/api/health").content)
    assert not health.busy and health.job_id is None


# ─── Helpers ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("segment", "safe"),
    [
        ("DESIGN.md", True),
        ("home-1440.png", True),
        ("ghi chú.txt", True),
        ("", False),
        (".", False),
        ("..", False),
        (".hidden", False),
        ("a/b", False),
        ("a\\b", False),
        ("C:", False),
        ("x\x00y", False),
    ],
)
def test_is_safe_segment(segment: str, safe: bool) -> None:
    assert server_module.is_safe_segment(segment) is safe


def test_safe_child(tmp_path: Path) -> None:
    base = tmp_path / "base"
    (base / "sub").mkdir(parents=True)
    (base / "sub" / "file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("x", encoding="utf-8")
    assert (
        server_module.safe_child(base, ["sub", "file.txt"]) == (base / "sub" / "file.txt").resolve()
    )
    assert server_module.safe_child(base, ["sub"]) is None  # a folder
    assert server_module.safe_child(base, []) is None
    assert server_module.safe_child(base, ["..", "outside.txt"]) is None
    assert server_module.safe_child(base, ["missing.txt"]) is None
    assert server_module.safe_child(tmp_path / "missing-base", ["x"]) is None


def test_files_url_and_attachment_header() -> None:
    assert server_module.files_url("stripe.com", "screenshots/home 1.png") == (
        "/files/stripe.com/screenshots/home%201.png"
    )
    assert server_module.files_url("a_b-c.d", "x#?.md") == "/files/a_b-c.d/x%23%3F.md"
    assert server_module.attachment_header("DESIGN.md") == 'attachment; filename="DESIGN.md"'
    header = server_module.attachment_header('evil"\r\nSet-Cookie: x.md')
    assert "\r" not in header and "\n" not in header
    assert header.startswith('attachment; filename="evil___Set-Cookie_ x.md"')


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_open_folder_uses_the_system_file_manager(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str
) -> None:
    started: list[Any] = []
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(
        os, "startfile", lambda path: started.append(("startfile", path)), raising=False
    )

    def fake_popen(command: list[str], **kwargs: Any) -> None:
        started.append(("popen", command, kwargs.get("start_new_session")))

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    # The real function (the harness fixture replaces it in the other tests).
    server_module.open_folder(tmp_path)
    expected = {
        "win32": [("startfile", str(tmp_path))],
        "darwin": [("popen", ["open", str(tmp_path)], True)],
        "linux": [("popen", ["xdg-open", str(tmp_path)], True)],
    }
    assert started == expected[platform]


def test_port_error_messages() -> None:
    error = server_module._port_error(8765, OSError(errno.EADDRINUSE, "in use"))
    assert error.message == "Cổng 8765 đang được chương trình khác dùng."
    other = server_module._port_error(0, OSError(errno.EINVAL, "Invalid argument"))
    assert other.message == "Không mở được máy chủ giao diện ở 127.0.0.1:0 (Invalid argument)."
    assert other.hint


# ─── Saved settings follow the environment unless the user changed them ──────

#: Fields the frontend's "Đặt lại" button sends with their default values.
RESETTABLE = (
    "pages",
    "dark",
    "lang",
    "prompt_only",
    "examples",
    "max_elements",
    "timeout_s",
    "delta_e",
    "browser",
    "open_after",
)
ENV_DEFAULTS = ("GDM_OUTPUT_DIR", "GDM_BROWSER", "GDM_TIMEOUT", "GDM_MAX_ELEMENTS")


def saved_file(harness: Harness) -> dict[str, Any]:
    data = json.loads(harness.settings_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_only_the_changed_settings_are_saved(harness: Harness, client: httpx.Client) -> None:
    assert client.put("/api/settings", json={"dark": True}).status_code == 200
    # Not output_dir (this session is locked to --out), nor any other default.
    assert saved_file(harness) == {"schema_version": 1, "dark": True}
    assert client.put("/api/settings", json={"pages": 3}).status_code == 200
    assert saved_file(harness) == {"schema_version": 1, "pages": 3, "dark": True}


def test_defaults_follow_the_environment_of_each_session(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ENV_DEFAULTS:
        monkeypatch.delenv(name, raising=False)
    first_root = (tmp_path / "project-a" / "output").resolve()
    monkeypatch.setenv("GDM_OUTPUT_DIR", str(first_root))
    first = harness.make(out_root=None, defaults=None)
    with api_client(first) as client:
        view = SettingsResponse.model_validate_json(client.get("/api/settings").content)
        assert view.output_root == str(first_root)
        assert client.put("/api/settings", json={"dark": True}).status_code == 200
    first.stop()
    assert saved_file(harness) == {"schema_version": 1, "dark": True}

    second_root = (tmp_path / "project-b" / "output").resolve()
    monkeypatch.setenv("GDM_OUTPUT_DIR", str(second_root))
    monkeypatch.setenv("GDM_BROWSER", "msedge")
    monkeypatch.setenv("GDM_TIMEOUT", "90")
    monkeypatch.setenv("GDM_MAX_ELEMENTS", "5000")
    second = harness.make(out_root=None, defaults=None)
    with api_client(second) as client:
        view = SettingsResponse.model_validate_json(client.get("/api/settings").content)
        job = start_job(client, "stripe.com")
        wait_job(client, job.id)
    assert view.settings == view.defaults.model_copy(update={"dark": True})
    assert view.settings.output_dir == second_root and view.output_root == str(second_root)
    assert (view.settings.browser, view.settings.timeout_s, view.settings.max_elements) == (
        "msedge",
        90.0,
        5000,
    )
    _request, settings = harness.runner.calls[-1]
    assert (settings.output_dir, settings.browser, settings.dark) == (second_root, "msedge", True)


def test_a_chosen_output_folder_is_kept_across_sessions(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GDM_OUTPUT_DIR", str(tmp_path / "env-output"))
    chosen = (tmp_path / "Designs").resolve()
    first = harness.make(out_root=None, defaults=None)
    with api_client(first) as client:
        assert client.put("/api/settings", json={"output_dir": str(chosen)}).status_code == 200
    assert saved_file(harness)["output_dir"] == str(chosen)
    monkeypatch.setenv("GDM_OUTPUT_DIR", str(tmp_path / "other-output"))
    second = harness.make(out_root=None, defaults=None)
    with api_client(second) as client:
        assert client.get("/api/settings").json()["output_root"] == str(chosen)


def test_resetting_to_the_defaults_empties_the_saved_settings(
    harness: Harness, client: httpx.Client
) -> None:
    client.put("/api/settings", json={"pages": 3, "browser": "chrome", "timeout_s": 120})
    view = SettingsResponse.model_validate_json(client.get("/api/settings").content)
    assert view.settings.pages == 3
    defaults = view.defaults.model_dump(mode="json")
    response = client.put("/api/settings", json={name: defaults[name] for name in RESETTABLE})
    assert response.status_code == 200
    assert SettingsResponse.model_validate_json(response.content).settings == view.defaults
    assert saved_file(harness) == {"schema_version": 1}


def test_a_refused_change_writes_nothing(harness: Harness, client: httpx.Client) -> None:
    client.put("/api/settings", json={"dark": True})
    before = harness.settings_path.read_text(encoding="utf-8")
    assert client.put("/api/settings", json={"dark": False, "pages": 99}).status_code == 422
    assert harness.settings_path.read_text(encoding="utf-8") == before
    assert client.get("/api/settings").json()["settings"]["dark"] is True


# ─── Unusable output folders ─────────────────────────────────────────────────


def missing_drive() -> str:
    """A Windows drive root that does not exist on this machine (skips elsewhere)."""
    if sys.platform != "win32":
        pytest.skip("drive letters exist only on Windows")
    for letter in "QZYXWVUTSRPONMLKJIHG":
        root = f"{letter}:\\"
        if not Path(root).exists():
            return root
    pytest.skip("every drive letter is in use")


def assert_output_dir_refused(response: httpx.Response, *fragments: str) -> None:
    assert response.status_code == 422, response.text
    error = error_of(response).error
    assert error.code == "invalid" and error.details == {"field": "output_dir"}
    for fragment in fragments:
        assert fragment in error.message
    assert error.hint == server_module.OUTPUT_DIR_HINT


def test_output_dir_on_a_missing_drive_is_refused(harness: Harness, client: httpx.Client) -> None:
    drive = missing_drive()
    response = client.put("/api/settings", json={"output_dir": f"{drive}khong-co-o-dia\\out"})
    assert_output_dir_refused(response, drive, "Không tìm thấy ổ đĩa")
    assert not harness.settings_path.exists()


def test_output_dir_inside_a_file_is_refused(client: httpx.Client, tmp_path: Path) -> None:
    file = tmp_path / "a-file.txt"
    file.write_text("x", encoding="utf-8")
    response = client.put("/api/settings", json={"output_dir": str(file / "sub" / "out")})
    assert_output_dir_refused(response, str(file), "là một tệp")


def test_output_dir_where_no_folder_can_be_created_is_refused(
    client: httpx.Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> str:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(tempfile, "mkdtemp", refuse)
    response = client.put("/api/settings", json={"output_dir": str(tmp_path / "new" / "out")})
    assert_output_dir_refused(response, "Không có quyền tạo thư mục", str(tmp_path))


def test_checking_an_output_dir_leaves_no_trace(client: httpx.Client, tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    response = client.put("/api/settings", json={"output_dir": str(parent / "new" / "out")})
    assert response.status_code == 200
    assert os.listdir(parent) == []  # the test folder is gone, "new" is created by a job


def test_out_root_that_is_a_file_is_refused_at_start(harness: Harness, tmp_path: Path) -> None:
    file = tmp_path / "afile.txt"
    file.write_text("x", encoding="utf-8")
    server = UiServer(
        mode="none",
        out_root=file,
        settings_path=harness.settings_path,
        installer=harness.installer,
        defaults=UiSettings(output_dir=harness.output_root),
    )
    with pytest.raises(GdmError) as caught:
        server.start()
    assert str(file.resolve()) in caught.value.message
    assert caught.value.hint and "--out" in caught.value.hint
    with pytest.raises(RuntimeError):
        _ = server.port  # nothing was bound


def test_out_root_on_a_missing_drive_is_refused_at_start(harness: Harness) -> None:
    drive = missing_drive()
    server = UiServer(
        mode="none",
        out_root=Path(f"{drive}out"),
        settings_path=harness.settings_path,
        installer=harness.installer,
        defaults=UiSettings(output_dir=harness.output_root),
    )
    with pytest.raises(GdmError) as caught:
        server.start()
    assert drive in caught.value.message


# ─── Results of the latest run only ──────────────────────────────────────────


def date_inputs_before_the_results(folder: Path, seconds: float = 60.0) -> None:
    """Screenshots, raw.json and analysis.json dated before DESIGN.md (a complete run)."""
    reference = (folder / "DESIGN.md").stat().st_mtime - seconds
    paths = [folder / "raw.json", folder / "analysis.json"]
    paths.extend((folder / "screenshots").glob("*.png"))
    for path in paths:
        if path.exists():
            os.utime(path, (reference, reference))


def _quiet(event: Any) -> None:
    """Message sink that drops the synthesizer's messages."""


def test_prompt_of_an_earlier_run_is_not_offered(client: httpx.Client, output_root: Path) -> None:
    folder = output_root / ACME_SITE
    first = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    assert first.prompt is not None and first.entry.has_prompt
    assert first.prompt_images  # the fixture was built with --prompt-only

    with use_sink(_quiet):
        run_synthesize(folder, lang="vi", prompt_only=False)
    assert (folder / "prompt.md").is_file()  # still on disk, from the first run
    second = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    assert second.prompt is None and second.prompt_images == []
    assert not second.entry.has_prompt
    assert "prompt.md" in [file.path for file in second.files]  # the Files tab lists it all
    assert not [
        entry
        for entry in client.get("/api/history").json()["entries"]
        if entry["site"] == ACME_SITE and entry["has_prompt"]
    ]

    with use_sink(_quiet):
        run_synthesize(folder, lang="en", prompt_only=True)
    third = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    assert third.prompt is not None and third.entry.has_prompt
    synthesis = json.loads((folder / "synthesis.json").read_text(encoding="utf-8"))
    assert third.prompt_images == [
        f"/files/{ACME_SITE}/{path}" for path in synthesis["image_paths"]
    ]


def test_prompt_without_a_synthesis_record_is_offered_as_found(
    client: httpx.Client, output_root: Path
) -> None:
    (output_root / ACME_SITE / "synthesis.json").unlink()
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    assert results.prompt is not None and results.prompt_images


@pytest.mark.parametrize("prompt_only", [False, True])
def test_job_files_list_prompt_md_only_when_the_job_wrote_it(
    client: httpx.Client, output_root: Path, prompt_only: bool
) -> None:
    assert (output_root / ACME_SITE / "prompt.md").is_file()
    job = start_job(client, f"https://{ACME_SITE}", prompt_only=prompt_only)
    done = wait_job(client, job.id)
    assert done.status == "succeeded"
    assert ("prompt.md" in done.files) is prompt_only


def test_results_show_the_screenshots_of_every_analysed_page(
    client: httpx.Client, output_root: Path
) -> None:
    folder = output_root / ACME_SITE
    analysis_path = folder / "analysis.json"
    data = json.loads(analysis_path.read_text(encoding="utf-8"))
    data["site"]["pages_analyzed"] = ["home", "pricing", "home-dark"]
    analysis_path.write_text(json.dumps(data), encoding="utf-8")
    shots = folder / "screenshots"
    for name in ("pricing-1440.png", "pricing-768.png", "pricing-390.png", "blog-1440.png"):
        shutil.copyfile(shots / "home-1440.png", shots / name)
    shutil.copyfile(shots / "home-1440.png", shots / "home-dark-768.png")  # page "home-dark"
    date_inputs_before_the_results(folder)
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    assert [
        (shot.page, shot.viewport_width, shot.color_scheme) for shot in results.screenshots
    ] == [
        ("home", 1440, "light"),
        ("home", 768, "light"),
        ("home", 390, "light"),
        ("home", 1440, "dark"),
        ("pricing", 1440, "light"),
        ("pricing", 390, "light"),
        ("pricing", 768, "light"),
        ("home-dark", 768, "light"),
    ]
    # "blog" was not analysed this time (a screenshot of an older run): not shown.
    assert "screenshots/blog-1440.png" not in [shot.path for shot in results.screenshots]
    assert results.screenshots[4].url == f"/files/{ACME_SITE}/screenshots/pricing-1440.png"
    assert results.warnings == data["warnings"]


def test_results_of_a_complete_run_carry_no_mixed_runs_note(
    client: httpx.Client, output_root: Path
) -> None:
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    assert server_module.mixed_runs_note(output_root / ACME_SITE) is None
    assert not [warning for warning in results.warnings if "chưa đi hết" in warning]


def test_results_say_when_an_unfinished_run_left_newer_screenshots(
    client: httpx.Client, output_root: Path
) -> None:
    folder = output_root / ACME_SITE
    analysis_warnings = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))[
        "warnings"
    ]
    later = (folder / "DESIGN.md").stat().st_mtime + 30
    os.utime(folder / "screenshots" / "home-1440.png", (later, later))
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    note = results.warnings[0]
    assert "chưa đi hết các bước" in note and "ảnh chụp mới hơn" in note
    assert "DESIGN.md" in note and "preview.html" in note and "tokens.json" in note
    assert "raw.json" not in note
    assert results.warnings[1:] == analysis_warnings


def _refuse_robots(folder: Path) -> None:
    """Rewrite raw.json as a capture that robots.txt disallowed, keeping its mtime."""
    raw_path = folder / "raw.json"
    stat = raw_path.stat()
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    data["robots"] = {
        "robots_url": "http://127.0.0.1/robots.txt",
        "allowed": False,
        "status_code": 200,
        "note": "robots.txt không cho phép bot.",
    }
    raw_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.utime(raw_path, (stat.st_atime, stat.st_mtime))


def test_results_keep_a_robots_refusal_after_the_job(
    client: httpx.Client, output_root: Path
) -> None:
    folder = output_root / ACME_SITE
    analysis_warnings = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))[
        "warnings"
    ]
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    assert results.warnings == analysis_warnings  # allowed: no note
    _refuse_robots(folder)
    results = ResultsResponse.model_validate_json(client.get(f"/api/results/{ACME_SITE}").content)
    note = results.warnings[0]
    assert note.startswith(server_module.ROBOTS_REFUSED_PREFIX)
    assert "http://127.0.0.1/robots.txt" in note and "tham khảo" in note
    assert results.warnings[1:] == analysis_warnings


def test_recorded_robots_reads_only_the_head_of_raw_json(tmp_path: Path) -> None:
    assert server_module.recorded_robots(tmp_path) is None  # no raw.json
    raw = tmp_path / "raw.json"
    raw.write_text('{"schema_version": 1, "robots": {"robots_url": "u", "allowed": null}}')
    robots = server_module.recorded_robots(tmp_path)
    assert robots is not None and robots.allowed is None
    assert server_module.robots_refused_note(tmp_path) is None  # unknown is not a refusal
    raw.write_text('{"robots": {"robots_url": "u", "allowed": false}, "pages": [' + "1," * 9)
    assert server_module.robots_refused_note(tmp_path) is not None  # truncated tail is fine
    raw.write_text('{"robots": {"allowed": "maybe"')
    assert server_module.recorded_robots(tmp_path) is None
    raw.write_text(" " * (70 * 1024) + '{"robots": {"robots_url": "u", "allowed": false}}')
    assert server_module.recorded_robots(tmp_path) is None  # beyond the head: unknown


def test_mixed_runs_note(tmp_path: Path) -> None:
    folder = tmp_path / "site"
    (folder / "screenshots").mkdir(parents=True)
    names = ("screenshots/home-1440.png", "raw.json", "analysis.json")
    outputs = ("DESIGN.md", "preview.html", "tokens.json")
    for name in (*names, *outputs):
        (folder / name).write_text("x", encoding="utf-8")

    def date(name: str, when: float) -> None:
        os.utime(folder / name, (when, when))

    base = 1_800_000_000.0
    for index, name in enumerate((*names, *outputs)):
        date(name, base + index)  # the order a complete run writes them
    assert server_module.mixed_runs_note(folder) is None
    # Re-running only the export or the synthesis is fine.
    date("preview.html", base + 500)
    assert server_module.mixed_runs_note(folder) is None
    # Within the tolerance of coarse file systems.
    date("analysis.json", base + 3 + server_module.STALE_TOLERANCE_S)
    assert server_module.mixed_runs_note(folder) is None
    # analysis.json rewritten after the results (the synthesis did not finish).
    date("analysis.json", base + 100)
    note = server_module.mixed_runs_note(folder)
    assert note is not None
    assert "kết quả phân tích (analysis.json) mới hơn DESIGN.md và tokens.json" in note
    assert "ảnh chụp" not in note.split("mới hơn")[0]
    # Without results there is nothing to compare.
    for name in outputs:
        (folder / name).unlink()
    assert server_module.mixed_runs_note(folder) is None
