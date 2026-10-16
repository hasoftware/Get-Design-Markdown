"""Settings of the local UI (saved per user) and the folders the UI uses.

Frozen interface: UiSettings and its bounds, the folder helpers (implemented here) and the
signatures of load_ui_settings / save_ui_settings.
To implement (the stubs raise NotImplementedYetError): load_ui_settings, save_ui_settings.

Folders (every helper returns an absolute path and creates nothing):

- config_dir(): $GDM_UI_CONFIG_DIR when set (tests always set it and must never touch the real
  folder), else
    Windows  %APPDATA%\\get-design-markdown
    macOS    ~/Library/Application Support/get-design-markdown
    others   $XDG_CONFIG_HOME/get-design-markdown, or ~/.config/get-design-markdown
- settings_path(): config_dir() / "ui-settings.json".
- log_dir(): $GDM_UI_LOG_DIR when set, else
    Windows  %LOCALAPPDATA%\\get-design-markdown\\logs
    macOS    ~/Library/Logs/get-design-markdown
    others   $XDG_STATE_HOME/get-design-markdown/logs, or ~/.local/state/get-design-markdown/logs
  The windowed exe writes log_dir() / "ui.log" (see ui/app.py).
- default_output_dir(config): the frozen exe uses <Documents>/Get-Design-Markdown (the user's
  Documents folder, also when it was moved, e.g. into OneDrive); `gdm ui` uses config.output_dir
  (GDM_OUTPUT_DIR, or ./output) made absolute against the current directory.

ui-settings.json is UiSettings.model_dump_json(indent=2): a JSON object with schema_version and
the fields below; output_dir is stored as an absolute path string.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field

from get_design_markdown.analyzer.colors import DEFAULT_DELTA_E
from get_design_markdown.config import Settings, load_settings
from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.base import GdmModel
from get_design_markdown.models.raw import BrowserChoice
from get_design_markdown.resources import is_frozen

APP_DIR_NAME = "get-design-markdown"
SETTINGS_FILE = "ui-settings.json"
LOG_FILE = "ui.log"
#: Folder created in the user's Documents for the exe's results.
EXE_OUTPUT_DIR_NAME = "Get-Design-Markdown"
CONFIG_DIR_ENV = "GDM_UI_CONFIG_DIR"
LOG_DIR_ENV = "GDM_UI_LOG_DIR"
UI_SETTINGS_SCHEMA_VERSION = 1

UiLang = Literal["en", "vi"]

# Bounds shared by UiSettings and ui.jobs.JobRequest (the frontend shows the same limits).
MIN_PAGES, MAX_PAGES = 0, 10
MIN_MAX_ELEMENTS, MAX_MAX_ELEMENTS = 100, 20000
MIN_TIMEOUT_S, MAX_TIMEOUT_S = 5.0, 600.0
MIN_DELTA_E, MAX_DELTA_E = 0.5, 20.0  # same range as `gdm analyze --delta-e`


class UiSettings(GdmModel):
    """What the user can change in the UI; every job starts from these values.

    Defaults come from UiSettings.defaults() (environment / .env through config.Settings),
    the saved file overrides them, and a job request may override them once (JobRequest).
    """

    schema_version: int = UI_SETTINGS_SCHEMA_VERSION
    #: Root folder of the results: <output_dir>/<domain>/DESIGN.md ...
    output_dir: Path
    #: Extra sub-pages found in the navigation (`--pages`).
    pages: int = Field(default=0, ge=MIN_PAGES, le=MAX_PAGES)
    #: Also capture prefers-color-scheme: dark (`--dark`).
    dark: bool = False
    #: Language of the DESIGN.md prose (`--lang`).
    lang: UiLang = "en"
    #: Also write prompt.md + resized screenshots (`--prompt-only`).
    prompt_only: bool = False
    #: Add the ex-* example surfaces to DESIGN.md (`--examples`).
    examples: bool = False
    #: Elements whose computed style is captured per page (`--max-elements`).
    max_elements: int = Field(default=3000, ge=MIN_MAX_ELEMENTS, le=MAX_MAX_ELEMENTS)
    #: Page load timeout in seconds (GDM_TIMEOUT).
    timeout_s: float = Field(default=45.0, ge=MIN_TIMEOUT_S, le=MAX_TIMEOUT_S)
    #: CIEDE2000 colour clustering threshold (`gdm analyze --delta-e`).
    delta_e: float = Field(default=DEFAULT_DELTA_E, ge=MIN_DELTA_E, le=MAX_DELTA_E)
    #: Browser for the capture (`--browser`, GDM_BROWSER).
    browser: BrowserChoice = "auto"
    #: When a job succeeds, the frontend switches to the result view by itself ("Tự mở kết
    #: quả"); when False it stays on the progress view with a "Xem kết quả" button. The server
    #: never opens an external browser for a job (run_get(open_browser=False)).
    open_after: bool = True

    @classmethod
    def defaults(cls, config: Settings | None = None, *, frozen: bool | None = None) -> UiSettings:
        """Defaults from config.Settings (load_settings() when None): output folder from
        default_output_dir(), max_elements / timeout_s / browser from the environment (clamped
        to the UI bounds), everything else as in the CLI."""
        config = config if config is not None else load_settings()
        return cls(
            output_dir=default_output_dir(config, frozen=frozen),
            max_elements=_clamp_int(config.max_elements, MIN_MAX_ELEMENTS, MAX_MAX_ELEMENTS),
            timeout_s=_clamp_float(config.timeout_s, MIN_TIMEOUT_S, MAX_TIMEOUT_S),
            browser=config.browser,
        )


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ─── Folders ─────────────────────────────────────────────────────────────────


def _env_dir(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser().resolve() if value else None


def _platform() -> str:
    """sys.platform (a function so tests can simulate other systems)."""
    return sys.platform


def _home() -> Path:
    return Path.home()


def config_dir() -> Path:
    """Per-user folder of ui-settings.json (see the module docstring)."""
    override = _env_dir(CONFIG_DIR_ENV)
    if override is not None:
        return override
    if _platform() == "win32":
        appdata = os.environ.get("APPDATA", "").strip()
        base = Path(appdata) if appdata else _home() / "AppData" / "Roaming"
        return (base / APP_DIR_NAME).resolve()
    if _platform() == "darwin":
        return (_home() / "Library" / "Application Support" / APP_DIR_NAME).resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else _home() / ".config"
    return (base / APP_DIR_NAME).resolve()


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILE


def log_dir() -> Path:
    """Folder of ui.log (see the module docstring)."""
    override = _env_dir(LOG_DIR_ENV)
    if override is not None:
        return override
    if _platform() == "win32":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else _home() / "AppData" / "Local"
        return (base / APP_DIR_NAME / "logs").resolve()
    if _platform() == "darwin":
        return (_home() / "Library" / "Logs" / APP_DIR_NAME).resolve()
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg) if xdg else _home() / ".local" / "state"
    return (base / APP_DIR_NAME / "logs").resolve()


def documents_dir() -> Path:
    """The user's Documents folder (Windows known folder, so a OneDrive move is followed);
    falls back to ~/Documents, or the home folder when that does not exist."""
    if _platform() == "win32":
        known = _windows_documents()
        if known is not None:
            return known
    documents = _home() / "Documents"
    return documents if documents.is_dir() else _home()


def _windows_documents() -> Path | None:
    if sys.platform != "win32":  # pragma: no cover - keeps mypy's platform checks happy
        return None
    import ctypes
    import uuid
    from ctypes import wintypes

    class _Guid(ctypes.Structure):
        _fields_ = (
            ("data1", wintypes.DWORD),
            ("data2", wintypes.WORD),
            ("data3", wintypes.WORD),
            ("data4", ctypes.c_ubyte * 8),
        )

    # FOLDERID_Documents
    raw = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}").bytes_le
    guid = _Guid.from_buffer_copy(raw)
    buffer = ctypes.c_wchar_p()
    try:
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        result = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buffer))
        try:
            if result != 0 or not buffer.value:
                return None
            return Path(buffer.value)
        finally:
            ole32.CoTaskMemFree(buffer)
    except (AttributeError, OSError):
        return None


def default_output_dir(config: Settings | None = None, *, frozen: bool | None = None) -> Path:
    """Results root used until the user picks another one (see the module docstring)."""
    exe = is_frozen() if frozen is None else frozen
    if exe:
        return (documents_dir() / EXE_OUTPUT_DIR_NAME).resolve()
    config = config if config is not None else load_settings()
    return config.output_dir.expanduser().resolve()


# ─── Persistence (to implement) ──────────────────────────────────────────────


def load_ui_settings(path: Path, defaults: UiSettings) -> UiSettings:
    """The saved settings at `path` laid over `defaults`; never raises for file problems.

    - Missing file: `defaults`.
    - Not readable / not JSON / not an object: `defaults` (the file is left in place and a
      warning is logged with the `logging` module).
    - Unknown keys (written by a newer version) are ignored; a key whose value fails
      validation keeps its default (other keys still apply); schema_version is not trusted.
    - A relative output_dir is resolved against the user's home folder.
    """
    raise NotImplementedYetError("Chưa đọc được cấu hình giao diện.")


def save_ui_settings(settings: UiSettings, path: Path) -> Path:
    """Write `settings` to `path` atomically (storage.save_model), creating its folder.

    output_dir is written as an absolute path. Returns `path`. An OSError becomes a
    ConfigError with a Vietnamese message naming the file and a hint.
    """
    raise NotImplementedYetError("Chưa lưu được cấu hình giao diện.")
