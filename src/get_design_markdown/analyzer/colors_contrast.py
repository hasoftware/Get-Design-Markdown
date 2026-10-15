"""WCAG contrast pairs: every own-text element's color over its effective background."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from get_design_markdown.analyzer.colors_samples import ColorSample
from get_design_markdown.analyzer.common import own_background
from get_design_markdown.analyzer.css_values import (
    contrast_levels,
    contrast_ratio,
    is_large_text,
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


@dataclass(slots=True)
class _PairGroup:
    sample: ColorSample
    occurrences: int = 0
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
    fails WCAG AA, sorted by occurrences (then lowest ratio first)."""
    groups: dict[tuple[str, str], _PairGroup] = {}
    order: list[tuple[str, str]] = []
    for sample in text_samples:
        if sample.traits.on_image:
            continue
        key = (sample.color.css_hex, sample.traits.effective_background.css_hex)
        group = groups.get(key)
        if group is None:
            group = groups[key] = _PairGroup(sample)
            order.append(key)
        group.occurrences += 1
        group.contexts[pair_context(sample)] += 1
    text_tokens = [token for token in tokens if token.role not in NON_TEXT_ROLES]
    pairs: list[ContrastPair] = []
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
    frequent = sorted(pairs, key=lambda pair: (-pair.occurrences, pair.ratio))
    kept = frequent[:MAX_FREQUENT_PAIRS]
    kept.extend(pair for pair in frequent[MAX_FREQUENT_PAIRS:] if not pair.passes_aa)
    return sorted(
        kept,
        key=lambda pair: (-pair.occurrences, pair.ratio, pair.foreground, pair.background),
    )
