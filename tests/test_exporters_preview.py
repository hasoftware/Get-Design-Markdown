"""exporters/preview.py: the static preview.html page."""

from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from get_design_markdown.analyzer import fonts
from get_design_markdown.analyzer.fonts import FontLookup
from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.exporters.preview import render_preview
from get_design_markdown.models.analysis import (
    Breakpoint,
    ColorRole,
    ColorToken,
    ComponentKind,
    ComponentSpec,
    DesignAnalysis,
    FontFamilyInfo,
    FontRole,
    ResponsiveChange,
    ResponsiveChangeKind,
    ShadowLevel,
    SiteInfo,
    TypeLevel,
)
from tests.conftest import FIXED_TIME
from tests.fixture_data import load_analysis_sample

GOLDEN = Path(__file__).parent / "golden" / "exporters" / "preview.html"
UPDATE_GOLDEN = os.environ.get("GDM_UPDATE_GOLDEN") == "1"
VOID = frozenset({"meta", "link", "input", "br", "img", "hr", "source", "wbr"})
ALLOWED_TAGS = frozenset(
    {
        "html", "head", "meta", "title", "link", "style", "script", "body", "header", "div",
        "p", "h1", "h2", "h3", "h4", "ul", "li", "strong", "a", "time", "button", "span",
        "nav", "main", "section", "figure", "figcaption", "code", "details", "summary",
        "table", "thead", "tbody", "tr", "th", "td", "dl", "dt", "dd", "small", "article",
        "input", "br", "footer",
    }
)  # fmt: skip


