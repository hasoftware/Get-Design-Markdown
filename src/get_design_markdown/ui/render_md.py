"""Safe Markdown rendering of DESIGN.md / prompt.md for the UI.

Frozen interface: the signatures below.
To implement (the stubs raise NotImplementedYetError): split_front_matter, render_markdown.

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

from get_design_markdown.errors import NotImplementedYetError


def split_front_matter(text: str) -> tuple[str | None, str]:
    """(front matter YAML without the "---" lines, remaining Markdown).

    Only a block that starts on the very first line ("---") and ends at the next line that is
    exactly "---" counts; otherwise (None, text).
    """
    raise NotImplementedYetError("Chưa hiển thị được Markdown.")


def render_markdown(text: str) -> tuple[str | None, str]:
    """(front matter YAML or None, HTML fragment of the Markdown body)."""
    raise NotImplementedYetError("Chưa hiển thị được Markdown.")
