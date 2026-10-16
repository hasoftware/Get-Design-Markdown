"""Browser availability and the Chromium download of the local UI (no real download)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from get_design_markdown.ui import app as ui_app
from get_design_markdown.ui import browser_install
from get_design_markdown.ui.browser_install import (
    AUTO_ORDER,
    BROWSER_LABELS,
    CHROMIUM_DISK_SIZE,
    CHROMIUM_DOWNLOAD_SIZE,
    CHROMIUM_MISSING,
    CHROMIUM_UNKNOWN,
    INSTALL_RETRY_HINT,
    INSTALL_STARTING,
    INSTALL_SUCCEEDED,
    LOG_TAIL_LINES,
    ONLY_SHELL_OPTION,
    BrowserInstaller,
    BrowserStatus,
    InstallStatus,
    browser_status,
    channel_candidates,
    download_title,
    failure_hint,
    install_command,
    parse_progress,
    run_install_cli,
)

ROOT = Path(__file__).resolve().parents[1]
REVISION = "1248"
CHROMIUM_VERSION = "156.0.8078.4"
WIN_SHELL = ("chrome-headless-shell-win64", "chrome-headless-shell.exe")


# ─── Browser status on a fake disk ───────────────────────────────────────────


class FakeMachine:
    """Environment variables and folders of a simulated machine under tmp_path."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch, system: str) -> None:
        self.root = root
        self.monkeypatch = monkeypatch
        self.local = root / "AppData" / "Local"
        self.program_files = root / "Program Files"
        self.program_files_x86 = root / "Program Files (x86)"
        browsers_json = root / "driver" / "package" / "browsers.json"
        browsers_json.parent.mkdir(parents=True)
        browsers_json.write_text(
            json.dumps(
                {
                    "browsers": [
                        {"name": "chromium", "revision": REVISION, "browserVersion": "x"},
                        {
                            "name": "chromium-headless-shell",
                            "revision": REVISION,
                            "browserVersion": CHROMIUM_VERSION,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.browsers_json = browsers_json
        monkeypatch.setattr(browser_install, "_platform", lambda: system)
        monkeypatch.setattr(browser_install, "_machine", lambda: "amd64")
        monkeypatch.setattr(browser_install, "_home", lambda: root / "home")
        monkeypatch.setattr(browser_install, "browsers_json_path", lambda: browsers_json)
        monkeypatch.setattr(browser_install, "_windows_file_version", lambda path: None)
        monkeypatch.setattr(browser_install, "is_frozen", lambda: False)
        monkeypatch.delenv(browser_install.BROWSERS_PATH_ENV, raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(self.local))
        monkeypatch.setenv("PROGRAMFILES", str(self.program_files))
        monkeypatch.setenv("PROGRAMFILES(X86)", str(self.program_files_x86))
        monkeypatch.delenv("HOMEDRIVE", raising=False)

    @staticmethod
    def touch(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return path

    def add_chromium(self, browsers_dir: Path | None = None) -> Path:
        base = browsers_dir or self.local / "ms-playwright"
        return self.touch(
            base / f"chromium_headless_shell-{REVISION}" / WIN_SHELL[0] / WIN_SHELL[1]
        )

    def add_edge(self, version: str | None = "141.0.3537.71") -> Path:
        exe = self.touch(
            self.program_files_x86 / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        )
        if version:
            (exe.parent / version).mkdir()
            (exe.parent / "140.0.1.1").mkdir()
            (exe.parent / "SetupMetrics").mkdir()
        return exe

    def add_chrome(self) -> Path:
        return self.touch(self.local / "Google" / "Chrome" / "Application" / "chrome.exe")


@pytest.fixture
def windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeMachine:
    return FakeMachine(tmp_path, monkeypatch, "win32")


def _no_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("browser_status() must not start a process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


def test_status_lists_the_three_browsers_in_auto_order(
    windows: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_processes(monkeypatch)
    status = browser_status()
    assert [info.name for info in status.browsers] == list(AUTO_ORDER)
    assert [info.label for info in status.browsers] == [BROWSER_LABELS[n] for n in AUTO_ORDER]
    assert status.auto is None
    assert all(not info.available and info.path is None for info in status.browsers)
    assert [info.detail for info in status.browsers] == [
        CHROMIUM_MISSING,
        "Chưa cài Microsoft Edge.",
        "Chưa cài Google Chrome.",
    ]
    BrowserStatus.model_validate_json(status.model_dump_json())


def test_status_finds_edge_and_reads_its_version_folder(windows: FakeMachine) -> None:
    edge = windows.add_edge()
    status = browser_status()
    assert status.auto == "msedge"
    info = status.browsers[1]
    assert info.available and info.path == str(edge) and info.version == "141.0.3537.71"
    assert info.detail is None


def test_status_prefers_chromium_then_edge_then_chrome(windows: FakeMachine) -> None:
    windows.add_chrome()
    assert browser_status().auto == "chrome"
    windows.add_edge(version=None)
    status = browser_status()
    assert status.auto == "msedge" and status.browsers[1].version is None
    shell = windows.add_chromium()
    status = browser_status()
    assert status.auto == "chromium"
    assert status.browsers[0].path == str(shell)
    assert status.browsers[0].version == CHROMIUM_VERSION
    assert all(info.available for info in status.browsers)


def test_status_follows_playwright_browsers_path(
    windows: FakeMachine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    custom = tmp_path / "custom-browsers"
    windows.add_chromium(custom)
    assert browser_status().auto is None  # not in the default folder
    monkeypatch.setenv(browser_install.BROWSERS_PATH_ENV, str(custom))
    assert browser_status().auto == "chromium"
    # "0": browsers inside the playwright package (next to the driver's browsers.json).
    monkeypatch.setenv(browser_install.BROWSERS_PATH_ENV, "0")
    assert browser_install.playwright_browsers_dir() == windows.browsers_json.parent / (
        ".local-browsers"
    )
    assert browser_status().auto is None
    windows.add_chromium(windows.browsers_json.parent / ".local-browsers")
    assert browser_status().auto == "chromium"


def test_status_without_a_readable_browsers_json(windows: FakeMachine) -> None:
    windows.add_chromium()
    windows.browsers_json.write_text("{not json", encoding="utf-8")
    info = browser_status().browsers[0]
    assert not info.available and info.detail == CHROMIUM_UNKNOWN


def test_status_survives_a_failing_check(
    windows: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    windows.add_chrome()

    def broken(name: str) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(browser_install, "channel_info", broken)
    status = browser_status()
    assert [info.available for info in status.browsers] == [False, False, False]
    assert status.browsers[2].detail == "Không kiểm tra được trình duyệt này."


def test_windows_channel_locations_follow_playwright(
    windows: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOMEDRIVE", "Z:")
    candidates = channel_candidates("msedge")
    assert candidates[:3] == [
        windows.local / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        windows.program_files / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        windows.program_files_x86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    assert len(candidates) == 5
    assert all(str(path).startswith("Z:") for path in candidates[3:])
    assert {path.parts[-4:] for path in candidates} == {
        ("Microsoft", "Edge", "Application", "msedge.exe")
    }
    # Duplicate prefixes are tried once.
    monkeypatch.setenv("PROGRAMFILES(X86)", str(windows.program_files))
    assert len(channel_candidates("chrome")) == 4


def test_macos_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    machine = FakeMachine(tmp_path, monkeypatch, "darwin")
    monkeypatch.setattr(browser_install, "_machine", lambda: "arm64")
    app = tmp_path / "Applications" / "Google Chrome.app"
    monkeypatch.setitem(browser_install._MAC_APP, "chrome", str(app))
    monkeypatch.setitem(browser_install._MAC_APP, "msedge", str(tmp_path / "none.app"))
    machine.touch(app / "Contents" / "MacOS" / "Google Chrome")
    (app / "Contents" / "Info.plist").write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
        b"<key>CFBundleShortVersionString</key><string>141.0.7390.55</string></dict></plist>"
    )
    shell = machine.touch(
        tmp_path
        / "home"
        / "Library"
        / "Caches"
        / "ms-playwright"
        / f"chromium_headless_shell-{REVISION}"
        / "chrome-headless-shell-mac-arm64"
        / "chrome-headless-shell"
    )
    status = browser_status()
    assert status.auto == "chromium" and status.browsers[0].path == str(shell)
    assert status.browsers[2].available and status.browsers[2].version == "141.0.7390.55"
    assert not status.browsers[1].available


def test_linux_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    machine = FakeMachine(tmp_path, monkeypatch, "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    edge = tmp_path / "opt" / "microsoft" / "msedge" / "msedge"
    monkeypatch.setitem(browser_install._LINUX_PATH, "msedge", str(edge))
    monkeypatch.setitem(browser_install._LINUX_PATH, "chrome", str(tmp_path / "no-chrome"))
    machine.touch(edge)
    status = browser_status()
    assert status.auto == "msedge" and status.browsers[1].version is None
    assert not status.browsers[2].available
    machine.touch(
        tmp_path
        / "cache"
        / "ms-playwright"
        / f"chromium_headless_shell-{REVISION}"
        / "chrome-headless-shell-linux64"
        / "chrome-headless-shell"
    )
    assert browser_status().auto == "chromium"


def test_real_status_is_fast_and_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_processes(monkeypatch)
    browser_status()  # warm up (imports)
    started = time.perf_counter()
    status = browser_status()
    assert time.perf_counter() - started < 0.5
    assert [info.name for info in status.browsers] == list(AUTO_ORDER)
    if status.auto is not None:
        assert next(info for info in status.browsers if info.name == status.auto).available


def test_frozen_exe_uses_the_per_user_browsers_folder(
    windows: FakeMachine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Let monkeypatch record the variable, so the value ensure_user_browsers_path() writes
    # straight into os.environ is removed after the test (later browser tests rely on it).
    monkeypatch.setenv(browser_install.BROWSERS_PATH_ENV, "")
    monkeypatch.delenv(browser_install.BROWSERS_PATH_ENV)
    browser_install.ensure_user_browsers_path()
    assert browser_install.BROWSERS_PATH_ENV not in os.environ  # not frozen: unchanged
    monkeypatch.setattr(browser_install, "is_frozen", lambda: True)
    browser_install.ensure_user_browsers_path()
    assert os.environ[browser_install.BROWSERS_PATH_ENV] == str(windows.local / "ms-playwright")
    # An explicit setting is kept.
    monkeypatch.setenv(browser_install.BROWSERS_PATH_ENV, "D:\\browsers")
    browser_install.ensure_user_browsers_path()
    assert os.environ[browser_install.BROWSERS_PATH_ENV] == "D:\\browsers"


def test_real_driver_files_exist() -> None:
    node, cli = browser_install.driver_executable()
    assert node.is_file() and cli.is_file()
    data = json.loads(browser_install.browsers_json_path().read_text(encoding="utf-8"))
    assert any(entry["name"] == "chromium-headless-shell" for entry in data["browsers"])


# ─── Install command ─────────────────────────────────────────────────────────


def test_install_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_install, "is_frozen", lambda: False)
    # Only the headless shell: the collector never starts the full Chromium (about 125 MB to
    # download instead of about 330 MB).
    assert install_command() == [
        sys.executable,
        "-m",
        "playwright",
        "install",
        "--only-shell",
        "chromium",
    ]
    assert ONLY_SHELL_OPTION == "--only-shell"
    monkeypatch.setattr(browser_install, "is_frozen", lambda: True)
    assert install_command() == [sys.executable, ui_app.INSTALL_BROWSER_COMMAND]
    assert ui_app.INSTALL_BROWSER_COMMAND == "install-browser"


def _script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_run_install_cli_runs_the_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    fake_cli = _script(
        tmp_path,
        "cli.py",
        """
        import os, sys
        print("ARGS", sys.argv[1:])
        print("PW", os.environ.get("PW_LANG_NAME"))
        sys.exit(int(os.environ.get("FAKE_CODE", "0")))
        """,
    )
    monkeypatch.setattr(
        browser_install, "driver_executable", lambda: (Path(sys.executable), fake_cli)
    )
    assert run_install_cli(["--dry-run"]) == 0
    out = capfd.readouterr().out
    assert "Đang tải Chromium cho Playwright" in out
    assert "ARGS ['install', '--only-shell', '--dry-run', 'chromium']" in out
    assert "PW python" in out  # the driver environment of playwright
    monkeypatch.setenv("FAKE_CODE", "7")
    assert run_install_cli() == 7


def test_run_install_cli_without_standard_handles_writes_to_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A windowed exe started without redirection has no stdout/stderr handles: the driver
    gets ui.log (sys.stdout there) instead of inheriting nothing and failing silently."""
    fake_cli = _script(
        tmp_path,
        "cli.py",
        """
        import sys
        print("DRIVER OUT")
        print("DRIVER ERR", file=sys.stderr)
        sys.exit(3)
        """,
    )
    monkeypatch.setattr(
        browser_install, "driver_executable", lambda: (Path(sys.executable), fake_cli)
    )
    monkeypatch.setattr(browser_install, "has_std_handles", lambda: False)
    log_path = tmp_path / "ui.log"
    with log_path.open("a", encoding="utf-8") as log:
        monkeypatch.setattr(sys, "stdout", log)
        monkeypatch.setattr(sys, "stderr", log)
        assert run_install_cli() == 3
    text = log_path.read_text(encoding="utf-8")
    assert "Đang tải Chromium cho Playwright" in text
    assert "DRIVER OUT" in text and "DRIVER ERR" in text
    assert "kết thúc với mã lỗi 3" in text


def test_has_std_handles_in_a_normal_process() -> None:
    assert browser_install.has_std_handles() is True


def test_run_install_cli_without_a_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        browser_install, "driver_executable", lambda: (tmp_path / "node.exe", tmp_path / "cli.js")
    )
    assert run_install_cli() == 1
    assert "thiếu trình điều khiển Playwright" in capsys.readouterr().err


def test_real_install_cli_dry_run(capfd: pytest.CaptureFixture[str]) -> None:
    """The real driver with --dry-run: prints what it would download, downloads nothing."""
    assert run_install_cli(["--dry-run"]) == 0
    out = capfd.readouterr().out
    assert "chromium" in out.lower() and REVISION in out
    # The headless shell (what chromium_info() looks for) and not the full Chromium build.
    assert f"(playwright chromium-headless-shell v{REVISION})" in out
    assert "(playwright chromium v" not in out
    assert f"chromium-{REVISION}" not in out.replace("chromium-headless-shell", "")


def test_the_headless_shell_folder_is_what_status_looks_for() -> None:
    """`--only-shell` installs chromium_headless_shell-<revision>, the folder chromium_info()
    checks: nothing else is needed for chromium to count as available."""
    data = json.loads(browser_install.browsers_json_path().read_text(encoding="utf-8"))
    names = {entry["name"] for entry in data["browsers"]}
    assert browser_install.HEADLESS_SHELL in names


def test_chromium_size_is_the_same_everywhere_it_is_quoted() -> None:
    """One measured figure: about 125 MB downloaded, about 300 MB on disk."""
    assert CHROMIUM_DOWNLOAD_SIZE == "khoảng 125 MB"
    assert CHROMIUM_DISK_SIZE == "khoảng 300 MB"
    assert CHROMIUM_DISK_SIZE in failure_hint(["Error: ENOSPC: no space left on device"])
    readme = (ROOT / "packaging" / "README-windows.txt").read_text(encoding="utf-8")
    assert f"tải về {CHROMIUM_DOWNLOAD_SIZE}" in readme
    assert f"{CHROMIUM_DISK_SIZE} trên ổ đĩa" in readme
    assert "150 MB" not in readme


# ─── Progress parsing ────────────────────────────────────────────────────────


def _bar(rows: int) -> str:
    """A progress bar as Playwright prints it without a terminal (10 rows of 8 squares)."""
    return "|" + "■" * (rows * 8) + " " * ((10 - rows) * 8) + "|"


@pytest.mark.parametrize(
    ("line", "percent"),
    [
        (f"{_bar(4)}  40% of 150.2 MiB", 40.0),
        (f"{_bar(0)}   0% of 98.4 MiB", 0.0),
        (f"{_bar(10)} 100% of 98.4 MiB", 100.0),
        ("150.2 MiB [====================] 12.5% 10.0s", 12.5),
        ("Downloading Chrome for Testing 156.0.8078.4", None),
        ("progress 150%", None),
        ("", None),
    ],
)
def test_parse_progress(line: str, percent: float | None) -> None:
    assert parse_progress(line) == percent


@pytest.mark.parametrize(
    ("line", "title"),
    [
        (
            "Downloading Chrome for Testing 156.0.8078.4 (playwright chromium v1248) from "
            "https://cdn.playwright.dev/builds/cft/156.0.8078.4/win64/chrome-win64.zip",
            "Chrome for Testing",
        ),
        (
            "Downloading Chrome Headless Shell 156.0.8078.4 (playwright chromium-headless-shell "
            "v1248) from https://cdn.playwright.dev/x.zip",
            "Chrome Headless Shell",
        ),
        (
            "Downloading Winldd playwright build v1007 from https://x/y.zip",
            "Winldd playwright build v1007",
        ),
        ("Chrome for Testing 156.0 downloaded to C:\\ms-playwright\\chromium-1248", None),
        ("|■■■■| 40% of 150 MiB", None),
    ],
)
def test_download_title(line: str, title: str | None) -> None:
    assert download_title(line) == title


def test_failure_hints() -> None:
    assert "Internet" in failure_hint(["Error: getaddrinfo ENOTFOUND cdn.playwright.dev"])
    assert "dung lượng" in failure_hint(["Error: ENOSPC: no space left on device"])
    assert "quyền" in failure_hint(["Error: EPERM: operation not permitted, rename"])
    assert failure_hint(["something else"]) == INSTALL_RETRY_HINT
    assert failure_hint([]) == INSTALL_RETRY_HINT


# ─── BrowserInstaller with a fake installer ──────────────────────────────────

FAKE_INSTALLER = """
import os, sys, time
from pathlib import Path

out = sys.stdout.buffer
def say(text, end="\\n"):
    out.write((text + end).encode("utf-8"))
    out.flush()

started = os.environ.get("FAKE_STARTED")
if started:
    with open(started, "a", encoding="utf-8") as handle:
        handle.write("x")
say("Downloading Chrome for Testing 156.0.8078.4 (playwright chromium v1248) from https://cdn")
say("|" + "\\u25a0" * 32 + " " * 48 + "|  40% of 150.2 MiB")
gate = os.environ.get("FAKE_GATE")
if gate:
    while not Path(gate).exists():
        time.sleep(0.02)
say("|  60% of 150.2 MiB", end="\\r")
say("|  80% of 150.2 MiB", end="\\r\\n")
say("Chrome for Testing 156.0.8078.4 (playwright chromium v1248) downloaded to C:\\\\pw")
say("Downloading Chrome Headless Shell 156.0.8078.4 (playwright chromium-headless-shell v1248) from https://cdn")
say("|  10% of 98.4 MiB")
for index in range(int(os.environ.get("FAKE_EXTRA_LINES", "0"))):
    say(f"line {index}")
for line in os.environ.get("FAKE_LAST", "").split("|"):
    if line:
        say(line)
sys.exit(int(os.environ.get("FAKE_CODE", "0")))
"""


@pytest.fixture
def fake_installer(tmp_path: Path) -> list[str]:
    return [sys.executable, str(_script(tmp_path, "installer.py", FAKE_INSTALLER))]


def _wait_for(predicate: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.02)


def test_installer_is_idle_before_start(fake_installer: list[str]) -> None:
    installer = BrowserInstaller(fake_installer)
    assert installer.status() == InstallStatus()
    assert installer.wait(0.01).state == "idle"


def test_installer_reports_progress_and_success(
    fake_installer: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = tmp_path / "gate"
    monkeypatch.setenv("FAKE_GATE", str(gate))
    installer = BrowserInstaller(fake_installer)
    first = installer.start()
    assert first.state == "running" and first.started_at is not None
    assert first.message == INSTALL_STARTING or "Đang tải" in first.message
    _wait_for(lambda: installer.status().percent == 40.0)
    running = installer.status()
    assert running.state == "running"
    assert running.message == "Đang tải Chromium… 40%"
    assert any("\u25a0" in line for line in running.log_tail)  # UTF-8 progress bar kept
    gate.write_text("go", encoding="utf-8")
    done = installer.wait(30)
    assert done.state == "succeeded" and done.percent == 100.0
    assert done.message == INSTALL_SUCCEEDED and done.error is None
    assert done.finished_at is not None and done.finished_at >= first.started_at
    assert "|  60% of 150.2 MiB" in done.log_tail and "|  80% of 150.2 MiB" in done.log_tail
    InstallStatus.model_validate_json(done.model_dump_json())


def test_installer_progress_messages_per_part(fake_installer: list[str]) -> None:
    installer = BrowserInstaller(fake_installer)
    seen: list[str] = []
    original = installer._on_line

    def record(line: str) -> None:
        original(line)
        seen.append(installer._status.message)

    installer._on_line = record  # type: ignore[method-assign]
    installer.start()
    assert installer.wait(30).state == "succeeded"
    assert "Đang tải Chromium… 0%" in seen
    assert "Đang tải Chromium… 80%" in seen
    assert "Đã tải xong Chromium." in seen
    assert "Đang tải Chromium (phần 2)… 10%" in seen
    # Playwright's download names stay in the log tail only.
    assert not any("Headless" in message or "Testing" in message for message in seen)


def test_installer_failure_has_a_vietnamese_message_and_hint(
    fake_installer: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODE", "3")
    monkeypatch.setenv("FAKE_LAST", "Error: getaddrinfo ENOTFOUND cdn.playwright.dev")
    installer = BrowserInstaller(fake_installer)
    installer.start()
    status = installer.wait(30)
    assert status.state == "failed" and status.error is not None
    assert status.error.message == "Không tải được Chromium (trình cài kết thúc với mã lỗi 3)."
    assert status.message == status.error.message
    assert status.error.hint is not None and "Internet" in status.error.hint
    assert status.log_tail[-1] == "Error: getaddrinfo ENOTFOUND cdn.playwright.dev"
    assert status.percent == 10.0  # the last progress seen is kept
    assert status.finished_at is not None


def test_installer_keeps_only_the_log_tail(
    fake_installer: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_EXTRA_LINES", "50")
    installer = BrowserInstaller(fake_installer)
    installer.start()
    status = installer.wait(30)
    assert len(status.log_tail) == LOG_TAIL_LINES
    assert status.log_tail[-1] == "line 49"


def test_installer_runs_one_install_at_a_time(
    fake_installer: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate, started = tmp_path / "gate", tmp_path / "started.txt"
    monkeypatch.setenv("FAKE_GATE", str(gate))
    monkeypatch.setenv("FAKE_STARTED", str(started))
    installer = BrowserInstaller(fake_installer)
    statuses: list[InstallStatus] = []
    threads = [
        threading.Thread(target=lambda: statuses.append(installer.start())) for _ in range(5)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert {status.state for status in statuses} == {"running"}
    assert len({status.started_at for status in statuses}) == 1
    _wait_for(lambda: started.exists())
    gate.write_text("go", encoding="utf-8")
    assert installer.wait(30).state == "succeeded"
    assert started.read_text(encoding="utf-8") == "x"  # a single process ran

    # Another start() after the end runs again, with a fresh log.
    second = installer.start()
    assert second.state == "running" and second.log_tail == []
    assert installer.wait(30).state == "succeeded"
    assert started.read_text(encoding="utf-8") == "xx"


def test_installer_command_that_cannot_start(tmp_path: Path) -> None:
    installer = BrowserInstaller([str(tmp_path / "missing-installer.exe")])
    status = installer.start()
    assert status.state == "failed" and status.error is not None
    assert status.error.message.startswith("Không chạy được trình cài Chromium")
    assert status.error.hint == INSTALL_RETRY_HINT
    assert installer.wait(1).state == "failed"


def test_installer_uses_install_command_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    class FakeProcess:
        def __init__(self, command: list[str], **kwargs: object) -> None:
            commands.append(command)
            assert kwargs["stdin"] is subprocess.DEVNULL
            assert kwargs["stderr"] is subprocess.STDOUT
            if sys.platform == "win32":
                assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
            raise OSError("not started")

    monkeypatch.setattr(browser_install, "install_command", lambda: ["installer", "chromium"])
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    installer = BrowserInstaller()
    assert installer.command is None
    assert installer.start().state == "failed"
    assert commands == [["installer", "chromium"]]
