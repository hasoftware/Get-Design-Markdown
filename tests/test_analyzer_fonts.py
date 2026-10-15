"""Tests of analyzer/fonts.py: the offline font knowledge base."""

from __future__ import annotations

import pytest

from get_design_markdown.analyzer.fonts import (
    _ENTRIES,
    GENERIC_SUBSTITUTES,
    FontLookup,
    clean_family_name,
    font_key,
    generic_substitutes,
    guess_category,
    is_icon_font,
    is_known_font,
    lookup_font,
)


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("sohne-var", "Söhne"),
        ("Sohne", "Söhne"),
        ("Söhne", "Söhne"),
        ("Inter Variable", "Inter"),
        ("InterVariable", "Inter"),
        ("Inter var", "Inter"),
        ("inter-var", "Inter"),
        ('"Inter"', "Inter"),
        ('"SF Pro Display"', "SF Pro Display"),
        ("__Inter_d65c78", "Inter"),
        ("__Inter_Fallback_d65c78", "Inter"),
        ("__GeistSans_3a0388", "Geist"),
        ("SFMono-Regular", "SF Mono"),
        ("-apple-system", "system-ui"),
        ("BlinkMacSystemFont", "system-ui"),
        ("GT America Standard", "GT America"),
        ("Helvetica Neue LT", "Helvetica Neue"),
        ("CircularXX", "Circular"),
        ("Circular Std Book", "Circular"),
        ("Tiempos Headline", "Tiempos"),
        ("Source Sans Pro", "Source Sans 3"),
        ("Times", "Times New Roman"),
    ],
)
def test_lookup_normalizes_names(raw: str, name: str) -> None:
    assert lookup_font(raw).name == name


def test_lookup_proprietary_font_has_free_substitutes() -> None:
    assert lookup_font("sohne-var") == FontLookup(
        name="Söhne",
        is_google_font=False,
        category="sans-serif",
        substitutes=("Inter", "Manrope"),
    )
    assert lookup_font('"SF Pro Display"').substitutes == ("Inter", "Roboto")
    assert lookup_font("Graphik").substitutes == ("Inter", "Figtree")
    assert lookup_font("Berkeley Mono").substitutes == ("JetBrains Mono", "Roboto Mono")
    assert lookup_font("Georgia").substitutes == ("Source Serif 4", "Lora")


def test_lookup_google_font_is_free() -> None:
    inter = lookup_font("Inter Variable")
    assert inter.is_google_font and inter.substitutes == () and inter.category == "sans-serif"
    mono = lookup_font("JetBrains Mono")
    assert mono.is_google_font and mono.category == "monospace"
    assert lookup_font("Playfair Display").category == "serif"
    assert lookup_font("Caveat").category == "handwriting"


def test_lookup_keeps_distinct_families_apart() -> None:
    assert lookup_font("Roboto Mono").name == "Roboto Mono"
    assert lookup_font("Roboto Mono").category == "monospace"
    assert lookup_font("Inter Tight").name == "Inter Tight"
    assert lookup_font("Söhne Mono").category == "monospace"
    assert lookup_font("Noto Sans JP").name == "Noto Sans JP"


@pytest.mark.parametrize(
    ("raw", "category", "substitutes"),
    [
        ("Acme Grotesk", "sans-serif", ("Inter", "Roboto")),
        ("Acme Mono", "monospace", ("JetBrains Mono", "Roboto Mono")),
        ("MyCode Font", "monospace", ("JetBrains Mono", "Roboto Mono")),
        ("Acme Serif Display", "serif", ("Source Serif 4", "Lora")),
        ("Acme Sans Serif", "sans-serif", ("Inter", "Roboto")),
        ("Acme Brush Script", "handwriting", ("Caveat", "Kalam")),
    ],
)
def test_unknown_fonts_get_category_from_hints(
    raw: str, category: str, substitutes: tuple[str, ...]
) -> None:
    result = lookup_font(raw)
    assert result.name == raw
    assert not result.is_google_font
    assert result.category == category
    assert result.substitutes == substitutes
    assert not is_known_font(raw)


def test_unknown_variable_font_name_is_cleaned() -> None:
    assert lookup_font("acme-sans-var").name == "acme sans"
    assert lookup_font("AcmeSansVariable").name == "AcmeSans"


def test_clean_family_name_and_key() -> None:
    assert clean_family_name("__Inter_d65c78") == "Inter"
    assert clean_family_name("  'Inter Variable' ") == "Inter"
    assert clean_family_name("SF Pro Display") == "SF Pro Display"
    assert font_key("Inter Variable") == font_key("inter") == "inter"
    assert font_key("SF Pro Display") == "sfprodisplay"
    assert font_key("Söhne") == font_key("sohne-var") == "sohne"


@pytest.mark.parametrize(
    ("family", "expected"),
    [
        ("Material Icons", True),
        ("Material Symbols Outlined", True),
        ("Font Awesome 6 Free", True),
        ("Apple Color Emoji", True),
        ("Segoe UI Emoji", True),
        ("Inter", False),
        ("Söhne", False),
    ],
)
def test_is_icon_font(family: str, expected: bool) -> None:
    assert is_icon_font(family) is expected


def test_guess_category() -> None:
    assert guess_category("Courier Something") == "monospace"
    assert guess_category("Garamond Premier") == "serif"
    assert guess_category("Whatever Sans") == "sans-serif"


def test_table_is_consistent() -> None:
    assert len(_ENTRIES) >= 150
    for entry in _ENTRIES:
        assert lookup_font(entry.name).name == entry.name
        for alias in entry.aliases:
            assert lookup_font(alias).name == entry.name, alias
        if entry.google:
            assert entry.substitutes == ()
        else:
            assert entry.substitutes, entry.name
        for substitute in entry.substitutes:
            assert lookup_font(substitute).is_google_font, (entry.name, substitute)
    for category, substitutes in GENERIC_SUBSTITUTES.items():
        assert generic_substitutes(category) == substitutes
        assert all(lookup_font(name).is_google_font for name in substitutes)