class _Checker(HTMLParser):
    """Collects tags, checks nesting and keeps the raw text of <style> and <script>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.raw: dict[str, list[str]] = {"style": [], "script": []}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> (open: {self.stack[-3:]})")
            return
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1] in self.raw:
            self.raw[self.stack[-1]].append(data)


def _parse(html: str) -> _Checker:
    checker = _Checker()
    checker.feed(html)
    checker.close()
    return checker


@pytest.fixture(scope="module")
def html() -> str:
    return render_preview(load_analysis_sample())


def _empty_analysis() -> DesignAnalysis:
    return DesignAnalysis(
        tool_version="0.1.0",
        analyzed_at=FIXED_TIME,
        site=SiteInfo(url="https://empty.example/", name="Empty"),
    )


# ─── Sample page ─────────────────────────────────────────────────────────────


def test_preview_is_well_formed_html(html: str) -> None:
    assert html.startswith("<!DOCTYPE html>")
    checker = _parse(html)
    assert checker.errors == []
    assert checker.stack == []
    assert {tag for tag, _ in checker.tags} <= ALLOWED_TAGS
    assert '<html lang="en" data-theme="light" data-site="acme-example">' in html


def test_preview_is_small_and_deterministic(html: str) -> None:
    assert len(html.encode("utf-8")) < 300_000
    assert render_preview(load_analysis_sample()) == html


def test_preview_matches_golden(html: str) -> None:
    if UPDATE_GOLDEN:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(html, encoding="utf-8", newline="\n")
    assert GOLDEN.read_text(encoding="utf-8") == html


def test_preview_external_resources(html: str) -> None:
    checker = _parse(html)
    assert "<script src" not in html
    scripts = [attrs for tag, attrs in checker.tags if tag == "script"]
    assert scripts == [{}]
    links = [attrs for tag, attrs in checker.tags if tag == "link"]
    assert len(links) == 1
    href = links[0]["href"] or ""
    assert href.startswith("https://fonts.googleapis.com/css2?")
    assert "family=Inter:wght@400;500;600" in href
    assert "family=Space+Grotesk:wght@700" in href
    assert "family=JetBrains+Mono:wght@400" in href
    assert href.endswith("&display=swap")
    for tag, attrs in checker.tags:
        if tag in ("img", "iframe", "video", "audio", "source", "object", "embed"):
            pytest.fail(f"unexpected resource <{tag}>")
        assert "src" not in attrs
    css = "".join(checker.raw["style"])
    assert "@import" not in css and "url(" not in css


def test_preview_shows_every_token_level_and_component(html: str) -> None:
    sample = load_analysis_sample()
    assert sample.dark is not None
    for token in [*sample.colors.tokens, *sample.dark.colors.tokens]:
        assert token.value in html, token
        assert f"--color-{token.name}:" in html
    for level in sample.typography.levels:
        assert f"<code>{level.name}</code>" in html
    for component in sample.components:
        assert f'id="cmp-{component.name}"' in html
    for step in sample.spacing.steps:
        assert f"--space-{step.name}:" in html
    for step in sample.radii:
        assert f"--radius-{step.name}:" in html
    for shadow in sample.shadows:
        assert f"--shadow-{shadow.name}:" in html
    for breakpoint in sample.breakpoints:
        assert f"<code>{breakpoint.name}</code>" in html
    assert "Navigation collapses to a hamburger" in html
    assert "64px → 48px" in html
    assert "Reference only — not the official design system" in html
    assert "không phải design system chính thức" in html


def test_preview_theme_toggle_and_variables(html: str) -> None:
    assert 'id="theme-toggle"' in html
    assert ':root[data-theme="light"]{color-scheme:light;' in html
    assert ':root[data-theme="dark"]{color-scheme:dark;' in html
    assert "--color-primary:#4f46e5" in html
    assert "--color-primary:#818cf8" in html
    assert "localStorage" in html and "try {" in html
    # Component stages keep the default capture's variables in the dark theme.
    assert ".pv-capture{color-scheme:light;" in html
    assert 'class="pv-stage pv-capture"' in html


def test_preview_components_use_tokens_and_real_hover(html: str) -> None:
    rules = dict(re.findall(r"(\.cx-\d+(?::hover, \.cx-\d+\.is-hover)?)\{([^}]*)\}", html))
    primary = rules[".cx-0"]
    assert "background-color:var(--color-primary, #4f46e5)" in primary
    assert "border-radius:var(--radius-md, 8px)" in primary
    assert "box-shadow:var(--shadow-sm, 0px 1px 2px 0px rgba(15, 23, 42, 0.06))" in primary
    assert rules[".cx-0:hover, .cx-0.is-hover"] == "background-color:#4338ca"
    assert "background-color:var(--color-surface, #f8fafc)" in rules[".cx-2:hover, .cx-2.is-hover"]
    assert "text-decoration-line:underline" in rules[".cx-4:hover, .cx-4.is-hover"]
    assert ".cx-6::placeholder{color:var(--color-faint, #94a3b8);opacity:1}" in html
    assert "padding:var(--space-xxl, 32px)" in rules[".cx-7"]
    assert "backdrop-filter:blur(12px)" in rules[".cx-9"]
    assert "border-width:0px 0px 1px" in rules[".cx-9"]
    assert 'class="cmp ck-button cx-0 is-hover"' in html


def test_preview_hover_opacity() -> None:
    analysis = load_analysis_sample()
    analysis.components[0].states["hover"]["opacity"] = "0.6"
    html = render_preview(analysis)
    assert ".cx-0:hover, .cx-0.is-hover{background-color:#4338ca;opacity:0.6}" in html


def test_preview_without_dark_has_no_toggle() -> None:
    analysis = load_analysis_sample()
    analysis.dark = None
    html = render_preview(analysis)
    assert 'data-theme="dark"' not in html
    assert "theme-toggle" not in html
    assert "<script" not in html
    assert "pv-capture" not in html.split("</style>")[1]
    assert _parse(html).errors == []


def test_preview_empty_analysis() -> None:
    html = render_preview(_empty_analysis())
    checker = _parse(html)
    assert checker.errors == [] and checker.stack == []
    assert "<link" not in html
    assert "No width breakpoints were found" in html
    assert 'id="components"' not in html


# ─── Security ────────────────────────────────────────────────────────────────

FONT_ATTACK = "x</style><script>alert(1)</script>"
CLASS_ATTACK = '"><img onerror=alert(1)>'
COLOR_ATTACK = "red;}body{display:none"


def _malicious_analysis() -> DesignAnalysis:
    analysis = load_analysis_sample()
    analysis.site = SiteInfo(
        url="javascript:alert(1)",
        name="<script>alert('name')</script>",
        title='</title><script>alert("t")</script>',
        lang='en"><script>alert(1)</script>',
        pages_analyzed=["<b>home</b>"],
    )
    analysis.typography.families[0] = FontFamilyInfo(
        name=FONT_ATTACK,
        stack=[FONT_ATTACK, "sans-serif;}</style>"],
        role=FontRole.PRIMARY,
        is_google_font=True,
        weights=[400],
        substitutes=["<i>sub</i>"],
    )
    analysis.typography.levels[0] = TypeLevel(
        name="display",
        size_px=64,
        font_family=FONT_ATTACK,
        weight=700,
        text_transform="uppercase;}</style>",
        samples=["</p><script>alert('sample')</script>"],
    )
    analysis.colors.tokens[0] = ColorToken(
        name="primary",
        value="#4f46e5",
        role=ColorRole.BRAND,
        evidence="<img src=x onerror=alert(1)>",
        css_variable="--x</code><script>",
    )
    analysis.shadows[0] = ShadowLevel(
        name="sm", css="0 1px 2px red;}</style><script>alert('shadow')</script>"
    )
    analysis.breakpoints.append(Breakpoint(name="</style><script>bp</script>", min_width_px=900))
    analysis.layout.changes.append(
        ResponsiveChange(
            from_width=768,
            to_width=390,
            kind=ResponsiveChangeKind.NAV_HIDDEN,
            evidence="<script>alert('evidence')</script>",
        )
    )
    analysis.components[0] = ComponentSpec(
        name="button-primary",
        kind=ComponentKind.BUTTON,
        properties={
            "backgroundColor": COLOR_ATTACK,
            "textColor": COLOR_ATTACK,
            "rounded": "8px;}</style><script>alert(1)</script>",
            "padding": "1px}body{display:none",
            "fontFamily": FONT_ATTACK,
            "fontSize": "15px;background:url(//evil)",
            "fontWeight": "500;}",
            "lineHeight": "20px</style>",
            "shadow": "0 0 0 red;}</style><img onerror=alert(1)>",
            "backdropFilter": "blur(2px);}</style>",
            "textTransform": "uppercase;}",
        },
        token_refs={"backgroundColor": "{colors.primary}</style>"},
        states={"hover": {"backgroundColor": COLOR_ATTACK, "textDecoration": "</style>"}},
        class_hints=[CLASS_ATTACK],
        samples=["<script>alert('label')</script>"],
    )
    analysis.warnings.append("<script>alert('warning')</script>")
    return analysis


def test_malicious_analysis_is_neutralized() -> None:
    html = render_preview(_malicious_analysis())
    checker = _parse(html)
    assert checker.errors == [] and checker.stack == []
    assert {tag for tag, _ in checker.tags} <= ALLOWED_TAGS
    assert [tag for tag, _ in checker.tags].count("script") == 1
    assert [tag for tag, _ in checker.tags].count("style") == 1
    assert "img" not in {tag for tag, _ in checker.tags}
    for tag, attrs in checker.tags:
        assert not any(name.startswith("on") for name in attrs), (tag, attrs)
        if tag == "a":
            assert not (attrs.get("href") or "").lower().startswith("javascript:")
    css = "".join(checker.raw["style"])
    assert html.count("</style>") == 1
    assert "body{display:none" not in css
    assert "url(" not in css and "evil" not in css
    # The hostile font name survives only as a harmless quoted identifier.
    assert '"xstylescriptalert1script"' in css
    neutral = css.replace("xstylescriptalert1script", "")
    assert "alert" not in neutral and "script" not in neutral
    assert '<html lang="en"' in html
    assert "&lt;script&gt;alert(&#39;label&#39;)&lt;/script&gt;" in html
    assert "&#34;&gt;&lt;img onerror=alert(1)&gt;" in html
    script = "".join(checker.raw["script"])
    assert "alert" not in script


# ─── Fonts ───────────────────────────────────────────────────────────────────


def _proprietary_font_analysis() -> DesignAnalysis:
    analysis = _empty_analysis()
    analysis.typography.families = [
        FontFamilyInfo(
            name="sohne-var",
            stack=["sohne-var", "SF Pro Display", "sans-serif"],
            role=FontRole.PRIMARY,
            weights=[300, 425],
            substitutes=["Inter", "Helvetica Neue"],
        )
    ]
    analysis.typography.levels = [
        TypeLevel(name="body", size_px=16, font_family="sohne-var", weight=300)
    ]
    return analysis


def test_google_substitute_for_proprietary_font(monkeypatch: pytest.MonkeyPatch) -> None:
    def lookup(family: str) -> FontLookup:
        return FontLookup(name=family, is_google_font=family == "Inter")

    monkeypatch.setattr(fonts, "lookup_font", lookup)
    html = render_preview(_proprietary_font_analysis())
    # Weight 425 of the variable original is requested as the nearest standard weight.
    assert (
        'href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400&amp;display=swap"'
        in html
    )
    assert '--font-primary:"sohne-var", "Inter", "SF Pro Display", sans-serif' in html
    assert "Inter (substitute)" in html


def test_no_substitute_before_font_table_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    def lookup(family: str) -> FontLookup:
        raise NotImplementedYetError("chưa có")

    monkeypatch.setattr(fonts, "lookup_font", lookup)
    html = render_preview(_proprietary_font_analysis())
    assert "<link" not in html
    assert '--font-primary:"sohne-var", "SF Pro Display", sans-serif' in html
