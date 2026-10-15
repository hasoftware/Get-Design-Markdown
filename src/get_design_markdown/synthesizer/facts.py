"""Resolved, deduplicated view of a DesignAnalysis shared by the front matter and the narrative.

Everything the synthesizer writes goes through DesignFacts, so the YAML tokens, the prose and
the validator agree: a token reference is only ever emitted when DesignFacts defines it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, TypeVar

from get_design_markdown.analyzer.css_values import (
    AA_LARGE,
    AA_NORMAL,
    BLACK,
    RGBA,
    WHITE,
    contrast_ratio,
    delta_e_2000,
    flatten,
    parse_box_shadow,
    parse_color,
    to_oklch,
)
from get_design_markdown.analyzer.naming import (
    COLOR_TOKEN_ORDER,
    COLOR_TOKEN_ROLES,
    COLORS,
    COMPONENT_NAME_ORDER,
    COMPONENTS,
    FULL_RADIUS_NAME,
    FULL_RADIUS_PX,
    HOVER_STATE,
    PILL_RADIUS_PX,
    ROUNDED,
    SPACING,
    TYPOGRAPHY,
    is_token_name,
    match_color_token,
    parse_token_ref,
    state_variant_name,
    token_ref,
)
from get_design_markdown.models.analysis import (
    ColorCluster,
    ColorRole,
    ColorToken,
    ComponentKind,
    ComponentSpec,
    ContrastPair,
    DesignAnalysis,
    FontFamilyInfo,
    FontRole,
    ScaleStep,
    TypeLevel,
)
from get_design_markdown.synthesizer.colornames import is_chromatic
from get_design_markdown.synthesizer.formatting import fmt_num, fmt_px

T = TypeVar("T")

#: Component properties allowed by the DESIGN.md spec, in the order they are written.
SPEC_COMPONENT_KEYS: Final = (
    "backgroundColor",
    "textColor",
    "typography",
    "rounded",
    "padding",
    "size",
    "height",
    "width",
)
COLOR_PROPERTY_KEYS: Final = frozenset({"backgroundColor", "textColor"})
TEXT_ROLES: Final = frozenset({ColorRole.TEXT, ColorRole.TEXT_MUTED})
STATUS_ROLES: Final = frozenset(
    {ColorRole.SUCCESS, ColorRole.WARNING, ColorRole.DANGER, ColorRole.INFO}
)
SURFACE_ROLES: Final = frozenset({ColorRole.BACKGROUND, ColorRole.SURFACE, ColorRole.NEUTRAL})
#: Kinds in the order components are listed (derived from naming.COMPONENT_NAME_ORDER).
KIND_ORDER: Final = (
    ComponentKind.BUTTON,
    ComponentKind.LINK,
    ComponentKind.INPUT,
    ComponentKind.CARD,
    ComponentKind.BADGE,
    ComponentKind.NAVIGATION,
    ComponentKind.FOOTER,
)
#: Two action colors closer than this (ΔE2000) count as one accent.
SAME_ACCENT_DELTA_E: Final = 3.0
#: A gradient color this close (ΔE2000, similar alpha) to a structural cluster is not decorative.
DECORATIVE_MATCH_DELTA_E: Final = 3.0
DECORATIVE_MATCH_ALPHA: Final = 0.05
#: The page headline (hero title) is the type level whose tags lead with this tag.
HEADLINE_TAG: Final = "h1"
#: A hero lead paragraph is at most this many times the body size.
LEAD_MAX_SCALE: Final = 1.5
#: Background tokens listed for the contrast of a component without its own background.
MAX_SURFACE_CHECKS: Final = 3
#: Two chromatic action colors whose OKLCH hues differ by at most this are one accent family.
SAME_FAMILY_HUE_DEG: Final = 20.0
#: Warning that analysis.json files of earlier versions carry when analyzer parts were skipped
#: (the glue now stops instead), and its labels.
SKIPPED_WARNING_PREFIX: Final = "Chưa phân tích"
SKIPPED_LABELS: Final = {
    "Màu sắc": "colors",
    "Typography": "typography",
    "Spacing": "spacing",
    "Bo góc": "rounded",
    "Đổ bóng": "shadows",
    "Breakpoint": "breakpoints",
    "Layout": "layout",
    "Component": "components",
}


@dataclass(frozen=True)
class ContrastIssue:
    """A measured text/background pair below WCAG AA (4.5:1)."""

    foreground: str
    background: str | None
    foreground_token: str | None
    background_token: str | None
    ratio: float
    context: str
    dark: bool = False
    #: Role of the foreground token (status colors get a different remedy).
    foreground_role: ColorRole | None = None

    @property
    def passes_large(self) -> bool:
        return self.ratio >= AA_LARGE


@dataclass(frozen=True)
class PairCheck:
    """A text/background pair whose ratio is recomputed from the values printed with it (the
    token value when a side is a token), so prose never quotes a ratio measured on a slightly
    different member color next to a token's hex."""

    foreground: str
    foreground_token: str | None
    background: str
    background_token: str | None
    ratio: float

    @property
    def passes(self) -> bool:
        return self.ratio >= AA_NORMAL

    @property
    def passes_large(self) -> bool:
        return self.ratio >= AA_LARGE

    @property
    def key(self) -> tuple[str, str]:
        """(foreground token or hex, background token or hex): one entry per printed pair."""
        return (
            self.foreground_token or self.foreground.lower(),
            self.background_token or self.background.lower(),
        )


