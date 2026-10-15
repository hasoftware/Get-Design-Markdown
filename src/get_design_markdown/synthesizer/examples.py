"""Illustrative "ex-*" surfaces for --examples (off by default).

Lesson from DESIGN-1win.md: example surfaces there referenced tokens that did not exist and
paired white text with a near-white background (1.12:1). Here every example is assembled only
from tokens defined in the front matter, and any example whose text/background contrast is
below WCAG AA (4.5:1) is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from get_design_markdown.analyzer.css_values import AA_NORMAL
from get_design_markdown.analyzer.naming import COLORS, ROUNDED, SPACING, TYPOGRAPHY, token_ref
from get_design_markdown.models.analysis import ColorRole
from get_design_markdown.synthesizer.facts import DesignFacts

EXAMPLE_NAMES: Final = ("ex-hero", "ex-feature-card", "ex-pricing-tier", "ex-signup-form")


@dataclass(frozen=True)
class ExampleSurface:
    name: str
    #: Front-matter properties (token references only).
    props: dict[str, str]
    #: WCAG ratio of textColor on backgroundColor.
    ratio: float


def _pick(facts: DesignFacts, group: str, *names: str) -> str | None:
    for name in names:
        if facts.has(group, name):
            return token_ref(group, name)
    return None


def _text_token(facts: DesignFacts) -> str | None:
    picked = _pick(facts, COLORS, "ink")
    if picked is not None:
        return picked
    for name, token in facts.colors.items():
        if token.role == ColorRole.TEXT and not name.startswith("on-"):
            return token_ref(COLORS, name)
    return None


def _component_ref(facts: DesignFacts, component: str, key: str) -> str | None:
    spec = facts.component_by_name(component)
    if spec is None:
        return None
    ref = spec.token_refs.get(key)
    return ref if facts.has_ref(ref) else None


def _largest_spacing(facts: DesignFacts, skip: int = 0) -> str | None:
    names = list(facts.spacing)
    if len(names) <= skip:
        return None
    return token_ref(SPACING, names[-1 - skip])


def _first_level(facts: DesignFacts, *preferred: str) -> str | None:
    picked = _pick(facts, TYPOGRAPHY, *preferred)
    if picked is not None:
        return picked
    first = next(iter(facts.levels), None)
    return None if first is None else token_ref(TYPOGRAPHY, first)


def _candidates(facts: DesignFacts) -> list[tuple[str, dict[str, str | None]]]:
    text = _text_token(facts)
    canvas = _pick(facts, COLORS, "canvas")
    surface = _pick(facts, COLORS, "surface", "surface-raised", "canvas")
    card_background = _component_ref(facts, "card", "backgroundColor") or surface
    card_rounded = _component_ref(facts, "card", "rounded") or _pick(
        facts, ROUNDED, "xl", "lg", "md"
    )
    card_padding = _component_ref(facts, "card", "padding") or _pick(facts, SPACING, "xl", "lg")
    input_rounded = _component_ref(facts, "text-input", "rounded") or _pick(
        facts, ROUNDED, "md", "sm"
    )
    return [
        (
            "ex-hero",
            {
                "backgroundColor": canvas,
                "textColor": text,
                "typography": _first_level(facts, "display", "heading"),
                "padding": _largest_spacing(facts),
            },
        ),
        (
            "ex-feature-card",
            {
                "backgroundColor": card_background,
                "textColor": text,
                "typography": _first_level(facts, "title", "subtitle", "heading"),
                "rounded": card_rounded,
                "padding": card_padding,
            },
        ),
        (
            "ex-pricing-tier",
            {
                "backgroundColor": surface,
                "textColor": text,
                "typography": _first_level(facts, "heading", "title", "display"),
                "rounded": card_rounded,
                "padding": _largest_spacing(facts, skip=1) or card_padding,
            },
        ),
        (
            "ex-signup-form",
            {
                "backgroundColor": surface,
                "textColor": text,
                "typography": _first_level(facts, "body", "body-sm", "ui"),
                "rounded": input_rounded,
                "padding": _pick(facts, SPACING, "xl", "lg", "md"),
            },
        ),
    ]


def build_examples(facts: DesignFacts) -> list[ExampleSurface]:
    """Example surfaces built from existing tokens that pass WCAG AA."""
    examples: list[ExampleSurface] = []
    for name, wanted in _candidates(facts):
        props = {key: value for key, value in wanted.items() if value is not None}
        background = facts.resolve(props.get("backgroundColor"))
        foreground = facts.resolve(props.get("textColor"))
        if background is None or foreground is None:
            continue
        ratio = facts.contrast_on(foreground, background)
        if ratio is None or ratio < AA_NORMAL:
            continue
        examples.append(ExampleSurface(name=name, props=props, ratio=ratio))
    return examples
