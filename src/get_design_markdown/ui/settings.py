"""Settings of the local UI (saved per user) and the folders the UI uses.

Frozen interface: UiSettings and its bounds, the folder helpers (implemented here) and the
signatures of load_ui_settings / save_ui_settings.

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

ui-settings.json is a JSON object with schema_version and UiSettings fields; output_dir is stored
as an absolute path string. The UI writes it with update_ui_settings(): only the fields the user
changed to a value other than its default are stored, so every other field keeps following the
defaults of each session (GDM_OUTPUT_DIR / the current folder, GDM_BROWSER, GDM_TIMEOUT,
GDM_MAX_ELEMENTS). save_ui_settings() writes every field (UiSettings.model_dump_json(indent=2)).
load_ui_settings() reads both forms.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from get_design_markdown.analyzer.colors import DEFAULT_DELTA_E
from get_design_markdown.config import Settings, load_settings
from get_design_markdown.errors import ConfigError
from get_design_markdown.models.base import GdmModel
from get_design_markdown.models.raw import BrowserChoice
from get_design_markdown.resources import is_frozen
from get_design_markdown.storage import save_json, save_model

logger = logging.getLogger("get_design_markdown.ui")

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


# ─── Persistence ─────────────────────────────────────────────────────────────


def absolute_output_dir(path: Path | str) -> Path:
    """`path` with "~" expanded, made absolute against the user's home folder (not the current
    directory, which is arbitrary for the exe) and normalised ("a/../b" -> "b")."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = _home() / candidate
    return Path(os.path.normpath(candidate))


def same_output_dir(first: Path | str, second: Path | str) -> bool:
    """True when two output folders name the same path (case-insensitive on Windows)."""
    return os.path.normcase(os.path.normpath(first)) == os.path.normcase(os.path.normpath(second))


def output_dir_problem(path: Path) -> str | None:
    """Why `path` (absolute) cannot hold the results, as a Vietnamese sentence; None when it can.

    The folder itself may not exist yet (jobs create it), but its drive must exist, its nearest
    existing parent must be a folder, and a folder must be creatable there: a temporary folder
    is created and removed again to find out (on Windows the access rights cannot be read
    reliably otherwise).
    """
    try:
        if path.exists() and not path.is_dir():
            return f"'{path}' là một tệp, không phải thư mục; hãy chọn một thư mục."
        anchor = path.anchor
        if anchor and not Path(anchor).exists():
            return f"Không tìm thấy ổ đĩa {anchor} trên máy này nên không lưu được vào '{path}'."
        existing = path
        while not existing.exists():
            if existing.parent == existing:
                return f"Không tìm thấy vị trí '{path}' trên máy này."
            existing = existing.parent
        if not existing.is_dir():
            return f"Không tạo được thư mục '{path}' vì '{existing}' là một tệp."
    except (OSError, ValueError):
        return f"Không dùng được '{path}' làm thư mục lưu kết quả."
    try:
        probe = tempfile.mkdtemp(prefix=".gdm-write-check-", dir=existing)
    except OSError as error:
        reason = error.strerror or type(error).__name__
        return f"Không có quyền tạo thư mục trong '{existing}' ({reason})."
    with contextlib.suppress(OSError):
        os.rmdir(probe)
    return None


def _output_dir_value(value: Any) -> Path | None:
    """A usable output_dir from the saved file, or None (blank or not a path string)."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    return absolute_output_dir(value)


def _read_document(path: Path) -> dict[str, Any] | None:
    """The JSON object stored at `path`; None when the file is missing or unusable (a warning
    is logged for an unusable one, which is left in place)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, ValueError) as error:
        logger.warning(
            "Không đọc được cấu hình giao diện %s (%s); dùng giá trị mặc định.", path, error
        )
        return None
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as error:
        logger.warning(
            "File cấu hình giao diện %s không phải JSON hợp lệ (%s); dùng giá trị mặc định.",
            path,
            error,
        )
        return None
    if not isinstance(data, dict):
        logger.warning(
            "File cấu hình giao diện %s không chứa một đối tượng JSON; dùng giá trị mặc định.",
            path,
        )
        return None
    return data


