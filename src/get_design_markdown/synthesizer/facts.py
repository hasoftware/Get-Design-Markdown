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
#: Two chromatic action colors whose OKLCH hues differ by at most this are one accent family.
SAME_FAMILY_HUE_DEG: Final = 20.0
#: Warning written by analyzer/analyze.py when analyzer parts were skipped, and its labels.
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
        return next(iter(self.levels.values()), None)

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
        matches a palette token — a gradient stop equal to the canvas is not an accent."""
        tokens = list(self.colors.values())
        result: list[ColorCluster] = []
        seen: set[str] = set()
        for cluster in sorted(
            (c for c in self.analysis.colors.clusters if c.decorative),
            key=lambda c: (-c.weight, c.id),
        ):
            if cluster.hex in seen or match_color_token(cluster.hex, tokens) is not None:
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

    def contrast_issues(self) -> list[ContrastIssue]:
        """Failing pairs (light theme first, then dark), worst first within each theme."""
        light = [
            self._issue(pair, dark=False)
            for pair in self.analysis.colors.contrast_pairs
            if not pair.passes_aa
        ]
        covered = {pair.context for pair in self.analysis.colors.contrast_pairs}
        for spec in self.components:
            ratio = spec.contrast_ratio
            if ratio is None or ratio >= AA_NORMAL or spec.name in covered:
                continue
            foreground = self.component_color(spec, "textColor")
            if foreground is None:
                continue
            fg_ref = spec.token_refs.get("textColor")
            bg_ref = spec.token_refs.get("backgroundColor")
            light.append(
                ContrastIssue(
                    foreground=foreground,
                    background=self.component_color(spec, "backgroundColor"),
                    foreground_token=_ref_name(fg_ref) if self.has_ref(fg_ref) else None,
                    background_token=_ref_name(bg_ref) if self.has_ref(bg_ref) else None,
                    ratio=ratio,
                    context=spec.name,
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

    def best_text_pair(self) -> ContrastPair | None:
        """The main running-text pair: a passing pair of light-theme tokens, preferring a
        primary text token (role TEXT, not muted) on the canvas, then the most frequent."""
        pairs = [
            pair
            for pair in self.analysis.colors.contrast_pairs
            if pair.passes_aa
            and pair.foreground_token in self.colors
            and pair.background_token in self.colors
        ]

        def rank(pair: ContrastPair) -> tuple[int, int, int]:
            foreground = self.colors[pair.foreground_token or ""]
            background = self.colors[pair.background_token or ""]
            return (
                foreground.role == ColorRole.TEXT and not foreground.name.startswith("on-"),
                background.role == ColorRole.BACKGROUND,
                pair.occurrences,
            )

        return max(pairs, key=rank, default=None)

    def skipped_parts(self) -> frozenset[str]:
        """Analyzer parts the glue skipped (not built yet), read from its warning
        "Chưa phân tích (để trống trong analysis.json): Typography (GĐ4), Đổ bóng (GĐ4)…".

        An empty list in analysis.json then means "not analyzed", not "none on the page".
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


def _same_value(first: str | None, second: str | None) -> bool:
    if first is None or second is None:
        return False
    if first.strip().lower() == second.strip().lower():
        return True
    a = parse_color(first)
    b = parse_color(second)
    return a is not None and b is not None and a == b