@dataclass(frozen=True)
class AlphaBlindPair:
    """A front-matter entry whose text sits on a translucent fill (see
    DesignFacts.alpha_blind_pairs)."""

    component: str
    #: Front-matter entry: the component or its "<name>-<state>" variant.
    entry: str
    fill: str
    #: Text contrast with the fill layered over the canvas.
    ratio: float
    #: What a checker that drops alpha computes for the same two values.
    alpha_blind_ratio: float


@dataclass(frozen=True)
class ActionColor:
    """A hue family of chromatic button backgrounds (one accent)."""

    hex: str
    token: str | None
    components: tuple[str, ...]
    #: Button instances using this family.
    count: int = 1
    #: Other hex values of the same family (e.g. a darker variant on another button).
    variants: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShadowProfile:
    """Drop shadows vs. 1px rings / inset highlights among the measured shadow levels."""

    #: Strongest color alpha among drop-shadow layers (offset or blur, not inset).
    drop_alpha: float
    has_drop: bool
    #: Layers that act as borders: inset highlights or 0-offset 0-blur spread rings.
    has_rings: bool


@dataclass
class _Family:
    color: RGBA
    hex: str
    token: str | None
    hue: float | None
    components: list[str]
    count: int
    variants: list[str] = field(default_factory=list)

    def accepts(self, color: RGBA, hue: float | None) -> bool:
        if delta_e_2000(self.color, color) < SAME_ACCENT_DELTA_E:
            return True
        if self.hue is None or hue is None:
            return False
        gap = abs(self.hue - hue) % 360.0
        return min(gap, 360.0 - gap) <= SAME_FAMILY_HUE_DEG

    def freeze(self) -> ActionColor:
        return ActionColor(
            self.hex, self.token, tuple(self.components), self.count, tuple(self.variants)
        )


def is_transparent_value(value: str) -> bool:
    if value.strip().lower() == "transparent":
        return True
    color = parse_color(value)
    return color is not None and color.is_transparent


def _first_unique(items: list[tuple[str, T]]) -> dict[str, T]:
    result: dict[str, T] = {}
    for name, item in items:
        if is_token_name(name) and name not in result:
            result[name] = item
    return result


