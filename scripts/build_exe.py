"""Build the Windows exe of the local UI (PyInstaller, onedir, windowed) and zip it.

    python -m uv run --group build --extra gui python scripts/build_exe.py [--zip]
                                                     [--dist DIR] [--skip-self-test]

1. Check the environment: Windows, PyInstaller (group `build`), pywebview (extra `gui`),
   Playwright's driver, and the same version in pyproject.toml and get_design_markdown.
2. Remove build/gdm-ui and dist/Get-Design-Markdown, then run PyInstaller with
   packaging/gdm-ui.spec (the icon is drawn at build time; no browser is bundled).
3. Add LICENSE.txt and README.txt (packaging/README-windows.txt, Vietnamese) next to the exe.
4. Run `Get-Design-Markdown.exe --self-test` (waits for it; the logs and settings it writes stay
   under build/gdm-ui) and fail when a step failed — unless --skip-self-test.
5. --zip: <dist>/Get-Design-Markdown-<version>-windows-x64.zip with the folder inside. When
   $GITHUB_OUTPUT is set: version, app_dir, exe_path (and zip_path) are written to it.
   <dist> is dist/ unless --dist names another folder (e.g. one holding only the exe build).
6. Print sizes and timings. Exit code 0 on success, 1 on a failed step, 2 on a bad environment.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
APP_NAME: Final = "Get-Design-Markdown"
SPEC_FILE: Final = REPO_ROOT / "packaging" / "gdm-ui.spec"
README_TEMPLATE: Final = REPO_ROOT / "packaging" / "README-windows.txt"
README_NAME: Final = "README.txt"
LICENSE_NAME: Final = "LICENSE.txt"
BUILD_ROOT: Final = REPO_ROOT / "build"
DIST_ROOT: Final = REPO_ROOT / "dist"
#: PyInstaller's work folder for the spec (build/<spec name>).
WORK_DIR: Final = BUILD_ROOT / "gdm-ui"
SELF_TEST_TIMEOUT_S: Final = 600.0
SYNC_COMMAND: Final = "python -m uv sync --extra gui --group build"
ERROR_PREFIX: Final = "Lỗi:"
HINT_PREFIX: Final = "Gợi ý:"


class BuildError(Exception):
    """A build step failed; `code` is the exit code (2: environment, 1: build)."""

    def __init__(self, message: str, hint: str | None = None, *, code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code


def say(message: str) -> None:
    print(message, flush=True)


# ─── Environment ─────────────────────────────────────────────────────────────


def project_version(pyproject: Path = REPO_ROOT / "pyproject.toml") -> str:
    """[project].version of pyproject.toml."""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def package_version() -> str:
    """get_design_markdown.__version__ of the installed (editable) package."""
    module = importlib.import_module("get_design_markdown")
    return str(module.__version__)


def zip_name(version: str) -> str:
    """dist file name of the zipped exe folder."""
    return f"{APP_NAME}-{version}-windows-x64.zip"


def _missing_modules(names: Sequence[str]) -> list[str]:
    missing: list[str] = []
    for name in names:
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    return missing


def check_environment(platform: str | None = None) -> str:
    """Raise BuildError (code 2) when the exe cannot be built here; returns the version."""
    if (platform or sys.platform) != "win32":
        raise BuildError(
            "Bản exe chỉ build được trên Windows.",
            hint="Chạy lệnh này trên máy Windows (hoặc job windows-latest của CI).",
            code=2,
        )
    missing = _missing_modules(["PyInstaller", "webview", "playwright"])
    if missing:
        raise BuildError(
            f"Thiếu gói để build exe: {', '.join(missing)}.",
            hint=f"Chạy: {SYNC_COMMAND}",
            code=2,
        )
    from playwright._impl._driver import compute_driver_executable

    node, cli = (Path(path) for path in compute_driver_executable())
    if not node.is_file() or not cli.is_file():
        raise BuildError(
            f"Thiếu trình điều khiển Playwright ({node}).",
            hint=f"Cài lại gói playwright: {SYNC_COMMAND} --reinstall-package playwright",
            code=2,
        )
    declared, packaged = project_version(), package_version()
    if declared != packaged:
        raise BuildError(
            f"Phiên bản không khớp: pyproject.toml ghi {declared}, "
            f"get_design_markdown.__version__ là {packaged}.",
            hint="Sửa cho hai nơi cùng một phiên bản rồi build lại.",
            code=2,
        )
    for path in (SPEC_FILE, README_TEMPLATE, REPO_ROOT / "LICENSE"):
        if not path.is_file():
            raise BuildError(f"Thiếu tệp {path.relative_to(REPO_ROOT)}.", code=2)
    return declared


# ─── Build ───────────────────────────────────────────────────────────────────


def app_dir(dist_root: Path = DIST_ROOT) -> Path:
    return dist_root / APP_NAME


def exe_path(dist_root: Path = DIST_ROOT) -> Path:
    return app_dir(dist_root) / f"{APP_NAME}.exe"


def clean(paths: Sequence[Path]) -> None:
    """Remove earlier build outputs (folders or files) that exist."""
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def pyinstaller_command(
    *, spec: Path = SPEC_FILE, dist_root: Path = DIST_ROOT, build_root: Path = BUILD_ROOT
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        str(spec),
        "--noconfirm",
        "--clean",
        "--log-level",
        "WARN",
        "--distpath",
        str(dist_root),
        "--workpath",
        str(build_root),
    ]


def run_pyinstaller(command: Sequence[str]) -> None:
    completed = subprocess.run(list(command), cwd=REPO_ROOT, check=False)
    if completed.returncode != 0:
        raise BuildError(
            f"PyInstaller dừng với mã lỗi {completed.returncode}.",
            hint="Xem thông báo của PyInstaller ở trên.",
        )


def readme_text(version: str, template: Path = README_TEMPLATE) -> str:
    """The Windows read-me shipped next to the exe (CRLF line endings for Notepad)."""
    text = template.read_text(encoding="utf-8").replace("{version}", version)
    return "\r\n".join(text.splitlines()) + "\r\n"


def add_documents(target: Path, version: str) -> None:
    """LICENSE.txt and the read-me next to the exe."""
    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    (target / LICENSE_NAME).write_text(
        "\r\n".join(license_text.splitlines()) + "\r\n", encoding="utf-8", newline=""
    )
    (target / README_NAME).write_text(readme_text(version), encoding="utf-8-sig", newline="")


def self_test_environment(work_dir: Path) -> dict[str, str]:
    """Environment of the self-test run: logs and settings stay in the build folder."""
    env = dict(os.environ)
    env["GDM_UI_LOG_DIR"] = str(work_dir / "self-test-logs")
    env["GDM_UI_CONFIG_DIR"] = str(work_dir / "self-test-config")
    return env


def run_self_test(exe: Path, report: Path, *, work_dir: Path = WORK_DIR) -> dict[str, Any]:
    """Run the built exe's --self-test, wait for it and return the report (BuildError when a
    step failed or no report was written)."""
    report.parent.mkdir(parents=True, exist_ok=True)
    if report.exists():
        report.unlink()
    try:
        completed = subprocess.run(
            [str(exe), "--self-test", "--self-test-report", str(report)],
            env=self_test_environment(work_dir),
            timeout=SELF_TEST_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise BuildError(
            f"Bài tự kiểm tra của exe chạy quá {SELF_TEST_TIMEOUT_S:.0f} giây."
        ) from None
    if not report.is_file():
        raise BuildError(
            f"Exe không ghi báo cáo tự kiểm tra (mã thoát {completed.returncode}).",
            hint=f"Xem nhật ký trong {work_dir / 'self-test-logs'}.",
        )
    data: dict[str, Any] = json.loads(report.read_text(encoding="utf-8"))
    for line in format_self_test(data):
        say(line)
    if completed.returncode != 0 or not data.get("ok"):
        raise BuildError(
            f"Bài tự kiểm tra của exe không đạt (mã thoát {completed.returncode}).",
            hint=f"Báo cáo: {report}; nhật ký: {work_dir / 'self-test-logs'}.",
        )
    return data


def format_self_test(report: dict[str, Any]) -> list[str]:
    """Lines summarising a SelfTestReport JSON."""
    lines = [f"Tự kiểm tra exe {report.get('version', '?')} (Python {report.get('python', '?')}):"]
    for step in report.get("steps", []):
        mark = "ĐẠT" if step.get("ok") else "LỖI"
        lines.append(
            f"  [{mark}] {step.get('name')} ({float(step.get('seconds', 0)):.2f} s): "
            f"{step.get('detail', '')}"
        )
    return lines


def iter_files(folder: Path) -> Iterator[Path]:
    """Every file under `folder`, sorted (stable zip order)."""
    files = (path for path in folder.rglob("*") if path.is_file())
    yield from sorted(files, key=lambda path: path.relative_to(folder).as_posix())


def folder_size(folder: Path) -> int:
    return sum(path.stat().st_size for path in iter_files(folder))


def make_zip(folder: Path, target: Path) -> Path:
    """Zip `folder` into `target` with the folder itself as the top-level entry."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in iter_files(folder):
            zf.write(path, arcname=f"{folder.name}/{path.relative_to(folder).as_posix()}")
    return target


