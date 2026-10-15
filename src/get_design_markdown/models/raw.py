"""Stage 1 (Collector) output: raw.json."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from get_design_markdown.models.base import GdmModel

RAW_SCHEMA_VERSION = 1
MAX_TEXT_LENGTH = 50

ColorScheme = Literal["light", "dark"]
#: Four-sided computed values in CSS order: top, right, bottom, left
#: (border-radius: top-left, top-right, bottom-right, bottom-left).
Sides = tuple[str, str, str, str]
#: Nearest landmark ancestor (or the element itself) of a sampled element.
Landmark = Literal["header", "nav", "main", "footer", "aside", "form", "dialog"]
#: How far page loading got before the collector moved on.
LoadState = Literal["networkidle", "load", "domcontentloaded"]

#: Separator used when a four-sided value is stored as one string (hover diffs).
SIDES_SEPARATOR = " | "
DEFAULT_SCREENSHOT_WIDTHS = (1440, 768, 390)


def join_sides(sides: Sides) -> str:
    """Collapse four sides into one string: a single value when all four are equal."""
    if len(set(sides)) == 1:
        return sides[0]
    return SIDES_SEPARATOR.join(sides)


def split_sides(value: str) -> Sides:
    """Inverse of join_sides()."""
    parts = [part.strip() for part in value.split(SIDES_SEPARATOR.strip())]
    if len(parts) == 4:
        return (parts[0], parts[1], parts[2], parts[3])
    single = value.strip()
    return (single, single, single, single)


class BoundingBox(GdmModel):
    """Document coordinates (CSS px) at the 1440px capture viewport."""

    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return max(self.width, 0.0) * max(self.height, 0.0)


class ComputedStyle(GdmModel):
    """Computed values exactly as returned by getComputedStyle().

    Field names are the snake_case form of the CSS property; HoverState.changed uses the same
    names as keys.
    """

    color: str = "rgb(0, 0, 0)"
    background_color: str = "rgba(0, 0, 0, 0)"
    background_image: str = "none"
    font_family: str = ""
    font_size: str = "16px"
    font_weight: str = "400"
    line_height: str = "normal"
    letter_spacing: str = "normal"
    text_transform: str = "none"
    text_decoration_line: str = "none"
    text_align: str = "start"
    padding: Sides = ("0px", "0px", "0px", "0px")
    margin: Sides = ("0px", "0px", "0px", "0px")
    row_gap: str = "normal"
    column_gap: str = "normal"
    border_radius: Sides = ("0px", "0px", "0px", "0px")
    border_width: Sides = ("0px", "0px", "0px", "0px")
    border_style: Sides = ("none", "none", "none", "none")
    border_color: Sides = (
        "rgb(0, 0, 0)",
        "rgb(0, 0, 0)",
        "rgb(0, 0, 0)",
        "rgb(0, 0, 0)",
    )
    box_shadow: str = "none"
    backdrop_filter: str = "none"
    opacity: str = "1"
    display: str = "block"
    position: str = "static"
    z_index: str = "auto"
    max_width: str = "none"
    cursor: str = "auto"
    overflow_x: str = "visible"


class ElementSample(GdmModel):
    """One visible element. `index` is its position in PageCapture.elements (document order)."""

    index: int
    tag: str
    role: str | None = None
    classes: list[str] = Field(default_factory=list)
    id: str | None = None
    #: innerText of the element (descendants included), whitespace-collapsed, <= 50 chars.
    text: str = ""
    #: True when the element has a non-blank text node as a direct child (it renders text
    #: in its own font); typography and text-color analysis should only count these.
    has_own_text: bool = False
    href: str | None = None
    input_type: str | None = None
    #: Computed color of ::placeholder for input/textarea elements.
    placeholder_color: str | None = None
    box: BoundingBox
    depth: int = 0
    #: Index of the nearest *sampled* ancestor (None for the root).
    parent_index: int | None = None
    child_count: int = 0
    landmark: Landmark | None = None
    #: Inside a cookie/consent banner, modal or other overlay that is not part of the design.
    in_overlay: bool = False
    styles: ComputedStyle = Field(default_factory=ComputedStyle)

    @field_validator("text", mode="before")
    @classmethod
    def _shorten_text(cls, value: object) -> object:
        if isinstance(value, str):
            collapsed = " ".join(value.split())
            return collapsed[:MAX_TEXT_LENGTH]
        return value


class CssVariable(GdmModel):
    name: str
    #: Value as written in the stylesheet / returned by getPropertyValue().
    declared: str | None = None
    #: Value after resolving var() references through a probe element, in computed form
    #: ("rgb(79, 70, 229)", "8px"...); None when it cannot be resolved.
    resolved: str | None = None
    source: Literal["root", "html", "stylesheet"]


class FontFace(GdmModel):
    family: str
    src: list[str] = Field(default_factory=list)
    weight: str = "normal"
    style: str = "normal"
    display: str | None = None
    stylesheet_url: str | None = None


class MediaQuery(GdmModel):
    query: str
    min_width_px: float | None = None
    max_width_px: float | None = None
    prefers_color_scheme: ColorScheme | None = None
    occurrences: int = 1


class StylesheetInfo(GdmModel):
    #: Response URL, or "inline:<page-name>#<n>" for <style> blocks.
    url: str
    inline: bool = False
    status: int | None = None
    size_bytes: int = 0
    rule_count: int = 0
    variables: list[CssVariable] = Field(default_factory=list)
    media_queries: list[MediaQuery] = Field(default_factory=list)
    font_faces: list[FontFace] = Field(default_factory=list)
    parse_errors: int = 0


class HoverState(GdmModel):
    element_index: int
    kind: Literal["button", "link"]
    #: ComputedStyle field name -> (before, after); only properties that changed.
    #: Four-sided fields are stored with join_sides().
    changed: dict[str, tuple[str, str]] = Field(default_factory=dict)


class LayoutSnapshot(GdmModel):
    """Layout facts measured at one viewport width (used for Responsive Behavior)."""

    viewport_width: int
    viewport_height: int = 0
    document_height: float = 0.0
    nav_visible: bool = False
    #: Number of visible links inside the main navigation.
    visible_nav_links: int = 0
    hamburger_visible: bool = False
    sidebar_visible: bool = False
    #: Columns of the dominant content grid: the column count whose grid containers cover the
    #: largest item area (collector/js/layout.js); a grid that stacks at this width counts as 1.
    main_columns: int = 1
    horizontal_scrollers: int = 0
    min_tap_target_px: float | None = None
    #: Width of the main content container and its horizontal padding at this viewport.
    container_width_px: float | None = None
    gutter_px: float | None = None
    #: Largest visible heading font size (h1/h2 or display text).
    max_heading_px: float | None = None


class Screenshot(GdmModel):
    name: str
    viewport_width: int
    viewport_height: int
    color_scheme: ColorScheme = "light"
    #: Relative to the site output folder, e.g. "screenshots/home-1440.png".
    path: str
    full_page: bool = False


class PageCapture(GdmModel):
    url: str
    final_url: str
    #: Short slug used in file names and reports: "home", "pricing", "docs-api"...
    name: str = "home"
    status_code: int | None = None
    load_state: LoadState | None = None
    title: str = ""
    meta_description: str | None = None
    lang: str | None = None
    color_scheme: ColorScheme = "light"
    document_height: float = 0.0
    root_font_size_px: float = 16.0
    css_variables: list[CssVariable] = Field(default_factory=list)
    #: Number of visible elements before applying max_elements.
    elements_total: int = 0
    elements: list[ElementSample] = Field(default_factory=list)
    hover_states: list[HoverState] = Field(default_factory=list)
    layouts: list[LayoutSnapshot] = Field(default_factory=list)
    screenshots: list[Screenshot] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RobotsCheck(GdmModel):
    robots_url: str
    #: None when robots.txt could not be evaluated (network error...).
    allowed: bool | None
    status_code: int | None = None
    note: str = ""


class CaptureOptions(GdmModel):
    """Options the collector ran with (stored in raw.json for reproducibility)."""

    max_elements: int = 3000
    dark: bool = False
    #: Number of extra sub-pages to discover from the navigation (--pages).
    pages: int = 0
    timeout_s: float = 45.0
    viewport_width: int = 1440
    viewport_height: int = 900
    screenshot_widths: list[int] = Field(default_factory=lambda: list(DEFAULT_SCREENSHOT_WIDTHS))
    hover_buttons: int = 10
    hover_links: int = 10


class RawCapture(GdmModel):
    schema_version: int = RAW_SCHEMA_VERSION
    tool_version: str
    source_url: str
    domain: str
    collected_at: datetime
    options: CaptureOptions | None = None
    robots: RobotsCheck | None = None
    #: pages[0] is always the main page.
    pages: list[PageCapture] = Field(default_factory=list)
    #: Main page captured again with prefers-color-scheme: dark (--dark).
    dark: PageCapture | None = None
    stylesheets: list[StylesheetInfo] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def main_page(self) -> PageCapture | None:
        return self.pages[0] if self.pages else None
