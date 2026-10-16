"""Static checks of the local UI frontend (ui/static): packaging, CSP, safety rules, wiring.

These run without a browser; tests/test_ui_frontend.py drives the same files in Chromium.
"""

from __future__ import annotations

import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from get_design_markdown.resources import resource_path

STATIC = Path(resource_path("ui", "static"))
ALLOWED_SUFFIXES = {".html", ".css", ".js", ".svg", ".png", ".ico"}
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
#: The only module allowed to read the server's rendered Markdown as HTML (DOMParser + srcdoc).
MARKDOWN_FRAME_MODULE = "frame.js"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _scripts() -> list[Path]:
    return sorted(STATIC.glob("*.js"))


def _code(path: Path) -> str:
    """A script without its comments (rules below are about code, not prose)."""
    text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)


class _IndexParser(HTMLParser):
    """Collects what the checks below need from index.html."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.label_for: list[str] = []
        self.scripts: list[dict[str, str | None]] = []
        self.inline_script_text = ""
        self.event_attributes: list[str] = []
        self.style_elements = 0
        self.links: list[dict[str, str | None]] = []
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(str(attributes["id"]))
        if tag == "label" and attributes.get("for"):
            self.label_for.append(str(attributes["for"]))
        if tag == "script":
            self.scripts.append(attributes)
            self._in_script = True
        if tag == "style":
            self.style_elements += 1
        if tag == "link":
            self.links.append(attributes)
        self.event_attributes += [name for name, _ in attrs if name.lower().startswith("on")]

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self.inline_script_text += data


@pytest.fixture(scope="module")
def index() -> _IndexParser:
    parser = _IndexParser()
    parser.feed(_read("index.html"))
    return parser


# ─── Packaging ───────────────────────────────────────────────────────────────


def test_static_files_use_packaged_suffixes_only() -> None:
    files = [path for path in STATIC.rglob("*") if path.is_file()]
    assert {path.name for path in files} >= {"index.html", "app.js", "app.css", "favicon.svg"}
    for path in files:
        assert path.suffix in ALLOWED_SUFFIXES, path.name


def test_text_files_are_utf8_with_lf_endings() -> None:
    for path in STATIC.rglob("*"):
        if path.suffix in {".html", ".css", ".js", ".svg"}:
            data = path.read_bytes()
            data.decode("utf-8")
            assert b"\r\n" not in data, path.name
            assert data.endswith(b"\n"), path.name


# ─── CSP and offline rules ───────────────────────────────────────────────────


def test_index_has_no_inline_script_or_handlers(index: _IndexParser) -> None:
    html = _read("index.html")
    assert '<html lang="vi">' in html
    assert index.inline_script_text.strip() == ""
    assert index.event_attributes == []
    assert index.scripts == [{"type": "module", "src": "/static/app.js"}]
    assert index.style_elements == 0
    hrefs = {link.get("href") for link in index.links}
    assert hrefs == {"/static/app.css", "/static/favicon.svg"}


def test_no_network_resources_outside_the_app() -> None:
    url_re = re.compile(r"https?://[\w.-]+\.[a-z]{2,}[^\s\"'`)]*", re.IGNORECASE)
    for path in STATIC.rglob("*"):
        if path.suffix not in {".html", ".css", ".js", ".svg"}:
            continue
        found = [
            url for url in url_re.findall(path.read_text(encoding="utf-8")) if url != SVG_NAMESPACE
        ]
        assert found == [], (path.name, found)
    css = _read("app.css")
    assert "@import" not in css
    assert "@font-face" not in css


def test_scripts_avoid_eval_and_html_injection() -> None:
    forbidden = {
        "eval(": re.compile(r"\beval\s*\("),
        "new Function": re.compile(r"\bnew\s+Function\b"),
        "string timer": re.compile(r"set(?:Timeout|Interval)\(\s*[\"'`]"),
        "innerHTML assignment": re.compile(r"\.(?:inner|outer)HTML\s*\+?=(?!=)"),
        "insertAdjacentHTML": re.compile(r"insertAdjacentHTML"),
        "document.write": re.compile(r"document\.write"),
    }
    for path in _scripts():
        text = _code(path)
        for label, pattern in forbidden.items():
            assert pattern.search(text) is None, (path.name, label)
        if path.name != MARKDOWN_FRAME_MODULE:
            assert "innerHTML" not in text, path.name
            assert "srcdoc" not in text, path.name
            assert "DOMParser" not in text, path.name


def test_markdown_frames_are_sandboxed_without_scripts() -> None:
    frame = _read(MARKDOWN_FRAME_MODULE)
    assert 'setAttribute("sandbox", "")' in frame
    assert "allow-" not in frame
    results = _read("results.js")
    assert 'sandbox: "allow-scripts"' in results
    for path in _scripts():
        assert "allow-same-origin" not in _code(path), path.name


def test_network_and_storage_go_through_guarded_helpers() -> None:
    for path in _scripts():
        text = _code(path)
        if path.name != "api.js":
            assert re.search(r"(?<![\w.])fetch\(", text) is None, path.name
        if path.name != "dom.js":
            assert "localStorage" not in text and "sessionStorage" not in text, path.name
    dom = _read("dom.js")
    # Every access to the storage areas sits inside try/catch.
    assert dom.count("try {") >= 4
    api = _read("api.js")
    assert "X-GDM-Token" in api
    assert "history.replaceState" in api


# ─── Wiring between files ────────────────────────────────────────────────────


def _node() -> str | None:
    """The Node.js runtime bundled with Playwright's driver, when it can be found."""
    try:
        from playwright._impl._driver import compute_driver_executable

        node = Path(compute_driver_executable()[0])
    except Exception:
        return None
    return str(node) if node.is_file() else None


