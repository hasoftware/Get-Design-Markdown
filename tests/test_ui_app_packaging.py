"""Packaging of the Windows exe: the generated icon, the PyInstaller spec, the launcher and
scripts/build_exe.py (its pure parts; the real build runs in the CI exe job)."""

from __future__ import annotations

import importlib.util
import json
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
