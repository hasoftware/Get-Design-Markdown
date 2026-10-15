"""Phrase catalogs (synthesizer/phrases/en.yaml, vi.yaml) used by the narrative rules
(stream: synthesizer, phase GĐ6).

A catalog is a nested YAML mapping whose leaves are str.format templates. Nested keys are
flattened into dotted keys: ``overview: {canvas: {near_black: ...}}`` becomes
``"overview.canvas.near_black"``. Every language file must define exactly the same keys with
the same placeholders (tests enforce it), so the narrative code never depends on the language.
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from functools import lru_cache
from typing import Final

import yaml

from get_design_markdown.errors import GdmError
from get_design_markdown.resources import resource_text

#: Languages with a phrase file (kept equal to render.SUPPORTED_LANGS).
LANGS: Final = ("en", "vi")


class UnsupportedLanguageError(GdmError):
    """No phrase catalog exists for the requested DESIGN.md language."""


def flatten_catalog(catalog: Mapping[str, object], prefix: str = "") -> dict[str, str]:
    """Nested mapping -> {"dotted.key": template}; scalar leaves are converted to str."""
    flat: dict[str, str] = {}
    for key, value in catalog.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(flatten_catalog(value, f"{dotted}."))
        elif value is None:
            flat[dotted] = ""
        else:
            flat[dotted] = str(value)
    return flat


def placeholders(template: str) -> frozenset[str]:
    """Field names used by a str.format template ("{site} has {n}" -> {"site", "n"})."""
    return frozenset(
        field.split(".")[0].split("[")[0]
        for _, field, _, _ in string.Formatter().parse(template)
        if field
    )


class Phrases:
    """Lookup of phrase templates by dotted key ("overview.canvas.near_black")."""

    def __init__(self, lang: str, catalog: Mapping[str, object]) -> None:
        self.lang = lang
        self.catalog = catalog
        self._flat = flatten_catalog(catalog)

    def get(self, key: str, **values: object) -> str:
        """Format the phrase `key` with str.format(**values); missing key -> KeyError."""
        try:
            template = self._flat[key]
        except KeyError:
            raise KeyError(f"Missing phrase {key!r} in the {self.lang!r} catalog") from None
        return template.format(**values)

    def has(self, key: str) -> bool:
        return key in self._flat

    def first(self, keys: tuple[str, ...], **values: object) -> str:
        """The first existing phrase among `keys` (most specific first), formatted."""
        for key in keys:
            if key in self._flat:
                return self.get(key, **values)
        raise KeyError(f"None of the phrases {keys!r} exist in the {self.lang!r} catalog")

    def phrase_keys(self) -> frozenset[str]:
        return frozenset(self._flat)

    def template(self, key: str) -> str:
        """The raw template of `key` (KeyError when missing)."""
        return self._flat[key]

    def join(self, items: list[str]) -> str:
        """Natural-language list: "a", "a and b", "a, b and c" (localized conjunction)."""
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        separator = self.get("list.separator")
        return separator.join(items[:-1]) + self.get("list.and") + items[-1]


@lru_cache(maxsize=len(LANGS))
def _catalog(lang: str) -> dict[str, object]:
    data = yaml.safe_load(resource_text("synthesizer", "phrases", f"{lang}.yaml"))
    if not isinstance(data, dict):
        raise ValueError(f"Phrase catalog {lang}.yaml must be a mapping")
    return data


def load_phrases(lang: str) -> Phrases:
    """Load synthesizer/phrases/<lang>.yaml (resources.resource_text); unknown lang ->
    GdmError subclass with a Vietnamese message listing en, vi."""
    if lang not in LANGS:
        raise UnsupportedLanguageError(
            f"Ngôn ngữ '{lang}' chưa được hỗ trợ cho DESIGN.md — chọn một trong: "
            f"{', '.join(LANGS)}.",
            hint="Dùng --lang en (mặc định) hoặc --lang vi.",
        )
    return Phrases(lang, _catalog(lang))
