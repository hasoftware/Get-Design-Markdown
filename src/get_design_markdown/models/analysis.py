"""Stage 2 (Analyzer) output: analysis.json."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from get_design_markdown.models.base import GdmModel, HexColor, TokenName
from get_design_markdown.models.raw import ColorScheme, LayoutSnapshot, Screenshot

ANALYSIS_SCHEMA_VERSION = 1

#: Usage channel of a color. Clustering never merges colors across channels.
ColorChannel = Literal["text", "background", "border", "gradient"]
FontCategory = Literal["sans-serif", "serif", "monospace", "display", "handwriting", "unknown"]


class ColorRole(StrEnum):
    BACKGROUND = "background"
    SURFACE = "surface"
    TEXT = "text"
    TEXT_MUTED = "text_muted"
    NEUTRAL = "neutral"
    BRAND = "brand"
    ACCENT = "accent"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    INFO = "info"
    BORDER = "border"
    DECORATIVE = "decorative"


class FontRole(StrEnum):
    PRIMARY = "primary"
    HEADING = "heading"
    MONO = "mono"
    DISPLAY = "display"


class ComponentKind(StrEnum):
    BUTTON = "button"
    LINK = "link"
    INPUT = "input"
    CARD = "card"
    NAVIGATION = "navigation"
    BADGE = "badge"
    FOOTER = "footer"


class ResponsiveChangeKind(StrEnum):
    """Layout change between two viewport widths; values are phrase keys of the synthesizer."""

    NAV_COLLAPSES_TO_HAMBURGER = "nav_collapses_to_hamburger"
    NAV_HIDDEN = "nav_hidden"
    SIDEBAR_HIDDEN = "sidebar_hidden"
    COLUMNS_REDUCE = "columns_reduce"
    HEADING_SCALES_DOWN = "heading_scales_down"
    CONTAINER_BECOMES_FLUID = "container_becomes_fluid"
    GUTTER_SHRINKS = "gutter_shrinks"
    HORIZONTAL_SCROLL_APPEARS = "horizontal_scroll_appears"
    TAP_TARGETS_SMALL = "tap_targets_small"


class SiteInfo(GdmModel):
    url: str
    name: str
    title: str = ""
    description: str | None = None
    lang: str | None = None
    #: When raw.json was collected (the "extraction date" written into DESIGN.md).
    collected_at: datetime | None = None
    #: Page names (PageCapture.name), main page first.
    pages_analyzed: list[str] = Field(default_factory=list)


# ─── Colors ──────────────────────────────────────────────────────────────────


class ColorUsage(GdmModel):
    """Area-weighted usage of a color family in every channel."""

    text: float = 0.0
    background: float = 0.0
    border: float = 0.0
    gradient: float = 0.0


class ColorCluster(GdmModel):
    """A CIEDE2000 cluster of colors built inside one usage channel (debug detail)."""

    id: str
    channel: ColorChannel
    #: Representative color; #rrggbbaa when alpha < 1.
    hex: HexColor
    alpha: float = Field(default=1.0, ge=0.0, le=1.0)
    #: Area-weighted usage inside `channel`.
    weight: float = 0.0
    #: weight / total weight of the channel.
    share: float = Field(default=0.0, ge=0.0, le=1.0)
    #: Number of elements contributing to this cluster.
    count: int = 0
    #: Usage of the same color (ΔE2000 < threshold) across all channels.
    usage: ColorUsage = Field(default_factory=ColorUsage)
    decorative: bool = False
    #: OKLCH components: lightness 0..1, chroma >= 0, hue in degrees (None when achromatic).
    lightness: float = 0.0
    chroma: float = 0.0
    hue: float | None = None
    css_variables: list[str] = Field(default_factory=list)
    members: list[HexColor] = Field(default_factory=list)
    #: Short selectors of example elements ("a.btn-primary").
    samples: list[str] = Field(default_factory=list)


class ColorToken(GdmModel):
    """Semantic palette entry written to the DESIGN.md front matter."""

    name: TokenName
    value: HexColor
    role: ColorRole
    cluster_id: str | None = None
    #: CSS custom property whose name hinted this role, e.g. "--color-primary".
    css_variable: str | None = None
    evidence: str = ""


class ContrastPair(GdmModel):
    foreground: HexColor
    background: HexColor
    #: Token names when the colors match palette tokens.
    foreground_token: TokenName | None = None
    background_token: TokenName | None = None
    ratio: float = Field(ge=1.0)
    passes_aa: bool
    passes_aa_large: bool
    passes_aaa: bool
    occurrences: int = 0
    #: Where the pair occurs, e.g. "button-primary" or "body text".
    context: str = ""


class ColorPalette(GdmModel):
    #: Appearance of the palette (decided from the canvas lightness, not from the media query).
    scheme: ColorScheme = "light"
    clusters: list[ColorCluster] = Field(default_factory=list)
    tokens: list[ColorToken] = Field(default_factory=list)
    contrast_pairs: list[ContrastPair] = Field(default_factory=list)


# ─── Typography ──────────────────────────────────────────────────────────────


class FontFamilyInfo(GdmModel):
    name: str
    stack: list[str] = Field(default_factory=list)
    role: FontRole
    category: FontCategory = "unknown"
    share: float = Field(default=0.0, ge=0.0, le=1.0)
    weights: list[int] = Field(default_factory=list)
    is_google_font: bool = False
    #: Freely available look-alikes (Google Fonts first), for the "Font Substitutes" note.
    substitutes: list[str] = Field(default_factory=list)


class TypeLevel(GdmModel):
    name: TokenName
    size_px: float
    font_family: str
    weight: int
    line_height_px: float | None = None
    #: Unitless line-height (line_height_px / size_px), as written in DESIGN.md.
    line_height: float | None = None
    letter_spacing_px: float = 0.0
    text_transform: str = "none"
    #: HTML tags mapped to this level (metadata), e.g. ["h1"] or ["button", "a"].
    tags: list[str] = Field(default_factory=list)
    count: int = 0
    samples: list[str] = Field(default_factory=list)


class Typography(GdmModel):
    families: list[FontFamilyInfo] = Field(default_factory=list)
    #: Ordered largest to smallest.
    levels: list[TypeLevel] = Field(default_factory=list)
    weights_used: list[int] = Field(default_factory=list)
    base_size_px: float | None = None
    scale_ratio: float | None = None
    scale_ratio_name: str | None = None


# ─── Spacing, shapes, breakpoints, layout ────────────────────────────────────


class ScaleStep(GdmModel):
    """One step of the spacing (xxs…xxxl) or radius (xs…full) scale, ordered ascending."""

    name: TokenName
    value_px: float
    count: int = 0
    #: Page-weighted usage (analyzer/common.page_weights: 1.0 per element on the main page,
    #: less on sub-pages). 0 in files written before this field existed; readers then fall
    #: back to `count`.
    weight: float = 0.0
    used_on: list[str] = Field(default_factory=list)


class SpacingSystem(GdmModel):
    base_unit_px: int | None = None
    steps: list[ScaleStep] = Field(default_factory=list)
    container_max_width_px: float | None = None
    #: Typical vertical gap between page sections.
    section_gap_px: float | None = None
    #: Most common padding shorthands, e.g. ["12px 20px", "32px"].
    common_paddings: list[str] = Field(default_factory=list)


class ShadowLevel(GdmModel):
    """Ordered from the subtlest to the strongest elevation."""

    name: TokenName
    #: Normalized CSS (css_values.format_box_shadow), all layers.
    css: str
    layers: int = 1
    count: int = 0
    used_on: list[str] = Field(default_factory=list)


class Breakpoint(GdmModel):
    name: str
    min_width_px: float | None = None
    max_width_px: float | None = None
    occurrences: int = 0


class ResponsiveChange(GdmModel):
    from_width: int
    to_width: int
    kind: ResponsiveChangeKind
    #: Numbers the phrase needs, e.g. {"from": 3, "to": 1} or {"from_px": 64, "to_px": 40}.
    params: dict[str, float] = Field(default_factory=dict)
    evidence: str = ""


class LayoutInfo(GdmModel):
    container_max_width_px: float | None = None
    #: Horizontal padding of the main container at desktop width.
    gutter_px: float | None = None
    shell: Literal["top-nav", "sidebar", "top-nav+sidebar", "unknown"] = "unknown"
    snapshots: list[LayoutSnapshot] = Field(default_factory=list)
    changes: list[ResponsiveChange] = Field(default_factory=list)


# ─── Components ──────────────────────────────────────────────────────────────


class ComponentSpec(GdmModel):
    """A component variant.

    `properties` keys (camelCase, raw measured values): backgroundColor, textColor, borderColor,
    borderWidth, rounded, padding, height, width, gap, fontFamily, fontSize, fontWeight,
    lineHeight, letterSpacing, textTransform, textDecoration, shadow, backdropFilter,
    placeholderColor, position, opacity. Colors are canonical hex (#rrggbb / #rrggbbaa) or
    "transparent"; lengths use px ("8px", padding as a CSS shorthand "12px 20px"); opacity is a
    unitless number ("0.8", mostly seen in hover states). `rounded` is one length, or - when the
    corners differ - a border-radius shorthand in corner order top-left, top-right,
    bottom-right, bottom-left, compressed like padding ("4px 4px 0px 0px" for a tab); token_refs
    then has no "rounded" entry, since a {rounded.*} step stands for four equal corners.
    DESIGN.md front matter keeps only the spec's component keys; tokens.json exports the rest.

    Form fields: single-line inputs and selects are "text-input" (kind input, with a height);
    <textarea> fields are "text-area" (kind input, variant "multiline") without a height, since
    their height follows rows and content.
    """

    #: Front-matter name, e.g. "button-primary"; hover becomes "button-primary-hover".
    name: TokenName
    kind: ComponentKind
    #: "primary", "secondary", "tertiary", "ghost", "outline", "pill"... (None when single).
    variant: str | None = None
    count: int = 0
    properties: dict[str, str] = Field(default_factory=dict)
    #: Property key -> token reference ("{colors.primary}", "{rounded.md}", "{spacing.md}"),
    #: plus "typography" -> "{typography.ui}" when the text style matches a type level.
    token_refs: dict[str, str] = Field(default_factory=dict)
    #: State name ("hover") -> changed properties (same keys and formats as `properties`).
    states: dict[str, dict[str, str]] = Field(default_factory=dict)
    #: State name -> property key -> token reference, when a state value matches a token.
    state_token_refs: dict[str, dict[str, str]] = Field(default_factory=dict)
    #: WCAG ratio of textColor over the effective background (None when not applicable).
    contrast_ratio: float | None = None
    class_hints: list[str] = Field(default_factory=list)
    samples: list[str] = Field(default_factory=list)


# ─── Root ────────────────────────────────────────────────────────────────────


class ThemeAnalysis(GdmModel):
    colors: ColorPalette


class AnalysisStats(GdmModel):
    pages: int = 0
    elements: int = 0
    stylesheets: int = 0
    css_variables: int = 0
    hover_states: int = 0
    color_clusters: int = 0
    components: int = 0


class DesignAnalysis(GdmModel):
    schema_version: int = ANALYSIS_SCHEMA_VERSION
    tool_version: str
    analyzed_at: datetime
    site: SiteInfo
    colors: ColorPalette = Field(default_factory=ColorPalette)
    typography: Typography = Field(default_factory=Typography)
    spacing: SpacingSystem = Field(default_factory=SpacingSystem)
    radii: list[ScaleStep] = Field(default_factory=list)
    shadows: list[ShadowLevel] = Field(default_factory=list)
    breakpoints: list[Breakpoint] = Field(default_factory=list)
    layout: LayoutInfo = Field(default_factory=LayoutInfo)
    components: list[ComponentSpec] = Field(default_factory=list)
    dark: ThemeAnalysis | None = None
    #: Screenshots of the main page (light, then dark), paths relative to the site folder.
    screenshots: list[Screenshot] = Field(default_factory=list)
    stats: AnalysisStats = Field(default_factory=AnalysisStats)
    warnings: list[str] = Field(default_factory=list)
