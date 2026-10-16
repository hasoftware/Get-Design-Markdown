"""History of analysed sites: the site folders under the output root.

Frozen interface: HistoryEntry, is_valid_site_name(), THUMBNAIL_PREFERENCE and the signatures
of read_entry / list_history.

A site folder is a direct child of the output root whose name is valid (is_valid_site_name) and
that holds at least one of raw.json, analysis.json or DESIGN.md. Reading the history never
raises for a damaged or foreign folder: the entry falls back to what can be read.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, TypeAdapter, ValidationError

from get_design_markdown.models.analysis import SiteInfo
from get_design_markdown.models.base import GdmModel
from get_design_markdown.storage import (
    ANALYSIS_FILE,
    DESIGN_FILE,
    PREVIEW_FILE,
    PROMPT_FILE,
    RAW_FILE,
    SCREENSHOTS_DIR,
    SYNTHESIS_FILE,
    TOKENS_FILE,
)

logger = logging.getLogger("get_design_markdown.ui")

#: Folder names storage.domain_of() produces: lowercase host (IDNA), "_<port>", "_" for
#: unsafe characters. Never "." / "..", never a leading dot, no separators.
SITE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,252}$")

#: Screenshot used as the thumbnail, first existing wins (paths relative to the site folder);
#: otherwise the first screenshots/*-1440.png, otherwise none.
THUMBNAIL_PREFERENCE = (
    "screenshots/home-1440.png",
    "screenshots/home-768.png",
    "screenshots/home-390.png",
)

#: Files whose presence makes a folder a site folder.
SITE_MARKERS = (RAW_FILE, ANALYSIS_FILE, DESIGN_FILE)
#: Files whose presence or change alters an entry (the cache key watches them).
_WATCHED_FILES = (
    RAW_FILE,
    ANALYSIS_FILE,
    DESIGN_FILE,
    PREVIEW_FILE,
    PROMPT_FILE,
    TOKENS_FILE,
    SYNTHESIS_FILE,
)
#: Entries kept by the list_history cache (one per site folder).
_CACHE_LIMIT = 1024


def is_valid_site_name(name: str) -> bool:
    """True for a folder name the UI may serve (/api/results/{site}, /files/{site}/...)."""
    return bool(SITE_NAME_RE.fullmatch(name)) and name not in (".", "..")


class HistoryEntry(GdmModel):
    """One analysed site as the history sidebar shows it."""

    #: Folder name under the output root, e.g. "stripe.com", "localhost_8000".
    site: str
    #: Display name: analysis.json site.name, else the folder name.
    name: str
    #: analysis.json site.url, else None.
    url: str | None = None
    #: analysis.json analyzed_at, else the newest modification time of the folder's
    #: raw.json / analysis.json / DESIGN.md (UTC).
    analyzed_at: datetime | None = None
    #: analysis.json site.pages_analyzed ("home", "pricing"...).
    pages: list[str] = Field(default_factory=list)
    has_raw: bool = False
    has_analysis: bool = False
    has_design: bool = False
    has_preview: bool = False
    has_prompt: bool = False
    has_tokens: bool = False
    #: Thumbnail screenshot relative to the site folder (see THUMBNAIL_PREFERENCE), or None.
    thumbnail: str | None = None


# ─── Reading one folder ──────────────────────────────────────────────────────

_DATETIME = TypeAdapter(datetime)


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _analysis_summary(path: Path) -> tuple[SiteInfo | None, datetime | None]:
    """site and analyzed_at of analysis.json, each None when missing or unreadable.

    Only these two parts are validated, so an analysis.json written by another version (whose
    other sections no longer match the models) still names its site in the history.
    """
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        logger.debug("Unreadable %s: %s", path, error)
        return None, None
    if not isinstance(data, dict):
        return None, None
    site: SiteInfo | None = None
    analyzed_at: datetime | None = None
    section = data.get("site")
    if isinstance(section, dict):
        # Fields added by a newer version are ignored rather than failing the whole section.
        known = {key: value for key, value in section.items() if key in SiteInfo.model_fields}
        try:
            site = SiteInfo.model_validate(known)
        except ValidationError:
            logger.debug("Invalid site section in %s", path)
    try:
        analyzed_at = _as_utc(_DATETIME.validate_python(data.get("analyzed_at")))
    except ValidationError:
        logger.debug("Invalid analyzed_at in %s", path)
    return site, analyzed_at


def _as_utc(moment: datetime) -> datetime:
    """Aware UTC datetime (a naive one is taken as UTC)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _newest_mtime(folder: Path, names: tuple[str, ...]) -> datetime | None:
    newest: float | None = None
    for name in names:
        try:
            mtime = (folder / name).stat().st_mtime
        except OSError:
            continue
        newest = mtime if newest is None else max(newest, mtime)
    return datetime.fromtimestamp(newest, UTC) if newest is not None else None