def human_size(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB"


def write_github_output(values: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


# ─── Main ────────────────────────────────────────────────────────────────────


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_exe.py",
        description="Build bản exe Windows của giao diện (PyInstaller) và nén thành .zip.",
    )
    parser.add_argument("--zip", action="store_true", help="Nén thư mục exe thành file .zip.")
    parser.add_argument(
        "--dist",
        type=Path,
        default=DIST_ROOT,
        metavar="THƯ_MỤC",
        help="Thư mục chứa thư mục exe và file .zip (mặc định: dist).",
    )
    parser.add_argument(
        "--skip-self-test",
        action="store_true",
        help="Bỏ qua bài tự kiểm tra của exe sau khi build (chỉ dùng khi thử cục bộ).",
    )
    return parser.parse_args(argv)


def build(options: argparse.Namespace) -> int:
    started = time.perf_counter()
    version = check_environment()
    dist_root: Path = options.dist.resolve()
    folder, archive_path = app_dir(dist_root), dist_root / zip_name(version)
    say(f"Build {APP_NAME} {version} (Python {sys.version.split()[0]})…")
    clean([WORK_DIR, folder, archive_path])
    run_pyinstaller(pyinstaller_command(dist_root=dist_root))
    exe = exe_path(dist_root)
    if not exe.is_file():
        raise BuildError(f"PyInstaller không tạo ra {exe}.")
    add_documents(folder, version)
    say(f"Đã build {exe} trong {time.perf_counter() - started:.0f} giây.")
    if options.skip_self_test:
        say("Bỏ qua bài tự kiểm tra (--skip-self-test).")
    else:
        run_self_test(exe, WORK_DIR / "self-test.json")
    say(f"Thư mục exe: {folder} ({human_size(folder_size(folder))})")
    outputs = {"version": version, "app_dir": str(folder), "exe_path": str(exe)}
    if options.zip:
        archive = make_zip(folder, archive_path)
        say(f"File zip: {archive} ({human_size(archive.stat().st_size)})")
        outputs["zip_path"] = str(archive)
    write_github_output(outputs)
    say(f"Xong sau {time.perf_counter() - started:.0f} giây.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_args(argv)
    try:
        return build(options)
    except BuildError as error:
        print(f"{ERROR_PREFIX} {error.message}", file=sys.stderr)
        if error.hint:
            print(f"{HINT_PREFIX} {error.hint}", file=sys.stderr)
        return error.code


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