def _saved_choices(document: Mapping[str, Any], defaults: UiSettings, path: Path) -> dict[str, Any]:
    """The known fields of `document` whose value is valid over `defaults` (output_dir made
    absolute); every other key is skipped, an invalid value with a logged warning."""
    choices: dict[str, Any] = {}
    merged: dict[str, Any] = defaults.model_dump()
    for key, value in document.items():
        if key == "schema_version" or key not in UiSettings.model_fields:
            continue
        if key == "output_dir":
            value = _output_dir_value(value)
            if value is None:
                logger.warning(
                    "Bỏ qua output_dir không hợp lệ trong %s; dùng giá trị mặc định.", path
                )
                continue
        try:
            UiSettings.model_validate({**merged, key: value})
        except ValidationError:
            logger.warning(
                "Bỏ qua giá trị không hợp lệ của '%s' trong %s; dùng giá trị mặc định.", key, path
            )
            continue
        merged[key] = value
        choices[key] = value
    return choices


def _over_defaults(defaults: UiSettings, choices: Mapping[str, Any]) -> UiSettings:
    return UiSettings.model_validate(
        {**defaults.model_dump(), **choices, "schema_version": UI_SETTINGS_SCHEMA_VERSION}
    )


def load_ui_settings(path: Path, defaults: UiSettings) -> UiSettings:
    """The saved settings at `path` laid over `defaults`; never raises for file problems.

    - Missing file: `defaults`.
    - Not readable / not JSON / not an object: `defaults` (the file is left in place and a
      warning is logged with the `logging` module).
    - Unknown keys (written by a newer version) are ignored; a key whose value fails
      validation keeps its default (other keys still apply); schema_version is not trusted.
    - A relative output_dir is resolved against the user's home folder.
    """
    document = _read_document(path)
    if document is None:
        return defaults
    return _over_defaults(defaults, _saved_choices(document, defaults, path))


def _same_setting(name: str, first: Any, second: Any) -> bool:
    if name == "output_dir":
        return same_output_dir(first, second)
    return bool(first == second)


def update_ui_settings(path: Path, defaults: UiSettings, changes: Mapping[str, Any]) -> UiSettings:
    """Apply the user's `changes` (UiSettings field -> value) to the file at `path` and return
    the resulting settings (`defaults` + the stored choices).

    A changed field equal to its default is removed from the file, any other changed field is
    stored; the fields not in `changes` keep what the file says (keys of a newer version
    included), so a default the user never changed is never written and keeps following the
    environment. Raises pydantic.ValidationError for an invalid value (nothing is written) and
    ConfigError when the file cannot be written.
    """
    document = _read_document(path) or {}
    choices = _saved_choices(document, defaults, path)
    updated = _over_defaults(defaults, {**choices, **changes})
    stored = set(choices)
    for name in changes:
        if name not in UiSettings.model_fields or name == "schema_version":
            continue
        if _same_setting(name, getattr(updated, name), getattr(defaults, name)):
            stored.discard(name)
        else:
            stored.add(name)
    values = updated.model_dump(mode="json")
    values["output_dir"] = str(absolute_output_dir(updated.output_dir))
    content: dict[str, Any] = {"schema_version": UI_SETTINGS_SCHEMA_VERSION}
    content.update({name: values[name] for name in UiSettings.model_fields if name in stored})
    # Keys written by a newer version are kept for it.
    content.update(
        {
            key: value
            for key, value in document.items()
            if key not in UiSettings.model_fields and key not in content
        }
    )
    _write_config(lambda: save_json(content, path), path)
    return updated


def save_ui_settings(settings: UiSettings, path: Path) -> Path:
    """Write `settings` to `path` atomically (storage.save_model), creating its folder.

    output_dir is written as an absolute path. Returns `path`. An OSError becomes a
    ConfigError with a Vietnamese message naming the file and a hint.
    """
    stored = settings.model_copy(
        update={
            "schema_version": UI_SETTINGS_SCHEMA_VERSION,
            "output_dir": absolute_output_dir(settings.output_dir),
        }
    )
    _write_config(lambda: save_model(stored, path), path)
    return path


def _write_config(write: Callable[[], Path], path: Path) -> None:
    """Run `write` (an atomic storage.save_* call for `path`); OSError -> ConfigError."""
    try:
        write()
    except OSError as error:
        _remove_quietly(path.with_name(path.name + ".tmp"))
        reason = error.strerror or type(error).__name__
        raise ConfigError(
            f"Không lưu được cấu hình giao diện vào {path}: {reason}.",
            hint=(
                "Kiểm tra quyền ghi và dung lượng trống của thư mục đó, hoặc chọn một thư mục "
                f"cấu hình khác bằng biến môi trường {CONFIG_DIR_ENV}."
            ),
        ) from None


def _remove_quietly(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
