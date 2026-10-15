"""Phrase catalogs: en/vi parity, formatting, unknown languages."""

from __future__ import annotations

import re

import pytest

from get_design_markdown.errors import GdmError
from get_design_markdown.synthesizer import render, validate
from get_design_markdown.synthesizer.catalog import (
    LANGS,
    Phrases,
    UnsupportedLanguageError,
    flatten_catalog,
    load_phrases,
    placeholders,
)

_VIETNAMESE_MARKS = re.compile(
    r"[ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ]"
)


def test_languages_match_render_constants() -> None:
    assert LANGS == render.SUPPORTED_LANGS
    assert validate.SECTION_ORDER == render.DESIGN_SECTIONS


def test_en_and_vi_define_identical_keys() -> None:
    en = load_phrases("en")
    vi = load_phrases("vi")
    assert en.phrase_keys() == vi.phrase_keys()
    assert len(en.phrase_keys()) > 300


def test_en_and_vi_use_identical_placeholders() -> None:
    en = load_phrases("en")
    vi = load_phrases("vi")
    mismatched = [
        key
        for key in en.phrase_keys()
        if placeholders(en.template(key)) != placeholders(vi.template(key))
    ]
    assert mismatched == []


@pytest.mark.parametrize("lang", LANGS)
def test_every_phrase_formats(lang: str) -> None:
    phrases = load_phrases(lang)
    for key in phrases.phrase_keys():
        values = dict.fromkeys(placeholders(phrases.template(key)), "X")
        assert isinstance(phrases.get(key, **values), str)


def test_vietnamese_catalog_has_diacritics_and_no_failing_pair_praise() -> None:
    vi = load_phrases("vi")
    texts = [vi.template(key) for key in vi.phrase_keys()]
    with_marks = [text for text in texts if _VIETNAMESE_MARKS.search(text.lower())]
    assert len(with_marks) > len(texts) * 0.6
    en = load_phrases("en")
    for key in en.phrase_keys():
        if key.startswith("prompt."):
            continue
        assert "legible" not in en.template(key).lower()
        assert "readable" not in en.template(key).lower()
        assert "dễ đọc" not in vi.template(key).lower()


def test_do_and_dont_prefixes() -> None:
    en = load_phrases("en")
    vi = load_phrases("vi")
    for key in en.phrase_keys():
        if key.startswith("dos.") and not key.endswith("_item"):
            assert en.template(key).startswith("Do ")
            assert vi.template(key).startswith("Nên ")
        if key.startswith("donts.") and ".remedy." not in key and not key.endswith("_item"):
            assert en.template(key).startswith("Don't ")
            assert vi.template(key).startswith("Không nên ")


def test_get_formats_and_missing_key_raises() -> None:
    phrases = Phrases("en", {"a": {"b": "Hello {name}"}, "n": 3})
    assert phrases.get("a.b", name="world") == "Hello world"
    assert phrases.get("n") == "3"
    assert phrases.has("a.b") and not phrases.has("a")
    with pytest.raises(KeyError):
        phrases.get("missing")
    assert phrases.first(("missing", "a.b"), name="x") == "Hello x"
    with pytest.raises(KeyError):
        phrases.first(("x", "y"))


def test_values_with_braces_are_not_reformatted() -> None:
    phrases = load_phrases("en")
    text = phrases.get("colors.used_by", components="{colors.primary}")
    assert text == "used by {colors.primary}"


def test_join_localized() -> None:
    en = load_phrases("en")
    vi = load_phrases("vi")
    assert en.join([]) == ""
    assert en.join(["a"]) == "a"
    assert en.join(["a", "b"]) == "a and b"
    assert en.join(["a", "b", "c"]) == "a, b and c"
    assert vi.join(["a", "b", "c"]) == "a, b và c"


def test_unknown_language_is_a_gdm_error_in_vietnamese() -> None:
    with pytest.raises(UnsupportedLanguageError) as info:
        load_phrases("fr")
    assert isinstance(info.value, GdmError)
    assert "en, vi" in info.value.message
    assert "Ngôn ngữ" in info.value.message


def test_flatten_and_placeholders() -> None:
    assert flatten_catalog({"a": {"b": {"c": "x"}}, "d": None}) == {"a.b.c": "x", "d": ""}
    assert placeholders("{site} has {count} and {{literal}}") == {"site", "count"}
