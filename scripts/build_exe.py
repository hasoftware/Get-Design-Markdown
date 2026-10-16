"""Build the Windows exe of the local UI (PyInstaller, onedir, windowed) and zip it.

    python -m uv run --group build --extra gui python scripts/build_exe.py [--zip]
                                                     [--dist DIR] [--skip-self-test]

1. Check the environment: Windows, PyInstaller (group `build`), pywebview (extra `gui`),
   Playwright's driver, and the same version in pyproject.toml and get_design_markdown.
2. Remove build/gdm-ui and dist/Get-Design-Markdown, then run PyInstaller with
   packaging/gdm-ui.spec (the icon is drawn at build time; no browser is bundled).
3. Add LICENSE.txt and README.txt (packaging/README-windows.txt, Vietnamese) next to the exe,
   and THIRD-PARTY-NOTICES.txt: the name, version, license and license/copyright texts of every
   bundled component (the distributions owning the files PyInstaller bundled, listed by the
   spec in bundled-sources.json; Python; OpenSSL, libffi and the WebView2 SDK from
   packaging/licenses). The build fails when a key component is missing from it.
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
import importlib.metadata
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import time
import tomllib
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
APP_NAME: Final = "Get-Design-Markdown"
SPEC_FILE: Final = REPO_ROOT / "packaging" / "gdm-ui.spec"
README_TEMPLATE: Final = REPO_ROOT / "packaging" / "README-windows.txt"
README_NAME: Final = "README.txt"
LICENSE_NAME: Final = "LICENSE.txt"
NOTICES_NAME: Final = "THIRD-PARTY-NOTICES.txt"
#: Written by packaging/gdm-ui.spec into PyInstaller's work folder: the source path of every
#: bundled file (scripts, pure modules, binaries, data).
BUNDLED_SOURCES_FILE: Final = "bundled-sources.json"
#: Folder next to the exe holding everything but the exe (PyInstaller >= 6 onedir).
CONTENTS_DIR: Final = "_internal"
#: License texts of bundled code that ships none itself, copied verbatim: OpenSSL 3's license
#: (Apache-2.0) and libffi's notice from CPython's Windows LICENSE.txt; LICENSE.txt and
#: NOTICE.txt of the Microsoft.Web.WebView2 NuGet package; the standard MIT License text.
LICENSES_DIR: Final = REPO_ROOT / "packaging" / "licenses"
STANDARD_LICENSE_FILES: Final = (
    "OpenSSL-LICENSE.txt",
    "libffi-LICENSE.txt",
    "WebView2-SDK-LICENSE.txt",
    "WebView2-SDK-NOTICE.txt",
    "MIT.txt",
)
#: Components THIRD-PARTY-NOTICES.txt must name (the build fails otherwise).
REQUIRED_NOTICES: Final = (
    "Python",
    "OpenSSL",
    "libffi",
    "Microsoft Edge WebView2 SDK",
    "pywebview",
    "pythonnet",
    "clr_loader",
    "playwright",
)
#: This project: its license is LICENSE.txt, not a third-party notice.
PROJECT_DISTRIBUTION: Final = "get-design-markdown"
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
    standard_licenses = [LICENSES_DIR / name for name in STANDARD_LICENSE_FILES]
    for path in (SPEC_FILE, README_TEMPLATE, REPO_ROOT / "LICENSE", *standard_licenses):
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


# ─── Third-party notices ─────────────────────────────────────────────────────

#: File names that hold license or copyright notices.
_NOTICE_FILE_RE = re.compile(
    r"^(?:LICEN[CS]E|COPYING|NOTICE|AUTHORS|COPYRIGHT|THIRD[-_ ]?PARTY)", re.IGNORECASE
)
_RULE = "=" * 78
_THIN_RULE = "-" * 78


@dataclass(frozen=True)
class Notice:
    """One bundled component in THIRD-PARTY-NOTICES.txt."""

    name: str
    version: str
    license: str
    #: (title, text): the component's license / copyright texts, verbatim.
    texts: tuple[tuple[str, str], ...] = ()
    url: str | None = None
    #: What the component is in the bundle (file names), when not obvious from the name.
    covers: str | None = None
    #: Notice files already shipped in the exe folder (relative paths), not repeated here.
    shipped: tuple[str, ...] = field(default_factory=tuple)


def canonical_name(name: str) -> str:
    """Normalized distribution name (PEP 503) for comparisons."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _path_key(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def load_bundled_sources(path: Path) -> list[str]:
    """The source paths the spec listed (BuildError when the list is missing or broken)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BuildError(
            f"Không đọc được danh sách tệp đã đóng gói {path} ({error}).",
            hint="Build lại bằng scripts/build_exe.py (packaging/gdm-ui.spec ghi danh sách này).",
        ) from None
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise BuildError(f"Danh sách tệp đã đóng gói {path} không đúng định dạng.")
    return data


def bundled_distributions(
    sources: Iterable[str],
    distributions: Iterable[importlib.metadata.Distribution] | None = None,
) -> list[importlib.metadata.Distribution]:
    """Installed distributions owning at least one bundled file (this project excluded),
    sorted by name."""
    candidates = list(
        importlib.metadata.distributions() if distributions is None else distributions
    )
    owners: dict[str, importlib.metadata.Distribution] = {}
    for dist in candidates:
        for file in dist.files or ():
            owners.setdefault(_path_key(str(dist.locate_file(file))), dist)
    found: dict[str, importlib.metadata.Distribution] = {}
    for source in sources:
        owner = owners.get(_path_key(source))
        if owner is None:
            continue
        key = canonical_name(owner.metadata["Name"] or "")
        if key and key != PROJECT_DISTRIBUTION:
            found.setdefault(key, owner)
    return [found[key] for key in sorted(found)]


def _license_label(metadata: Any) -> str:
    """SPDX expression, short License field or license classifiers of a distribution."""
    expression = (metadata.get("License-Expression") or "").strip()
    if expression:
        return str(expression)
    declared = (metadata.get("License") or "").strip()
    if declared and "\n" not in declared and len(declared) <= 200:
        return str(declared)
    classifiers = [
        str(item).split("::")[-1].strip()
        for item in metadata.get_all("Classifier") or []
        if str(item).startswith("License ::")
    ]
    return ", ".join(classifiers) or "xem văn bản bên dưới"


def _home_page(metadata: Any) -> str | None:
    home = (metadata.get("Home-page") or "").strip()
    if home:
        return str(home)
    urls: list[tuple[str, str]] = []
    for item in metadata.get_all("Project-URL") or []:
        label, _, url = str(item).partition(",")
        urls.append((label.strip().lower(), url.strip()))
    for wanted in ("homepage", "source", "source code", "repository", "github"):
        for label, url in urls:
            if label == wanted and url:
                return url
    return urls[0][1] if urls and urls[0][1] else None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip("\n")


def _standard_text(name: str) -> str:
    return _read_text(LICENSES_DIR / name)


def distribution_notice(dist: importlib.metadata.Distribution, app_folder: Path) -> Notice:
    """Notice of one bundled distribution: the license files it ships (dist-info files are
    copied here; other notice files are copied unless the exe folder already ships them)."""
    metadata = dist.metadata
    name = str(metadata["Name"])
    texts: list[tuple[str, str]] = []
    shipped: list[str] = []
    for file in sorted(dist.files or (), key=lambda item: item.as_posix()):
        parts = file.parts
        in_dist_info = any(part.endswith(".dist-info") for part in parts[:-1])
        is_notice = _NOTICE_FILE_RE.match(file.name) is not None
        if not is_notice and not (in_dist_info and "licenses" in parts[:-1]):
            continue
        source = Path(str(dist.locate_file(file)))
        if not source.is_file():
            continue
        relative = f"{CONTENTS_DIR}/{file.as_posix()}"
        if not in_dist_info and (app_folder / relative).is_file():
            shipped.append(relative)
            continue
        texts.append((file.as_posix(), _read_text(source)))
    if not texts:
        texts.extend(_fallback_texts(metadata))
    return Notice(
        name=name,
        version=str(dist.version),
        license=_license_label(metadata),
        texts=tuple(texts),
        url=_home_page(metadata),
        shipped=tuple(shipped),
    )


def _fallback_texts(metadata: Any) -> list[tuple[str, str]]:
    """Texts for a distribution that ships no license file: a full-text License field, or the
    standard MIT text under the author named in the metadata."""
    declared = str(metadata.get("License") or "").strip()
    if "\n" in declared:
        return [("License (siêu dữ liệu của gói)", declared)]
    label = _license_label(metadata)
    author = str(metadata.get("Author") or metadata.get("Author-email") or "").strip()
    if re.fullmatch(r"MIT( License)?", label, re.IGNORECASE) and author:
        return [
            (
                "MIT License (gói không kèm tệp giấy phép; văn bản chuẩn của giấy phép khai "
                "trong siêu dữ liệu)",
                f"Copyright (c) {author}\n\n{_standard_text('MIT.txt')}",
            )
        ]
    return [("Giấy phép", f"{label} (gói không kèm tệp giấy phép).")]


def python_license_file(prefix: Path | None = None) -> Path:
    """LICENSE.txt of the Python the exe bundles (BuildError when it cannot be found)."""
    base = Path(sys.base_prefix) if prefix is None else prefix
    for candidate in (base / "LICENSE.txt", base / "LICENSE"):
        if candidate.is_file():
            return candidate
    raise BuildError(
        f"Không tìm thấy tệp giấy phép của Python trong {base}.",
        hint="Dùng một bản Python 3 đầy đủ (python.org hoặc uv python install).",
    )


def _bundled_names(sources: Iterable[str], pattern: str) -> list[str]:
    regex = re.compile(pattern, re.IGNORECASE)
    names = {Path(source).name for source in sources if regex.fullmatch(Path(source).name)}
    return sorted(names, key=str.lower)


def _dll_version(sources: Iterable[str], name: str) -> str | None:
    """File version of the bundled DLL `name` (Windows version resource), None if unknown."""
    from get_design_markdown.ui.browser_install import executable_version

    for source in sources:
        path = Path(source)
        if path.name.lower() == name.lower() and path.is_file():
            return executable_version(path)
    return None


def native_notices(sources: Sequence[str], *, python_license: Path | None = None) -> list[Notice]:
    """Python itself and the native libraries it or pywebview brings along."""
    major, minor = sys.version_info[:2]
    python_dlls = _bundled_names(sources, r"python3\d*\.dll|vcruntime140(_1)?\.dll")
    notices = [
        Notice(
            name="Python",
            version=platform.python_version(),
            license="PSF-2.0",
            url="https://www.python.org/",
            covers=", ".join([*python_dlls, "thư viện chuẩn (base_library.zip, *.pyd)"]),
            texts=(
                (
                    "LICENSE.txt của Python",
                    _read_text(python_license or python_license_file())
                    + "\n\nDanh sách đầy đủ phần mềm bên thứ ba có trong CPython: "
                    f"https://docs.python.org/{major}.{minor}/license.html",
                ),
            ),
        )
    ]
    openssl = _bundled_names(sources, r"lib(ssl|crypto)-\d+(-x64)?\.dll")
    if openssl:
        match = re.search(r"OpenSSL\s+(\S+)", ssl.OPENSSL_VERSION)
        notices.append(
            Notice(
                name="OpenSSL",
                version=match.group(1) if match else "3",
                license="Apache-2.0",
                url="https://www.openssl.org/",
                covers=", ".join(openssl),
                texts=(
                    (
                        "OpenSSL License (Apache License 2.0)",
                        "Copyright The OpenSSL Project Authors. All Rights Reserved.\n\n"
                        + _standard_text("OpenSSL-LICENSE.txt"),
                    ),
                ),
            )
        )
    libffi = _bundled_names(sources, r"libffi(-\d+)?\.dll")
    if libffi:
        notices.append(
            Notice(
                name="libffi",
                version="3",
                license="MIT",
                url="https://sourceware.org/libffi/",
                covers=", ".join(libffi),
                texts=(("libffi LICENSE", _standard_text("libffi-LICENSE.txt")),),
            )
        )
    webview2 = _bundled_names(sources, r"Microsoft\.Web\.WebView2\.[\w.]+\.dll|WebView2Loader\.dll")
    if webview2:
        notices.append(
            Notice(
                name="Microsoft Edge WebView2 SDK",
                version=_dll_version(sources, "Microsoft.Web.WebView2.Core.dll") or "1.0",
                license="BSD-3-Clause",
                url="https://www.nuget.org/packages/Microsoft.Web.WebView2",
                covers=", ".join(webview2) + " (đi kèm pywebview)",
                texts=(
                    ("LICENSE.txt", _standard_text("WebView2-SDK-LICENSE.txt")),
                    ("NOTICE.txt", _standard_text("WebView2-SDK-NOTICE.txt")),
                ),
            )
        )
    if _bundled_names(sources, r"sqlite3\.dll"):
        notices.append(
            Notice(
                name="SQLite",
                version="3",
                license="Public Domain",
                url="https://www.sqlite.org/copyright.html",
                covers="sqlite3.dll",
                texts=(("Bản quyền", "SQLite is in the public domain."),),
            )
        )
    return notices


def third_party_notices(
    sources: Sequence[str],
    app_folder: Path,
    *,
    distributions: Iterable[importlib.metadata.Distribution] | None = None,
    python_license: Path | None = None,
) -> list[Notice]:
    """Every bundled component: Python and native libraries first, then the distributions."""
    notices = native_notices(sources, python_license=python_license)
    notices += [
        distribution_notice(dist, app_folder)
        for dist in bundled_distributions(sources, distributions)
    ]
    return notices


def render_notices(version: str, notices: Sequence[Notice]) -> str:
    """THIRD-PARTY-NOTICES.txt (LF line endings; written with CRLF)."""
    lines = [
        f"Get Design Markdown {version} — Thông báo về phần mềm bên thứ ba",
        "(Third-party notices)",
        _RULE,
        "",
        "Get Design Markdown là phần mềm tự do theo GPL-3.0-or-later (xem LICENSE.txt). Bản exe",
        "này kèm theo các thành phần mã nguồn mở dưới đây; mỗi mục ghi tên, phiên bản, giấy phép",
        "và nguyên văn thông báo bản quyền, giấy phép mà thành phần đó được phân phối kèm.",
        "",
        "This Windows build bundles the open-source components listed below, each with its",
        "version, license and the copyright and license notices it is distributed under.",
        "",
        f"Thành phần ({len(notices)}):",
    ]
    lines += [f"  - {notice.name} {notice.version} — {notice.license}" for notice in notices]
    for notice in notices:
        lines += ["", "", _RULE, f"{notice.name} {notice.version}", f"License: {notice.license}"]
        if notice.url:
            lines.append(f"Home page: {notice.url}")
        if notice.covers:
            lines.append(f"Trong bản exe: {notice.covers}")
        for title, text in notice.texts:
            lines += [_THIN_RULE, f"[{title}]", "", text.rstrip()]
        if notice.shipped:
            lines += [_THIN_RULE, "Các thông báo khác của thành phần này nằm sẵn trong thư mục:"]
            lines += [f"  {path}" for path in notice.shipped]
    return "\n".join(lines) + "\n"


def check_notices(notices: Sequence[Notice], required: Sequence[str] = REQUIRED_NOTICES) -> None:
    """BuildError when a required component is missing or one has no notice text at all."""
    names = {canonical_name(notice.name) for notice in notices}
    missing = [name for name in required if canonical_name(name) not in names]
    if missing:
        raise BuildError(
            f"{NOTICES_NAME} thiếu thành phần: {', '.join(missing)}.",
            hint="Kiểm tra packaging/gdm-ui.spec (bundled-sources.json) và packaging/licenses.",
        )
    empty = [notice.name for notice in notices if not notice.texts and not notice.shipped]
    if empty:
        raise BuildError(f"{NOTICES_NAME}: không có văn bản giấy phép cho {', '.join(empty)}.")


def write_third_party_notices(
    target: Path,
    version: str,
    sources_file: Path,
    *,
    required: Sequence[str] = REQUIRED_NOTICES,
) -> list[Notice]:
    """Write THIRD-PARTY-NOTICES.txt next to the exe (UTF-8 with BOM, CRLF, like README.txt)."""
    notices = third_party_notices(load_bundled_sources(sources_file), target)
    check_notices(notices, required)
    text = render_notices(version, notices)
    (target / NOTICES_NAME).write_text(
        "\r\n".join(text.splitlines()) + "\r\n", encoding="utf-8-sig", newline=""
    )
    return notices


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
    notices = write_third_party_notices(folder, version, WORK_DIR / BUNDLED_SOURCES_FILE)
    say(f"Đã ghi {NOTICES_NAME}: {len(notices)} thành phần bên thứ ba.")
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
