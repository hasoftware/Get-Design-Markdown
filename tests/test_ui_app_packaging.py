"""Packaging of the Windows exe: the generated icon, the PyInstaller spec, the launcher and
scripts/build_exe.py (its pure parts; the real build runs in the CI exe job)."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from PIL import Image
from scripts import build_exe

from get_design_markdown import __version__

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_gdm_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─── Icon ────────────────────────────────────────────────────────────────────


def test_icon_is_a_multi_size_ico(tmp_path: Path) -> None:
    make_icon = _load(PACKAGING / "make_icon.py")
    target = make_icon.make_icon(tmp_path / "out" / "icon.ico")
    assert target == tmp_path / "out" / "icon.ico"
    with Image.open(target) as image:
        assert image.format == "ICO"
        sizes = set(image.info["sizes"])
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes


def test_icon_artwork_is_opaque_inside_and_transparent_at_the_corners() -> None:
    make_icon = _load(PACKAGING / "make_icon.py")
    master = make_icon.draw_master(256)
    assert master.size == (256, 256) and master.mode == "RGBA"
    assert master.getpixel((0, 0))[3] == 0  # rounded corner
    assert master.getpixel((128, 128))[3] == 255
    colors = master.getcolors(maxcolors=1 << 20)
    assert colors is not None and len(colors) > 4  # background, page, lines, swatches


def test_icon_script_usage(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    make_icon = _load(PACKAGING / "make_icon.py")
    assert make_icon.main([]) == 2
    assert "Cách dùng" in capsys.readouterr().err
    assert make_icon.main([str(tmp_path / "x.ico")]) == 0
    assert (tmp_path / "x.ico").is_file()


# ─── Spec and launcher ───────────────────────────────────────────────────────


def test_spec_builds_a_windowed_onedir_exe_without_browsers() -> None:
    text = (PACKAGING / "gdm-ui.spec").read_text(encoding="utf-8")
    compile(text, "gdm-ui.spec", "exec")  # valid Python
    for expected in (
        'APP_NAME = "Get-Design-Markdown"',
        "console=False",
        "exclude_binaries=True",
        "COLLECT(",
        'collect_data_files("get_design_markdown")',
        'collect_submodules("get_design_markdown")',
        '"webview.platforms.winforms"',
        '"webview.platforms.edgechromium"',
        "launcher.py",
        "make_icon(",
        "version=version_info",
        ".local-browsers",
    ):
        assert expected in text, expected
    assert "upx=True" not in text
    assert "onefile" not in text.lower()


def test_launcher_calls_the_ui_main() -> None:
    text = (PACKAGING / "launcher.py").read_text(encoding="utf-8")
    compile(text, "launcher.py", "exec")
    assert "from get_design_markdown.ui.app import main" in text
    assert 'if __name__ == "__main__":' in text


def test_spec_lists_the_bundled_sources_for_the_notices() -> None:
    text = (PACKAGING / "gdm-ui.spec").read_text(encoding="utf-8")
    assert f'"{build_exe.BUNDLED_SOURCES_FILE}"' in text
    assert "a.scripts, a.pure, a.binaries, a.datas" in text


# ─── Launcher: start-up failures ─────────────────────────────────────────────

GREENLET_ERROR = "DLL load failed while importing _greenlet: The filename or extension is too long."


@pytest.fixture
def launcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    """packaging/launcher.py with its log folder in tmp_path and message boxes recorded."""
    module = _load(PACKAGING / "launcher.py")
    monkeypatch.setenv("GDM_UI_LOG_DIR", str(tmp_path / "logs"))
    boxes: list[str] = []

    def record(text: str) -> bool:
        boxes.append(text)
        return True

    monkeypatch.setattr(module, "show_message_box", record)
    module.boxes = boxes  # type: ignore[attr-defined]
    return module


def _failing_load() -> Any:
    raise ImportError(GREENLET_ERROR)


def test_launcher_runs_the_ui_main_when_it_imports(launcher: ModuleType) -> None:
    calls: list[str] = []
    launcher.run([], load=lambda: lambda: calls.append("main"))
    assert calls == ["main"] and launcher.boxes == []


def test_launcher_import_failure_is_logged_and_shown_in_vietnamese(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    long_folder = Path("C:/") / ("x" * 190) / "Get-Design-Markdown"
    monkeypatch.setattr(launcher, "app_folder", lambda: long_folder)
    with pytest.raises(SystemExit) as caught:
        launcher.run([], load=_failing_load)
    assert caught.value.code == 1
    log = (tmp_path / "logs" / "ui.log").read_text(encoding="utf-8")
    assert "Traceback" in log and GREENLET_ERROR in log
    assert str(long_folder) in log
    [box] = launcher.boxes
    assert box.startswith("Get Design Markdown không khởi động được.")
    assert GREENLET_ERROR in box
    assert "đường dẫn quá dài" in box and "C:\\Apps" in box
    assert f"Nhật ký: {tmp_path / 'logs' / 'ui.log'}" in box


def test_launcher_other_failures_suggest_extracting_again(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "app_folder", lambda: Path("C:/Apps/Get-Design-Markdown"))

    def broken() -> Any:
        raise ModuleNotFoundError("No module named 'get_design_markdown.ui.server'")

    with pytest.raises(SystemExit):
        launcher.run([], load=broken)
    [box] = launcher.boxes
    assert "giải nén lại" in box and "quá dài" not in box


class _FileNameTooLong(OSError):
    winerror = 206  # ERROR_FILENAME_EXCED_RANGE


def test_launcher_recognises_path_errors(launcher: ModuleType) -> None:
    short = Path("C:/Apps/Get-Design-Markdown")
    assert _FileNameTooLong.winerror == launcher.ERROR_FILENAME_EXCED_RANGE
    assert launcher.path_too_long(_FileNameTooLong("x"), "x", short)
    assert launcher.path_too_long(ImportError(GREENLET_ERROR), GREENLET_ERROR, short)
    assert launcher.path_too_long(ImportError("x"), "x", Path("C:/") / ("y" * 160))
    assert not launcher.path_too_long(ImportError("x"), "x", short)


def test_launcher_self_test_writes_a_failed_report_without_a_dialog(
    launcher: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from get_design_markdown.ui import app as ui_app

    report = tmp_path / "out" / "self-test.json"
    with pytest.raises(SystemExit) as caught:
        launcher.run(["--self-test", "--self-test-report", str(report)], load=_failing_load)
    assert caught.value.code == 1
    assert launcher.boxes == []  # never blocks a CI run or build_exe.py
    parsed = ui_app.SelfTestReport.model_validate_json(report.read_text(encoding="utf-8"))
    assert parsed.ok is False and parsed.version == __version__
    assert [step.name for step in parsed.steps] == list(ui_app.SELF_TEST_STEPS)
    assert not any(step.ok for step in parsed.steps)
    assert GREENLET_ERROR in parsed.steps[0].detail and "quá dài" in parsed.steps[0].detail
    assert f"Báo cáo: {report}" in capsys.readouterr().err
    # Same build_exe.py summary as a report written by the app itself.
    lines = build_exe.format_self_test(json.loads(report.read_text(encoding="utf-8")))
    assert lines[1].startswith("  [LỖI] imports")


def test_launcher_self_test_report_defaults_to_the_log_folder(
    launcher: ModuleType, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit):
        launcher.run(["--self-test"], load=_failing_load)
    assert (tmp_path / "logs" / "self-test.json").is_file()
    with pytest.raises(SystemExit):
        launcher.run(
            ["--self-test", f"--self-test-report={tmp_path / 'r.json'}"], load=_failing_load
        )
    assert (tmp_path / "r.json").is_file()


def test_launcher_constants_match_the_app(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from get_design_markdown.ui import app as ui_app
    from get_design_markdown.ui import settings

    assert launcher.SELF_TEST_STEPS == ui_app.SELF_TEST_STEPS
    assert launcher.SELF_TEST_REPORT_FILE == ui_app.SELF_TEST_REPORT_FILE
    assert launcher.MESSAGE_BOX_TITLE == ui_app.MESSAGE_BOX_TITLE
    assert launcher.LOG_FILE == settings.LOG_FILE
    assert launcher.LOG_DIR_ENV == settings.LOG_DIR_ENV
    assert launcher.APP_DIR_NAME == settings.APP_DIR_NAME
    assert launcher.log_dir() == settings.log_dir()
    monkeypatch.delenv("GDM_UI_LOG_DIR")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(settings, "_platform", lambda: "win32")
    assert launcher.log_dir() == settings.log_dir()


def test_launcher_script_exits_with_a_report_when_the_app_cannot_import(tmp_path: Path) -> None:
    """The real script in a child process, with an app package whose import fails like a DLL
    that cannot load: it must exit 1 promptly with a report, never wait on a dialog."""
    fake = tmp_path / "fake-site" / "get_design_markdown"
    (fake / "ui").mkdir(parents=True)
    (fake / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    (fake / "ui" / "__init__.py").write_text("", encoding="utf-8")
    (fake / "ui" / "app.py").write_text(f"raise ImportError({GREENLET_ERROR!r})\n", "utf-8")
    report = tmp_path / "report.json"
    env = {
        **os.environ,
        "PYTHONPATH": str(fake.parent),
        "GDM_UI_LOG_DIR": str(tmp_path / "logs"),
        "PYTHONIOENCODING": "utf-8",
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(PACKAGING / "launcher.py"),
            "--self-test",
            "--self-test-report",
            str(report),
        ],
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 1, completed.stderr.decode("utf-8", "replace")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] is False and data["version"] == "9.9.9"
    assert GREENLET_ERROR in data["steps"][0]["detail"]
    assert GREENLET_ERROR in (tmp_path / "logs" / "ui.log").read_text(encoding="utf-8")


def test_packaging_is_not_an_importable_package() -> None:
    """A packaging/__init__.py would shadow the `packaging` library PyInstaller and pip use."""
    assert not (PACKAGING / "__init__.py").exists()


def test_windows_readme() -> None:
    text = build_exe.readme_text("9.8.7")
    assert text.startswith("Get Design Markdown 9.8.7 cho Windows")
    assert "\r\n" in text and "\n" not in text.replace("\r\n", "")
    for expected in (
        "More info",
        "Run anyway",
        "Tải Chromium",
        "WebView2",
        "--self-test",
        "GPL-3.0-or-later",
        "https://github.com/hasoftware/Get-Design-Markdown",
        "không sao chép logo",
        build_exe.NOTICES_NAME,
        "đường dẫn ngắn",
        "C:\\Apps",
    ):
        assert expected in text, expected


# ─── scripts/build_exe.py ────────────────────────────────────────────────────


def test_zip_name_and_versions() -> None:
    assert build_exe.zip_name("0.2.0") == "Get-Design-Markdown-0.2.0-windows-x64.zip"
    assert build_exe.project_version() == build_exe.package_version() == __version__


def test_environment_check_is_windows_only() -> None:
    with pytest.raises(build_exe.BuildError) as caught:
        build_exe.check_environment(platform="linux")
    assert caught.value.code == 2 and "Windows" in caught.value.message


def test_environment_check_names_missing_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_exe, "_missing_modules", lambda names: ["PyInstaller", "webview"])
    with pytest.raises(build_exe.BuildError) as caught:
        build_exe.check_environment(platform="win32")
    assert caught.value.code == 2
    assert "PyInstaller, webview" in caught.value.message
    assert caught.value.hint == f"Chạy: {build_exe.SYNC_COMMAND}"


def test_environment_check_compares_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_exe, "_missing_modules", lambda names: [])
    monkeypatch.setattr(build_exe, "package_version", lambda: "0.0.1")
    with pytest.raises(build_exe.BuildError) as caught:
        build_exe.check_environment(platform="win32")
    assert "Phiên bản không khớp" in caught.value.message
    monkeypatch.setattr(build_exe, "package_version", build_exe.project_version)
    assert build_exe.check_environment(platform="win32") == __version__


def test_pyinstaller_command(tmp_path: Path) -> None:
    command = build_exe.pyinstaller_command(dist_root=tmp_path / "d", build_root=tmp_path / "b")
    assert command[:3] == [sys.executable, "-m", "PyInstaller"]
    assert str(build_exe.SPEC_FILE) in command and "--noconfirm" in command
    assert command[command.index("--distpath") + 1] == str(tmp_path / "d")
    assert command[command.index("--workpath") + 1] == str(tmp_path / "b")


def test_paths_of_the_build() -> None:
    assert build_exe.app_dir(Path("dist")) == Path("dist") / "Get-Design-Markdown"
    assert build_exe.exe_path(Path("dist")).name == "Get-Design-Markdown.exe"


def _fake_app(tmp_path: Path) -> Path:
    folder = tmp_path / "Get-Design-Markdown"
    (folder / "_internal" / "get_design_markdown").mkdir(parents=True)
    (folder / "Get-Design-Markdown.exe").write_bytes(b"MZ" + b"\0" * 100)
    (folder / "_internal" / "get_design_markdown" / "a.js").write_text("x", encoding="utf-8")
    return folder


def test_documents_next_to_the_exe(tmp_path: Path) -> None:
    folder = _fake_app(tmp_path)
    build_exe.add_documents(folder, "1.2.3")
    license_text = (folder / build_exe.LICENSE_NAME).read_bytes()
    assert b"GNU GENERAL PUBLIC LICENSE" in license_text and b"\r\n" in license_text
    readme = (folder / build_exe.README_NAME).read_bytes()
    assert readme.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM for Notepad
    assert "1.2.3" in readme.decode("utf-8-sig")


# ─── Third-party notices ─────────────────────────────────────────────────────


def _fake_distribution(
    site: Path,
    name: str,
    *,
    license_files: dict[str, str] | None = None,
    metadata: str = "",
    extra_files: dict[str, str] | None = None,
) -> importlib.metadata.Distribution:
    """An installed distribution `name` 1.0 under `site` (module <name>/__init__.py)."""
    info = site / f"{name}-1.0.dist-info"
    package = site / name
    info.mkdir(parents=True)
    package.mkdir()
    files = {f"{name}/__init__.py": ""}
    files.update({f"{info.name}/{path}": text for path, text in (license_files or {}).items()})
    files.update(extra_files or {})
    for path, text in files.items():
        (site / path).parent.mkdir(parents=True, exist_ok=True)
        (site / path).write_text(text, encoding="utf-8")
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {name}\nVersion: 1.0\n{metadata}", encoding="utf-8"
    )
    record = [*files, f"{info.name}/METADATA", f"{info.name}/RECORD"]
    (info / "RECORD").write_text("".join(f"{path},,\n" for path in record), encoding="utf-8")
    return importlib.metadata.PathDistribution(info)


NATIVE_SOURCES = [
    "C:/Python312/python312.dll",
    "C:/Python312/vcruntime140.dll",
    "C:/Python312/DLLs/libssl-3.dll",
    "C:/Python312/DLLs/libcrypto-3.dll",
    "C:/Python312/DLLs/libffi-8.dll",
    "C:/venv/site-packages/webview/lib/Microsoft.Web.WebView2.Core.dll",
    "C:/venv/site-packages/webview/lib/runtimes/win-x64/native/WebView2Loader.dll",
]


def test_notices_of_bundled_distributions_and_native_libraries(tmp_path: Path) -> None:
    site = tmp_path / "site-packages"
    app = tmp_path / "Get-Design-Markdown"
    shipped_notice = app / build_exe.CONTENTS_DIR / "bundled" / "driver" / "LICENSE"
    shipped_notice.parent.mkdir(parents=True)
    shipped_notice.write_text("Node.js license", encoding="utf-8")
    bundled = _fake_distribution(
        site,
        "bundled",
        license_files={"licenses/LICENSE": "Copyright (c) 2024 Bundled Authors\nBSD text"},
        metadata="License-Expression: BSD-3-Clause\nHome-page: https://example.org/bundled\n",
        extra_files={
            "bundled/driver/LICENSE": "Node.js license",
            "bundled/vendor/LICENSE.txt": "Copyright (c) Vendored Authors",
        },
    )
    no_file = _fake_distribution(
        site, "nofile", metadata="Author: Jane Doe\nLicense: MIT\nClassifier: Topic :: X\n"
    )
    not_bundled = _fake_distribution(site, "unused", license_files={"LICENSE": "unused"})
    project = _fake_distribution(site, "get_design_markdown", license_files={"LICENSE": "GPL"})
    python_license = tmp_path / "python-LICENSE.txt"
    python_license.write_text("PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2", encoding="utf-8")
    sources = [
        *NATIVE_SOURCES,
        str(site / "bundled" / "__init__.py"),
        str(site / "nofile" / "__init__.py"),
        str(site / "get_design_markdown" / "__init__.py"),
    ]
    notices = build_exe.third_party_notices(
        sources,
        app,
        distributions=[bundled, no_file, not_bundled, project],
        python_license=python_license,
    )
    names = [notice.name for notice in notices]
    assert names == [
        "Python",
        "OpenSSL",
        "libffi",
        "Microsoft Edge WebView2 SDK",
        "bundled",
        "nofile",
    ]
    by_name = {notice.name: notice for notice in notices}
    assert by_name["bundled"].license == "BSD-3-Clause"
    assert by_name["bundled"].url == "https://example.org/bundled"
    # dist-info license files and vendored notices are copied; shipped ones only referenced.
    copied = dict(by_name["bundled"].texts)
    assert copied["bundled-1.0.dist-info/licenses/LICENSE"].startswith("Copyright (c) 2024")
    assert copied["bundled/vendor/LICENSE.txt"] == "Copyright (c) Vendored Authors"
    assert by_name["bundled"].shipped == ("_internal/bundled/driver/LICENSE",)
    # No license file: the standard MIT text under the author of the metadata.
    [(_, mit)] = by_name["nofile"].texts
    assert mit.startswith("Copyright (c) Jane Doe\n\nPermission is hereby granted")
    assert by_name["OpenSSL"].covers == "libcrypto-3.dll, libssl-3.dll"

    text = build_exe.render_notices("1.2.3", notices)
    assert text.startswith("Get Design Markdown 1.2.3 — Thông báo về phần mềm bên thứ ba")
    for expected in (
        "  - Python ",
        "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2",
        "  - OpenSSL ",
        "Copyright The OpenSSL Project Authors",
        "Version 2.0, January 2004",
        "END OF TERMS AND CONDITIONS",
        "libffi - Copyright (c) 1996-2022",
        "Copyright (C) Microsoft Corporation. All rights reserved.",
        "  - bundled 1.0 — BSD-3-Clause",
        "_internal/bundled/driver/LICENSE",
    ):
        assert expected in text, expected
    assert "  - unused " not in text and "get_design_markdown" not in text
    build_exe.check_notices(notices, ["Python", "OpenSSL", "libffi", "bundled"])
    with pytest.raises(build_exe.BuildError, match="pywebview"):
        build_exe.check_notices(notices, ["pywebview"])


def test_notices_fail_without_license_text() -> None:
    empty = build_exe.Notice(name="bare", version="1", license="MIT")
    with pytest.raises(build_exe.BuildError, match="bare"):
        build_exe.check_notices([empty], [])


def test_notices_of_the_real_gui_build_name_pywebview_pythonnet_and_openssl(
    tmp_path: Path,
) -> None:
    """The installed `gui` extra (pywebview, pythonnet, clr_loader) as the exe bundles it."""
    if sys.platform != "win32":
        pytest.skip("the exe is built on Windows only")
    sources: list[str] = list(NATIVE_SOURCES)
    for module in ("webview", "pythonnet", "clr_loader", "playwright", "pydantic", "PIL"):
        spec = importlib.util.find_spec(module)
        if spec is None or spec.origin is None:
            pytest.skip(f"{module} is not installed (uv sync --extra gui)")
        sources.append(spec.origin)
    notices = build_exe.third_party_notices(sources, tmp_path)
    build_exe.check_notices(notices)  # REQUIRED_NOTICES
    text = build_exe.render_notices(__version__, notices)
    for name in ("pywebview", "pythonnet", "clr_loader", "playwright", "pydantic", "pillow"):
        assert f"\n  - {name} " in text.lower() or f"\n  - {name} " in text, name
    assert "OpenSSL" in text and "Apache License" in text
    assert "Roman Sirokov" in text  # pywebview's copyright line


def test_standard_license_texts_are_shipped() -> None:
    for name in build_exe.STANDARD_LICENSE_FILES:
        assert (build_exe.LICENSES_DIR / name).is_file(), name
    texts = {
        name: (build_exe.LICENSES_DIR / name).read_text(encoding="utf-8")
        for name in build_exe.STANDARD_LICENSE_FILES
    }
    assert texts["OpenSSL-LICENSE.txt"].lstrip().startswith("Apache License")
    assert "Anthony Green" in texts["libffi-LICENSE.txt"]
    assert "Microsoft Corporation" in texts["WebView2-SDK-LICENSE.txt"]
    assert texts["MIT.txt"].startswith("Permission is hereby granted")


def test_write_third_party_notices_next_to_the_exe_and_in_the_zip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = _fake_app(tmp_path)
    sources_file = tmp_path / build_exe.BUNDLED_SOURCES_FILE
    sources_file.write_text(json.dumps(NATIVE_SOURCES), encoding="utf-8")
    python_license = tmp_path / "LICENSE.txt"
    python_license.write_text("PSF LICENSE", encoding="utf-8")
    monkeypatch.setattr(build_exe, "python_license_file", lambda prefix=None: python_license)
    notices = build_exe.write_third_party_notices(
        folder, "1.2.3", sources_file, required=["Python", "OpenSSL"]
    )
    assert [notice.name for notice in notices][:2] == ["Python", "OpenSSL"]
    raw = (folder / build_exe.NOTICES_NAME).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")
    archive = build_exe.make_zip(folder, tmp_path / "app.zip")
    with zipfile.ZipFile(archive) as zf:
        assert f"Get-Design-Markdown/{build_exe.NOTICES_NAME}" in zf.namelist()
        packed = zf.read(f"Get-Design-Markdown/{build_exe.NOTICES_NAME}").decode("utf-8-sig")
    assert "OpenSSL" in packed and "PSF LICENSE" in packed
    with pytest.raises(build_exe.BuildError, match="pywebview"):
        build_exe.write_third_party_notices(folder, "1.2.3", sources_file)


def test_missing_bundled_sources_list(tmp_path: Path) -> None:
    with pytest.raises(build_exe.BuildError, match="danh sách tệp đã đóng gói"):
        build_exe.load_bundled_sources(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(build_exe.BuildError, match="định dạng"):
        build_exe.load_bundled_sources(broken)


def test_python_license_file(tmp_path: Path) -> None:
    with pytest.raises(build_exe.BuildError, match="giấy phép của Python"):
        build_exe.python_license_file(tmp_path)
    (tmp_path / "LICENSE.txt").write_text("PSF", encoding="utf-8")
    assert build_exe.python_license_file(tmp_path) == tmp_path / "LICENSE.txt"


def test_make_zip_keeps_the_folder_name(tmp_path: Path) -> None:
    folder = _fake_app(tmp_path)
    archive = build_exe.make_zip(folder, tmp_path / "out" / "app.zip")
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert zf.testzip() is None
    assert names == sorted(names)
    assert "Get-Design-Markdown/Get-Design-Markdown.exe" in names
    assert "Get-Design-Markdown/_internal/get_design_markdown/a.js" in names
    assert build_exe.folder_size(folder) == 102 + 1
    # A second zip replaces the first one.
    assert build_exe.make_zip(folder, archive) == archive


def test_human_size() -> None:
    assert build_exe.human_size(62 * 1024 * 1024) == "62.0 MB"


def test_self_test_environment_keeps_logs_in_the_build_folder(tmp_path: Path) -> None:
    env = build_exe.self_test_environment(tmp_path)
    assert env["GDM_UI_LOG_DIR"] == str(tmp_path / "self-test-logs")
    assert env["GDM_UI_CONFIG_DIR"] == str(tmp_path / "self-test-config")


REPORT: dict[str, Any] = {
    "ok": True,
    "version": "0.2.0",
    "python": "3.12.1",
    "steps": [
        {"name": "imports", "ok": True, "seconds": 0.3, "detail": "76 module"},
        {"name": "browser", "ok": True, "seconds": 1.8, "detail": "auto -> msedge 141.0"},
    ],
}


def _fake_run(report: dict[str, Any], code: int) -> Any:
    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert command[1:3] == ["--self-test", "--self-test-report"]
        assert "GDM_UI_LOG_DIR" in kwargs["env"] and kwargs["timeout"] > 0
        Path(command[3]).write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(command, code)

    return run


def test_run_self_test_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(REPORT, 0))
    data = build_exe.run_self_test(
        tmp_path / "exe", tmp_path / "r" / "report.json", work_dir=tmp_path
    )
    assert data["ok"] is True
    out = capsys.readouterr().out
    assert "[ĐẠT] browser (1.80 s): auto -> msedge 141.0" in out


def test_run_self_test_fails_on_a_failed_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failed = {**REPORT, "ok": False}
    monkeypatch.setattr(subprocess, "run", _fake_run(failed, 1))
    with pytest.raises(build_exe.BuildError, match="không đạt"):
        build_exe.run_self_test(tmp_path / "exe", tmp_path / "report.json", work_dir=tmp_path)


def test_run_self_test_without_a_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def no_report(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(subprocess, "run", no_report)
    stale = tmp_path / "report.json"
    stale.write_text("{}", encoding="utf-8")  # from an earlier run: must not be trusted
    with pytest.raises(build_exe.BuildError, match="không ghi báo cáo"):
        build_exe.run_self_test(tmp_path / "exe", stale, work_dir=tmp_path)


def test_github_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(target))
    build_exe.write_github_output({"zip_path": "dist/x.zip", "version": "0.2.0"})
    assert target.read_text(encoding="utf-8") == "zip_path=dist/x.zip\nversion=0.2.0\n"
    monkeypatch.delenv("GITHUB_OUTPUT")
    build_exe.write_github_output({"a": "b"})  # no-op


def test_main_reports_errors_in_vietnamese(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse() -> str:
        raise build_exe.BuildError(
            "Bản exe chỉ build được trên Windows.", hint="Dùng Windows.", code=2
        )

    monkeypatch.setattr(build_exe, "check_environment", refuse)
    assert build_exe.main(["--zip"]) == 2
    err = capsys.readouterr().err
    assert "Lỗi: Bản exe chỉ build được trên Windows." in err and "Gợi ý: Dùng Windows." in err


def test_parse_args() -> None:
    options = build_exe.parse_args(["--zip", "--skip-self-test", "--dist", "exe-dist"])
    assert options.zip and options.skip_self_test and options.dist == Path("exe-dist")
    options = build_exe.parse_args([])
    assert not options.zip and not options.skip_self_test
    assert options.dist == build_exe.DIST_ROOT
