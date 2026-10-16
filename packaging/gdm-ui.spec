# PyInstaller spec of the Windows exe: Get-Design-Markdown.exe (onedir, windowed).
#
# Build it with scripts/build_exe.py (checks the environment, runs this spec, self-tests the
# exe and zips it):
#     python -m uv run --group build --extra gui python scripts/build_exe.py --zip
#
# - Entry point: packaging/launcher.py -> get_design_markdown.ui.app.main().
# - Package data (collector JS, phrase YAML, Jinja templates, ui/static) is collected under
#   _internal/get_design_markdown/, where resources.package_dir() looks when frozen.
# - Playwright's driver (node + its JS package) is bundled through Playwright's own PyInstaller
#   hook; no browser is: the exe uses Edge / Chrome, or downloads Playwright's Chromium into the
#   user's ms-playwright folder (`Get-Design-Markdown.exe install-browser`).
# - pywebview + pythonnet + clr_loader come with their hooks (native window over WebView2).
# - The icon is drawn at build time by packaging/make_icon.py; the version resource comes from
#   get_design_markdown.__version__.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

PACKAGING_DIR = Path(SPECPATH).resolve()
ROOT = PACKAGING_DIR.parent
sys.path.insert(0, str(PACKAGING_DIR))

from make_icon import make_icon  # noqa: E402

import get_design_markdown  # noqa: E402

APP_NAME = "Get-Design-Markdown"
VERSION = get_design_markdown.__version__
WORK_DIR = Path(workpath)

# ─── What goes in ────────────────────────────────────────────────────────────

hiddenimports = [
    name
    for name in collect_submodules("get_design_markdown")
    if not name.endswith(".__main__")
]
# The native window: pywebview's WinForms platform (WebView2 through pythonnet). It is imported
# lazily by the app, so name the modules explicitly.
hiddenimports += [
    "webview",
    "webview._version",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "webview.platforms.mshtml",
    "webview.platforms.win32",
    "clr",
    "clr_loader",
]
datas = collect_data_files("get_design_markdown")

excludes = [
    # Development and test tools.
    "pytest",
    "_pytest",
    "mypy",
    "ruff",
    "tests",
    "IPython",
    # GUI toolkits the window does not use on Windows (pywebview uses WinForms + WebView2).
    "tkinter",
    "webview.platforms.qt",
    "webview.platforms.gtk",
    "webview.platforms.cocoa",
    "webview.platforms.android",
    "webview.platforms.cef",
    "qtpy",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "gi",
]

a = Analysis(
    [str(PACKAGING_DIR / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# Never ship a browser build, even when Playwright was installed with its browsers inside the
# package (PLAYWRIGHT_BROWSERS_PATH=0).
a.datas = [
    entry
    for entry in a.datas
    if ".local-browsers" not in Path(entry[0]).parts and "ms-playwright" not in Path(entry[0]).parts
]

pyz = PYZ(a.pure)

# ─── Icon and version resource ───────────────────────────────────────────────

icon_path = make_icon(WORK_DIR / "icon.ico")


def _version_tuple(text):
    numbers = [int(part) for part in text.split(".") if part.isdigit()][:4]
    return tuple(numbers + [0] * (4 - len(numbers)))


version_numbers = _version_tuple(VERSION)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=version_numbers,
        prodvers=version_numbers,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "hasoftware"),
                        StringStruct(
                            "FileDescription", "Get Design Markdown - design reference extractor"
                        ),
                        StringStruct("FileVersion", VERSION),
                        StringStruct("InternalName", APP_NAME),
                        StringStruct("LegalCopyright", "GPL-3.0-or-later"),
                        StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                        StringStruct("ProductName", "Get Design Markdown"),
                        StringStruct("ProductVersion", VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

# ─── Executable and folder ───────────────────────────────────────────────────

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
