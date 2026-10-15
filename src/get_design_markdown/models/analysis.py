"""Stage 2 (Analyzer) output: analysis.json."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from get_design_markdown.models.base import GdmModel, HexColor, TokenName
from get_design_markdown.models.raw import ColorScheme, LayoutSnapshot

ANALYSIS_SCHEMA_VERSION = 1


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


class SiteInfo(GdmModel):
    url: str
    name: str
    title: str = ""
    description: str | None = None
    lang: str | None = None
    pages_analyzed: list[str] = Field(default_factory=list)


# ─── Colors ──────────────────────────────────────────────────────────────────


class ColorUsage(GdmModel):
    """Area-weighted usage of a color per channel."""

    text: float = 0.0
    background: float = 0.0
    border: float = 0.0
    gradient: float = 0.0


class ColorCluster(GdmModel):
    id: str
    hex: HexColor
    alpha: float = Field(default=1.0, ge=0.0, le=1.0)
    weight: float = 0.0
    share: float = Field(default=0.0, ge=0.0, le=1.0)
    usage: ColorUsage = Field(default_factory=ColorUsage)
    decorative: bool = False
    #: OKLCH components: lightness 0..1, chroma >= 0, hue in degrees (None when achromatic).
    lightness: float = 0.0
    chroma: float = 0.0
    hue: float | None = None
    css_variables: list[str] = Field(default_factory=list)
    members: list[HexColor] = Field(default_factory=list)


class ColorToken(GdmModel):
    name: TokenName
    value: HexColor
    role: ColorRole
    cluster_id: str | None = None
    evidence: str = ""


class ContrastPair(GdmModel):
    foreground: HexColor
    background: HexColor
    ratio: float = Field(ge=1.0)
    passes_aa: bool
    passes_aa_large: bool
    passes_aaa: bool
    occurrences: int = 0
    context: str = ""


class ColorPalette(GdmModel):
    scheme: ColorScheme = "light"
    clusters: list[ColorCluster] = Field(default_factory=list)
    tokens: list[ColorToken] = Field(default_factory=list)
    contrast_pairs: list[ContrastPair] = Field(default_factory=list)


# ─── Typography ──────────────────────────────────────────────────────────────


class FontFamilyInfo(GdmModel):
    name: str
    stack: list[str] = Field(default_factory=list)
    role: FontRole
    share: float = Field(default=0.0, ge=0.0, le=1.0)
    weights: list[int] = Field(default_factory=list)
    is_google_font: bool = False
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
    tags: list[str] = Field(default_factory=list)
    count: int = 0
    samples: list[str] = Field(default_factory=list)


class Typography(GdmModel):
    families: list[FontFamilyInfo] = Field(default_factory=list)
    levels: list[TypeLevel] = Field(default_factory=list)
    weights_used: list[int] = Field(default_factory=list)
    base_size_px: float | None = None
    scale_ratio: float | None = None
    scale_ratio_name: str | None = None


# ─── Spacing, shapes, breakpoints, layout ────────────────────────────────────


class ScaleStep(GdmModel):
    name: TokenName
    value_px: float
    count: int = 0
    used_on: list[str] = Field(default_factory=list)


class SpacingSystem(GdmModel):
    base_unit_px: int | None = None
    steps: list[ScaleStep] = Field(default_factory=list)
    container_max_width_px: float | None = None
    common_paddings: list[str] = Field(default_factory=list)


class ShadowLevel(GdmModel):
    name: TokenName
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
    #: Key into synthesizer phrase files, e.g. "nav_collapses_to_hamburger".
    description_key: str
    evidence: str = ""


class LayoutInfo(GdmModel):
    container_max_width_px: float | None = None
    shell: Literal["top-nav", "sidebar", "top-nav+sidebar", "unknown"] = "unknown"
    snapshots: list[LayoutSnapshot] = Field(default_factory=list)
    changes: list[ResponsiveChange] = Field(default_factory=list)


# ─── Components ──────────────────────────────────────────────────────────────


class ComponentSpec(GdmModel):
    #: Front-matter name, e.g. "button-primary"; hover becomes "button-primary-hover".
    name: TokenName
    kind: ComponentKind
    count: int = 0
    #: Raw measured values, e.g. {"backgroundColor": "#635bff", "padding": "8px 12px"}.
    properties: dict[str, str] = Field(default_factory=dict)
    #: Same keys as properties, mapped to token references, e.g. "{colors.primary}".
    token_refs: dict[str, str] = Field(default_factory=dict)
    #: State name ("hover", ...) -> changed properties (raw values).
    states: dict[str, dict[str, str]] = Field(default_factory=dict)
    class_hints: list[str] = Field(default_factory=list)
    samples: list[str] = Field(default_factory=list)


# ─── Root ────────────────────────────────────────────────────────────────────


class ThemeAnalysis(GdmModel):
    colors: ColorPalette


class AnalysisStats(GdmModel):
    pages: int = 0
    elements: int = 0
    stylesheets: int = 0
    color_clusters: int = 0


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
    stats: AnalysisStats = Field(default_factory=AnalysisStats)
    warnings: list[str] = Field(default_factory=list)
