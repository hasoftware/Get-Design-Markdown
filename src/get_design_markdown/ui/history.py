"""History of analysed sites: the site folders under the output root.

Frozen interface: HistoryEntry, is_valid_site_name(), THUMBNAIL_PREFERENCE and the signatures
of read_entry / list_history.
To implement (the stubs raise NotImplementedYetError): read_entry, list_history.

A site folder is a direct child of the output root whose name is valid (is_valid_site_name) and
that holds at least one of raw.json, analysis.json or DESIGN.md. Reading the history never
raises for a damaged or foreign folder: the entry falls back to what can be read.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from pydantic import Field

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.base import GdmModel

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


# ─── To implement ────────────────────────────────────────────────────────────


def read_entry(folder: Path) -> HistoryEntry | None:
    """The entry of one site folder, or None when it is not a site folder (see the module
    docstring). Reads analysis.json only (never raw.json, which can weigh tens of MB);
    an unreadable analysis.json leaves name = folder name and url/pages empty."""
    raise NotImplementedYetError("Chưa đọc được lịch sử phân tích.")


def list_history(output_root: Path) -> list[HistoryEntry]:
    """Every site folder under `output_root`, newest first (analyzed_at, then folder name).

    A missing output root gives []. May cache entries by (folder, file mtimes).
    """
    raise NotImplementedYetError("Chưa đọc được lịch sử phân tích.")
