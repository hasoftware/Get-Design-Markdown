"""Find important sub-pages from the navigation of a captured page (--pages N)
(stream: collector).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from get_design_markdown.errors import InvalidUrlError
from get_design_markdown.models.raw import ElementSample, PageCapture
from get_design_markdown.storage import normalize_url, page_slug

#: Paths that are not design references: auth, commerce, legal and job/status pages.
EXCLUDED_PATH_RE = re.compile(
    r"(log-?in|sign-?in|sign-?up|register|logout|account|auth|cart|checkout|legal|privacy|"
    r"terms|cookies?|careers|jobs|status)",
    re.IGNORECASE,
)
FILE_EXTENSIONS = (
    ".pdf",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".avif",
    ".ico",
    ".mp4",
    ".webm",
    ".mov",
    ".mp3",
    ".xml",
    ".json",
    ".txt",
    ".rss",
    ".csv",
    ".dmg",
    ".exe",
    ".gz",
)
_TRACKING_PARAMS_RE = re.compile(r"^(utm_.*|ref|gclid|fbclid|mc_cid|mc_eid|_hs.*)$", re.I)
#: Nav links within this distance from the top count as the header navigation.
HEADER_AREA_PX = 200.0


@dataclass
class _Link:
    url: str
    key: str
    score: float
    order: int
    occurrences: int = 1


def _host(netloc_host: str | None) -> str:
    return (netloc_host or "").lower().removeprefix("www.")


def _same_site(first: str, second: str) -> bool:
    a, b = urlsplit(first), urlsplit(second)
    return _host(a.hostname) == _host(b.hostname) and a.port == b.port


def _clean_query(query: str) -> str:
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)]
    kept = [(k, v) for k, v in pairs if not _TRACKING_PARAMS_RE.match(k)]
    return urlencode(kept)


def candidate_url(href: str, *, base_url: str) -> str | None:
    """Absolute, normalized http(s) URL for a link, or None when it is not a page link; pure."""
    raw = href.strip()
    if not raw or raw.lower().startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
        return None
    absolute = urljoin(base_url, raw)
    parts = urlsplit(absolute)
    if parts.scheme.lower() not in ("http", "https"):
        return None
    path = parts.path or "/"
    if path.lower().endswith(FILE_EXTENSIONS):
        return None
    cleaned = urlunsplit((parts.scheme, parts.netloc, path, _clean_query(parts.query), ""))
    try:
        return normalize_url(cleaned)
    except InvalidUrlError:
        return None


def url_key(url: str) -> str:
    """Comparison key: host without www., path without trailing slash, query; pure."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    port = f":{parts.port}" if parts.port else ""
    return f"{_host(parts.hostname)}{port}{path}?{parts.query}"


def is_excluded_path(url: str) -> bool:
    return bool(EXCLUDED_PATH_RE.search(urlsplit(url).path))


def link_score(element: ElementSample, url: str) -> float:
    """Importance of one link occurrence: header nav > other nav > header > body > footer."""
    score = 0.0
    if element.landmark == "nav":
        score += 3.0 if element.box.y < HEADER_AREA_PX else 2.0
    elif element.landmark == "header":
        score += 1.0
    elif element.landmark == "footer":
        score -= 1.0
    segments = [part for part in urlsplit(url).path.split("/") if part]
    if len(segments) == 1:
        score += 1.0
    return score


def _links(page: PageCapture) -> Iterable[tuple[ElementSample, str]]:
    base = page.final_url or page.url
    for element in page.elements:
        if element.tag not in ("a", "area") or not element.href or element.in_overlay:
            continue
        url = candidate_url(element.href, base_url=base)
        if url is not None:
            yield element, url


def discover_pages(page: PageCapture, *, limit: int) -> list[str]:
    """Up to `limit` absolute same-site URLs ranked by importance (header nav first); pure.

    Excludes the page itself, fragments, mailto/tel/javascript links, files (pdf, zip, images),
    login/sign-up/legal pages and duplicates after normalization.
    """
    if limit <= 0:
        return []
    base = page.final_url or page.url
    own_keys = {url_key(normalize_url(page.url)), url_key(normalize_url(base))}
    found: dict[str, _Link] = {}
    for order, (element, url) in enumerate(_links(page)):
        if not _same_site(url, base) or is_excluded_path(url):
            continue
        key = url_key(url)
        if key in own_keys:
            continue
        score = link_score(element, url)
        link = found.get(key)
        if link is None:
            found[key] = _Link(url=url, key=key, score=score, order=order)
        else:
            link.occurrences += 1
            link.score = max(link.score, score)
    ranked = sorted(
        found.values(),
        key=lambda link: (-(link.score + 0.5 * (link.occurrences - 1)), link.order),
    )
    return [link.url for link in ranked[:limit]]


def unique_page_name(url: str, taken: Iterable[str]) -> str:
    """page_slug(url) made unique against `taken` with -2, -3...; pure."""
    used = set(taken)
    base = page_slug(url)
    if base not in used:
        return base
    number = 2
    while f"{base}-{number}" in used:
        number += 1
    return f"{base}-{number}"