def recorded_prompt(folder: Path) -> tuple[str | None, list[str]] | None:
    """What the latest synthesis wrote for an AI assistant, from synthesis.json: (prompt_path,
    image_paths) relative to the folder with "/" separators ("prompt.md",
    ["prompt-images/desktop.png"]); prompt_path is None when it ran without --prompt-only, so a
    prompt.md left by an earlier run is not current. None when synthesis.json is missing or
    unreadable (callers then go by the files present)."""
    try:
        data: Any = json.loads((folder / SYNTHESIS_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        logger.debug("Unreadable %s: %s", folder / SYNTHESIS_FILE, error)
        return None
    if not isinstance(data, dict):
        return None
    prompt = data.get("prompt_path")
    if prompt is not None and not isinstance(prompt, str):
        return None
    images = data.get("image_paths")
    image_paths = (
        [item.replace("\\", "/") for item in images if isinstance(item, str) and item]
        if isinstance(images, list)
        else []
    )
    return (prompt.replace("\\", "/") if prompt else None), image_paths


def _thumbnail(folder: Path) -> str | None:
    for relative in THUMBNAIL_PREFERENCE:
        if _is_file(folder / relative):
            return relative
    try:
        candidates = sorted(
            path.name
            for path in (folder / SCREENSHOTS_DIR).glob("*-1440.png")
            if not path.name.startswith(".") and _is_file(path)
        )
    except OSError:
        return None
    return f"{SCREENSHOTS_DIR}/{candidates[0]}" if candidates else None


def read_entry(folder: Path) -> HistoryEntry | None:
    """The entry of one site folder, or None when it is not a site folder (see the module
    docstring). Reads analysis.json only (never raw.json, which can weigh tens of MB);
    an unreadable analysis.json leaves name = folder name and url/pages empty."""
    if not is_valid_site_name(folder.name):
        return None
    try:
        if not folder.is_dir():
            return None
    except OSError:
        return None
    present = {name for name in _WATCHED_FILES if _is_file(folder / name)}
    markers = tuple(name for name in SITE_MARKERS if name in present)
    if not markers:
        return None
    site, analyzed_at = (
        _analysis_summary(folder / ANALYSIS_FILE) if ANALYSIS_FILE in present else (None, None)
    )
    if analyzed_at is None:
        analyzed_at = _newest_mtime(folder, markers)
    name = site.name.strip() if site is not None and site.name.strip() else folder.name
    has_prompt = PROMPT_FILE in present
    if has_prompt and SYNTHESIS_FILE in present:
        # A prompt.md that the latest synthesis did not write belongs to an earlier run.
        recorded = recorded_prompt(folder)
        has_prompt = recorded is None or recorded[0] is not None
    return HistoryEntry(
        site=folder.name,
        name=name,
        url=site.url if site is not None else None,
        analyzed_at=analyzed_at,
        pages=list(site.pages_analyzed) if site is not None else [],
        has_raw=RAW_FILE in present,
        has_analysis=ANALYSIS_FILE in present,
        has_design=DESIGN_FILE in present,
        has_preview=PREVIEW_FILE in present,
        has_prompt=has_prompt,
        has_tokens=TOKENS_FILE in present,
        thumbnail=_thumbnail(folder),
    )


# ─── Listing the output root ─────────────────────────────────────────────────

#: Stat of a watched file: (mtime_ns, size), or None when it is missing.
_FileStamp = tuple[int, int] | None
_CacheKey = tuple[_FileStamp, ...]

_cache: dict[str, tuple[_CacheKey, HistoryEntry | None]] = {}
_cache_lock = threading.Lock()


def _stamp(path: Path) -> _FileStamp:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _cache_key(folder: Path) -> _CacheKey:
    """What read_entry depends on: the watched files and the screenshots folder (whose
    modification time changes when screenshots are added or removed)."""
    stamps = [_stamp(folder / name) for name in _WATCHED_FILES]
    stamps.append(_stamp(folder / SCREENSHOTS_DIR))
    return tuple(stamps)


def _cached_entry(folder: Path) -> HistoryEntry | None:
    key = _cache_key(folder)
    cache_id = os.path.normcase(str(folder))
    with _cache_lock:
        hit = _cache.get(cache_id)
    if hit is not None and hit[0] == key:
        return hit[1]
    entry = read_entry(folder)
    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[cache_id] = (key, entry)
    return entry


def clear_cache() -> None:
    """Forget the cached entries (tests; the cache also refreshes by itself on changes)."""
    with _cache_lock:
        _cache.clear()


def _sort_key(entry: HistoryEntry) -> tuple[float, str]:
    """Newest first, then by folder name (A to Z); entries without a date last."""
    timestamp = entry.analyzed_at.timestamp() if entry.analyzed_at is not None else float("-inf")
    return (-timestamp, entry.site)


def list_history(output_root: Path) -> list[HistoryEntry]:
    """Every site folder under `output_root`, newest first (analyzed_at, then folder name).

    A missing output root gives []. May cache entries by (folder, file mtimes).
    """
    try:
        children = list(os.scandir(output_root))
    except OSError:
        return []
    entries: list[HistoryEntry] = []
    for child in children:
        if not is_valid_site_name(child.name):
            continue
        try:
            if not child.is_dir():
                continue
            entry = _cached_entry(Path(child.path))
        except OSError as error:
            logger.debug("Skipping %s: %s", child.path, error)
            continue
        if entry is not None:
            entries.append(entry)
    entries.sort(key=_sort_key)
    return entries