def test_scripts_parse() -> None:
    """A syntax error would stop the whole page; catch it without a browser."""
    node = _node()
    if node is None:
        pytest.skip("Không tìm thấy Node.js của Playwright để kiểm tra cú pháp JavaScript.")
    for path in _scripts():
        result = subprocess.run(
            [node, "--check", str(path)], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, (path.name, result.stderr[-800:])


def test_module_imports_resolve() -> None:
    import_re = re.compile(r"""from\s+["']\./([\w-]+\.js)["']""")
    for path in _scripts():
        for target in import_re.findall(path.read_text(encoding="utf-8")):
            assert (STATIC / target).is_file(), (path.name, target)


def test_every_id_used_by_scripts_exists(index: _IndexParser) -> None:
    ids = set(index.ids)
    assert len(ids) == len(index.ids), "duplicate id in index.html"
    used: set[str] = set()
    for path in _scripts():
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"""byId\(\s*["']([\w-]+)["']\s*\)""", text))
        used |= set(re.findall(r"""getElementById\(\s*["']([\w-]+)["']\s*\)""", text))
    assert used, "no element lookups found"
    assert sorted(used - ids) == []


def test_labels_point_to_existing_controls(index: _IndexParser) -> None:
    ids = set(index.ids)
    assert index.label_for
    for target in index.label_for:
        assert target in ids, target
    html = _read("index.html")
    for setting in re.findall(r'data-setting="(\w+)"', html):
        assert f'id="opt-{setting}-error"' in html, setting


def test_icons_used_by_scripts_exist_in_the_sprite() -> None:
    html = _read("index.html")
    sprite = set(re.findall(r'<symbol id="i-([\w-]+)"', html))
    used = set(re.findall(r'href="#i-([\w-]+)"', html))
    for path in _scripts():
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"""\bicon\(\s*["']([\w-]+)["']""", text))
        for block in re.findall(r"ICONS\s*=\s*\{([^}]*)\}", text):
            used |= set(re.findall(r""":\s*["']([\w-]+)["']""", block))
    assert used <= sprite, sorted(used - sprite)


# ─── Theme and copy ──────────────────────────────────────────────────────────


def _declared_tokens(block: str) -> set[str]:
    return set(re.findall(r"(--[\w-]+)\s*:", block))


def test_dark_theme_redefines_every_colour_token() -> None:
    css = _read("app.css")
    root = css.split(":root {", 1)[1].split("}", 1)[0]
    colours = {
        name
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", root)
        if value.strip().startswith(("#", "rgb"))
    }
    assert colours
    media = css.split(
        '@media (prefers-color-scheme: dark) {\n  :root:not([data-theme="light"]) {', 1
    )[1]
    media_block = media.split("}", 1)[0]
    forced_block = css.split(':root[data-theme="dark"] {', 1)[1].split("}", 1)[0]
    assert colours <= _declared_tokens(media_block)
    assert _declared_tokens(media_block) == _declared_tokens(forced_block)
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css


def test_vietnamese_copy_has_diacritics() -> None:
    html = _read("index.html")
    for phrase in (
        "Phân tích",
        "Tùy chọn nâng cao",
        "Bắt đầu trong 3 bước",
        "Công cụ chỉ để tham khảo và học hỏi phong cách; không sao chép logo hay tài sản có "
        "bản quyền.",
        "Phiên làm việc đã hết hạn",
        "Xem trước",
        "Dùng với AI",
        "Ảnh chụp",
    ):
        assert phrase in html, phrase
    # Common words typed without their accents would read as broken Vietnamese.
    text = re.sub(r"<[^>]+>", " ", html)
    for unaccented in (" Phan tich ", " Tuy chon ", " trang web cua ", " Huong dan "):
        assert unaccented not in text, unaccented
