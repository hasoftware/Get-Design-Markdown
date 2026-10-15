"""Semantic color tokens from the clusters: canvas, surfaces, text, hairlines, brand, status.

Every token is a cluster representative, i.e. a color really used on the page. CSS variable
names only rank candidates that qualify anyway; status tokens need explicit evidence (a variable
name, a class name or a ✓/⚠/✕ symbol) and are never inferred from the hue alone.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from get_design_markdown.analyzer.colors_cluster import Cluster
from get_design_markdown.analyzer.colors_hints import (
    STATUS_WORDS,
    is_generated_name,
    name_words,
    status_from_text,
    status_from_words,
)
from get_design_markdown.analyzer.colors_samples import ColorSample
from get_design_markdown.analyzer.common import ancestors, element_index
from get_design_markdown.analyzer.css_values import (
    NEUTRAL_MAX_CHROMA,
    OKLCH,
    RGBA,
    WHITE,
    contrast_ratio,
    delta_e_2000,
    flatten,
    to_oklch,
)
from get_design_markdown.analyzer.naming import COLOR_TOKEN_ORDER, COLOR_TOKEN_ROLES
from get_design_markdown.models.analysis import ColorChannel, ColorToken
from get_design_markdown.models.raw import ElementSample, PageCapture

MAX_TOKENS = 14
#: Lowest-priority tokens are dropped first when more than MAX_TOKENS qualify.
DROP_ORDER = (
    "tertiary",
    "on-secondary",
    "info",
    "hairline-strong",
    "surface-raised",
    "warning",
    "on-inverse",
    "secondary",
)
#: Neutral chroma limit by OKLCH lightness: dark tints (navy ink, slate) stay neutral up to
#: 0.1, mid tones up to 0.06, very light tints only up to 0.03 (a pale pink is not a gray).
DARK_NEUTRAL_MAX_CHROMA = 0.1
DARK_NEUTRAL_LIGHTNESS = (0.3, 0.4)
LIGHT_NEUTRAL_MAX_CHROMA = 0.03
LIGHT_NEUTRAL_LIGHTNESS = (0.8, 0.95)
#: Brand and status colors need at least this OKLCH chroma (and must not be neutral).
CHROMATIC_MIN_CHROMA = NEUTRAL_MAX_CHROMA
#: A CSS variable hint only promotes a candidate scoring at least this share of the best one.
HINT_MIN_SCORE_SHARE = 0.5
#: "Sits on canvas/surface": effective background this close (ΔE2000) to a base surface.
ON_BASE_DELTA_E = 3.0
SURFACE_MIN_SHARE = 0.02
SURFACE_CARD_MIN_SHARE = 0.01
SURFACE_MAX_LIGHTNESS_GAP = 0.15
SURFACE_MIN_DELTA_E = 1.0
INVERSE_MIN_SHARE = 0.01
INVERSE_FALLBACK_MIN_DELTA_E = 2.0
INVERSE_FALLBACK_MIN_LIGHTNESS_GAP = 0.03
INK_SIGNIFICANT_SHARE = 0.25
TEXT_ROLE_MIN_DELTA_E = 5.0
#: Muted text is clearly dimmer than ink, faint clearly dimmer than muted (OKLCH lightness).
MUTED_MIN_LIGHTNESS_GAP = 0.15
FAINT_MIN_LIGHTNESS_GAP = 0.08
MUTED_MIN_CONTRAST = 3.0
FAINT_MIN_CONTRAST = 1.5
FAINT_MIN_TEXT_SHARE = 0.003
HAIRLINE_STRONG_MIN_DELTA_E = 3.0
ACTION_MIN_USES = 2
ACCENT_MIN_HUE_GAP = 30.0
FALLBACK_BRAND_MIN_SHARE = 0.005
STATUS_VARIABLE_SCORE = 10.0
STATUS_ORDER = ("success", "warning", "danger", "info")
CLASS_HINT_DEPTH = 2


# ─── Color predicates ────────────────────────────────────────────────────────


def neutral_chroma_limit(lightness: float) -> float:
    """Largest OKLCH chroma of a (tinted) neutral at this lightness."""
    dark_full, dark_end = DARK_NEUTRAL_LIGHTNESS
    light_start, light_full = LIGHT_NEUTRAL_LIGHTNESS
    if lightness <= dark_full:
        return DARK_NEUTRAL_MAX_CHROMA
    if lightness < dark_end:
        ratio = (dark_end - lightness) / (dark_end - dark_full)
        return NEUTRAL_MAX_CHROMA + ratio * (DARK_NEUTRAL_MAX_CHROMA - NEUTRAL_MAX_CHROMA)
    if lightness <= light_start:
        return NEUTRAL_MAX_CHROMA
    if lightness < light_full:
        ratio = (lightness - light_start) / (light_full - light_start)
        return NEUTRAL_MAX_CHROMA - ratio * (NEUTRAL_MAX_CHROMA - LIGHT_NEUTRAL_MAX_CHROMA)
    return LIGHT_NEUTRAL_MAX_CHROMA


def is_neutralish(color: RGBA) -> bool:
    """Gray or tinted neutral (slate, navy ink, warm paper); the allowed chroma shrinks for
    very light colors and grows for very dark ones (see neutral_chroma_limit)."""
    oklch = to_oklch(color)
    return oklch.chroma < neutral_chroma_limit(oklch.lightness)


def is_chromatic(color: RGBA) -> bool:
    """A real hue for brand/status roles: OKLCH chroma >= 0.06 and not a tinted neutral."""
    return to_oklch(color).chroma >= CHROMATIC_MIN_CHROMA and not is_neutralish(color)


def hue_gap(first: OKLCH, second: OKLCH) -> float:
    if first.hue is None or second.hue is None:
        return 180.0
    gap = abs(first.hue - second.hue) % 360.0
    return min(gap, 360.0 - gap)


# ─── Assignment state ────────────────────────────────────────────────────────


@dataclass(slots=True)
class Pick:
    name: str
    cluster: Cluster
    evidence: str


@dataclass(slots=True)
class _TextStats:
    on_base_weight: float = 0.0
    on_base_elements: set[tuple[int, int]] = field(default_factory=set)
    on_base_samples: list[ColorSample] = field(default_factory=list)
    contrast: float = 1.0


@dataclass(slots=True)
class RoleContext:
    clusters: dict[ColorChannel, list[Cluster]]
    canvas_cluster: Cluster | None
    canvas: RGBA
    threshold: float
    text_total: float
    picks: dict[str, Pick] = field(default_factory=dict)
    _indexes: dict[int, dict[int, ElementSample]] = field(default_factory=dict)
    _apparent: dict[str, RGBA] = field(default_factory=dict)

    def apparent(self, cluster: Cluster) -> RGBA:
        """The cluster color as seen on the canvas (translucent colors flattened)."""
        key = f"{cluster.channel}:{cluster.id}"
        color = self._apparent.get(key)
        if color is None:
            color = flatten(cluster.rep, self.canvas)
            self._apparent[key] = color
        return color

    def index(self, page: PageCapture) -> dict[int, ElementSample]:
        found = self._indexes.get(id(page))
        if found is None:
            found = self._indexes[id(page)] = element_index(page)
        return found

    def pick(self, name: str, cluster: Cluster, evidence: str) -> Pick:
        chosen = Pick(name, cluster, evidence)
        self.picks[name] = chosen
        return chosen

    def picked(self, name: str) -> Cluster | None:
        chosen = self.picks.get(name)
        return None if chosen is None else chosen.cluster

    def base_colors(self) -> list[RGBA]:
        colors = [self.canvas]
        for name in ("surface", "surface-raised"):
            cluster = self.picked(name)
            if cluster is not None:
                colors.append(self.apparent(cluster))
        return colors


def _hinted(cluster: Cluster, name: str) -> bool:
    return any(name in variable.hints for variable in cluster.variables)


def _hint_variable(cluster: Cluster, name: str) -> str | None:
    """The most generic (fewest words, then shortest) variable hinting `name`."""
    names = [variable.name for variable in cluster.variables if name in variable.hints]
    if not names:
        return None
    return min(names, key=_variable_rank)


def _variable_rank(name: str) -> tuple[int, int, int, str]:
    """Prefer names that say "color", then the fewest words, then the shortest."""
    words = name_words(name)
    return (0 if {"color", "colour", "colors"} & set(words) else 1, len(words), len(name), name)


def _ranked(
    candidates: Iterable[Cluster],
    name: str,
    score: Callable[[Cluster], float],
    *,
    gate: Callable[[Cluster], float] | None = None,
) -> list[Cluster]:
    """By descending score; a candidate hinted by a CSS variable goes first when its gate
    measure (default: the score) reaches half of the best one — a variable name never beats a
    color used far more."""
    pool = list(candidates)
    measure = gate or score
    best = max((measure(cluster) for cluster in pool), default=0.0)

    def promoted(cluster: Cluster) -> bool:
        return _hinted(cluster, name) and measure(cluster) >= HINT_MIN_SCORE_SHARE * best

    return sorted(
        pool,
        key=lambda cluster: (
            0 if promoted(cluster) else 1,
            -score(cluster),
            -cluster.weight,
            cluster.id,
        ),
    )


def _on_colors(sample: ColorSample, colors: Sequence[RGBA]) -> bool:
    if sample.traits.on_image:
        return False
    background = sample.traits.effective_background
    return any(delta_e_2000(background, color) < ON_BASE_DELTA_E for color in colors)


def _percent(share: float) -> str:
    value = share * 100
    return f"{value:.1f}%" if value < 10 else f"{value:.0f}%"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def describe_elements(samples: Iterable[ColorSample]) -> str:
    """ "3 buttons, 2 links" — the two most common element kinds of the samples."""
    kinds: dict[str, set[tuple[int, int]]] = {}
    weights: dict[str, float] = {}
    for sample in samples:
        traits = sample.traits
        element = sample.ref.element
        if sample.synthetic is not None or element.tag in ("html", "body"):
            kind = "page root"
        elif sample.placeholder:
            kind = "placeholder"
        elif traits.button_like:
            kind = "button"
        elif traits.link:
            kind = "link"
        elif element.tag in ("input", "select", "textarea"):
            kind = "input"
        elif traits.heading:
            kind = "heading"
        elif traits.card_like:
            kind = "card"
        elif traits.full_width or element.tag in ("section", "header", "footer", "main"):
            kind = "section"
        else:
            kind = "element"
        kinds.setdefault(kind, set()).add(sample.key)
        weights[kind] = weights.get(kind, 0.0) + sample.weight
    ordered = sorted(kinds.items(), key=lambda item: (-weights[item[0]], item[0]))
    parts: list[str] = []
    for kind, keys in ordered[:2]:
        parts.append(kind if kind == "page root" else _plural(len(keys), kind))
    return ", ".join(parts) if parts else "no elements"


# ─── Canvas & surfaces ───────────────────────────────────────────────────────


def choose_canvas(backgrounds: Sequence[Cluster]) -> Cluster | None:
    """Heaviest opaque background of html/body/main and full-width bands."""
    best: Cluster | None = None
    best_score = 0.0
    for cluster in backgrounds:
        if cluster.decorative or not cluster.rep.is_opaque:
            continue
        score = sum(
            sample.weight
            for sample in cluster.samples
            if sample.synthetic is not None or sample.traits.page_root or sample.traits.full_width
        )
        if score > best_score:
            best, best_score = cluster, score
    return best


def _canvas_evidence(context: RoleContext, cluster: Cluster) -> str:
    if any(sample.synthetic is not None for sample in cluster.samples) and all(
        sample.synthetic is not None or sample.traits.page_root for sample in cluster.samples
    ):
        return "browser default canvas behind transparent html/body"
    page_level = [
        sample
        for sample in cluster.samples
        if sample.synthetic is not None or sample.traits.page_root or sample.traits.full_width
    ]
    return (
        f"page background ({describe_elements(page_level)}); "
        f"{_percent(cluster.share)} of background area"
    )


def _background_evidence(cluster: Cluster) -> str:
    return (
        f"background of {describe_elements(cluster.samples)}; "
        f"{_percent(cluster.share)} of background area"
    )


def _card_weight(cluster: Cluster) -> float:
    return sum(sample.weight for sample in cluster.samples if sample.traits.card_like)


def _lightness(color: RGBA) -> float:
    return to_oklch(color).lightness


def assign_surfaces(context: RoleContext) -> None:
    backgrounds = context.clusters["background"]
    canvas = context.canvas
    canvas_lightness = _lightness(canvas)

    def neutral_near_canvas(cluster: Cluster) -> bool:
        if cluster.decorative or cluster is context.canvas_cluster:
            return False
        color = context.apparent(cluster)
        return (
            is_neutralish(color)
            and abs(_lightness(color) - canvas_lightness) < SURFACE_MAX_LIGHTNESS_GAP
            and delta_e_2000(color, canvas) >= SURFACE_MIN_DELTA_E
        )

    surfaces = [
        cluster
        for cluster in backgrounds
        if neutral_near_canvas(cluster)
        and (
            cluster.share >= SURFACE_MIN_SHARE
            or (cluster.share >= SURFACE_CARD_MIN_SHARE and _card_weight(cluster) > 0)
        )
    ]
    ranked = _ranked(surfaces, "surface", lambda cluster: cluster.weight)
    surface = ranked[0] if ranked else None
    if surface is not None:
        context.pick("surface", surface, _background_evidence(surface))
        surface_color = context.apparent(surface)
        raised = [
            cluster
            for cluster in backgrounds
            if cluster is not surface
            and neutral_near_canvas(cluster)
            and cluster.share >= SURFACE_CARD_MIN_SHARE
            and _card_weight(cluster) > 0
            and delta_e_2000(context.apparent(cluster), surface_color) >= SURFACE_MIN_DELTA_E
        ]
        ranked_raised = _ranked(raised, "surface-raised", _card_weight)
        if ranked_raised:
            chosen = ranked_raised[0]
            context.pick("surface-raised", chosen, _background_evidence(chosen))

    dark_canvas = canvas_lightness < 0.5
    inverse = [
        cluster
        for cluster in backgrounds
        if not cluster.decorative
        and cluster is not context.canvas_cluster
        and cluster.share >= INVERSE_MIN_SHARE
        and is_neutralish(context.apparent(cluster))
        and (_lightness(context.apparent(cluster)) < 0.5) != dark_canvas
    ]
    if not inverse and dark_canvas:
        inverse = _deep_bands(context, surface)
    ranked_inverse = _ranked(inverse, "surface-inverse", lambda cluster: cluster.weight)
    if ranked_inverse:
        chosen = ranked_inverse[0]
        context.pick("surface-inverse", chosen, _background_evidence(chosen))


def _deep_bands(context: RoleContext, surface: Cluster | None) -> list[Cluster]:
    """Dark themes: a band (footer, full-width section) that steps away from the canvas in the
    direction opposite to the surfaces — the "inverse" band of a dark page."""
    canvas = context.canvas
    canvas_lightness = _lightness(canvas)
    surface_direction = -1.0
    if surface is not None:
        surface_direction = math.copysign(
            1.0, _lightness(context.apparent(surface)) - canvas_lightness
        )
    result: list[Cluster] = []
    for cluster in context.clusters["background"]:
        if cluster.decorative or cluster is context.canvas_cluster or cluster is surface:
            continue
        if cluster.share < INVERSE_MIN_SHARE:
            continue
        color = context.apparent(cluster)
        if not is_neutralish(color):
            continue
        gap = _lightness(color) - canvas_lightness
        if abs(gap) < INVERSE_FALLBACK_MIN_LIGHTNESS_GAP or math.copysign(1.0, gap) == (
            surface_direction
        ):
            continue
        if delta_e_2000(color, canvas) < INVERSE_FALLBACK_MIN_DELTA_E:
            continue
        if surface is not None and (
            delta_e_2000(color, context.apparent(surface)) < INVERSE_FALLBACK_MIN_DELTA_E
        ):
            continue
        if not any(
            sample.traits.full_width or sample.traits.landmark == "footer"
            for sample in cluster.samples
        ):
            continue
        result.append(cluster)
    return result


# ─── Text ────────────────────────────────────────────────────────────────────


def _text_stats(context: RoleContext) -> dict[str, _TextStats]:
    bases = context.base_colors()
    stats: dict[str, _TextStats] = {}
    for cluster in context.clusters["text"]:
        entry = _TextStats(contrast=contrast_ratio(cluster.rep, context.canvas))
        for sample in cluster.samples:
            if _on_colors(sample, bases):
                entry.on_base_weight += sample.weight
                entry.on_base_elements.add(sample.key)
                entry.on_base_samples.append(sample)
        stats[cluster.id] = entry
    return stats


def _text_evidence(context: RoleContext, cluster: Cluster, stats: _TextStats) -> str:
    return (
        f"text of {describe_elements(stats.on_base_samples)} on canvas/surface; "
        f"{stats.contrast:.1f}:1 on canvas"
    )


def assign_text(context: RoleContext) -> None:
    stats = _text_stats(context)
    texts = [
        cluster
        for cluster in context.clusters["text"]
        if not cluster.decorative
        and stats[cluster.id].on_base_weight > 0
        and is_neutralish(context.apparent(cluster))
    ]
    if not texts:
        return
    top = max(stats[cluster.id].on_base_weight for cluster in texts)
    significant = [
        cluster
        for cluster in texts
        if stats[cluster.id].on_base_weight >= INK_SIGNIFICANT_SHARE * top
        and stats[cluster.id].contrast >= FAINT_MIN_CONTRAST
    ]
    hinted_ink = [cluster for cluster in significant if _hinted(cluster, "ink")]
    pool = hinted_ink or significant
    if not pool:
        return
    ink = max(
        pool,
        key=lambda cluster: (
            stats[cluster.id].contrast,
            stats[cluster.id].on_base_weight,
            cluster.id,
        ),
    )
    context.pick("ink", ink, _text_evidence(context, ink, stats[ink.id]))
    ink_color = context.apparent(ink)
    ink_contrast = stats[ink.id].contrast

    ink_lightness = _lightness(ink_color)
    muted_candidates = [
        cluster
        for cluster in texts
        if cluster is not ink
        and delta_e_2000(context.apparent(cluster), ink_color) >= TEXT_ROLE_MIN_DELTA_E
        and abs(_lightness(context.apparent(cluster)) - ink_lightness) >= MUTED_MIN_LIGHTNESS_GAP
        and MUTED_MIN_CONTRAST <= stats[cluster.id].contrast < ink_contrast
    ]
    ranked_muted = _ranked(
        muted_candidates, "muted", lambda cluster: stats[cluster.id].on_base_weight
    )
    muted = ranked_muted[0] if ranked_muted else None
    if muted is not None:
        context.pick("muted", muted, _text_evidence(context, muted, stats[muted.id]))

    reference = muted or ink
    reference_contrast = stats[reference.id].contrast
    reference_lightness = _lightness(context.apparent(reference))
    reference_gap = FAINT_MIN_LIGHTNESS_GAP if muted is not None else MUTED_MIN_LIGHTNESS_GAP
    others = [ink] if muted is None else [ink, muted]
    faint_candidates = [
        cluster
        for cluster in texts
        if cluster not in others
        and all(
            delta_e_2000(context.apparent(cluster), context.apparent(other))
            >= TEXT_ROLE_MIN_DELTA_E
            for other in others
        )
        and FAINT_MIN_CONTRAST <= stats[cluster.id].contrast < reference_contrast
        and abs(_lightness(context.apparent(cluster)) - reference_lightness) >= reference_gap
        and (
            len(stats[cluster.id].on_base_elements) >= 2
            or stats[cluster.id].on_base_weight >= FAINT_MIN_TEXT_SHARE * context.text_total
        )
    ]
    ranked_faint = _ranked(
        faint_candidates,
        "faint",
        lambda cluster: -stats[cluster.id].contrast,
        gate=lambda cluster: stats[cluster.id].on_base_weight,
    )
    if ranked_faint:
        faint = ranked_faint[0]
        context.pick("faint", faint, _text_evidence(context, faint, stats[faint.id]))


def _texts_on(
    context: RoleContext, color: RGBA, *, delta_e: float
) -> dict[str, tuple[Cluster, list[ColorSample], float]]:
    """Text clusters drawn on `color`: id -> (cluster, samples there, their weight)."""
    found: dict[str, tuple[Cluster, list[ColorSample], float]] = {}
    for cluster in context.clusters["text"]:
        samples = [
            sample
            for sample in cluster.samples
            if not sample.placeholder
            and not sample.traits.on_image
            and delta_e_2000(sample.traits.effective_background, color) < delta_e
        ]
        if samples:
            found[cluster.id] = (cluster, samples, sum(sample.weight for sample in samples))
    return found


def _dominant_text_on(
    context: RoleContext, color: RGBA, *, delta_e: float
) -> tuple[Cluster, list[ColorSample]] | None:
    """Heaviest text cluster drawn on `color`, with its samples there."""
    found = _texts_on(context, color, delta_e=delta_e)
    if not found:
        return None
    best_id = min(found, key=lambda key: (-found[key][2], key))
    cluster, samples, _ = found[best_id]
    return cluster, samples


def assign_on_inverse(context: RoleContext) -> None:
    inverse = context.picked("surface-inverse")
    if inverse is None:
        return
    inverse_color = context.apparent(inverse)
    found = _texts_on(context, inverse_color, delta_e=ON_BASE_DELTA_E)
    if not found:
        return
    top = max(weight for _, _, weight in found.values())
    # Like ink: the highest-contrast text among the significant ones on the inverse band.
    cluster, samples, _ = max(
        (entry for entry in found.values() if entry[2] >= INK_SIGNIFICANT_SHARE * top),
        key=lambda entry: (contrast_ratio(entry[0].rep, inverse_color), entry[2], entry[0].id),
    )
    taken = {pick.cluster.rep.css_hex for pick in context.picks.values()}
    if cluster.rep.css_hex in taken:
        return
    ratio = contrast_ratio(cluster.rep, inverse_color)
    context.pick(
        "on-inverse",
        cluster,
        f"text of {describe_elements(samples)} on surface-inverse; {ratio:.1f}:1",
    )


# ─── Borders ─────────────────────────────────────────────────────────────────


def assign_hairlines(context: RoleContext) -> None:
    bases = context.base_colors()
    weights: dict[str, float] = {}
    elements: dict[str, set[tuple[int, int]]] = {}
    candidates: list[Cluster] = []
    for cluster in context.clusters["border"]:
        if cluster.decorative or not is_neutralish(context.apparent(cluster)):
            continue
        on_base = [sample for sample in cluster.samples if _on_colors(sample, bases)]
        if not on_base:
            continue
        weights[cluster.id] = sum(sample.weight for sample in on_base)
        elements[cluster.id] = {sample.key for sample in on_base}
        candidates.append(cluster)
    ranked = _ranked(candidates, "hairline", lambda cluster: weights[cluster.id])
    if not ranked:
        return
    hairline = ranked[0]
    context.pick("hairline", hairline, f"borders of {describe_elements(hairline.samples)}")
    hairline_color = context.apparent(hairline)
    hairline_contrast = contrast_ratio(hairline_color, context.canvas)
    strong = [
        cluster
        for cluster in candidates
        if cluster is not hairline
        and len(elements[cluster.id]) >= 2
        and delta_e_2000(context.apparent(cluster), hairline_color) >= HAIRLINE_STRONG_MIN_DELTA_E
        and contrast_ratio(context.apparent(cluster), context.canvas) > hairline_contrast
    ]
    ranked_strong = _ranked(strong, "hairline-strong", lambda cluster: weights[cluster.id])
    if ranked_strong:
        chosen = ranked_strong[0]
        context.pick("hairline-strong", chosen, f"borders of {describe_elements(chosen.samples)}")


# ─── Brand & actions ─────────────────────────────────────────────────────────


def _button_score(cluster: Cluster) -> float:
    return sum(
        sample.ref.weight * math.sqrt(max(sample.traits.area, 1.0))
        for sample in cluster.samples
        if sample.traits.button_like and sample.synthetic is None
    )


def _button_elements(cluster: Cluster) -> set[tuple[int, int]]:
    return {sample.key for sample in cluster.samples if sample.traits.button_like}


def _link_samples(cluster: Cluster) -> list[ColorSample]:
    return [
        sample
        for sample in cluster.samples
        if not sample.placeholder and (sample.traits.link or sample.traits.button_like)
    ]


def _link_elements(cluster: Cluster) -> set[tuple[int, int]]:
    return {sample.key for sample in _link_samples(cluster)}


def _brand_evidence(cluster: Cluster) -> str:
    parts: list[str] = []
    buttons = len(_button_elements(cluster)) if cluster.channel == "background" else 0
    if buttons:
        parts.append(f"background of {_plural(buttons, 'button')}")
    elif cluster.channel == "text":
        links = len(_link_elements(cluster))
        if links:
            parts.append(f"text of {_plural(links, 'link')}")
    if not parts:
        parts.append(f"{cluster.channel} of {describe_elements(cluster.samples)}")
    return "; ".join(parts)


def assign_brand(context: RoleContext) -> None:
    def chromatic(cluster: Cluster) -> bool:
        return not cluster.decorative and is_chromatic(context.apparent(cluster))

    def enough_buttons(cluster: Cluster) -> bool:
        count = len(_button_elements(cluster))
        return count >= ACTION_MIN_USES or (count == 1 and _hinted(cluster, "primary"))

    buttons = [
        cluster
        for cluster in context.clusters["background"]
        if chromatic(cluster) and enough_buttons(cluster)
    ]
    links = [
        cluster
        for cluster in context.clusters["text"]
        if chromatic(cluster) and len(_link_elements(cluster)) >= ACTION_MIN_USES
    ]
    primary: Cluster | None = None
    ranked_buttons = _ranked(buttons, "primary", _button_score)
    ranked_links = _ranked(links, "primary", lambda cluster: len(_link_elements(cluster)))
    if ranked_buttons:
        primary = ranked_buttons[0]
    elif ranked_links:
        primary = ranked_links[0]
    else:
        fallback = [
            cluster
            for channel in ("background", "text", "border")
            for cluster in context.clusters[channel]
            if chromatic(cluster)
            and cluster.share >= FALLBACK_BRAND_MIN_SHARE
            and _hinted(cluster, "primary")
        ]
        ranked_fallback = _ranked(fallback, "primary", lambda cluster: cluster.share)
        primary = ranked_fallback[0] if ranked_fallback else None
    if primary is None:
        return
    context.pick("primary", primary, _brand_evidence(primary))

    chosen: list[Cluster] = [primary]
    actions = [
        cluster for cluster in ranked_buttons if len(_button_elements(cluster)) >= ACTION_MIN_USES
    ] + ranked_links
    for name in ("secondary", "tertiary"):
        candidates = [
            cluster
            for cluster in actions
            if cluster not in chosen
            and all(
                hue_gap(cluster.oklch, other.oklch) >= ACCENT_MIN_HUE_GAP
                and delta_e_2000(context.apparent(cluster), context.apparent(other))
                >= context.threshold
                for other in chosen
            )
        ]
        if not candidates:
            break
        hinted = [cluster for cluster in candidates if _hinted(cluster, name)]
        accent = (hinted or candidates)[0]
        context.pick(name, accent, _brand_evidence(accent))
        chosen.append(accent)

    for brand_name, on_name in (("primary", "on-primary"), ("secondary", "on-secondary")):
        brand = context.picked(brand_name)
        if brand is None:
            continue
        brand_color = context.apparent(brand)
        dominant = _dominant_text_on(context, brand_color, delta_e=context.threshold)
        if dominant is None:
            continue
        text, samples = dominant
        ratio = contrast_ratio(text.rep, brand_color)
        context.pick(
            on_name,
            text,
            f"text of {describe_elements(samples)} on {brand_name}; {ratio:.1f}:1",
        )


# ─── Status ──────────────────────────────────────────────────────────────────


def _element_status_hint(context: RoleContext, sample: ColorSample) -> str | None:
    element = sample.ref.element
    status = status_from_text(element.text) if element.has_own_text else None
    if status is not None:
        return status
    nodes: list[ElementSample] = [element]
    for depth, ancestor in enumerate(ancestors(element, context.index(sample.ref.page))):
        if depth >= CLASS_HINT_DEPTH:
            break
        nodes.append(ancestor)
    for node in nodes:
        words: list[str] = []
        for name in node.classes:
            words.extend(name_words(name))
        if node.id:
            words.extend(name_words(node.id))
        if node.role == "alert":
            words.append("alert")
        status = status_from_words(words)
        if status is not None:
            return status
    return None


def assign_status(context: RoleContext) -> None:
    brand_ids = {
        id(cluster)
        for name in ("primary", "secondary", "tertiary")
        if (cluster := context.picked(name)) is not None
    }
    brand_colors = [
        context.apparent(cluster)
        for name in ("primary", "secondary", "tertiary")
        if (cluster := context.picked(name)) is not None
    ]
    scores: dict[str, list[tuple[float, int, float, Cluster, str]]] = {
        status: [] for status in STATUS_ORDER
    }
    channel_rank = {"text": 0, "background": 1, "border": 2}
    for channel in ("text", "background", "border"):
        for cluster in context.clusters[channel]:
            if cluster.decorative or id(cluster) in brand_ids:
                continue
            color = context.apparent(cluster)
            if not is_chromatic(color):
                continue
            if any(delta_e_2000(color, brand) < SURFACE_MIN_DELTA_E for brand in brand_colors):
                continue
            reasons: dict[str, list[str]] = {}
            totals: dict[str, float] = {}
            for variable in sorted(cluster.variables, key=lambda item: _variable_rank(item.name)):
                for status in STATUS_ORDER:
                    if status in variable.hints:
                        if status not in totals:
                            reasons.setdefault(status, []).append(f"css var {variable.name}")
                        totals[status] = totals.get(status, 0.0) + STATUS_VARIABLE_SCORE
            hinted_elements: dict[str, set[tuple[int, int]]] = {}
            for sample in cluster.samples:
                found = _element_status_hint(context, sample)
                if found is not None:
                    hinted_elements.setdefault(found, set()).add(sample.key)
            for status, keys in hinted_elements.items():
                totals[status] = totals.get(status, 0.0) + len(keys)
                reasons.setdefault(status, []).append(
                    f"{channel} of {_plural(len(keys), 'element')} with a {status} hint"
                )
            for status, total in totals.items():
                reason = "; ".join(reasons[status])
                scores[status].append(
                    (total, channel_rank[channel], cluster.weight, cluster, reason)
                )
    used: set[int] = set()
    for status in STATUS_ORDER:
        options = sorted(
            (option for option in scores[status] if id(option[3]) not in used),
            key=lambda option: (-option[0], option[1], -option[2], option[3].id),
        )
        if not options:
            continue
        _, _, _, chosen, reason = options[0]
        used.add(id(chosen))
        context.pick(status, chosen, reason)


# ─── Tokens ──────────────────────────────────────────────────────────────────


def _token_variable(pick: Pick) -> str | None:
    return _hint_variable(pick.cluster, pick.name)


def build_tokens(context: RoleContext) -> list[ColorToken]:
    names = [name for name in COLOR_TOKEN_ORDER if name in context.picks]
    for name in DROP_ORDER:
        if len(names) <= MAX_TOKENS:
            break
        if name in names:
            names.remove(name)
    names = names[:MAX_TOKENS] if len(names) > MAX_TOKENS else names
    tokens: list[ColorToken] = []
    for name in names:
        pick = context.picks[name]
        variable = _token_variable(pick)
        evidence = pick.evidence
        mentioned = variable
        neutral_names = [
            item.name
            for item in pick.cluster.variables
            if not item.hints and not is_generated_name(item.name)
        ]
        if mentioned is None and neutral_names:
            # A role-free name ("--blurple", "--gray-900") is still useful evidence; a name
            # hinting another role would contradict the token and is not repeated.
            mentioned = min(neutral_names, key=_variable_rank)
        if mentioned is not None and mentioned not in evidence:
            evidence = f"{evidence}; css var {mentioned}"
        tokens.append(
            ColorToken(
                name=name,
                value=pick.cluster.rep.css_hex,
                role=COLOR_TOKEN_ROLES[name],
                cluster_id=pick.cluster.id or None,
                css_variable=variable,
                evidence=evidence,
            )
        )
    return tokens


def assign_roles(
    clusters: dict[ColorChannel, list[Cluster]],
    *,
    canvas_cluster: Cluster | None,
    threshold: float,
    text_total: float,
) -> tuple[RoleContext, list[ColorToken]]:
    """Pick every semantic token; returns the context (picks) and the ordered tokens."""
    canvas = canvas_cluster.rep if canvas_cluster is not None else WHITE
    context = RoleContext(
        clusters=clusters,
        canvas_cluster=canvas_cluster,
        canvas=canvas,
        threshold=threshold,
        text_total=text_total,
    )
    if canvas_cluster is not None:
        context.pick("canvas", canvas_cluster, _canvas_evidence(context, canvas_cluster))
    else:
        # Nothing opaque paints the page: the browser's default white canvas shows through.
        context.pick(
            "canvas",
            Cluster("background", WHITE),
            "no opaque page background; browser default white canvas",
        )
    assign_surfaces(context)
    assign_text(context)
    assign_hairlines(context)
    assign_brand(context)
    assign_status(context)
    assign_on_inverse(context)
    return context, build_tokens(context)


__all__ = [
    "STATUS_WORDS",
    "RoleContext",
    "assign_roles",
    "choose_canvas",
    "describe_elements",
    "hue_gap",
    "is_neutralish",
]
