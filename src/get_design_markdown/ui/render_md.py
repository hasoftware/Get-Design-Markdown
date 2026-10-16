"""Safe Markdown rendering of DESIGN.md / prompt.md for the UI.

Frozen interface: the signatures below.

Rules:
- markdown-it-py, "commonmark" preset with tables enabled ("table"), `html=False`: raw HTML in
  the document is escaped, never passed through; no plugins that need extra packages.
- Links keep markdown-it's validateLink (javascript:, vbscript:, file: and non-image data:
  URLs are dropped); rendered links get rel="noopener noreferrer".
- Output is an HTML *fragment* (no <html>/<head>), UTF-8 text; fenced code keeps its
  language as class="language-<lang>".
- The front matter (DESIGN.md starts with a YAML block between two "---" lines) is not
  rendered: it is returned separately, without the "---" lines, so the UI can show it in a
  code block.
- The UI shows the fragment inside a sandboxed iframe without scripts (see ui/server.py);
  rendering must still be safe if it were inserted into the page directly.
"""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from functools import lru_cache

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import OptionsDict

#: Line that opens and closes the YAML front matter.
FRONT_MATTER_FENCE = "---"
#: rel attribute of every rendered link.
LINK_REL = "noopener noreferrer"
_BOM = "﻿"


def _link_open(
    self: RendererHTML,
    tokens: Sequence[Token],
    idx: int,
    options: OptionsDict,
    env: MutableMapping[str, object],
) -> str:
    """Render <a ...> like markdown-it does, plus rel="noopener noreferrer"."""
    tokens[idx].attrSet("rel", LINK_REL)
    return self.renderToken(tokens, idx, options, env)


@lru_cache(maxsize=1)
def _markdown() -> MarkdownIt:
    """The shared parser (rendering keeps no state between calls, so threads can share it)."""
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    parser.add_render_rule("link_open", _link_open)
    return parser


def _is_fence(line: str) -> bool:
    """True for a line that is exactly "---" (its line ending and trailing blanks ignored)."""
    return line.rstrip() == FRONT_MATTER_FENCE


def split_front_matter(text: str) -> tuple[str | None, str]:
    """(front matter YAML without the "---" lines, remaining Markdown).

    Only a block that starts on the very first line ("---") and ends at the next line that is
    exactly "---" counts; otherwise (None, text).
    """
    lines = text.removeprefix(_BOM).splitlines(keepends=True)
    if not lines or not _is_fence(lines[0]):
        return None, text
    for index in range(1, len(lines)):
        if _is_fence(lines[index]):
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    return None, text


def render_markdown(text: str) -> tuple[str | None, str]:
    """(front matter YAML or None, HTML fragment of the Markdown body)."""
    front_matter, body = split_front_matter(text)
    return front_matter, _markdown().render(body)
