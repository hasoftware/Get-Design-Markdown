"""preview.html: static page showing swatches, type specimens and rebuilt components.

PUBLIC ENTRY POINT (frozen signature, called by exporters/export.py):
    render_preview(analysis) -> str

The page is one HTML5 document with an inline <style>, at most one small inline <script> (the
light/dark toggle, only when a dark capture exists) and at most one external resource: a
fonts.googleapis.com stylesheet for the Google Fonts the site uses. It is themed with the site's
own tokens (CSS custom properties `--color-*`, `--font-*`, `--radius-*`, `--space-*`,
`--shadow-*`) so components rebuilt from ComponentSpec switch with the theme wherever the
analyzer linked them to a token. Text goes through Jinja autoescaping; CSS is assembled from
values rebuilt by safe_css only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from get_design_markdown.analyzer.css_values import (
    RGBA,
    WHITE,
    contrast_ratio,
    format_px,
    parse_color,
)
from get_design_markdown.analyzer.naming import (
    COMPONENT_NAME_ORDER,
    PILL_RADIUS_PX,
    parse_token_ref,
)
from get_design_markdown.exporters.dtcg import number
from get_design_markdown.exporters.preview_styles import (
    MONO_STACK,
    SANS_STACK,
    ComponentContext,
    FontResolver,
    component_rules,
    font_label,
    palette_block,
    palette_fallbacks,
    radius_css,
    rule,
    scale_variables,
    shadow_names,
    theme_block,
    var,
    weights_by_family,
)
from get_design_markdown.exporters.safe_css import (
    css_color,
    css_shadow,
    html_lang,
    http_url,
    ident,
    sides_px,
)
from get_design_markdown.models.analysis import (
    ColorPalette,
    ColorRole,
    ComponentKind,
    ComponentSpec,
    DesignAnalysis,
    ResponsiveChangeKind,
    TypeLevel,
)
from get_design_markdown.resources import render_template

TEMPLATE = "preview.html.j2"
DISCLAIMER_EN = "Reference only — not the official design system of this site."
DISCLAIMER_VI = "Tài liệu tham khảo — không phải design system chính thức của trang."
MAX_TEXT = 60
MAX_CLUSTERS_PER_CHANNEL = 48
CLUSTER_CHANNELS = ("background", "text", "border", "gradient")
#: Order of ComponentSpec property keys in the spec tables (others follow alphabetically).
PROPERTY_ORDER = (
    "backgroundColor",
    "textColor",
    "borderColor",
    "borderWidth",
    "rounded",
    "padding",
    "height",
    "width",
    "gap",
    "fontFamily",
    "fontSize",
    "fontWeight",
    "lineHeight",
    "letterSpacing",
    "textTransform",
    "textDecoration",
    "shadow",
    "backdropFilter",
    "opacity",
    "placeholderColor",
    "position",
)
SAMPLE_TEXT = {
    "display": "Build something remarkable",
    "heading": "A section heading in this style",
    "title": "Card and section titles",
    "subtitle": "Supporting subtitle text",
    "ui": "Button and control label",
    "caption": "Caption, footnote and meta text",
    "overline": "Overline label",
    "code": "const theme = await tokens.load();",
}
BODY_SAMPLE = "The quick brown fox jumps over the lazy dog. 0123456789"
CHANGE_TITLES: dict[ResponsiveChangeKind, str] = {
    ResponsiveChangeKind.NAV_COLLAPSES_TO_HAMBURGER: "Navigation collapses to a hamburger",
    ResponsiveChangeKind.NAV_HIDDEN: "Navigation hidden",
    ResponsiveChangeKind.SIDEBAR_HIDDEN: "Sidebar hidden",
    ResponsiveChangeKind.COLUMNS_REDUCE: "Columns reduce",
    ResponsiveChangeKind.HEADING_SCALES_DOWN: "Headings scale down",
    ResponsiveChangeKind.CONTAINER_BECOMES_FLUID: "Container becomes fluid",
    ResponsiveChangeKind.GUTTER_SHRINKS: "Gutter shrinks",
    ResponsiveChangeKind.HORIZONTAL_SCROLL_APPEARS: "Horizontal scrolling appears",
    ResponsiveChangeKind.TAP_TARGETS_SMALL: "Tap targets below 44px",
}
TEXT_ROLES = frozenset({ColorRole.TEXT, ColorRole.TEXT_MUTED})
SURFACE_ROLES = frozenset({ColorRole.BACKGROUND, ColorRole.SURFACE})
_SCREENSHOT_PATH_RE = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*\.(?:png|jpe?g|webp)$")


# ─── View models ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Theme:
    id: str
    label: str
    scheme: str
    palette: ColorPalette


@dataclass(frozen=True)
class Contrast:
    label: str
    ratio: str
    level: str
    ok: bool
    #: False for non-text checks (borders): the ratio is shown without a WCAG verdict.
    graded: bool = True


@dataclass(frozen=True)
class SwatchValue:
    theme: str
    hex: str | None
    checks: list[Contrast]


@dataclass(frozen=True)
class Swatch:
    name: str
    css_class: str
    role: str
    evidence: str
    css_variable: str | None
    values: list[SwatchValue]


@dataclass(frozen=True)
class Chip:
    css_class: str
    hex: str
    note: str


@dataclass(frozen=True)
class PairRow:
    css_class: str
    foreground: str
    background: str
    foreground_token: str | None
    background_token: str | None
    ratio: str
    aa: bool
    aa_large: bool
    aaa: bool
    context: str
    occurrences: int


@dataclass(frozen=True)
class ThemeColors:
    theme: Theme
    decorative: list[Chip]
    clusters: list[tuple[str, list[Chip]]]
    pairs: list[PairRow]


@dataclass(frozen=True)
class FamilyView:
    name: str
    role: str
    category: str
    weights: str
    google: bool
    substitutes: str
    rendered_with: str | None
    css_class: str


@dataclass(frozen=True)
class LevelView:
    name: str
    css_class: str
    sample: str
    family: str
    size: str
    weight: int
    line_height: str
    letter_spacing: str
    transform: str | None
    tags: str
    count: int


@dataclass(frozen=True)
class ScaleRow:
    name: str
    value: str
    css_class: str
    used_on: str
    count: int


@dataclass(frozen=True)
class ShadowView:
    name: str
    css_class: str
    css: str
    layers: int
    used_on: str


@dataclass(frozen=True)
class SpecRow:
    key: str
    value: str
    ref: str | None


@dataclass(frozen=True)
class ComponentView:
    name: str
    kind: str
    variant: str | None
    count: int
    css_class: str
    render: str
    label: str
    specs: list[SpecRow]
    states: list[tuple[str, list[SpecRow]]]
    hover: bool
    contrast: str | None
    class_hints: str
    #: Card mock.
    title_class: str
    body_class: str
    #: Navigation mock.
    nav_links: list[str] = field(default_factory=list)
    nav_link_class: str | None = None
    nav_button_class: str | None = None
    nav_button_label: str = ""
    #: Navigation/footer mock without horizontal padding of its own: add the page gutter.
    gutter: bool = False


@dataclass(frozen=True)
class BreakpointView:
    name: str
    min_width: str | None
    max_width: str | None
    occurrences: int
    css_class: str
    #: Label anchored to the left of the mark (marks near the right end of the ruler).
    end: bool = False


@dataclass(frozen=True)
class ChangeView:
    widths: str
    title: str
    params: str
    evidence: str


@dataclass(frozen=True)
class SnapshotRow:
    viewport: str
    columns: str
    nav: str
    container: str
    gutter: str
    heading: str
    tap: str


@dataclass(frozen=True)
class LinkView:
    label: str
    href: str


@dataclass
class _Css:
    """Collects generated rules in order."""

    rules: list[str] = field(default_factory=list)

    def add(self, *texts: str) -> None:
        self.rules.extend(text for text in texts if text)

    def render(self) -> str:
        # Defence in depth: sanitized values never contain "<", but a stray one must never
        # be able to close the <style> element.
        return "\n".join(self.rules).replace("<", "\\3c ")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _text(value: str | None, limit: int = MAX_TEXT) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _contrast(
    label: str, foreground: RGBA, background: RGBA, base: RGBA, *, graded: bool = True
) -> Contrast:
    ratio = contrast_ratio(foreground, background, base=base)
    if ratio >= 7.0:
        level = "AAA"
    elif ratio >= 4.5:
        level = "AA"
    elif ratio >= 3.0:
        level = "AA large"
    else:
        level = "Fail"
    return Contrast(label, f"{ratio:.2f}:1", level, ratio >= 4.5, graded)


def _swatch_checks(
    name: str,
    color: RGBA,
    palette: ColorPalette,
    colors: dict[str, RGBA],
    canvas: RGBA,
) -> list[Contrast]:
    """Contrast checks that make sense for the token's role.

    Text tokens are measured on canvas ("on-x" tokens on their partner x), surfaces and
    action colors with the most legible text token on them, borders against canvas without a
    WCAG verdict (hairlines are decorative by design).
    """
    roles = {ident(token.name): token.role for token in palette.tokens}
    role = roles.get(name)
    checks: list[Contrast] = []
    if name.startswith("on-"):
        partner = name.removeprefix("on-")
        target = colors.get(partner) or colors.get(f"surface-{partner}")
        if target is not None:
            return [_contrast(f"on {partner}", color, target, canvas)]
    if role is ColorRole.BORDER:
        return [_contrast("vs canvas", color, canvas, canvas, graded=False)]
    if role not in SURFACE_ROLES and name != "canvas":
        checks.append(_contrast("on canvas", color, canvas, canvas))
    if role not in TEXT_ROLES:
        texts = [
            (token, colors[token])
            for token, token_role in roles.items()
            if token_role in TEXT_ROLES and token != name and token in colors
        ]
        if texts:
            best = max(texts, key=lambda item: contrast_ratio(item[1], color, base=canvas))
            checks.append(_contrast(f"{best[0]} on it", best[1], color, canvas))
    return checks


def _px(value: float | None) -> str | None:
    return None if value is None else format_px(value)


def _join(values: Sequence[str]) -> str:
    return ", ".join(_text(value, 40) for value in values if value)


def _palette_colors(palette: ColorPalette) -> dict[str, RGBA]:
    colors: dict[str, RGBA] = {}
    for token in palette.tokens:
        name = ident(token.name)
        color = parse_color(token.value)
        if color is not None and name not in colors:
            colors[name] = color
    return colors


def _themes(analysis: DesignAnalysis) -> list[Theme]:
    """data-theme "light" holds the default capture, "dark" the prefers-color-scheme: dark
    capture; labels name the capture because either one may render light or dark."""
    main = analysis.colors
    themes = [Theme("light", "default capture", main.scheme, main)]
    if analysis.dark is not None:
        dark = analysis.dark.colors
        themes.append(Theme("dark", "dark-mode capture", dark.scheme, dark))
    return themes


def _site_slug(url: str) -> str:
    host = re.sub(r"^[a-z]+://", "", url.strip().lower()).split("/")[0]
    return ident(host.removeprefix("www."), fallback="site")


# ─── Builders ────────────────────────────────────────────────────────────────


class _PreviewBuilder:
    def __init__(self, analysis: DesignAnalysis) -> None:
        self.analysis = analysis
        self.css = _Css()
        self.themes = _themes(analysis)
        self.fonts = FontResolver(analysis.typography)
        self.level_classes: dict[str, str] = {}

    # Colors ------------------------------------------------------------------

    def swatches(self) -> list[Swatch]:
        names: list[str] = []
        info: dict[str, tuple[str, str, str | None]] = {}
        for theme in self.themes:
            for token in theme.palette.tokens:
                name = ident(token.name)
                if name not in info:
                    names.append(name)
                    role = token.role.value.replace("_", " ")
                    info[name] = (role, _text(token.evidence, 90), token.css_variable)
        theme_colors = {theme.id: _palette_colors(theme.palette) for theme in self.themes}
        swatches: list[Swatch] = []
        for index, name in enumerate(names):
            css_class = f"sw-{index}"
            self.css.add(rule(f".{css_class}", [("background-color", var(f"--color-{name}"))]))
            values: list[SwatchValue] = []
            for theme in self.themes:
                colors = theme_colors[theme.id]
                color = colors.get(name)
                background, _ = palette_fallbacks(theme.palette)
                canvas = colors.get("canvas") or parse_color(background) or WHITE
                checks = (
                    []
                    if color is None
                    else _swatch_checks(name, color, theme.palette, colors, canvas)
                )
                values.append(
                    SwatchValue(theme.id, None if color is None else color.css_hex, checks)
                )
            role, evidence, css_variable = info[name]
            swatches.append(Swatch(name, css_class, role, evidence, css_variable, values))
        return swatches

    def theme_colors(self) -> list[ThemeColors]:
        result: list[ThemeColors] = []
        for theme in self.themes:
            palette = theme.palette
            decorative: list[Chip] = []
            seen: set[str] = set()
            for index, cluster in enumerate(palette.clusters):
                color = css_color(cluster.hex)
                if not cluster.decorative or color is None or color in seen:
                    continue
                seen.add(color)
                css_class = f"dc-{theme.id}-{index}"
                self.css.add(rule(f".{css_class}", [("background-color", color)]))
                decorative.append(Chip(css_class, color, f"{cluster.channel}"))
            groups: list[tuple[str, list[Chip]]] = []
            for channel in CLUSTER_CHANNELS:
                chips: list[Chip] = []
                for index, cluster in enumerate(palette.clusters):
                    color = css_color(cluster.hex)
                    if cluster.channel != channel or color is None:
                        continue
                    if len(chips) >= MAX_CLUSTERS_PER_CHANNEL:
                        break
                    css_class = f"cl-{theme.id}-{index}"
                    self.css.add(rule(f".{css_class}", [("background-color", color)]))
                    note = f"{cluster.share * 100:.1f}%"
                    if cluster.decorative:
                        note += " · decorative"
                    chips.append(Chip(css_class, color, note))
                if chips:
                    groups.append((channel, chips))
            pairs: list[PairRow] = []
            for index, pair in enumerate(palette.contrast_pairs):
                foreground = css_color(pair.foreground)
                background = css_color(pair.background)
                if foreground is None or background is None:
                    continue
                css_class = f"cp-{theme.id}-{index}"
                self.css.add(
                    rule(
                        f".{css_class}",
                        [("color", foreground), ("background-color", background)],
                    )
                )
                pairs.append(
                    PairRow(
                        css_class,
                        foreground,
                        background,
                        pair.foreground_token,
                        pair.background_token,
                        f"{pair.ratio:.2f}:1",
                        pair.passes_aa,
                        pair.passes_aa_large,
                        pair.passes_aaa,
                        _text(pair.context, 48),
                        pair.occurrences,
                    )
                )
            result.append(ThemeColors(theme, decorative, groups, pairs))
        return result

    # Typography ----------------------------------------------------------------

    def families(self) -> list[FamilyView]:
        views: list[FamilyView] = []
        for index, family in enumerate(self.analysis.typography.families):
            css_class = f"ff-{index}"
            self.css.add(rule(f".{css_class}", [("font-family", self.fonts.stack(family.name))]))
            views.append(
                FamilyView(
                    name=font_label(family.name),
                    role=family.role.value,
                    category=family.category,
                    weights=", ".join(str(weight) for weight in family.weights),
                    google=family.is_google_font,
                    substitutes=_join(family.substitutes),
                    rendered_with=self.fonts.rendered_with.get(family.name),
                    css_class=css_class,
                )
            )
        return views

    def levels(self) -> list[LevelView]:
        views: list[LevelView] = []
        for index, level in enumerate(self.analysis.typography.levels):
            css_class = f"tl-{index}"
            self.level_classes.setdefault(level.name, css_class)
            self.css.add(rule(f".{css_class}", self.level_declarations(level)))
            sample = next((text for text in level.samples if text.strip()), "")
            if not sample:
                sample = SAMPLE_TEXT.get(level.name, BODY_SAMPLE)
            line_height = (
                f"{number(level.line_height, digits=3)}"
                if level.line_height is not None
                else (_px(level.line_height_px) or "normal")
            )
            views.append(
                LevelView(
                    name=level.name,
                    css_class=css_class,
                    sample=_text(sample, 90),
                    family=font_label(level.font_family),
                    size=format_px(level.size_px),
                    weight=level.weight,
                    line_height=line_height,
                    letter_spacing=format_px(level.letter_spacing_px),
                    transform=None if level.text_transform == "none" else level.text_transform,
                    tags=", ".join(level.tags),
                    count=level.count,
                )
            )
        return views

    def level_declarations(self, level: TypeLevel) -> list[tuple[str, str | None]]:
        if level.line_height is not None and level.line_height > 0:
            line_height: str | None = f"{number(level.line_height, digits=3)}"
        else:
            line_height = _px(level.line_height_px)
        transform = (
            level.text_transform
            if level.text_transform
            in (
                "uppercase",
                "lowercase",
                "capitalize",
            )
            else None
        )
        return [
            ("font-family", self.fonts.stack(level.font_family)),
            ("font-size", format_px(max(level.size_px, 1.0))),
            ("font-weight", str(max(1, min(1000, level.weight)))),
            ("line-height", line_height),
            ("letter-spacing", format_px(level.letter_spacing_px)),
            ("text-transform", transform),
        ]

    # Scales ------------------------------------------------------------------

    def spacing(self) -> list[ScaleRow]:
        rows: list[ScaleRow] = []
        for index, step in enumerate(self.analysis.spacing.steps):
            if step.value_px < 0:
                continue
            css_class = f"sp-{index}"
            value = format_px(step.value_px)
            self.css.add(rule(f".{css_class}", [("width", f"min({value}, 100%)")]))
            rows.append(ScaleRow(step.name, value, css_class, _join(step.used_on), step.count))
        return rows

    def radii(self) -> list[ScaleRow]:
        rows: list[ScaleRow] = []
        for index, step in enumerate(self.analysis.radii):
            css_class = f"rd-{index}"
            self.css.add(
                rule(f".{css_class}", [("border-radius", var(f"--radius-{ident(step.name)}"))])
            )
            value = (
                f"{radius_css(step.value_px)} (pill)"
                if step.value_px >= PILL_RADIUS_PX
                else format_px(step.value_px)
            )
            rows.append(ScaleRow(step.name, value, css_class, _join(step.used_on), step.count))
        return rows

    def shadows(self) -> list[ShadowView]:
        views: list[ShadowView] = []
        for index, level in enumerate(self.analysis.shadows):
            css = css_shadow(level.css)
            if css is None:
                continue
            css_class = f"sh-{index}"
            self.css.add(
                rule(f".{css_class}", [("box-shadow", var(f"--shadow-{ident(level.name)}", css))])
            )
            views.append(ShadowView(level.name, css_class, css, level.layers, _join(level.used_on)))
        return views

    # Components ----------------------------------------------------------------

    def components(self) -> list[ComponentView]:
        analysis = self.analysis
        context = ComponentContext(
            fonts=self.fonts,
            levels={level.name: level for level in analysis.typography.levels},
            color_names=frozenset(
                name for theme in self.themes for name in _palette_colors(theme.palette)
            ),
            spacing_names=frozenset(ident(step.name) for step in analysis.spacing.steps),
            radius_names=frozenset(ident(step.name) for step in analysis.radii),
            shadow_names=shadow_names(analysis.shadows),
        )
        specs = _ordered_components(analysis.components)
        classes = {id(spec): f"cx-{index}" for index, spec in enumerate(specs)}
        for spec in specs:
            self.css.add(*component_rules(spec, f".{classes[id(spec)]}", context))
        nav_link = next(
            (s for s in specs if s.kind is ComponentKind.LINK and s.variant == "nav"), None
        ) or next((s for s in specs if s.name == "nav-link"), None)
        button = next(
            (s for s in specs if s.name == "button-primary"),
            next((s for s in specs if s.kind is ComponentKind.BUTTON), None),
        )
        title_class = self._level_class(("title", "subtitle", "heading"))
        body_class = self._level_class(("body", "body-sm", "body-lg"))
        views: list[ComponentView] = []
        for spec in specs:
            nav_links: list[str] = []
            if spec.kind is ComponentKind.NAVIGATION:
                nav_links = [
                    _text(text, 24) for text in (nav_link.samples if nav_link else []) if text
                ][:4] or ["Product", "Pricing", "Docs"]
            views.append(
                ComponentView(
                    name=spec.name,
                    kind=spec.kind.value,
                    variant=spec.variant,
                    count=spec.count,
                    css_class=classes[id(spec)],
                    render=_render_kind(spec),
                    label=_component_label(spec),
                    specs=_spec_rows(spec.properties, spec.token_refs),
                    states=[
                        (state, _spec_rows(changes, spec.state_token_refs.get(state, {})))
                        for state, changes in spec.states.items()
                    ],
                    hover="hover" in spec.states,
                    contrast=None
                    if spec.contrast_ratio is None
                    else f"{spec.contrast_ratio:.2f}:1",
                    class_hints=_join(spec.class_hints[:6]),
                    title_class=title_class,
                    body_class=body_class,
                    nav_links=nav_links,
                    nav_link_class=None if nav_link is None else classes[id(nav_link)],
                    nav_button_class=None if button is None else classes[id(button)],
                    nav_button_label=_component_label(button) if button is not None else "",
                    gutter=_needs_gutter(spec),
                )
            )
        return views

    def _level_class(self, names: Sequence[str]) -> str:
        for name in names:
            if name in self.level_classes:
                return self.level_classes[name]
        return ""

    # Layout ------------------------------------------------------------------

    def breakpoints(self) -> tuple[list[BreakpointView], str]:
        widths = [
            value
            for bp in self.analysis.breakpoints
            for value in (bp.min_width_px, bp.max_width_px)
            if value is not None and value >= 0
        ]
        scale = max([1440.0, *[width * 1.1 for width in widths]])
        views: list[BreakpointView] = []
        for index, bp in enumerate(self.analysis.breakpoints):
            position = bp.min_width_px if bp.min_width_px is not None else bp.max_width_px
            css_class = f"bp-{index}"
            if position is not None and position >= 0:
                self.css.add(rule(f".{css_class}", [("left", f"{position / scale * 100:.2f}%")]))
            views.append(
                BreakpointView(
                    _text(bp.name, 24),
                    _px(bp.min_width_px),
                    _px(bp.max_width_px),
                    bp.occurrences,
                    css_class,
                    position is not None and position / scale > 0.6,
                )
            )
        return views, format_px(scale)

    def changes(self) -> list[ChangeView]:
        views: list[ChangeView] = []
        for change in self.analysis.layout.changes:
            views.append(
                ChangeView(
                    f"{change.from_width} → {change.to_width}px",
                    CHANGE_TITLES.get(change.kind, change.kind.value.replace("_", " ")),
                    _format_params(change.params),
                    _text(change.evidence, 120),
                )
            )
        return views

    def snapshots(self) -> list[SnapshotRow]:
        rows: list[SnapshotRow] = []
        for snap in self.analysis.layout.snapshots:
            nav = f"{snap.visible_nav_links} links" if snap.nav_visible else "hidden"
            if snap.hamburger_visible:
                nav += " + hamburger"
            rows.append(
                SnapshotRow(
                    viewport=f"{snap.viewport_width}px",
                    columns=str(snap.main_columns) if snap.main_columns is not None else "—",
                    nav=nav,
                    container=_px(snap.container_width_px) or "—",
                    gutter=_px(snap.gutter_px) or "—",
                    heading=_px(snap.max_heading_px) or "—",
                    tap=_px(snap.min_tap_target_px) or "—",
                )
            )
        return rows

    # Root CSS ------------------------------------------------------------------

    def root_css(self) -> str:
        analysis = self.analysis
        declarations: list[tuple[str, str | None]] = list(self.fonts.variables())
        declarations += scale_variables(analysis.spacing.steps, analysis.radii, analysis.shadows)
        body_family = next(
            (level.font_family for level in analysis.typography.levels if level.name == "body"),
            None,
        )
        heading_family = next(
            (
                level.font_family
                for level in analysis.typography.levels
                if level.name in ("display", "heading")
            ),
            None,
        )
        declarations += [
            (
                "--pv-font",
                self.fonts.stack(body_family)
                if body_family
                else self.fonts.role_stack("primary", SANS_STACK),
            ),
            (
                "--pv-heading-font",
                self.fonts.stack(heading_family)
                if heading_family
                else self.fonts.role_stack("heading", var("--pv-font")),
            ),
            ("--pv-mono", self.fonts.role_stack("mono", MONO_STACK)),
            ("--pv-gutter", format_px(analysis.layout.gutter_px or 24.0)),
        ]
        blocks = [rule(":root", declarations)]
        blocks += [theme_block(theme.id, theme.palette) for theme in self.themes]
        if len(self.themes) > 1:
            # Component stages keep the default capture's colors in every theme: component
            # styles (and their hover states) were only measured there.
            blocks.append(palette_block(".pv-capture", self.themes[0].palette))
        return "\n".join(block for block in blocks if block)

    def build(self) -> str:
        analysis = self.analysis
        site = analysis.site
        swatches = self.swatches()
        theme_colors = self.theme_colors()
        families = self.families()
        levels = self.levels()
        spacing = self.spacing()
        radii = self.radii()
        shadows = self.shadows()
        components = self.components()
        breakpoints, ruler_max = self.breakpoints()
        css = self.root_css() + "\n" + self.css.render()
        extra_weights = weights_by_family(analysis.typography, analysis.components)
        date = site.collected_at or analysis.analyzed_at
        container = (
            analysis.spacing.container_max_width_px or analysis.layout.container_max_width_px
        )
        has_dark = len(self.themes) > 1
        sections = [
            ("colors", "Colors", bool(swatches)),
            ("contrast", "Contrast", any(item.pairs for item in theme_colors)),
            ("typography", "Typography", bool(families or levels)),
            ("spacing", "Spacing", bool(spacing)),
            ("radius", "Radius", bool(radii)),
            ("shadows", "Shadows", bool(shadows)),
            ("components", "Components", bool(components)),
            ("layout", "Layout", True),
        ]
        return render_template(
            TEMPLATE,
            lang=html_lang(site.lang),
            site_name=_text(site.name or site.url, 80) or "Website",
            site_url=_text(site.url, 200),
            site_href=http_url(site.url),
            site_slug=_site_slug(site.url),
            title_text=_text(site.title, 120),
            date=date.date().isoformat(),
            pages=", ".join(_text(page, 40) for page in site.pages_analyzed) or "home",
            disclaimer_en=DISCLAIMER_EN,
            disclaimer_vi=DISCLAIMER_VI,
            tool_version=_text(analysis.tool_version, 20),
            google_fonts=self.fonts.google_link(extra_weights),
            css=css,
            has_dark=has_dark,
            themes=self.themes,
            sections=[(anchor, title) for anchor, title, present in sections if present],
            swatches=swatches,
            theme_colors=theme_colors,
            families=families,
            levels=levels,
            typography=analysis.typography,
            spacing=spacing,
            base_unit=analysis.spacing.base_unit_px,
            container=_px(container),
            section_gap=_px(analysis.spacing.section_gap_px),
            paddings=", ".join(_text(item, 30) for item in analysis.spacing.common_paddings),
            radii=radii,
            shadows=shadows,
            components=components,
            breakpoints=breakpoints,
            ruler_max=ruler_max,
            changes=self.changes(),
            snapshots=self.snapshots(),
            shell=analysis.layout.shell,
            gutter=_px(analysis.layout.gutter_px),
            screenshots=_screenshot_links(analysis),
            warnings=[_text(warning, 300) for warning in analysis.warnings],
            stats=analysis.stats,
        )


def _ordered_components(components: Sequence[ComponentSpec]) -> list[ComponentSpec]:
    """Components in naming.COMPONENT_NAME_ORDER, unknown names last (stable)."""
    order = {name: index for index, name in enumerate(COMPONENT_NAME_ORDER)}
    return sorted(
        components,
        key=lambda spec: order.get(spec.name, len(order)),
    )


def _render_kind(spec: ComponentSpec) -> str:
    return {
        ComponentKind.BUTTON: "button",
        ComponentKind.LINK: "link",
        ComponentKind.INPUT: "input",
        ComponentKind.CARD: "card",
        ComponentKind.NAVIGATION: "nav",
        ComponentKind.BADGE: "badge",
        ComponentKind.FOOTER: "footer",
    }.get(spec.kind, "box")


def _needs_gutter(spec: ComponentSpec) -> bool:
    if spec.kind not in (ComponentKind.NAVIGATION, ComponentKind.FOOTER):
        return False
    sides = sides_px(spec.properties.get("padding"))
    return sides is None or (sides[1] <= 0 and sides[3] <= 0)


def _format_params(params: Mapping[str, float]) -> str:
    """{"from_px": 64, "to_px": 48} -> "64px → 48px"; {"below_px": 1024} -> "below 1024px"."""

    def value(key: str) -> str:
        return f"{number(params[key])}{'px' if key.endswith('_px') else ''}"

    parts: list[str] = []
    used: set[str] = set()
    for first, second in (("from", "to"), ("from_px", "to_px")):
        if first in params and second in params:
            parts.append(f"{value(first)} → {value(second)}")
            used.update((first, second))
    for key in params:
        if key not in used:
            parts.append(f"{key.removesuffix('_px').replace('_', ' ')} {value(key)}")
    return " · ".join(parts)


def _component_label(spec: ComponentSpec) -> str:
    if spec.kind is ComponentKind.INPUT:
        return "you@example.com"
    sample = next((text for text in spec.samples if text.strip()), "")
    if sample and spec.kind not in (ComponentKind.NAVIGATION, ComponentKind.FOOTER):
        return _text(sample, 32)
    defaults = {
        ComponentKind.BUTTON: "Button",
        ComponentKind.LINK: "Text link",
        ComponentKind.CARD: "Card title",
        ComponentKind.BADGE: "Badge",
    }
    return defaults.get(spec.kind, spec.name)


def _spec_rows(properties: dict[str, str], refs: dict[str, str]) -> list[SpecRow]:
    order = {key: index for index, key in enumerate(PROPERTY_ORDER)}
    keys = sorted(
        set(properties) | {key for key in refs if key not in properties},
        key=lambda key: (order.get(key, len(order)), key),
    )
    rows: list[SpecRow] = []
    for key in keys:
        reference = refs.get(key)
        parsed = parse_token_ref(reference or "")
        rows.append(
            SpecRow(
                key,
                _text(properties.get(key, "—"), 80),
                None if parsed is None else "{" + f"{parsed[0]}.{parsed[1]}" + "}",
            )
        )
    return rows


def _screenshot_links(analysis: DesignAnalysis) -> list[LinkView]:
    links: list[LinkView] = []
    for shot in analysis.screenshots:
        path = shot.path.replace("\\", "/")
        if ".." in path.split("/") or not _SCREENSHOT_PATH_RE.match(path):
            continue
        scheme = " dark" if shot.color_scheme == "dark" else ""
        links.append(LinkView(f"{shot.viewport_width}px{scheme}", path))
    return links


def render_preview(analysis: DesignAnalysis) -> str:
    """Self-contained HTML (inline CSS/JS; the only external resource is one optional Google
    Fonts stylesheet, offline the font stacks apply): color swatches with hex + role +
    contrast, typography specimens per level, buttons/inputs/cards/badges/nav rebuilt from
    ComponentSpec with hover styles, spacing/radius/shadow scales, light/dark toggle when
    analysis.dark exists (pure; templates/preview.html.j2)."""
    return _PreviewBuilder(analysis).build()