class DesignFacts:
    """Deduplicated tokens, components and derived facts of one DesignAnalysis."""

    def __init__(self, analysis: DesignAnalysis) -> None:
        self.analysis = analysis
        order = {name: index for index, name in enumerate(COLOR_TOKEN_ORDER)}
        tokens = sorted(
            enumerate(analysis.colors.tokens),
            key=lambda item: (order.get(item[1].name, len(order)), item[0]),
        )
        self.colors: dict[str, ColorToken] = _first_unique([(t.name, t) for _, t in tokens])
        self.levels: dict[str, TypeLevel] = _first_unique(
            [(level.name, level) for level in analysis.typography.levels]
        )
        self.radii: dict[str, ScaleStep] = _first_unique([(s.name, s) for s in analysis.radii])
        self.spacing: dict[str, ScaleStep] = _first_unique(
            [(s.name, s) for s in analysis.spacing.steps]
        )
        self.families: dict[str, FontFamilyInfo] = {}
        for family in analysis.typography.families:
            self.families.setdefault(family.name, family)
        self.components: list[ComponentSpec] = self._ordered_components()
        self.canvas: RGBA = self._canvas()

    # ─── Tokens ──────────────────────────────────────────────────────────────

    def _ordered_components(self) -> list[ComponentSpec]:
        name_order = {name: index for index, name in enumerate(COMPONENT_NAME_ORDER)}
        kind_order = {kind: index for index, kind in enumerate(KIND_ORDER)}
        indexed = [
            (index, spec)
            for index, spec in enumerate(self.analysis.components)
            if is_token_name(spec.name)
        ]
        indexed.sort(
            key=lambda item: (
                kind_order.get(item[1].kind, len(kind_order)),
                name_order.get(item[1].name, len(name_order)),
                item[0],
            )
        )
        seen: set[str] = set()
        result: list[ComponentSpec] = []
        for _, spec in indexed:
            if spec.name not in seen:
                seen.add(spec.name)
                result.append(spec)
        return result

    def _canvas(self) -> RGBA:
        candidates = [self.colors.get("canvas")] + [
            token for token in self.colors.values() if token.role == ColorRole.BACKGROUND
        ]
        for token in candidates:
            if token is not None:
                color = parse_color(token.value)
                if color is not None:
                    return color if color.is_opaque else color.with_alpha(1.0)
        return BLACK if self.analysis.colors.scheme == "dark" else WHITE

    def group(self, group: str) -> dict[str, object]:
        if group == COLORS:
            return dict(self.colors)
        if group == TYPOGRAPHY:
            return dict(self.levels)
        if group == ROUNDED:
            return dict(self.radii)
        if group == SPACING:
            return dict(self.spacing)
        return {}

    def has(self, group: str, name: str) -> bool:
        return name in self.group(group)

    def has_ref(self, ref: str | None) -> bool:
        """True when `ref` is a {group.name} reference to a primitive token defined here
        (components are never valid reference targets)."""
        if ref is None:
            return False
        parsed = parse_token_ref(ref)
        return parsed is not None and parsed[0] != COMPONENTS and self.has(*parsed)

    def value(self, group: str, name: str) -> str:
        """Display value of a token: "#4f46e5", "8px", "Inter 15px / 500 / 1.33"."""
        if group == COLORS:
            return self.colors[name].value
        if group == ROUNDED:
            return self.radius_value(self.radii[name])
        if group == SPACING:
            return fmt_px(self.spacing[name].value_px)
        if group == TYPOGRAPHY:
            return self.level_summary(self.levels[name])
        raise KeyError(f"{group}.{name}")

    def raw_value(self, ref: str) -> str | None:
        """Primitive value a reference resolves to (None for typography or unknown refs)."""
        parsed = parse_token_ref(ref)
        if parsed is None or not self.has_ref(ref) or parsed[0] == TYPOGRAPHY:
            return None
        return self.value(*parsed)

    def ref(self, group: str, name: str) -> str:
        """ "`{colors.primary}` — #4f46e5": a reference always written next to its value."""
        return f"`{token_ref(group, name)}` — {self.value(group, name)}"

    def ref_from_string(self, ref: str) -> str | None:
        parsed = parse_token_ref(ref)
        if parsed is None or not self.has_ref(ref):
            return None
        return self.ref(*parsed)

    @staticmethod
    def radius_value(step: ScaleStep) -> str:
        if step.name == FULL_RADIUS_NAME or step.value_px >= PILL_RADIUS_PX:
            return fmt_px(FULL_RADIUS_PX)
        return fmt_px(step.value_px)

    @staticmethod
    def line_height_ratio(level: TypeLevel) -> float | None:
        if level.line_height is not None:
            return round(level.line_height, 2)
        if level.line_height_px is not None and level.size_px > 0:
            return round(level.line_height_px / level.size_px, 2)
        return None

    def level_summary(self, level: TypeLevel) -> str:
        parts = [f"{level.font_family} {fmt_px(level.size_px)}", str(level.weight)]
        ratio = self.line_height_ratio(level)
        if ratio is not None:
            parts.append(fmt_num(ratio))
        return " / ".join(parts)

    def color_display(self, hex_value: str, token: str | None) -> str:
        """A measured color shown with its token when it has one."""
        if token is not None and token in self.colors:
            reference = self.ref(COLORS, token)
            if self.colors[token].value == hex_value:
                return reference
            return f"{hex_value} (≈ {reference})"
        return hex_value

    # ─── Components ──────────────────────────────────────────────────────────

    def _spec_value(self, key: str, ref: str | None, raw: str | None) -> str | None:
        if ref is not None and self.has_ref(ref.strip()):
            return ref.strip()
        if key == "typography" or raw is None or not raw.strip():
            return None
        value = raw.strip()
        if key in COLOR_PROPERTY_KEYS and is_transparent_value(value):
            return None
        return value

    def component_props(self, spec: ComponentSpec) -> dict[str, str]:
        """Spec properties of a component as written in the front matter."""
        props: dict[str, str] = {}
        for key in SPEC_COMPONENT_KEYS:
            value = self._spec_value(key, spec.token_refs.get(key), spec.properties.get(key))
            if value is not None:
                props[key] = value
        return props

    def flat_hex(self, value: str | None) -> str | None:
        """Opaque "#rrggbb" a translucent color shows over the canvas; None for opaque, fully
        transparent or non-colors."""
        color = parse_color(value)
        if color is None or color.is_opaque or color.is_transparent:
            return None
        return flatten(color, self.canvas).css_hex

    def alpha_blind_pairs(self) -> list[AlphaBlindPair]:
        """Front-matter entries pairing text with a raw translucent fill. Checkers that ignore
        alpha (the official DESIGN.md linter) read #f7f8f8 on #ffffff0d as 1.06:1; the fill
        really sits over the canvas, where the text reaches 17:1. The front matter keeps the
        measured translucent value, so the prose documents both readings."""
        result: list[AlphaBlindPair] = []
        for spec in self.components:
            base = self.component_props(spec)
            entries = [(spec.name, base)] + [
                (state_variant_name(spec.name, state), self.state_props(spec, state))
                for state in self.states(spec)
            ]
            for name, props in entries:
                # The entry writes the fill itself; its text may come from the base component.
                fill = props.get("backgroundColor")
                text = self.resolve(props.get("textColor") or base.get("textColor"))
                if fill is None or text is None or parse_token_ref(fill) is not None:
                    continue
                color = parse_color(fill)
                foreground = parse_color(text)
                if color is None or foreground is None or color.is_opaque or color.is_transparent:
                    continue
                measured = self.contrast_on(text, fill)
                if measured is None:
                    continue
                naive = contrast_ratio(foreground.with_alpha(1.0), color.with_alpha(1.0))
                result.append(AlphaBlindPair(spec.name, name, fill, measured, naive))
        return result

    def resolve(self, value: str | None) -> str | None:
        """A front-matter value with references replaced by their primitive value."""
        if value is None:
            return None
        if parse_token_ref(value) is not None:
            return self.raw_value(value)
        return value

    def states(self, spec: ComponentSpec) -> list[str]:
        names = [name for name in spec.states if is_token_name(name)]
        names += [
            name
            for name in spec.state_token_refs
            if is_token_name(name) and name not in spec.states
        ]
        return sorted(set(names), key=lambda name: (name != HOVER_STATE, name))

    def state_props(self, spec: ComponentSpec, state: str) -> dict[str, str]:
        """Spec properties that really change in `state` (refs preferred)."""
        changed = spec.states.get(state, {})
        refs = spec.state_token_refs.get(state, {})
        base = self.component_props(spec)
        result: dict[str, str] = {}
        for key in SPEC_COMPONENT_KEYS:
            if key not in changed and key not in refs:
                continue
            ref = refs.get(key)
            raw = changed.get(key)
            value: str | None = None
            if ref is not None and self.has_ref(ref.strip()):
                value = ref.strip()
            elif key != "typography" and raw is not None and raw.strip():
                value = raw.strip()
                if key in COLOR_PROPERTY_KEYS and is_transparent_value(value):
                    value = "transparent" if key in base else None
            if value is None:
                continue
            before = base.get(key)
            if before is not None and _same_value(self.resolve(before), self.resolve(value)):
                continue
            result[key] = value
        return result

    def front_matter_components(self) -> dict[str, dict[str, str]]:
        """Components and their "<name>-<state>" variants, in listing order."""
        result: dict[str, dict[str, str]] = {}
        for spec in self.components:
            props = self.component_props(spec)
            if props and spec.name not in result:
                result[spec.name] = props
            for state in self.states(spec):
                state_props = self.state_props(spec, state)
                variant = state_variant_name(spec.name, state)
                if state_props and variant not in result:
                    result[variant] = state_props
        return result

    def component_by_name(self, name: str) -> ComponentSpec | None:
        return next((spec for spec in self.components if spec.name == name), None)

    def components_of_kind(self, kind: ComponentKind) -> list[ComponentSpec]:
        return [spec for spec in self.components if spec.kind == kind]

    def color_usage(self) -> dict[str, list[str]]:
        """Color token -> components whose properties reference it (any property)."""
        usage: dict[str, list[str]] = {}
        for spec in self.components:
            refs = list(spec.token_refs.values())
            for state_refs in spec.state_token_refs.values():
                refs.extend(state_refs.values())
            for ref in refs:
                parsed = parse_token_ref(ref)
                if parsed is None or parsed[0] != COLORS or parsed[1] not in self.colors:
                    continue
                names = usage.setdefault(parsed[1], [])
                if spec.name not in names:
                    names.append(spec.name)
        return usage

    def component_color(self, spec: ComponentSpec, key: str) -> str | None:
        """Resolved, non-transparent hex of a component color property."""
        ref = spec.token_refs.get(key)
        value = self.raw_value(ref) if ref is not None and self.has_ref(ref) else None
        if value is None:
            value = spec.properties.get(key)
        if value is None or is_transparent_value(value):
            return None
        color = parse_color(value)
        return None if color is None else color.css_hex

    # ─── Derived facts ───────────────────────────────────────────────────────

    def action_colors(self) -> list[ActionColor]:
        """Chromatic button backgrounds grouped into hue families, primary first.

        Two colors belong to the same family when they are within ΔE2000 3 or within
        SAME_FAMILY_HUE_DEG of OKLCH hue (#635bff and #533afd are one indigo accent).
        """
        families: list[_Family] = []
        for spec in self.components_of_kind(ComponentKind.BUTTON):
            hex_value = self.component_color(spec, "backgroundColor")
            color = parse_color(hex_value)
            if hex_value is None or color is None or not is_chromatic(color):
                continue
            ref = spec.token_refs.get("backgroundColor")
            parsed = parse_token_ref(ref) if ref is not None and self.has_ref(ref) else None
            hue = to_oklch(color).hue
            count = max(spec.count, 1)
            family = next((f for f in families if f.accepts(color, hue)), None)
            if family is None:
                token = parsed[1] if parsed else None
                families.append(_Family(color, hex_value, token, hue, [spec.name], count))
                continue
            family.components.append(spec.name)
            family.count += count
            if hex_value != family.hex and hex_value not in family.variants:
                family.variants.append(hex_value)
        return [family.freeze() for family in families]

    def most_common_radius(self) -> ScaleStep | None:
        steps = [step for step in self.radii.values() if step.count > 0] or list(
            self.radii.values()
        )
        if not steps:
            return None
        return max(steps, key=lambda step: step.count)

    def shadow_profile(self) -> ShadowProfile:
        drop: list[float] = []
        rings = False
        for level in self.analysis.shadows:
            for layer in parse_box_shadow(level.css):
                if layer.color is not None and layer.color.is_transparent:
                    continue
                alpha = 1.0 if layer.color is None else layer.color.alpha
                flat = layer.blur == 0 and layer.offset_x == 0 and layer.offset_y == 0
                if layer.inset or flat:
                    rings = True
                else:
                    drop.append(alpha)
        return ShadowProfile(max(drop, default=0.0), bool(drop), rings)

    def weights(self) -> list[int]:
        weights = set(self.analysis.typography.weights_used)
        if not weights:
            weights = {level.weight for level in self.levels.values()}
        return sorted(weights)

    def family_with_role(self, *roles: FontRole) -> FontFamilyInfo | None:
        for role in roles:
            for family in self.families.values():
                if family.role == role:
                    return family
        return None

    def heading_level(self) -> TypeLevel | None:
        """The largest type level."""
        return next(iter(self.levels.values()), None)

    def headline_level(self) -> TypeLevel | None:
        """The level of the page headline: the level whose tags lead with h1 (tags are listed
        by frequency), else the level where h1 ranks highest, else the largest level.

        The largest level is often a one-off closing banner (a 72px h2 above the footer), not
        the hero title an agent should reproduce.
        """
        tagged = [
            (level.tags.index(HEADLINE_TAG), -level.count, index, level)
            for index, level in enumerate(self.levels.values())
            if HEADLINE_TAG in level.tags
        ]
        if tagged:
            return min(tagged, key=lambda item: item[:3])[3]
        return self.heading_level()

    def lead_level(self) -> TypeLevel | None:
        """Supporting copy under the headline: the largest level led by <p> that is a lead
        paragraph (between the body size and LEAD_MAX_SCALE x the body size, below the
        headline), else the body level. A 32px <p> is a pull quote, not a hero lead."""
        body = self.body_level()
        if body is None:
            return None
        headline = self.headline_level()
        candidates = [
            level
            for level in self.levels.values()
            if level.tags[:1] == ["p"]
            and body.size_px <= level.size_px <= body.size_px * LEAD_MAX_SCALE
            and (headline is None or level.size_px < headline.size_px)
        ]
        return max(candidates, key=lambda level: level.size_px, default=body)

    def body_level(self) -> TypeLevel | None:
        for name in ("body", "body-lg", "body-sm"):
            if name in self.levels:
                return self.levels[name]
        tagged = [level for level in self.levels.values() if "p" in level.tags]
        return max(tagged, key=lambda level: level.count, default=None)

    def surfaces_by_lightness(self) -> list[ColorToken]:
        """Canvas/surface tokens, from the canvas outwards (by OKLCH lightness distance)."""
        tokens = [
            token
            for token in self.colors.values()
            if token.role in SURFACE_ROLES and parse_color(token.value) is not None
        ]
        base = to_oklch(self.canvas).lightness

        def distance(item: tuple[int, ColorToken]) -> tuple[int, float, int]:
            index, token = item
            color = parse_color(token.value)
            lightness = base if color is None else to_oklch(color.with_alpha(1.0)).lightness
            return (0 if token.role == ColorRole.BACKGROUND else 1, abs(lightness - base), index)

        return [token for _, token in sorted(enumerate(tokens), key=distance)]

    def decorative_clusters(self) -> list[ColorCluster]:
        """Decorative-only colors (heaviest first, one per hex), excluding any color that
        matches a palette token or a structural (non-decorative) cluster of any channel — a
        gradient stop equal to the canvas, or to a measured secondary text color, is not an
        accent to keep away from text."""
        tokens = list(self.colors.values())
        structural = [
            color
            for cluster in self.analysis.colors.clusters
            if not cluster.decorative and (color := parse_color(cluster.hex)) is not None
        ]
        result: list[ColorCluster] = []
        seen: set[str] = set()
        for cluster in sorted(
            (c for c in self.analysis.colors.clusters if c.decorative),
            key=lambda c: (-c.weight, c.id),
        ):
            if cluster.hex in seen or match_color_token(cluster.hex, tokens) is not None:
                continue
            color = parse_color(cluster.hex)
            if color is not None and any(_near(color, other) for other in structural):
                continue
            seen.add(cluster.hex)
            result.append(cluster)
        return result

    def has_dark_theme(self) -> bool:
        """True when the prefers-color-scheme: dark capture really switches appearance."""
        dark = self.analysis.dark
        return (
            dark is not None
            and bool(dark.colors.tokens)
            and dark.colors.scheme != self.analysis.colors.scheme
        )

    def contrast_on(self, foreground: str, background: str) -> float | None:
        try:
            return contrast_ratio(foreground, background, base=self.canvas)
        except ValueError:
            return None

    def text_alternative(self, background: str, exclude: str | None) -> tuple[str, float] | None:
        """The text token with the lowest ratio that still passes AA on `background`."""
        options: list[tuple[float, str]] = []
        for name, token in self.colors.items():
            if token.role not in TEXT_ROLES or name == exclude:
                continue
            ratio = self.contrast_on(token.value, background)
            if ratio is not None and ratio >= AA_NORMAL:
                options.append((ratio, name))
        if not options:
            return None
        ratio, name = min(options)
        return name, ratio

    # ─── Contrast recomputed from printed values ─────────────────────────────

    def check_pair(
        self,
        foreground: str,
        foreground_token: str | None,
        background: str,
        background_token: str | None,
    ) -> PairCheck | None:
        ratio = self.contrast_on(foreground, background)
        if ratio is None:
            return None
        return PairCheck(foreground, foreground_token, background, background_token, ratio)

    def check_tokens(self, foreground: str, background: str) -> PairCheck | None:
        """Contrast of two palette tokens, from their front-matter values."""
        if foreground not in self.colors or background not in self.colors:
            return None
        return self.check_pair(
            self.colors[foreground].value,
            foreground,
            self.colors[background].value,
            background,
        )

    def measured_backgrounds(self, foreground: str, token: str | None) -> list[str]:
        """Background tokens a text color was measured on (light theme, most frequent first):
        pairs labelled with `token`, or with the same foreground hex when it is no token."""
        counts: dict[str, int] = {}
        for pair in self.analysis.colors.contrast_pairs:
            background = pair.background_token
            if background is None or background not in self.colors:
                continue
            if token is not None:
                same = pair.foreground_token == token
            else:
                same = pair.foreground_token is None and _same_value(pair.foreground, foreground)
            if same:
                counts[background] = counts.get(background, 0) + max(pair.occurrences, 1)
        return sorted(counts, key=lambda name: -counts[name])

    def text_checks(self, foreground: str, token: str | None) -> list[PairCheck]:
        """A text color without a background of its own, recomputed on each background token
        it was measured on (worst first; the canvas when it was measured on none)."""
        names = self.measured_backgrounds(foreground, token)[:MAX_SURFACE_CHECKS]
        checks = [
            self.check_pair(foreground, token, self.colors[name].value, name) for name in names
        ]
        if not names:
            canvas = "canvas" if "canvas" in self.colors else None
            background = self.colors["canvas"].value if canvas else self.canvas.css_hex
            checks.append(self.check_pair(foreground, token, background, canvas))
        return sorted((c for c in checks if c is not None), key=lambda c: c.ratio)

    def component_token(self, spec: ComponentSpec, key: str) -> str | None:
        ref = spec.token_refs.get(key)
        return _ref_name(ref) if self.has_ref(ref) else None

    def component_contrasts(self, spec: ComponentSpec) -> list[PairCheck]:
        """Text contrast of a component, recomputed from the colors written for it: on its own
        fill, or — without one — on every surface its text color was measured on."""
        foreground = self.component_color(spec, "textColor")
        if foreground is None:
            return []
        fg_token = self.component_token(spec, "textColor")
        background = self.component_color(spec, "backgroundColor")
        if background is None:
            return self.text_checks(foreground, fg_token)
        bg_token = self.component_token(spec, "backgroundColor")
        check = self.check_pair(foreground, fg_token, background, bg_token)
        return [] if check is None else [check]

    def text_failures(self, token: str) -> list[PairCheck]:
        """Pairs below AA where the palette token is used as a text color (measured pairs and
        component text), recomputed from token values, worst first."""
        if token not in self.colors:
            return []
        value = self.colors[token].value
        checks = [
            check
            for name in self.measured_backgrounds(value, token)
            if (check := self.check_tokens(token, name)) is not None
        ]
        for spec in self.components:
            if self.component_token(spec, "textColor") == token:
                checks.extend(self.component_contrasts(spec))
        failing: dict[tuple[str, str], PairCheck] = {}
        for check in checks:
            if not check.passes:
                failing.setdefault(check.key, check)
        return sorted(failing.values(), key=lambda check: check.ratio)

    def contrast_issues(self) -> list[ContrastIssue]:
        """Failing pairs (light theme first, then dark), worst first within each theme.

        Light pairs come from the analysis (measured member colors, shown with the ≈ token
        notation when they differ from the token), then from component text recomputed on
        the token values printed for the component."""
        light = [
            self._issue(pair, dark=False)
            for pair in self.analysis.colors.contrast_pairs
            if not pair.passes_aa
        ]
        covered = {_issue_key(issue) for issue in light}
        for spec in self.components:
            for check in self.component_contrasts(spec):
                if check.passes or check.key in covered:
                    continue
                covered.add(check.key)
                role = None
                if check.foreground_token is not None:
                    role = self.colors[check.foreground_token].role
                light.append(
                    ContrastIssue(
                        foreground=check.foreground,
                        background=check.background,
                        foreground_token=check.foreground_token,
                        background_token=check.background_token,
                        ratio=check.ratio,
                        context=spec.name,
                        foreground_role=role,
                    )
                )
        dark: list[ContrastIssue] = []
        if self.analysis.dark is not None and self.has_dark_theme():
            seen = {(issue.foreground, issue.background, issue.ratio) for issue in light}
            dark = [
                self._issue(pair, dark=True)
                for pair in self.analysis.dark.colors.contrast_pairs
                if not pair.passes_aa and (pair.foreground, pair.background, pair.ratio) not in seen
            ]
        light.sort(key=lambda issue: issue.ratio)
        dark.sort(key=lambda issue: issue.ratio)
        return light + dark

    def _issue(self, pair: ContrastPair, *, dark: bool) -> ContrastIssue:
        role: ColorRole | None = None
        if not dark and pair.foreground_token in self.colors:
            role = self.colors[pair.foreground_token].role
        elif pair.foreground_token is not None:
            role = COLOR_TOKEN_ROLES.get(pair.foreground_token)
        return ContrastIssue(
            foreground=pair.foreground,
            background=pair.background,
            foreground_token=pair.foreground_token,
            background_token=pair.background_token,
            ratio=pair.ratio,
            context=pair.context,
            dark=dark,
            foreground_role=role,
        )

    def best_text_pair(self) -> PairCheck | None:
        """The main running-text pair: a measured pair of light-theme tokens that passes AA
        on the token values, preferring a primary text token (role TEXT, not on-*) on the
        canvas, then the most frequent. The ratio is recomputed from the token values."""
        occurrences: dict[tuple[str, str], int] = {}
        for pair in self.analysis.colors.contrast_pairs:
            if pair.foreground_token in self.colors and pair.background_token in self.colors:
                key = (pair.foreground_token or "", pair.background_token or "")
                occurrences[key] = occurrences.get(key, 0) + pair.occurrences
        ranked: list[tuple[tuple[bool, bool, int], PairCheck]] = []
        for (fg, bg), count in occurrences.items():
            check = self.check_tokens(fg, bg)
            if check is None or not check.passes:
                continue
            foreground = self.colors[fg]
            rank = (
                foreground.role == ColorRole.TEXT and not fg.startswith("on-"),
                self.colors[bg].role == ColorRole.BACKGROUND,
                count,
            )
            ranked.append((rank, check))
        if not ranked:
            return None
        return max(ranked, key=lambda item: item[0])[1]

    def skipped_parts(self) -> frozenset[str]:
        """Analyzer parts an earlier version of the glue skipped, read from its warning
        "Chưa phân tích (để trống trong analysis.json): Typography (…), Đổ bóng (…)".

        An empty list in such an analysis.json means "not analyzed", not "none on the page".
        Current versions stop with an error instead, so this is only for older files.
        """
        skipped: set[str] = set()
        for warning in self.analysis.warnings:
            if not warning.startswith(SKIPPED_WARNING_PREFIX):
                continue
            for label, part in SKIPPED_LABELS.items():
                if f"{label} (" in warning:
                    skipped.add(part)
        return frozenset(skipped)

    def missing_groups(self) -> list[str]:
        """Analysis parts that are empty (not measured or skipped)."""
        skipped = self.skipped_parts()
        missing: list[str] = []
        if not self.colors:
            missing.append("colors")
        if not self.levels:
            missing.append("typography")
        if not self.spacing:
            missing.append("spacing")
        if not self.radii:
            missing.append("rounded")
        if "shadows" in skipped:
            missing.append("shadows")
        missing.extend(part for part in ("breakpoints", "layout") if part in skipped)
        if not self.components:
            missing.append("components")
        return missing


def _ref_name(ref: str | None) -> str | None:
    parsed = parse_token_ref(ref) if ref is not None else None
    return None if parsed is None else parsed[1]


def _issue_key(issue: ContrastIssue) -> tuple[str, str]:
    return (
        issue.foreground_token or issue.foreground.lower(),
        issue.background_token or (issue.background or "").lower(),
    )


def _near(first: RGBA, second: RGBA) -> bool:
    return (
        abs(first.alpha - second.alpha) <= DECORATIVE_MATCH_ALPHA
        and delta_e_2000(first, second) < DECORATIVE_MATCH_DELTA_E
    )


def _same_value(first: str | None, second: str | None) -> bool:
    if first is None or second is None:
        return False
    if first.strip().lower() == second.strip().lower():
        return True
    a = parse_color(first)
    b = parse_color(second)
    return a is not None and b is not None and a == b
