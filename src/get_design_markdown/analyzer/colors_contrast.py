"""WCAG contrast pairs: every own-text element's color over its effective background.

occurrences is the raw number of elements; pairs are ranked by page-weighted occurrences (main
page 1.0, sub-pages per common.page_weights), so a long sub-page cannot push the main page's
pairs out of the list.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from get_design_markdown.analyzer.colors_roles import is_chromatic
from get_design_markdown.analyzer.colors_samples import ColorSample
from get_design_markdown.analyzer.common import own_background
from get_design_markdown.analyzer.css_values import (
    contrast_levels,
    contrast_ratio,
    is_large_text,
    parse_color,
    parse_font_weight,
    parse_px,
)
from get_design_markdown.analyzer.naming import match_color_token
from get_design_markdown.models.analysis import ColorRole, ColorToken, ContrastPair

#: Pairs kept besides the failing ones (sorted by occurrences).
MAX_FREQUENT_PAIRS = 20
#: Below this ratio the text is invisible on its computed background: it is drawn over
#: something the capture cannot see (image, canvas, animation), so the pair is not reported.
MIN_REPORTED_RATIO = 1.2
FOREGROUND_ROLES = frozenset(
    {
        ColorRole.TEXT,
        ColorRole.TEXT_MUTED,
        ColorRole.BRAND,
        ColorRole.ACCENT,
        ColorRole.SUCCESS,
        ColorRole.WARNING,
        ColorRole.DANGER,
        ColorRole.INFO,
    }
)
NON_TEXT_ROLES = frozenset({ColorRole.BORDER, ColorRole.BACKGROUND, ColorRole.SURFACE})
BACKGROUND_ROLES = frozenset({ColorRole.BACKGROUND, ColorRole.SURFACE})
ACTION_ROLES = frozenset({ColorRole.BRAND, ColorRole.ACCENT})


def _names_text(token: ColorToken) -> bool:
    """Whether text of the token's color is "text in that token": not for surface, border or
    canvas tokens, nor for a neutral action fill (a light-gray "invert" button background is
    not the color of gray body text that happens to be close to it)."""
    if token.role in NON_TEXT_ROLES:
        return False
    if token.role in ACTION_ROLES:
        color = parse_color(token.value)
        return color is not None and is_chromatic(color)
    return True


@dataclass(slots=True)
class _PairGroup:
    sample: ColorSample
    occurrences: int = 0
    #: Page weights of the occurrences (summed with math.fsum at the end).
    weights: list[float] = field(default_factory=list)
    contexts: Counter[str] = field(default_factory=Counter)


def pair_context(sample: ColorSample) -> str:
    """ "button", "link", "heading", "footer text", "placeholder" or "body text", plus
    " (large)" for WCAG large text."""
    traits = sample.traits
    element = sample.ref.element
    if sample.placeholder:
        return "placeholder"
    if traits.button_like and own_background(element) is not None:
        base = "button"
    elif traits.link:
        base = "link"
    elif traits.heading:
        base = "heading"
    elif traits.landmark == "footer":
        base = "footer text"
    else:
        base = "body text"
    size = parse_px(element.styles.font_size) or 16.0
    if is_large_text(size, parse_font_weight(element.styles.font_weight)):
        return f"{base} (large)"
    return base


def contrast_pairs(
    text_samples: Sequence[ColorSample], tokens: Sequence[ColorToken]
) -> list[ContrastPair]:
    """Foreground/background pairs grouped by colors; the 20 most frequent plus every pair that
    fails WCAG AA, sorted by page-weighted occurrences (then raw occurrences, then lowest ratio
    first)."""
    groups: dict[tuple[str, str], _PairGroup] = {}
    order: list[tuple[str, str]] = []
    for sample in text_samples:
        if sample.traits.on_image or sample.traits.layered_text:
            continue
        key = (sample.color.css_hex, sample.traits.effective_background.css_hex)
        group = groups.get(key)
        if group is None:
            group = groups[key] = _PairGroup(sample)
            order.append(key)
        group.occurrences += 1
        group.weights.append(sample.ref.weight)
        group.contexts[pair_context(sample)] += 1
    text_tokens = [token for token in tokens if _names_text(token)]
    pairs: list[ContrastPair] = []
    weighted: dict[tuple[str, str], float] = {}
    for key in order:
        group = groups[key]
        foreground = group.sample.color
        background = group.sample.traits.effective_background
        ratio = round(contrast_ratio(foreground, background), 2)
        if ratio < MIN_REPORTED_RATIO:
            continue
        passes_aa, passes_aa_large, passes_aaa = contrast_levels(ratio)
        foreground_token = match_color_token(foreground, text_tokens, prefer_roles=FOREGROUND_ROLES)
        background_token = match_color_token(background, tokens, prefer_roles=BACKGROUND_ROLES)
        context = max(group.contexts.items(), key=lambda item: (item[1], item[0] != "placeholder"))[
            0
        ]
        weighted[(foreground.css_hex, background.css_hex)] = math.fsum(group.weights)
        pairs.append(
            ContrastPair(
                foreground=foreground.css_hex,
                background=background.css_hex,
                foreground_token=None if foreground_token is None else foreground_token.name,
                background_token=None if background_token is None else background_token.name,
                ratio=max(ratio, 1.0),
                passes_aa=passes_aa,
                passes_aa_large=passes_aa_large,
                passes_aaa=passes_aaa,
                occurrences=group.occurrences,
                context=context,
            )
        )

    def rank(pair: ContrastPair) -> tuple[float, int, float, str, str]:
        return (
            -weighted[(pair.foreground, pair.background)],
            -pair.occurrences,
            pair.ratio,
            pair.foreground,
            pair.background,
        )

    frequent = sorted(pairs, key=rank)
    kept = frequent[:MAX_FREQUENT_PAIRS]
    kept.extend(pair for pair in frequent[MAX_FREQUENT_PAIRS:] if not pair.passes_aa)
    return sorted(kept, key=rank)
