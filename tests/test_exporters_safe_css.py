"""exporters/safe_css.py: untrusted CSS values are rebuilt, never copied."""

from __future__ import annotations

import pytest

from get_design_markdown.exporters.safe_css import (
    clean_font_name,
    css_backdrop_filter,
    css_color,
    css_font_stack,
    css_font_weight,
    css_length,
    css_line_height,
    css_opacity,
    css_shadow,
    css_sides,
    css_text_decoration,
    css_text_transform,
    html_lang,
    http_url,
    ident,
    sides_px,
    unique,
)

UNSAFE = ("<", ">", ";", "{", "}", "\\", "/*")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#4F46E5", "#4f46e5"),
        ("rgb(79, 70, 229)", "#4f46e5"),
        ("rgba(255, 255, 255, 0.8)", "#ffffffcc"),
        ("transparent", "#00000000"),
        ("white", "#ffffff"),
        ("hsl(240 100% 50%)", "#0000ff"),
        ("red;}body{display:none", None),
        ("expression(alert(1))", None),
        ("", None),
        (None, None),
    ],
)
def test_css_color(value: str | None, expected: str | None) -> None:
    assert css_color(value) == expected


def test_css_length_normalizes_and_rejects() -> None:
    assert css_length("1.5rem") == "24px"
    assert css_length("12px") == "12px"
    assert css_length("-0.8px") == "-0.8px"
    assert css_length("-4px", allow_negative=False) is None
    assert css_length("50%") == "50%"
    assert css_length("250%") == "100%"
    assert css_length("calc(100% - 2px)") is None
    assert css_length("1e9px") is None
    assert css_length("12px;}x{") is None
    assert css_length(None) is None


def test_sides() -> None:
    assert css_sides("12px 20px") == "12px 20px"
    assert css_sides("64px 0px 48px") == "64px 0px 48px"
    assert css_sides("1px 2px 3px 4px 5px") is None
    assert css_sides("12px x") is None
    assert css_sides("10%") is None
    assert sides_px("8px 12px") == (8.0, 12.0, 8.0, 12.0)
    assert sides_px("64px 0px 48px") == (64.0, 0.0, 48.0, 0.0)
    assert sides_px("4px") == (4.0, 4.0, 4.0, 4.0)
    assert sides_px("auto") is None
    assert sides_px(None) is None


def test_css_shadow_rebuilds_layers() -> None:
    css = "rgba(15, 23, 42, 0.12) 0px 8px 24px -4px, 0 2px 6px rgba(15,23,42,.06)"
    assert css_shadow(css) == (
        "0px 8px 24px -4px rgba(15, 23, 42, 0.12), 0px 2px 6px 0px rgba(15, 23, 42, 0.06)"
    )
    assert css_shadow("none") is None
    rebuilt = css_shadow("0 0 0 red;}</style><script>alert(1)</script>")
    assert rebuilt is not None and not any(char in rebuilt for char in UNSAFE)


def test_font_stack_quotes_and_cleans_names() -> None:
    assert css_font_stack(["Inter", "system-ui", "sans-serif"]) == '"Inter", system-ui, sans-serif'
    assert css_font_stack('"Sohne", -apple-system') == '"Sohne", -apple-system, sans-serif'
    assert css_font_stack(["JetBrains Mono"], category="monospace") == '"JetBrains Mono", monospace'
    malicious = css_font_stack(["x</style><script>alert(1)</script>", 'a"b;}c'])
    assert malicious == '"xstylescriptalert1script", "abc", sans-serif'
    assert css_font_stack([]) == "sans-serif"
    assert css_font_stack(["Inter", "inter", "INTER"]) == '"Inter", sans-serif'
    assert clean_font_name("  Söhne  Breit ") == "Söhne Breit"
    assert clean_font_name("ヒラギノ角ゴ ProN") == "ヒラギノ角ゴ ProN"
    assert clean_font_name('＜style＞\n"\\') == "style"


def test_keywords_and_numbers() -> None:
    assert css_font_weight("600") == "600"
    assert css_font_weight("bold") == "700"
    assert css_font_weight("600;}x") is None
    assert css_font_weight(1200) == "1000"
    assert css_line_height("1.5") == "1.5"
    assert css_line_height("20px") == "20px"
    assert css_line_height("normal") == "normal"
    assert css_line_height("1;}") is None
    assert css_text_transform("UPPERCASE") == "uppercase"
    assert css_text_transform("expression") is None
    assert css_text_decoration("underline solid rgb(0, 0, 0)") == "underline"
    assert css_text_decoration("none") == "none"
    assert css_text_decoration("blink") is None


def test_opacity() -> None:
    assert css_opacity("0.6") == "0.6"
    assert css_opacity("60%") == "0.6"
    assert css_opacity("1.5") == "1"
    assert css_opacity("0") == "0"
    assert css_opacity("0.5;}") is None
    assert css_opacity(None) is None


def test_backdrop_filter() -> None:
    assert css_backdrop_filter("blur(12px)") == "blur(12px)"
    assert css_backdrop_filter("saturate(180%) blur(20px)") == "saturate(1.8) blur(20px)"
    assert css_backdrop_filter("hue-rotate(90deg)") == "hue-rotate(90deg)"
    assert css_backdrop_filter("url(#x) blur(2px);}") == "blur(2px)"
    assert css_backdrop_filter("none") is None
    assert css_backdrop_filter("blur(1px)</style>") == "blur(1px)"


def test_identifiers_lang_and_urls() -> None:
    assert ident("Body LG") == "body-lg"
    assert ident("</style>") == "style"
    assert ident("!!!") == "x"
    assert html_lang("vi") == "vi"
    assert html_lang("en-US") == "en-US"
    assert html_lang('en"><script>') == "en"
    assert html_lang(None) == "en"
    assert http_url("https://stripe.com/") == "https://stripe.com/"
    assert http_url("javascript:alert(1)") is None
    assert http_url('https://x.com/"onmouseover=') is None
    assert unique(["a", "b", "a"]) == ["a", "b"]
