"""Output layout (output/<domain>/...) and JSON persistence of stage models."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import BaseModel, ValidationError

from get_design_markdown.errors import InvalidDataError, InvalidUrlError, OutputNotFoundError

RAW_FILE = "raw.json"
ANALYSIS_FILE = "analysis.json"
SYNTHESIS_FILE = "synthesis.json"
DESIGN_FILE = "DESIGN.md"
PROMPT_FILE = "prompt.md"
TOKENS_FILE = "tokens.json"
PREVIEW_FILE = "preview.html"
SCREENSHOTS_DIR = "screenshots"
#: Resized screenshots referenced by prompt.md (--prompt-only).
PROMPT_IMAGES_DIR = "prompt-images"

M = TypeVar("M", bound=BaseModel)

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_UNSAFE_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def normalize_url(url: str) -> str:
    """Return an absolute http(s) URL, adding https:// when no scheme is given."""
    candidate = url.strip()
    if not candidate:
        raise InvalidUrlError("URL đang để trống.", hint="Ví dụ: gdm collect https://stripe.com")
    if not _SCHEME_RE.match(candidate):
        candidate = "https://" + candidate
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidUrlError(
            f"Chỉ hỗ trợ URL http/https, không hỗ trợ '{parts.scheme}://'.",
            hint="Ví dụ: https://stripe.com",
        )
    host = parts.hostname
    if not host or ("." not in host and host != "localhost"):
        raise InvalidUrlError(
            f"URL không hợp lệ: {url!r} (thiếu tên miền).", hint="Ví dụ: https://stripe.com"
        )
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def domain_of(url: str) -> str:
    """Folder-safe domain name: lowercase host without 'www.', port kept as '_<port>'."""
    parts = urlsplit(normalize_url(url))
    host = (parts.hostname or "").removeprefix("www.")
    name = f"{host}_{parts.port}" if parts.port else host
    return _UNSAFE_PATH_CHARS.sub("_", name)


def site_dir(output_root: Path, url: str) -> Path:
    return output_root / domain_of(url)


def page_slug(url: str) -> str:
    """Short page name from the URL path: "/" -> "home", "/docs/api/" -> "docs-api"."""
    path = unquote(urlsplit(url).path).strip("/").lower()
    path = re.sub(r"\.(html?|php|aspx?)$", "", path)
    slug = re.sub(r"[^a-z0-9]+", "-", path).strip("-")
    return slug[:40].strip("-") or "home"


def screenshot_filename(page_name: str, viewport_width: int, color_scheme: str = "light") -> str:
    """Path relative to the site folder: "screenshots/home-1440.png", "...home-dark-1440.png"."""
    scheme = "-dark" if color_scheme == "dark" else ""
    return f"{SCREENSHOTS_DIR}/{page_name}{scheme}-{viewport_width}.png"


def resolve_site_dir(target: str, output_root: Path) -> Path:
    """Accept either an existing folder or a URL/domain mapped to output_root/<domain>."""
    path = Path(target)
    if path.is_dir():
        return path
    try:
        mapped = site_dir(output_root, target)
    except InvalidUrlError:
        mapped = None
    if mapped is not None and mapped.is_dir():
        return mapped
    raise OutputNotFoundError(
        f"Không tìm thấy thư mục dữ liệu '{target}'.",
        hint="Chạy 'gdm collect <url>' trước, rồi truyền thư mục output/<domain>.",
    )


def save_text(text: str, path: Path) -> Path:
    """Write UTF-8 text (LF newlines) atomically (temp file + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)
    return path


def save_model(model: BaseModel, path: Path) -> Path:
    """Write a model as pretty JSON atomically (temp file + replace)."""
    return save_text(model.model_dump_json(indent=2), path)


def save_json(data: Any, path: Path) -> Path:
    """Write plain JSON data (tokens.json...) atomically, UTF-8, 2-space indent."""
    return save_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", path)


def load_model(model_type: type[M], path: Path) -> M:
    if not path.is_file():
        raise OutputNotFoundError(
            f"Không tìm thấy file {path}.",
            hint=_missing_file_hint(path.name),
        )
    try:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "(gốc)"
        raise InvalidDataError(
            f"File {path} không đúng định dạng: {location} — {first['msg']} "
            f"({exc.error_count()} lỗi).",
            hint="File có thể được tạo bởi phiên bản cũ; hãy chạy lại bước tạo ra nó.",
        ) from None


def _missing_file_hint(filename: str) -> str:
    producers = {
        RAW_FILE: "gdm collect <url>",
        ANALYSIS_FILE: "gdm analyze <thư-mục>",
        SYNTHESIS_FILE: "gdm synthesize <thư-mục>",
    }
    command = producers.get(filename)
    return f"Chạy '{command}' trước." if command else "Kiểm tra lại đường dẫn."
