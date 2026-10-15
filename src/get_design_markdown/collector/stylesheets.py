"""Capture CSS responses and parse them with tinycss2.

Stylesheets are read from the network (page.on("response")) because the cssRules of
cross-origin stylesheets are unreadable from JavaScript. Inline <style> blocks (and
CSSOM-inserted rules of CSS-in-JS libraries) are read from the page.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import tinycss2
import tinycss2.ast
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Response

from get_design_markdown.analyzer.css_values import parse_media_query, split_top_level
from get_design_markdown.models.raw import CssVariable, FontFace, MediaQuery, StylesheetInfo

#: Responses larger than this are skipped (minified CSS bundles are far smaller).
MAX_STYLESHEET_BYTES = 5 * 1024 * 1024
#: Variables kept per stylesheet (design systems rarely declare more on :root).
MAX_VARIABLES = 2000
#: Grouping at-rules whose block contains rules that still apply to the page.
GROUPING_AT_RULES = frozenset(
    {"media", "supports", "layer", "container", "document", "-moz-document", "scope"}
)

_SPACE_RE = re.compile(r"\s+")
_ROOT_SELECTOR_RE = re.compile(r"^(?::root|html)(?![\w-])[^\s>+~]*$", re.IGNORECASE)
_INLINE_SCRIPT = """
() => {
  const blocks = [];
  const serialize = (sheet) => {
    try {
      return Array.from(sheet.cssRules || [], (rule) => rule.cssText).join("\\n");
    } catch (error) {
      return "";
    }
  };
  for (const style of document.querySelectorAll("style")) {
    if ((style.id || "").startsWith("gdm-")) continue;  // our own overlay-hiding style
    let text = style.textContent || "";
    if (!text.trim() && style.sheet) text = serialize(style.sheet);
    if (text.trim()) blocks.push(text);
  }
  for (const sheet of document.adoptedStyleSheets || []) {
    const text = serialize(sheet);
    if (text.trim()) blocks.push(text);
  }
  return blocks;
}
"""


# ─── Pure parsing ────────────────────────────────────────────────────────────


def normalize_media_query(prelude: str) -> str:
    """Lowercase, single spaces, "(min-width:768px)and" -> "(min-width: 768px) and"."""
    text = _SPACE_RE.sub(" ", prelude.strip().lower())
    text = re.sub(r"\(\s*", "(", text)
    text = re.sub(r"\s*\)", ")", text)
    text = re.sub(r"\s*:\s*", ": ", text)
    text = re.sub(r"\)\s*(and|or)\b", r") \1", text)
    text = re.sub(r"\b(and|or|not|only)\s*\(", r"\1 (", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*(<=|>=|<|>|=)\s*", r" \1 ", text)
    return _SPACE_RE.sub(" ", text).strip()


def media_query_from_prelude(prelude: str) -> MediaQuery:
    """MediaQuery from an @media prelude (uses css_values.parse_media_query)."""
    query = normalize_media_query(prelude)
    widths = parse_media_query(query)
    return MediaQuery(
        query=query,
        min_width_px=widths.min_width_px,
        max_width_px=widths.max_width_px,
        prefers_color_scheme=widths.prefers_color_scheme,
    )


def _serialize(nodes: Iterable[Any]) -> str:
    return str(tinycss2.serialize(list(nodes))).strip()


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _targets_root(selector_list: str) -> bool:
    return any(_ROOT_SELECTOR_RE.match(part.strip()) for part in split_top_level(selector_list))


def _has_nested_blocks(content: Sequence[Any] | None) -> bool:
    return bool(content) and any(
        getattr(token, "type", "") in ("{} block", "at-keyword") for token in content or ()
    )


@dataclass
class _ParseState:
    rule_count: int = 0
    parse_errors: int = 0
    #: name -> (priority, CssVariable); lower priority wins (top-level light rules first).
    variables: dict[str, tuple[int, CssVariable]] = field(default_factory=dict)
    media: dict[str, MediaQuery] = field(default_factory=dict)
    font_faces: list[FontFace] = field(default_factory=list)

    def add_variable(self, name: str, declared: str, priority: int) -> None:
        current = self.variables.get(name)
        if current is not None and current[0] <= priority:
            return
        if current is None and len(self.variables) >= MAX_VARIABLES:
            return
        self.variables[name] = (
            priority,
            CssVariable(name=name, declared=declared[:1000], source="stylesheet"),
        )

    def add_media(self, prelude: str) -> MediaQuery:
        media = media_query_from_prelude(prelude)
        existing = self.media.get(media.query)
        if existing is None:
            self.media[media.query] = media
            return media
        existing.occurrences += 1
        return existing


def _font_face(content: Sequence[Any] | None, url: str, state: _ParseState) -> FontFace | None:
    declarations = tinycss2.parse_declaration_list(
        content or [], skip_comments=True, skip_whitespace=True
    )
    values: dict[str, str] = {}
    for item in declarations:
        if isinstance(item, tinycss2.ast.ParseError):
            state.parse_errors += 1
        elif isinstance(item, tinycss2.ast.Declaration):
            values[item.lower_name] = _serialize(item.value)
    family = _unquote(values.get("font-family", ""))
    if not family:
        return None
    src = [part.strip() for part in split_top_level(values.get("src", "")) if part.strip()]
    return FontFace(
        family=family,
        src=src,
        weight=values.get("font-weight", "normal") or "normal",
        style=values.get("font-style", "normal") or "normal",
        display=values.get("font-display") or None,
        stylesheet_url=url,
    )


def _walk(
    nodes: Iterable[Any],
    state: _ParseState,
    *,
    url: str,
    in_media: bool,
    dark: bool,
    depth: int = 0,
) -> None:
    if depth > 12:
        return
    for node in nodes:
        if isinstance(node, tinycss2.ast.ParseError):
            state.parse_errors += 1
        elif isinstance(node, tinycss2.ast.QualifiedRule):
            state.rule_count += 1
            selector = _serialize(node.prelude)
            if _targets_root(selector):
                priority = (2 if dark or "dark" in selector.lower() else 0) + (1 if in_media else 0)
                for item in tinycss2.parse_blocks_contents(
                    node.content or [], skip_comments=True, skip_whitespace=True
                ):
                    if isinstance(item, tinycss2.ast.ParseError):
                        state.parse_errors += 1
                    elif isinstance(item, tinycss2.ast.Declaration) and item.name.startswith("--"):
                        state.add_variable(item.name, _serialize(item.value), priority)
            elif _has_nested_blocks(node.content):
                # CSS nesting: nested @media / style rules inside a style rule.
                nested = [
                    item
                    for item in tinycss2.parse_blocks_contents(
                        node.content or [], skip_comments=True, skip_whitespace=True
                    )
                    if not isinstance(item, tinycss2.ast.Declaration)
                ]
                _walk(nested, state, url=url, in_media=in_media, dark=dark, depth=depth + 1)
        elif isinstance(node, tinycss2.ast.AtRule):
            keyword = node.lower_at_keyword
            if keyword == "font-face":
                face = _font_face(node.content, url, state)
                if face is not None:
                    state.font_faces.append(face)
            elif keyword == "property":
                name = _serialize(node.prelude)
                if name.startswith("--"):
                    for item in tinycss2.parse_declaration_list(
                        node.content or [], skip_comments=True, skip_whitespace=True
                    ):
                        if isinstance(item, tinycss2.ast.Declaration) and (
                            item.lower_name == "initial-value"
                        ):
                            state.add_variable(name, _serialize(item.value), 4)
            elif keyword in GROUPING_AT_RULES and node.content is not None:
                nested_dark = dark
                nested_media = in_media
                if keyword == "media":
                    media = state.add_media(_serialize(node.prelude))
                    nested_dark = dark or media.prefers_color_scheme == "dark"
                    nested_media = True
                children = tinycss2.parse_blocks_contents(
                    node.content, skip_comments=True, skip_whitespace=True
                )
                _walk(
                    [item for item in children if not isinstance(item, tinycss2.ast.Declaration)],
                    state,
                    url=url,
                    in_media=nested_media,
                    dark=nested_dark,
                    depth=depth + 1,
                )


def parse_stylesheet(
    url: str, css_text: str, *, status: int | None = None, inline: bool = False
) -> StylesheetInfo:
    """Parse CSS with tinycss2: :root/html variables, @media (nested), @font-face, rule count."""
    state = _ParseState()
    rules = tinycss2.parse_stylesheet(css_text, skip_comments=True, skip_whitespace=True)
    _walk(rules, state, url=url, in_media=False, dark=False)
    return StylesheetInfo(
        url=url,
        inline=inline,
        status=status,
        size_bytes=len(css_text.encode("utf-8")),
        rule_count=state.rule_count,
        variables=[variable for _, variable in state.variables.values()],
        media_queries=list(state.media.values()),
        font_faces=state.font_faces,
        parse_errors=state.parse_errors,
    )


# ─── Recording ───────────────────────────────────────────────────────────────


def is_stylesheet_response(resource_type: str, content_type: str | None) -> bool:
    return resource_type == "stylesheet" or "text/css" in (content_type or "").lower()


@dataclass
class _Captured:
    url: str
    status: int | None
    text: str


class StylesheetRecorder:
    """Attach before navigation (page.on("response")) and collect every text/css response,
    including cross-origin ones whose cssRules are unreadable from JavaScript."""

    def __init__(self, page: Page, *, page_name: str = "home") -> None:
        self.page = page
        self.page_name = page_name
        self.warnings: list[str] = []
        self._captured: list[_Captured] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._seen: set[str] = set()
        page.on("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        try:
            resource_type = response.request.resource_type
            content_type = response.headers.get("content-type")
        except PlaywrightError:  # pragma: no cover - page closed meanwhile
            return
        if not is_stylesheet_response(resource_type, content_type):
            return
        if response.status >= 300 or response.url in self._seen:
            return
        self._seen.add(response.url)
        self._tasks.append(asyncio.ensure_future(self._read(response)))

    async def _read(self, response: Response) -> None:
        length = response.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > MAX_STYLESHEET_BYTES:
            self._skip_large(response.url, int(length))
            return
        try:
            body = await response.body()
        except PlaywrightError:
            return
        if len(body) > MAX_STYLESHEET_BYTES:
            self._skip_large(response.url, len(body))
            return
        text = body.decode("utf-8", errors="replace").lstrip("﻿")
        self._captured.append(_Captured(response.url, response.status, text))

    def _skip_large(self, url: str, size: int) -> None:
        self.warnings.append(
            f"Bỏ qua stylesheet {url} vì quá lớn ({size / 1024 / 1024:.1f} MB > "
            f"{MAX_STYLESHEET_BYTES // 1024 // 1024} MB)."
        )

    async def inline_blocks(self) -> list[str]:
        try:
            blocks = await self.page.evaluate(_INLINE_SCRIPT)
        except PlaywrightError:
            return []
        return [str(block) for block in blocks or [] if isinstance(block, str)]

    async def finish_keyed(self) -> list[tuple[str, StylesheetInfo]]:
        """Like finish(), with a deduplication key per sheet (URL, or digest of inline CSS)."""
        self.page.remove_listener("response", self._on_response)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        keyed = [
            (item.url, parse_stylesheet(item.url, item.text, status=item.status))
            for item in self._captured
        ]
        for position, text in enumerate(await self.inline_blocks(), start=1):
            digest = hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()
            url = f"inline:{self.page_name}#{position}"
            keyed.append((f"inline:{digest}", parse_stylesheet(url, text, inline=True)))
        return keyed

    async def finish(self) -> list[StylesheetInfo]:
        """Parse all recorded responses (+ inline <style> blocks) into StylesheetInfo."""
        return [sheet for _, sheet in await self.finish_keyed()]


@dataclass
class StylesheetRegistry:
    """Stylesheets of every captured page, deduplicated by URL (inline blocks by content)."""

    sheets: list[StylesheetInfo] = field(default_factory=list)
    _keys: set[str] = field(default_factory=set)

    def add(self, keyed: Iterable[tuple[str, StylesheetInfo]]) -> int:
        """Add (key, sheet) pairs from StylesheetRecorder.finish_keyed(); returns how many
        were new."""
        added = 0
        for key, sheet in keyed:
            if key in self._keys:
                continue
            self._keys.add(key)
            self.sheets.append(sheet)
            added += 1
        return added
