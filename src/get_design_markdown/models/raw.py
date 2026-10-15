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


class BoundingBox(GdmModel):
    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return max(self.width, 0.0) * max(self.height, 0.0)


class ComputedStyle(GdmModel):
    """Computed values exactly as returned by getComputedStyle()."""

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
    opacity: str = "1"
    display: str = "block"
    position: str = "static"
    cursor: str = "auto"
    overflow_x: str = "visible"


class ElementSample(GdmModel):
    index: int
    tag: str
    role: str | None = None
    classes: list[str] = Field(default_factory=list)
    id: str | None = None
    text: str = ""
    href: str | None = None
    input_type: str | None = None
    box: BoundingBox
    depth: int = 0
    parent_index: int | None = None
    child_count: int = 0
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
    declared: str | None = None
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
    url: str
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
    #: property name -> (before, after); only properties that changed.
    changed: dict[str, tuple[str, str]] = Field(default_factory=dict)


class LayoutSnapshot(GdmModel):
    viewport_width: int
    nav_visible: bool = False
    hamburger_visible: bool = False
    sidebar_visible: bool = False
    main_columns: int = 1
    horizontal_scrollers: int = 0
    min_tap_target_px: float | None = None


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
    title: str = ""
    meta_description: str | None = None
    lang: str | None = None
    color_scheme: ColorScheme = "light"
    document_height: float = 0.0
    css_variables: list[CssVariable] = Field(default_factory=list)
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


class RawCapture(GdmModel):
    schema_version: int = RAW_SCHEMA_VERSION
    tool_version: str
    source_url: str
    domain: str
    collected_at: datetime
    robots: RobotsCheck | None = None
    #: pages[0] is always the main page.
    pages: list[PageCapture] = Field(default_factory=list)
    dark: PageCapture | None = None
    stylesheets: list[StylesheetInfo] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def main_page(self) -> PageCapture | None:
        return self.pages[0] if self.pages else None
