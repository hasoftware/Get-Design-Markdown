"""Safe Markdown rendering of DESIGN.md / prompt.md (ui/render_md.py)."""

from __future__ import annotations

import re
import threading
from html.parser import HTMLParser
from pathlib import Path

import pytest

from get_design_markdown.ui.render_md import LINK_REL, render_markdown, split_front_matter

GOLDEN = Path(__file__).parent / "golden" / "synthesizer"

#: Elements that must never appear in the rendered fragment.
FORBIDDEN_TAGS = frozenset(
    {
        "script",
        "iframe",
        "object",
        "embed",
        "style",
        "svg",
        "math",
        "form",
        "input",
        "base",
        "meta",
        "link",
        "frame",
        "frameset",
        "html",
        "head",
        "body",
    }
)
#: href/src values a rendered document may carry.
SAFE_URL = re.compile(r"^(https?:|mailto:|#|/|data:image/(gif|png|jpeg|webp);|[^:]*$)", re.I)


class _Markup(HTMLParser):
    """The real elements and attributes of an HTML fragment (escaped text is not markup)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attributes.extend((name, value or "") for name, value in attrs)


def _markup(html: str) -> _Markup:
    parser = _Markup()
    parser.feed(html)
    parser.close()
    return parser


def assert_safe(html: str) -> None:
    """No dangerous element, no event-handler attribute, no script-capable URL."""
    markup = _markup(html)
    assert not FORBIDDEN_TAGS.intersection(markup.tags), html
    for name, value in markup.attributes:
        assert not name.startswith("on"), html
        if name in ("href", "src"):
            assert SAFE_URL.match(value), value


# ─── Front matter ────────────────────────────────────────────────────────────


def test_front_matter_is_split_without_the_fences() -> None:
    text = "---\nversion: alpha\nname: Acme\n---\n# Title\n\nBody.\n"
    assert split_front_matter(text) == ("version: alpha\nname: Acme\n", "# Title\n\nBody.\n")


@pytest.mark.parametrize(
    "text",
    [
        "# Title\n\n---\nnot: front matter\n---\n",  # not on the first line
        "\n---\nversion: alpha\n---\nBody\n",  # a blank line first
        "---\nversion: alpha\nno closing fence\n",
        "----\nversion: alpha\n----\nBody\n",  # four dashes are not a fence
        " ---\nversion: alpha\n---\nBody\n",  # indented
        "",
        "Just text",
    ],
)
def test_text_without_front_matter_is_returned_unchanged(text: str) -> None:
    assert split_front_matter(text) == (None, text)


def test_front_matter_variants() -> None:
    assert split_front_matter("---\r\nversion: alpha\r\n---\r\nBody\r\n") == (
        "version: alpha\r\n",
        "Body\r\n",
    )
    assert split_front_matter("--- \nkey: 1\n---  \nBody") == ("key: 1\n", "Body")
    assert split_front_matter("﻿---\nkey: 1\n---\nBody") == ("key: 1\n", "Body")
    assert split_front_matter("---\n---\nBody") == ("", "Body")
    assert split_front_matter("---\nkey: 1\n---") == ("key: 1\n", "")
    # Only the first closing fence ends the block; a later "---" is a Markdown rule.
    front, body = split_front_matter("---\na: 1\n---\nText\n\n---\nMore\n")
    assert front == "a: 1\n" and body == "Text\n\n---\nMore\n"


def test_render_markdown_returns_the_front_matter_and_renders_only_the_body() -> None:
    front, html = render_markdown("---\nversion: alpha\ncolors: {}\n---\n# Acme\n")
    assert front == "version: alpha\ncolors: {}\n"
    assert html == "<h1>Acme</h1>\n"
    assert render_markdown("# Plain\n") == (None, "<h1>Plain</h1>\n")


# ─── Markdown features ───────────────────────────────────────────────────────


def test_tables_are_rendered() -> None:
    _, html = render_markdown("| Token | Value |\n|---|---|\n| `primary` | #635bff |\n")
    assert "<table>" in html and "<th>Token</th>" in html
    assert "<td><code>primary</code></td>" in html and "<td>#635bff</td>" in html


def test_fenced_code_keeps_its_language_class() -> None:
    _, html = render_markdown("```css\n.btn { color: red; }\n```\n")
    assert html == '<pre><code class="language-css">.btn { color: red; }\n</code></pre>\n'
    _, html = render_markdown('```"><script>alert(1)</script>\nx\n```\n')
    assert_safe(html)
    assert 'class="language-&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in html


def test_output_is_a_fragment() -> None:
    _, html = render_markdown("# A\n\nB\n")
    assert _markup(html).tags == ["h1", "p"]


def test_links_get_rel_noopener_noreferrer() -> None:
    _, html = render_markdown(
        "[Stripe](https://stripe.com) and <https://linear.app> and [rel](/x 'title')\n"
    )
    anchors = re.findall(r"<a [^>]*>", html)
    assert len(anchors) == 3
    assert all(f'rel="{LINK_REL}"' in anchor for anchor in anchors)
    assert '<a href="https://stripe.com" rel="noopener noreferrer">Stripe</a>' in html


# ─── Safety ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        '<iframe src="https://evil.example"></iframe>',
        "<style>body{display:none}</style>",
        "<svg onload=alert(1)>",
        "<a href='javascript:alert(1)'>x</a>",
        "Text <b onmouseover=alert(1)>bold</b>",
        "<div>\n<script>alert(1)</script>\n</div>",
        "<!-- comment --><base href='https://evil.example/'>",
        "<form action=https://evil.example><input name=x></form>",
        "<meta http-equiv=refresh content='0;url=https://evil.example'>",
    ],
)
def test_raw_html_is_escaped(source: str) -> None:
    _, html = render_markdown(source + "\n")
    assert_safe(html)
    assert "&lt;" in html
    assert _markup(html).tags == ["p"]


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "javascript&colon;alert(1)",
        "&#106;avascript:alert(1)",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    ],
)
def test_dangerous_link_schemes_are_dropped(link: str) -> None:
    _, html = render_markdown(f"[click]({link}) ![img]({link})\n")
    assert_safe(html)
    assert "a" not in _markup(html).tags and "img" not in _markup(html).tags


def test_image_data_urls_are_kept() -> None:
    _, html = render_markdown("![dot](data:image/png;base64,iVBORw0KGgo=)\n")
    assert '<img src="data:image/png;base64,iVBORw0KGgo=" alt="dot" />' in html


def test_attribute_values_are_escaped() -> None:
    _, html = render_markdown('[x](https://e.example/"onmouseover="alert(1) "t\\"itle")\n')
    assert_safe(html)
    _, html = render_markdown('![a" onerror="alert(1)](https://e.example/i.png)\n')
    assert_safe(html)
    assert _markup(html).attributes == [
        ("src", "https://e.example/i.png"),
        ("alt", 'a" onerror="alert(1)'),
    ]


# ─── Real documents ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["acme-en.md", "acme-vi.md", "acme-en-examples.md"])
def test_golden_design_md_renders_without_its_front_matter(name: str) -> None:
    text = (GOLDEN / name).read_text(encoding="utf-8")
    front, html = render_markdown(text)
    assert front is not None and front.startswith("version: alpha")
    assert "colors:" in front and "\n---" not in front
    assert "version: alpha" not in html
    assert html.startswith("<h1>")
    assert "<h2>Overview</h2>" in html and "<h2>Agent Prompt Guide</h2>" in html
    assert "<table>" in html
    assert_safe(html)


def test_rendering_is_thread_safe() -> None:
    text = (GOLDEN / "acme-en.md").read_text(encoding="utf-8")
    expected = render_markdown(text)
    results: list[tuple[str | None, str]] = []
    lock = threading.Lock()

    def work() -> None:
        for _ in range(5):
            rendered = render_markdown(text)
            with lock:
                results.append(rendered)

    threads = [threading.Thread(target=work) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 30
    assert all(result == expected for result in results)
