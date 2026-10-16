"""Deterministic generator of the shared, read-only test fixtures (maintainer-owned, frozen).

    python -m uv run python -m tests.fixtures.build_samples

writes tests/fixtures/raw_sample.json, analysis_sample.json and site_contract.json.
The page is a fictional light marketing site, "Acme Cloud" (acme.example), with a dark variant.
tests/test_fixtures.py fails when the JSON files drift from this generator.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from get_design_markdown.analyzer.analyze import build_site_info
from get_design_markdown.analyzer.common import (
    effective_background,
    element_index,
    iter_elements,
    short_selector,
    visible_area,
)
from get_design_markdown.analyzer.css_values import (
    contrast_levels,
    contrast_ratio,
    gradient_colors,
    parse_color,
    parse_px,
    to_oklch,
)
from get_design_markdown.analyzer.naming import COLOR_TOKEN_ROLES
from get_design_markdown.models.analysis import (
    AnalysisStats,
    Breakpoint,
    ColorChannel,
    ColorCluster,
    ColorPalette,
    ColorRole,
    ColorToken,
    ColorUsage,
    ComponentKind,
    ComponentSpec,
    ContrastPair,
    DesignAnalysis,
    FontFamilyInfo,
    FontRole,
    LayoutInfo,
    ResponsiveChange,
    ResponsiveChangeKind,
    ScaleStep,
    ShadowLevel,
    SpacingSystem,
    ThemeAnalysis,
    TypeLevel,
    Typography,
)
from get_design_markdown.models.raw import (
    BoundingBox,
    CaptureOptions,
    ColorScheme,
    ComputedStyle,
    CssVariable,
    ElementSample,
    FontFace,
    HoverState,
    LayoutSnapshot,
    MediaQuery,
    PageCapture,
    RawCapture,
    RobotsCheck,
    Screenshot,
    StylesheetInfo,
)

FIXTURES_DIR = Path(__file__).resolve().parent
RAW_SAMPLE_PATH = FIXTURES_DIR / "raw_sample.json"
ANALYSIS_SAMPLE_PATH = FIXTURES_DIR / "analysis_sample.json"
SITE_CONTRACT_PATH = FIXTURES_DIR / "site_contract.json"

TOOL_VERSION = "0.1.0"
SOURCE_URL = "https://acme.example/"
DOMAIN = "acme.example"
COLLECTED_AT = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
ANALYZED_AT = datetime(2026, 10, 15, 8, 5, tzinfo=UTC)

SANS = 'Inter, system-ui, -apple-system, "Segoe UI", sans-serif'
DISPLAY = '"Space Grotesk", Inter, system-ui, sans-serif'
MONO = '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace'

LIGHT: dict[str, str] = {
    "canvas": "rgb(255, 255, 255)",
    "surface": "rgb(248, 250, 252)",
    "inverse": "rgb(11, 16, 32)",
    "hairline": "rgb(226, 232, 240)",
    "hairline_strong": "rgb(203, 213, 225)",
    "ink": "rgb(15, 23, 42)",
    "muted": "rgb(71, 85, 105)",
    "faint": "rgb(148, 163, 184)",
    "copyright": "rgb(100, 116, 139)",
    "primary": "rgb(79, 70, 229)",
    "primary_hover": "rgb(67, 56, 202)",
    "primary_soft": "rgb(238, 242, 255)",
    "on_primary": "rgb(255, 255, 255)",
    "secondary_bg": "rgb(15, 23, 42)",
    "secondary_hover": "rgb(30, 41, 59)",
    "on_secondary": "rgb(255, 255, 255)",
    "success": "rgb(22, 163, 74)",
    "nav_bg": "rgba(255, 255, 255, 0.8)",
    "code_bg": "rgb(15, 23, 42)",
    "code_text": "rgb(226, 232, 240)",
    "footer_text": "rgb(148, 163, 184)",
    "footer_heading": "rgb(255, 255, 255)",
    "footer_border": "rgb(30, 41, 59)",
    "shadow_sm": "rgba(15, 23, 42, 0.06) 0px 1px 2px 0px",
    "shadow_md": (
        "rgba(15, 23, 42, 0.12) 0px 8px 24px -4px, rgba(15, 23, 42, 0.06) 0px 2px 6px 0px"
    ),
}
DARK: dict[str, str] = {
    "canvas": "rgb(11, 16, 32)",
    "surface": "rgb(17, 24, 39)",
    "inverse": "rgb(5, 7, 15)",
    "hairline": "rgb(30, 41, 59)",
    "hairline_strong": "rgb(51, 65, 85)",
    "ink": "rgb(248, 250, 252)",
    "muted": "rgb(148, 163, 184)",
    "faint": "rgb(100, 116, 139)",
    "copyright": "rgb(100, 116, 139)",
    "primary": "rgb(129, 140, 248)",
    "primary_hover": "rgb(165, 180, 252)",
    "primary_soft": "rgba(129, 140, 248, 0.15)",
    "on_primary": "rgb(11, 16, 32)",
    "secondary_bg": "rgb(248, 250, 252)",
    "secondary_hover": "rgb(226, 232, 240)",
    "on_secondary": "rgb(11, 16, 32)",
    "success": "rgb(74, 222, 128)",
    "nav_bg": "rgba(11, 16, 32, 0.8)",
    "code_bg": "rgb(2, 6, 23)",
    "code_text": "rgb(226, 232, 240)",
    "footer_text": "rgb(148, 163, 184)",
    "footer_heading": "rgb(255, 255, 255)",
    "footer_border": "rgb(30, 41, 59)",
    "shadow_sm": "rgba(0, 0, 0, 0.4) 0px 1px 2px 0px",
    "shadow_md": "rgba(0, 0, 0, 0.5) 0px 8px 24px -4px, rgba(0, 0, 0, 0.3) 0px 2px 6px 0px",
}
TRANSPARENT = "rgba(0, 0, 0, 0)"
HERO_GRADIENT = (
    "radial-gradient(60% 60% at 50% 0%, rgba(99, 102, 241, 0.18) 0%, "
    "rgba(236, 72, 153, 0.12) 40%, rgba(0, 0, 0, 0) 70%)"
)
INHERITED = (
    "color",
    "font_family",
    "font_size",
    "font_weight",
    "line_height",
    "letter_spacing",
    "text_transform",
    "text_align",
    "cursor",
)


def sides(
    top: str, right: str | None = None, bottom: str | None = None, left: str | None = None
) -> tuple[str, str, str, str]:
    right = top if right is None else right
    bottom = top if bottom is None else bottom
    left = right if left is None else left
    return (top, right, bottom, left)


def border(width: str, color: str, style: str = "solid") -> dict[str, Any]:
    return {
        "border_width": sides(width),
        "border_style": sides(style),
        "border_color": sides(color),
    }


@dataclass
class _Spec:
    tag: str
    parent: int | None
    box: tuple[float, float, float, float]
    own_text: str
    attrs: dict[str, Any]
    styles: dict[str, Any]
    children: list[int] = field(default_factory=list)


class PageBuilder:
    """Builds ElementSamples in document order with CSS inheritance and innerText."""

    def __init__(self, root_styles: Mapping[str, Any]) -> None:
        self.specs: list[_Spec] = []
        self.root_styles = dict(root_styles)

    def add(
        self,
        tag: str,
        parent: int | None,
        box: tuple[float, float, float, float],
        text: str = "",
        **kwargs: Any,
    ) -> int:
        attr_names = {
            "classes",
            "role",
            "id",
            "href",
            "input_type",
            "placeholder_color",
            "landmark",
            "in_overlay",
        }
        attrs = {key: value for key, value in kwargs.items() if key in attr_names}
        styles = {key: value for key, value in kwargs.items() if key not in attr_names}
        inherited = self.root_styles if parent is None else self.specs[parent].styles
        for name in INHERITED:
            if name not in styles and name in inherited:
                styles[name] = inherited[name]
        if parent is not None:
            parent_attrs = self.specs[parent].attrs
            attrs.setdefault("landmark", parent_attrs.get("landmark"))
            attrs.setdefault("in_overlay", parent_attrs.get("in_overlay", False))
        index = len(self.specs)
        self.specs.append(_Spec(tag, parent, box, text, attrs, styles))
        if parent is not None:
            self.specs[parent].children.append(index)
        return index

    def _inner_text(self, index: int) -> str:
        spec = self.specs[index]
        parts = [spec.own_text] if spec.own_text else []
        parts.extend(self._inner_text(child) for child in spec.children)
        return " ".join(part for part in parts if part)

    def _depth(self, index: int) -> int:
        parent = self.specs[index].parent
        return 0 if parent is None else self._depth(parent) + 1

    def build(self) -> list[ElementSample]:
        elements: list[ElementSample] = []
        for index, spec in enumerate(self.specs):
            x, y, width, height = spec.box
            elements.append(
                ElementSample(
                    index=index,
                    tag=spec.tag,
                    role=spec.attrs.get("role"),
                    classes=list(spec.attrs.get("classes", [])),
                    id=spec.attrs.get("id"),
                    text=self._inner_text(index),
                    has_own_text=bool(spec.own_text),
                    href=spec.attrs.get("href"),
                    input_type=spec.attrs.get("input_type"),
                    placeholder_color=spec.attrs.get("placeholder_color"),
                    box=BoundingBox(x=x, y=y, width=width, height=height),
                    depth=self._depth(index),
                    parent_index=spec.parent,
                    child_count=len(spec.children),
                    landmark=spec.attrs.get("landmark"),
                    in_overlay=bool(spec.attrs.get("in_overlay", False)),
                    styles=ComputedStyle(**spec.styles),
                )
            )
        return elements


@dataclass
class BuiltPage:
    elements: list[ElementSample]
    ids: dict[str, int]
    groups: dict[str, list[int]]


def build_elements(c: Mapping[str, str]) -> BuiltPage:
    """The Acme Cloud home page in palette `c` (LIGHT or DARK)."""
    b = PageBuilder({})
    ids: dict[str, int] = {}
    nav_link = {
        "classes": ["nav-link"],
        "color": c["muted"],
        "font_size": "15px",
        "font_weight": "500",
        "line_height": "20px",
        "padding": sides("8px", "12px"),
        "border_radius": sides("6px"),
        "display": "inline-flex",
        "cursor": "pointer",
    }
    button_base = {
        "font_size": "15px",
        "font_weight": "500",
        "line_height": "20px",
        "border_radius": sides("8px"),
        "display": "inline-flex",
        "cursor": "pointer",
        "text_align": "center",
    }

    html = b.add(
        "html",
        None,
        (0, 0, 1440, 2504),
        color=c["ink"],
        font_family=SANS,
        font_size="16px",
        line_height="normal",
    )
    body = b.add(
        "body",
        html,
        (0, 0, 1440, 2504),
        background_color=c["canvas"],
        line_height="25.6px",
    )
    header = b.add(
        "header",
        body,
        (0, 0, 1440, 64),
        classes=["site-header"],
        landmark="header",
        background_color=c["nav_bg"],
        backdrop_filter="blur(12px)",
        position="sticky",
        z_index="50",
        border_width=("0px", "0px", "1px", "0px"),
        border_style=("none", "none", "solid", "none"),
        border_color=(c["ink"], c["ink"], c["hairline"], c["ink"]),
    )
    ids["header"] = header
    nav_inner = b.add(
        "div",
        header,
        (120, 0, 1200, 64),
        classes=["container", "nav-inner"],
        display="flex",
        max_width="1200px",
        padding=sides("0px", "24px"),
        column_gap="32px",
    )
    b.add(
        "a",
        nav_inner,
        (144, 20, 84, 24),
        "Acme",
        classes=["logo"],
        href="/",
        font_family=DISPLAY,
        font_size="20px",
        font_weight="700",
        line_height="24px",
        letter_spacing="-0.4px",
        cursor="pointer",
    )
    nav = b.add(
        "nav",
        nav_inner,
        (260, 14, 520, 36),
        classes=["main-nav"],
        landmark="nav",
        display="flex",
        column_gap="4px",
    )
    ids["nav"] = nav
    nav_links = []
    for x, label, href in (
        (260, "Product", "/product"),
        (344, "Solutions", "/solutions"),
        (438, "Pricing", "/pricing"),
        (518, "Docs", "/docs"),
    ):
        nav_links.append(b.add("a", nav, (x, 14, 84, 36), label, href=href, **nav_link))
    actions = b.add(
        "div",
        nav_inner,
        (1040, 14, 256, 36),
        classes=["nav-actions"],
        display="flex",
        column_gap="12px",
    )
    nav_links.append(b.add("a", actions, (1040, 14, 80, 36), "Sign in", href="/login", **nav_link))
    ids["nav_cta"] = b.add(
        "a",
        actions,
        (1132, 14, 164, 36),
        "Get started",
        classes=["btn", "btn-primary", "btn-sm"],
        href="/signup",
        color=c["on_primary"],
        background_color=c["primary"],
        padding=sides("8px", "16px"),
        box_shadow=c["shadow_sm"],
        **button_base,
    )
    main = b.add("main", body, (0, 64, 1440, 2060), landmark="main", display="block")
    hero = b.add(
        "section",
        main,
        (0, 64, 1440, 700),
        classes=["hero"],
        background_image=HERO_GRADIENT,
        padding=sides("96px", "0px"),
        text_align="center",
    )
    hero_inner = b.add(
        "div",
        hero,
        (120, 160, 1200, 508),
        classes=["container", "css-1q2w3e4"],
        max_width="1200px",
        padding=sides("0px", "24px"),
    )
    ids["badge"] = b.add(
        "span",
        hero_inner,
        (610, 160, 220, 28),
        "New · Acme Workflows 2.0",
        classes=["badge", "badge-pill"],
        display="inline-flex",
        background_color=c["primary_soft"],
        color=c["primary"],
        font_size="13px",
        font_weight="600",
        line_height="20px",
        padding=sides("4px", "12px"),
        border_radius=sides("9999px"),
    )
    b.add(
        "h1",
        hero_inner,
        (220, 212, 1000, 134),
        "Ship cloud apps without the ops",
        classes=["hero-title"],
        font_family=DISPLAY,
        font_size="64px",
        font_weight="700",
        line_height="67.2px",
        letter_spacing="-1.5px",
        margin=("24px", "0px", "0px", "0px"),
    )
    b.add(
        "p",
        hero_inner,
        (320, 370, 800, 64),
        "Acme gives your team one place to build, deploy and scale.",
        classes=["lead"],
        color=c["muted"],
        font_size="20px",
        line_height="32px",
        margin=("24px", "0px", "0px", "0px"),
    )
    hero_actions = b.add(
        "div",
        hero_inner,
        (470, 466, 500, 44),
        classes=["hero-actions"],
        display="flex",
        column_gap="12px",
        margin=("32px", "0px", "0px", "0px"),
    )
    ids["hero_cta"] = b.add(
        "a",
        hero_actions,
        (470, 466, 190, 44),
        "Start building free",
        classes=["btn", "btn-primary"],
        href="/signup",
        color=c["on_primary"],
        background_color=c["primary"],
        padding=sides("12px", "20px"),
        box_shadow=c["shadow_sm"],
        **button_base,
    )
    ids["outline"] = b.add(
        "a",
        hero_actions,
        (672, 466, 150, 44),
        "Book a demo",
        classes=["btn", "btn-outline"],
        href="/demo",
        color=c["ink"],
        background_color=TRANSPARENT,
        padding=sides("11px", "19px"),
        **border("1px", c["hairline"]),
        **button_base,
    )
    ids["ghost"] = b.add(
        "a",
        hero_actions,
        (834, 470, 136, 36),
        "Watch video",
        classes=["btn", "btn-ghost"],
        href="/video",
        color=c["primary"],
        background_color=TRANSPARENT,
        padding=sides("8px", "12px"),
        **button_base,
    )
    form = b.add(
        "form",
        hero_inner,
        (470, 542, 500, 44),
        classes=["signup"],
        landmark="form",
        display="flex",
        column_gap="8px",
        margin=("32px", "0px", "0px", "0px"),
    )
    ids["input"] = b.add(
        "input",
        form,
        (470, 542, 340, 44),
        classes=["input"],
        input_type="email",
        placeholder_color=c["faint"],
        background_color=c["canvas"],
        color=c["ink"],
        font_size="15px",
        font_weight="400",
        line_height="22px",
        padding=sides("10px", "14px"),
        border_radius=sides("6px"),
        box_shadow=c["shadow_sm"],
        cursor="text",
        display="inline-block",
        text_align="start",
        **border("1px", c["hairline_strong"]),
    )
    ids["secondary"] = b.add(
        "button",
        form,
        (818, 542, 152, 44),
        "Get updates",
        classes=["btn", "btn-secondary"],
        input_type="submit",
        color=c["on_secondary"],
        background_color=c["secondary_bg"],
        padding=sides("12px", "20px"),
        **button_base,
    )
    b.add(
        "p",
        hero_inner,
        (620, 602, 200, 20),
        "✓ No credit card required",
        classes=["hero-note"],
        color=c["success"],
        font_size="13px",
        line_height="19.5px",
        margin=("16px", "0px", "0px", "0px"),
    )
    b.add(
        "p",
        hero_inner,
        (620, 646, 200, 20),
        "Trusted by 10,000+ teams",
        classes=["trusted"],
        color=c["faint"],
        font_size="13px",
        line_height="19.5px",
        margin=("24px", "0px", "0px", "0px"),
    )

    features = b.add(
        "section",
        main,
        (0, 764, 1440, 800),
        classes=["features"],
        background_color=c["surface"],
        padding=sides("96px", "0px"),
    )
    features_inner = b.add(
        "div",
        features,
        (120, 860, 1200, 608),
        classes=["container"],
        max_width="1200px",
        padding=sides("0px", "24px"),
    )
    heading2 = {
        "font_family": DISPLAY,
        "font_size": "40px",
        "font_weight": "700",
        "line_height": "46px",
        "letter_spacing": "-0.8px",
        "text_align": "center",
    }
    b.add("h2", features_inner, (144, 860, 1152, 46), "Everything you need to ship", **heading2)
    b.add(
        "p",
        features_inner,
        (144, 922, 1152, 26),
        "From first commit to global scale.",
        classes=["section-lead"],
        color=c["muted"],
        text_align="center",
        margin=("16px", "0px", "0px", "0px"),
    )
    grid = b.add(
        "div",
        features_inner,
        (144, 996, 1152, 304),
        classes=["grid", "cards"],
        display="grid",
        row_gap="24px",
        column_gap="24px",
        margin=("48px", "0px", "0px", "0px"),
    )
    cards = []
    for x, title, copy in (
        (144, "Deploy in seconds", "Push to main and Acme builds, tests and ships every commit."),
        (536, "Scale automatically", "Traffic spikes are absorbed across 35 regions."),
        (928, "Observe everything", "Logs, traces and metrics in one searchable timeline."),
    ):
        card = b.add(
            "article",
            grid,
            (x, 996, 368, 304),
            classes=["card", "sc-bdVaJa"],
            background_color=c["canvas"],
            border_radius=sides("16px"),
            padding=sides("32px"),
            box_shadow=c["shadow_md"],
            **border("1px", c["hairline"]),
        )
        cards.append(card)
        b.add(
            "div",
            card,
            (x + 32, 1028, 48, 48),
            classes=["card-icon"],
            background_color=c["primary_soft"],
            border_radius=sides("12px"),
        )
        b.add(
            "h3",
            card,
            (x + 32, 1100, 304, 32),
            title,
            font_size="24px",
            font_weight="600",
            line_height="32px",
            margin=("24px", "0px", "0px", "0px"),
        )
        b.add(
            "p",
            card,
            (x + 32, 1144, 304, 77),
            copy,
            color=c["muted"],
            margin=("12px", "0px", "0px", "0px"),
        )
    ids["card"] = cards[0]
    code_box = b.add(
        "div",
        features_inner,
        (420, 1348, 600, 56),
        classes=["code-sample"],
        background_color=c["code_bg"],
        border_radius=sides("12px"),
        padding=sides("16px", "20px"),
        margin=("48px", "276px", "0px", "276px"),
    )
    b.add(
        "code",
        code_box,
        (440, 1364, 560, 24),
        "npm i @acme/cli && acme deploy",
        color=c["code_text"],
        font_family=MONO,
        font_size="14px",
        line_height="22.4px",
        display="inline",
        text_align="start",
    )

    pricing = b.add(
        "section",
        main,
        (0, 1564, 1440, 560),
        classes=["pricing-teaser"],
        background_color=c["canvas"],
        padding=sides("96px", "0px"),
    )
    pricing_inner = b.add(
        "div",
        pricing,
        (120, 1660, 1200, 368),
        classes=["container"],
        max_width="1200px",
        padding=sides("0px", "24px"),
        text_align="center",
    )
    b.add("h2", pricing_inner, (144, 1660, 1152, 46), "Simple, usage-based pricing", **heading2)
    b.add(
        "p",
        pricing_inner,
        (144, 1722, 1152, 32),
        "Start free. Pay only for what you use.",
        classes=["section-lead"],
        color=c["muted"],
        font_size="20px",
        line_height="32px",
        margin=("16px", "0px", "0px", "0px"),
    )
    stats = b.add(
        "div",
        pricing_inner,
        (144, 1802, 1152, 120),
        classes=["stats"],
        display="grid",
        column_gap="24px",
        margin=("48px", "0px", "0px", "0px"),
    )
    for x, value, label in (
        (144, "99.99%", "Uptime SLA"),
        (536, "120ms", "Median deploy time"),
        (928, "35+", "Global regions"),
    ):
        stat = b.add("div", stats, (x, 1802, 368, 120), classes=["stat"])
        b.add("p", stat, (x, 1802, 368, 46), value, classes=["stat-value"], **heading2)
        b.add(
            "p",
            stat,
            (x, 1856, 368, 20),
            label,
            classes=["stat-label"],
            color=c["muted"],
            font_size="13px",
            line_height="19.5px",
            margin=("8px", "0px", "0px", "0px"),
        )
    ids["text_link"] = b.add(
        "a",
        pricing_inner,
        (640, 1970, 160, 26),
        "See full pricing →",
        classes=["text-link"],
        href="/pricing",
        color=c["primary"],
        font_weight="500",
        display="inline",
        margin=("48px", "0px", "0px", "0px"),
    )

    footer = b.add(
        "footer",
        body,
        (0, 2124, 1440, 380),
        classes=["site-footer"],
        landmark="footer",
        background_color=c["inverse"],
        color=c["footer_text"],
        font_size="14px",
        line_height="20px",
        padding=("64px", "0px", "48px", "0px"),
        text_align="start",
    )
    ids["footer"] = footer
    footer_grid = b.add(
        "div",
        footer,
        (120, 2188, 1200, 200),
        classes=["container", "footer-grid"],
        display="grid",
        column_gap="32px",
        max_width="1200px",
        padding=sides("0px", "24px"),
    )
    footer_links = []
    columns = (
        (144, "Product", (("Features", "/features"), ("Pricing", "/pricing"))),
        (446, "Company", (("About", "/about"), ("Blog", "/blog"), ("Careers", "/careers"))),
        (748, "Resources", (("Docs", "/docs"), ("Status", "https://status.acme.example/"))),
        (1050, "Legal", (("Privacy", "/legal/privacy"), ("Terms", "/legal/terms"))),
    )
    for x, title, links in columns:
        column = b.add("div", footer_grid, (x, 2188, 270, 180), classes=["footer-col"])
        b.add(
            "h4",
            column,
            (x, 2188, 270, 20),
            title,
            color=c["footer_heading"],
            font_weight="600",
        )
        for position, (label, href) in enumerate(links):
            footer_links.append(
                b.add(
                    "a",
                    column,
                    (x, 2224 + 32 * position, 80, 20),
                    label,
                    classes=["footer-link"],
                    href=href,
                    cursor="pointer",
                    margin=("12px", "0px", "0px", "0px"),
                )
            )
    bottom = b.add(
        "div",
        footer,
        (144, 2420, 1152, 36),
        classes=["footer-bottom"],
        padding=("24px", "0px", "0px", "0px"),
        margin=("32px", "0px", "0px", "0px"),
        border_width=("1px", "0px", "0px", "0px"),
        border_style=("solid", "none", "none", "none"),
        border_color=(c["footer_border"], c["footer_text"], c["footer_text"], c["footer_text"]),
    )
    b.add(
        "p",
        bottom,
        (144, 2444, 300, 20),
        "© 2026 Acme Cloud, Inc.",
        classes=["copyright"],
        color=c["copyright"],
        font_size="13px",
        line_height="19.5px",
    )

    banner = b.add(
        "div",
        body,
        (24, 816, 480, 60),
        classes=["cookie-banner"],
        id="cookie-consent",
        role="dialog",
        landmark="dialog",
        in_overlay=True,
        background_color="rgb(17, 24, 39)",
        color="rgb(229, 231, 235)",
        border_radius=sides("12px"),
        padding=sides("16px"),
        position="fixed",
        z_index="1000",
        box_shadow="rgba(0, 0, 0, 0.3) 0px 20px 40px 0px",
        font_size="14px",
        line_height="20px",
    )
    b.add("p", banner, (40, 832, 340, 28), "We use cookies to improve your experience.")
    b.add(
        "button",
        banner,
        (404, 828, 84, 36),
        "Accept",
        classes=["cookie-accept"],
        background_color="rgb(245, 158, 11)",
        color="rgb(17, 24, 39)",
        border_radius=sides("6px"),
        padding=sides("8px", "14px"),
        cursor="pointer",
    )
    elements = b.build()
    return BuiltPage(elements, ids, {"nav_links": nav_links, "footer_links": footer_links})


# ─── Raw capture ─────────────────────────────────────────────────────────────


def css_variables(c: Mapping[str, str], source: Literal["root", "stylesheet"]) -> list[CssVariable]:
    def var(name: str, declared: str, resolved: str | None) -> CssVariable:
        return CssVariable(name=name, declared=declared, resolved=resolved, source=source)

    return [
        var("--color-primary", _hex(c["primary"]), c["primary"]),
        var("--color-primary-hover", _hex(c["primary_hover"]), c["primary_hover"]),
        var("--color-ink", _hex(c["ink"]), c["ink"]),
        var("--color-muted", _hex(c["muted"]), c["muted"]),
        var("--color-surface", _hex(c["surface"]), c["surface"]),
        var("--color-border", _hex(c["hairline"]), c["hairline"]),
        var("--color-success", _hex(c["success"]), c["success"]),
        var("--color-danger", "#dc2626", "rgb(220, 38, 38)"),
        var("--radius-sm", "6px", "6px"),
        var("--radius-md", "8px", "8px"),
        var("--radius-lg", "16px", "16px"),
        var("--space-1", "0.25rem", "4px"),
        var("--space-4", "1rem", "16px"),
        var("--font-sans", "Inter, system-ui, sans-serif", "Inter, system-ui, sans-serif"),
        var("--shadow-card", "0 8px 24px -4px rgb(15 23 42 / 0.12)", c["shadow_md"]),
        var(
            "--brand-gradient",
            "linear-gradient(135deg, #6366f1, #ec4899)",
            "linear-gradient(135deg, rgb(99, 102, 241), rgb(236, 72, 153))",
        ),
        var("--header-height", "64px", "64px"),
    ]


def screenshot(width: int, height: int, scheme: ColorScheme = "light") -> Screenshot:
    suffix = "-dark" if scheme == "dark" else ""
    return Screenshot(
        name=f"home{suffix}-{width}",
        viewport_width=width,
        viewport_height=height,
        color_scheme=scheme,
        path=f"screenshots/home{suffix}-{width}.png",
        full_page=True,
    )


LAYOUTS = [
    LayoutSnapshot(
        viewport_width=1440,
        viewport_height=900,
        document_height=2504,
        nav_visible=True,
        visible_nav_links=5,
        main_columns=3,
        min_tap_target_px=36,
        container_width_px=1200,
        gutter_px=24,
        max_heading_px=64,
    ),
    LayoutSnapshot(
        viewport_width=768,
        viewport_height=1024,
        document_height=3420,
        hamburger_visible=True,
        main_columns=2,
        min_tap_target_px=40,
        container_width_px=768,
        gutter_px=24,
        max_heading_px=48,
    ),
    LayoutSnapshot(
        viewport_width=390,
        viewport_height=844,
        document_height=5180,
        hamburger_visible=True,
        main_columns=1,
        min_tap_target_px=40,
        container_width_px=390,
        gutter_px=20,
        max_heading_px=40,
    ),
]


def hover_states(page: BuiltPage, c: Mapping[str, str]) -> list[HoverState]:
    ids = page.ids

    def button(key: str, changed: dict[str, tuple[str, str]]) -> HoverState:
        return HoverState(element_index=ids[key], kind="button", changed=changed)

    states = [
        button("nav_cta", {"background_color": (c["primary"], c["primary_hover"])}),
        button("hero_cta", {"background_color": (c["primary"], c["primary_hover"])}),
        button(
            "outline",
            {
                "background_color": (TRANSPARENT, c["surface"]),
                "border_color": (c["hairline"], c["hairline_strong"]),
            },
        ),
        button("ghost", {"background_color": (TRANSPARENT, c["primary_soft"])}),
        button("secondary", {"background_color": (c["secondary_bg"], c["secondary_hover"])}),
    ]
    states.extend(
        HoverState(element_index=index, kind="link", changed={"color": (c["muted"], c["ink"])})
        for index in page.groups["nav_links"]
    )
    states.append(
        HoverState(
            element_index=ids["text_link"],
            kind="link",
            changed={
                "color": (c["primary"], c["primary_hover"]),
                "text_decoration_line": ("none", "underline"),
            },
        )
    )
    states.extend(
        HoverState(
            element_index=index,
            kind="link",
            changed={"color": (c["footer_text"], c["footer_heading"])},
        )
        for index in page.groups["footer_links"][:3]
    )
    return states


def build_page(scheme: ColorScheme) -> PageCapture:
    c = LIGHT if scheme == "light" else DARK
    built = build_elements(c)
    light = scheme == "light"
    return PageCapture(
        url=SOURCE_URL,
        final_url=SOURCE_URL,
        name="home",
        status_code=200,
        load_state="networkidle",
        title="Acme Cloud — Ship cloud apps without the ops",
        meta_description=(
            "Acme Cloud is the developer platform to build, deploy and scale cloud apps."
        ),
        lang="en",
        color_scheme=scheme,
        document_height=2504,
        root_font_size_px=16,
        css_variables=css_variables(c, "root"),
        elements_total=len(built.elements),
        elements=built.elements,
        hover_states=hover_states(built, c) if light else [],
        layouts=LAYOUTS if light else [],
        screenshots=(
            [screenshot(1440, 900), screenshot(768, 1024), screenshot(390, 844)]
            if light
            else [screenshot(1440, 900, "dark")]
        ),
        warnings=(
            ["Phát hiện banner cookie — các phần tử của nó được đánh dấu in_overlay."]
            if light
            else []
        ),
    )


def build_stylesheets() -> list[StylesheetInfo]:
    def media(query: str, occurrences: int, **kwargs: Any) -> MediaQuery:
        return MediaQuery(query=query, occurrences=occurrences, **kwargs)

    app_css = "https://acme.example/_next/static/css/app-3f9a1c.css"
    gstatic = "https://fonts.gstatic.com/s"
    return [
        StylesheetInfo(
            url=app_css,
            status=200,
            size_bytes=48213,
            rule_count=812,
            variables=css_variables(LIGHT, "stylesheet")[:8],
            media_queries=[
                media("(min-width: 640px)", 12, min_width_px=640),
                media("(min-width: 768px)", 30, min_width_px=768),
                media("(min-width: 1024px)", 41, min_width_px=1024),
                media("(min-width: 1280px)", 9, min_width_px=1280),
                media("(max-width: 767px)", 6, max_width_px=767),
                media("(prefers-color-scheme: dark)", 1, prefers_color_scheme="dark"),
                media("(prefers-reduced-motion: reduce)", 2),
                media("print", 1),
            ],
            font_faces=[
                FontFace(
                    family="Inter",
                    src=[f'url({gstatic}/inter/v13/UcC73FwrK3iLTeHuS.woff2) format("woff2")'],
                    weight="100 900",
                    display="swap",
                    stylesheet_url=app_css,
                ),
                FontFace(
                    family="Space Grotesk",
                    src=[f'url({gstatic}/spacegrotesk/v16/V8mDoQDjQSkFtoMM.woff2) format("woff2")'],
                    weight="300 700",
                    display="swap",
                    stylesheet_url=app_css,
                ),
                FontFace(
                    family="JetBrains Mono",
                    src=['url(/fonts/jetbrains-mono.woff2) format("woff2")'],
                    weight="400",
                    display="swap",
                    stylesheet_url=app_css,
                ),
            ],
        ),
        StylesheetInfo(
            url="inline:home#1",
            inline=True,
            size_bytes=1260,
            rule_count=14,
            variables=[
                CssVariable(
                    name="--header-height", declared="64px", resolved="64px", source="stylesheet"
                )
            ],
            media_queries=[media("(min-width: 1024px)", 3, min_width_px=1024)],
        ),
        StylesheetInfo(
            url="https://cdn.acme-static.example/marketing/v2/theme.css",
            status=200,
            size_bytes=9120,
            rule_count=96,
            parse_errors=1,
            media_queries=[
                media("(min-width: 768px)", 4, min_width_px=768),
                media("(max-width: 639.98px)", 2, max_width_px=639.98),
            ],
        ),
    ]


def build_raw() -> RawCapture:
    return RawCapture(
        tool_version=TOOL_VERSION,
        source_url=SOURCE_URL,
        domain=DOMAIN,
        collected_at=COLLECTED_AT,
        options=CaptureOptions(dark=True),
        robots=RobotsCheck(
            robots_url="https://acme.example/robots.txt",
            allowed=True,
            status_code=200,
            note="robots.txt cho phép truy cập /.",
        ),
        pages=[build_page("light")],
        dark=build_page("dark"),
        stylesheets=build_stylesheets(),
    )


# ─── Analysis (reference answers for raw_sample) ─────────────────────────────


def _hex(value: str) -> str:
    color = parse_color(value)
    assert color is not None
    return color.css_hex


CHANNELS: tuple[ColorChannel, ...] = ("background", "text", "border", "gradient")
CLUSTER_PREFIX = {"background": "bg", "text": "text", "border": "border", "gradient": "gradient"}


def build_clusters(page: PageCapture) -> list[ColorCluster]:
    """Exact-color grouping per channel (the fixture uses no near-duplicate colors)."""
    weights: dict[tuple[ColorChannel, str], float] = defaultdict(float)
    counts: Counter[tuple[ColorChannel, str]] = Counter()
    samples: dict[tuple[ColorChannel, str], list[str]] = defaultdict(list)

    def add(channel: ColorChannel, value: str, weight: float, selector: str) -> None:
        color = parse_color(value)
        if color is None or color.is_transparent or weight <= 0:
            return
        key = (channel, color.css_hex)
        weights[key] += weight
        counts[key] += 1
        if selector not in samples[key] and len(samples[key]) < 3:
            samples[key].append(selector)

    for ref in iter_elements([page]):
        element = ref.element
        styles = element.styles
        area = visible_area(element)
        selector = short_selector(element)
        add("background", styles.background_color, area, selector)
        if element.has_own_text:
            add("text", styles.color, area, selector)
        for position, (width, style, side_color) in enumerate(
            zip(styles.border_width, styles.border_style, styles.border_color, strict=True)
        ):
            length = element.box.width if position % 2 == 0 else element.box.height
            if style != "none":
                add("border", side_color, length * (parse_px(width) or 0.0), selector)
        stops = [s for s in gradient_colors(styles.background_image) if not s.is_transparent]
        for stop in stops:
            add("gradient", stop.to_css(), area / len(stops), selector)

    resolved = {
        _hex(variable.resolved): variable.name
        for variable in page.css_variables
        if variable.resolved and parse_color(variable.resolved) is not None
    }
    totals: dict[str, float] = defaultdict(float)
    for (channel, _), weight in weights.items():
        totals[channel] += weight
    clusters: list[ColorCluster] = []
    for channel in CHANNELS:
        keys = sorted((k for k in weights if k[0] == channel), key=lambda k: (-weights[k], k[1]))
        for position, key in enumerate(keys, start=1):
            color = parse_color(key[1])
            assert color is not None
            oklch = to_oklch(color)
            per_channel = {other: round(weights.get((other, key[1]), 0.0), 1) for other in CHANNELS}
            clusters.append(
                ColorCluster(
                    id=f"{CLUSTER_PREFIX[channel]}-{position}",
                    channel=channel,
                    hex=key[1],
                    alpha=color.alpha,
                    weight=round(weights[key], 1),
                    share=round(weights[key] / totals[channel], 4),
                    count=counts[key],
                    usage=ColorUsage(**per_channel),
                    decorative=channel == "gradient",
                    lightness=oklch.lightness,
                    chroma=oklch.chroma,
                    hue=oklch.hue,
                    css_variables=[resolved[key[1]]] if key[1] in resolved else [],
                    members=[key[1]],
                    samples=samples[key],
                )
            )
    return clusters


#: name -> (palette key, cluster channel, evidence)
TOKEN_PLAN: dict[str, tuple[str, ColorChannel, str]] = {
    "primary": ("primary", "background", "background of button-primary; text of links"),
    "on-primary": ("on_primary", "text", "text on button-primary"),
    "canvas": ("canvas", "background", "body background, largest area"),
    "surface": ("surface", "background", "alternate section background"),
    "surface-inverse": ("inverse", "background", "footer background"),
    "hairline": ("hairline", "border", "1px borders of cards, header, outline button"),
    "ink": ("ink", "text", "headings and body text"),
    "muted": ("muted", "text", "secondary paragraphs and nav links"),
    "faint": ("faint", "text", "captions and footer links"),
    "success": ("success", "text", "positive inline note"),
}
TOKEN_VARIABLES = {
    "primary": "--color-primary",
    "ink": "--color-ink",
    "muted": "--color-muted",
    "surface": "--color-surface",
    "hairline": "--color-border",
    "success": "--color-success",
}


def build_tokens(c: Mapping[str, str], clusters: list[ColorCluster]) -> list[ColorToken]:
    by_key = {(cluster.channel, cluster.hex): cluster for cluster in clusters}
    tokens: list[ColorToken] = []
    for name, (key, channel, evidence) in TOKEN_PLAN.items():
        value = _hex(c[key])
        cluster = by_key.get((channel, value))
        tokens.append(
            ColorToken(
                name=name,
                value=value,
                role=COLOR_TOKEN_ROLES[name],
                cluster_id=None if cluster is None else cluster.id,
                css_variable=TOKEN_VARIABLES.get(name),
                evidence=evidence,
            )
        )
    return tokens


NON_TEXT_ROLES = {ColorRole.BORDER, ColorRole.BACKGROUND, ColorRole.SURFACE}
CONTRAST_CONTEXT = {
    ("ink", "canvas"): "body text",
    ("muted", "canvas"): "secondary text",
    ("muted", "surface"): "secondary text on surface",
    ("ink", "surface"): "headings on surface",
    ("on_primary", "primary"): "button-primary",
    ("primary", "primary_soft"): "badge",
    ("on_secondary", "secondary_bg"): "button-secondary",
    ("success", "canvas"): "inline success note",
    ("faint", "canvas"): "caption",
    ("footer_text", "inverse"): "footer links",
    ("footer_heading", "inverse"): "footer headings",
    ("copyright", "inverse"): "footer copyright",
    ("code_text", "code_bg"): "code sample",
    ("primary", "canvas"): "links",
}


def build_contrast_pairs(
    page: PageCapture, c: Mapping[str, str], tokens: list[ColorToken]
) -> list[ContrastPair]:
    index = element_index(page)
    canvas = parse_color(c["canvas"])
    assert canvas is not None
    occurrences: Counter[tuple[str, str]] = Counter()
    for ref in iter_elements([page]):
        element = ref.element
        foreground = parse_color(element.styles.color)
        if not element.has_own_text or foreground is None:
            continue
        background = effective_background(element, index, base=canvas)
        occurrences[(foreground.css_hex, background.css_hex)] += 1
    token_names: dict[str, list[str]] = defaultdict(list)
    for token in tokens:
        token_names[token.value].append(token.name)
    palette_keys: dict[str, list[str]] = defaultdict(list)
    for key, value in c.items():
        color = parse_color(value)
        if color is not None and "shadow" not in key:
            palette_keys[color.css_hex].append(key)

    def token_for(value: str, preferred: tuple[str, ...], *, text: bool) -> str | None:
        names = token_names.get(value, [])
        if text:
            names = [name for name in names if COLOR_TOKEN_ROLES[name] not in NON_TEXT_ROLES]
        for name in preferred:
            if name in names:
                return name
        return names[0] if names else None

    pairs: list[ContrastPair] = []
    for (fg_hex, bg_hex), count in sorted(
        occurrences.items(), key=lambda item: (-item[1], item[0])
    ):
        ratio = round(contrast_ratio(fg_hex, bg_hex), 2)
        passes_aa, passes_aa_large, passes_aaa = contrast_levels(ratio)
        context = next(
            (
                CONTRAST_CONTEXT[(fg, bg)]
                for fg in palette_keys[fg_hex]
                for bg in palette_keys[bg_hex]
                if (fg, bg) in CONTRAST_CONTEXT
            ),
            "text",
        )
        pairs.append(
            ContrastPair(
                foreground=fg_hex,
                background=bg_hex,
                foreground_token=token_for(
                    fg_hex, ("on-primary", "ink", "muted", "faint"), text=True
                ),
                background_token=token_for(bg_hex, ("canvas", "surface", "primary"), text=False),
                ratio=ratio,
                passes_aa=passes_aa,
                passes_aa_large=passes_aa_large,
                passes_aaa=passes_aaa,
                occurrences=count,
                context=context,
            )
        )
    return pairs


def build_palette(page: PageCapture, scheme: ColorScheme) -> ColorPalette:
    c = LIGHT if scheme == "light" else DARK
    clusters = build_clusters(page)
    tokens = build_tokens(c, clusters)
    return ColorPalette(
        scheme=scheme,
        clusters=clusters,
        tokens=tokens,
        contrast_pairs=build_contrast_pairs(page, c, tokens),
    )


def _level(
    name: str,
    size: float,
    family: str,
    weight: int,
    line_height_px: float,
    tags: list[str],
    count: int,
    samples: list[str],
    letter_spacing: float = 0.0,
) -> TypeLevel:
    return TypeLevel(
        name=name,
        size_px=size,
        font_family=family,
        weight=weight,
        line_height_px=line_height_px,
        line_height=round(line_height_px / size, 2),
        letter_spacing_px=letter_spacing,
        tags=tags,
        count=count,
        samples=samples,
    )


def build_typography() -> Typography:
    hero = ["Ship cloud apps without the ops"]
    return Typography(
        families=[
            FontFamilyInfo(
                name="Inter",
                stack=["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
                role=FontRole.PRIMARY,
                category="sans-serif",
                share=0.8,
                weights=[400, 500, 600],
                is_google_font=True,
            ),
            FontFamilyInfo(
                name="Space Grotesk",
                stack=["Space Grotesk", "Inter", "system-ui", "sans-serif"],
                role=FontRole.HEADING,
                category="sans-serif",
                share=0.17,
                weights=[700],
                is_google_font=True,
            ),
            FontFamilyInfo(
                name="JetBrains Mono",
                stack=["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
                role=FontRole.MONO,
                category="monospace",
                share=0.03,
                weights=[400],
                is_google_font=True,
            ),
        ],
        levels=[
            _level("display", 64, "Space Grotesk", 700, 67.2, ["h1"], 1, hero, -1.5),
            _level(
                "heading",
                40,
                "Space Grotesk",
                700,
                46,
                ["h2", "p"],
                5,
                ["Everything you need to ship", "99.99%"],
                -0.8,
            ),
            _level("title", 24, "Inter", 600, 32, ["h3"], 3, ["Deploy in seconds"]),
            _level("body-lg", 20, "Inter", 400, 32, ["p"], 2, ["Start free. Pay only for what"]),
            _level("body", 16, "Inter", 400, 25.6, ["p", "a"], 5, ["From first commit to global"]),
            _level("ui", 15, "Inter", 500, 20, ["a", "button"], 10, ["Get started", "Pricing"]),
            _level("body-sm", 14, "Inter", 400, 20, ["a", "h4"], 13, ["Features", "Product"]),
            _level("code", 14, "JetBrains Mono", 400, 22.4, ["code"], 1, ["npm i @acme/cli"]),
            _level("caption", 13, "Inter", 400, 19.5, ["p", "span"], 7, ["Uptime SLA"]),
        ],
        weights_used=[400, 500, 600, 700],
        base_size_px=16,
        scale_ratio=1.25,
        scale_ratio_name="major third",
    )


SHADOW_SM = "0px 1px 2px 0px rgba(15, 23, 42, 0.06)"
SHADOW_MD = "0px 8px 24px -4px rgba(15, 23, 42, 0.12), 0px 2px 6px 0px rgba(15, 23, 42, 0.06)"
UI_TEXT = {"fontFamily": "Inter", "fontSize": "15px", "fontWeight": "500", "lineHeight": "20px"}


def build_components() -> list[ComponentSpec]:
    button_refs = {"rounded": "{rounded.md}", "typography": "{typography.ui}"}
    return [
        ComponentSpec(
            name="button-primary",
            kind=ComponentKind.BUTTON,
            variant="primary",
            count=2,
            properties={
                "backgroundColor": "#4f46e5",
                "textColor": "#ffffff",
                "rounded": "8px",
                "padding": "12px 20px",
                "height": "44px",
                **UI_TEXT,
                "shadow": SHADOW_SM,
            },
            token_refs={
                "backgroundColor": "{colors.primary}",
                "textColor": "{colors.on-primary}",
                **button_refs,
            },
            states={"hover": {"backgroundColor": "#4338ca"}},
            contrast_ratio=6.29,
            class_hints=["btn", "btn-primary", "btn-sm"],
            samples=["Start building free", "Get started"],
        ),
        ComponentSpec(
            name="button-secondary",
            kind=ComponentKind.BUTTON,
            variant="secondary",
            count=1,
            properties={
                "backgroundColor": "#0f172a",
                "textColor": "#ffffff",
                "rounded": "8px",
                "padding": "12px 20px",
                "height": "44px",
                **UI_TEXT,
            },
            token_refs={
                "backgroundColor": "{colors.ink}",
                "textColor": "{colors.on-primary}",
                **button_refs,
            },
            states={"hover": {"backgroundColor": "#1e293b"}},
            contrast_ratio=17.85,
            class_hints=["btn", "btn-secondary"],
            samples=["Get updates"],
        ),
        ComponentSpec(
            name="button-outline",
            kind=ComponentKind.BUTTON,
            variant="outline",
            count=1,
            properties={
                "backgroundColor": "transparent",
                "textColor": "#0f172a",
                "borderColor": "#e2e8f0",
                "borderWidth": "1px",
                "rounded": "8px",
                "padding": "11px 19px",
                "height": "44px",
                **UI_TEXT,
            },
            token_refs={
                "textColor": "{colors.ink}",
                "borderColor": "{colors.hairline}",
                **button_refs,
            },
            states={"hover": {"backgroundColor": "#f8fafc", "borderColor": "#cbd5e1"}},
            state_token_refs={"hover": {"backgroundColor": "{colors.surface}"}},
            contrast_ratio=17.85,
            class_hints=["btn", "btn-outline"],
            samples=["Book a demo"],
        ),
        ComponentSpec(
            name="button-ghost",
            kind=ComponentKind.BUTTON,
            variant="ghost",
            count=1,
            properties={
                "backgroundColor": "transparent",
                "textColor": "#4f46e5",
                "rounded": "8px",
                "padding": "8px 12px",
                "height": "36px",
                **UI_TEXT,
            },
            token_refs={"textColor": "{colors.primary}", **button_refs},
            states={"hover": {"backgroundColor": "#eef2ff"}},
            contrast_ratio=6.29,
            class_hints=["btn", "btn-ghost"],
            samples=["Watch video"],
        ),
        ComponentSpec(
            name="text-link",
            kind=ComponentKind.LINK,
            variant="inline",
            count=1,
            properties={
                "textColor": "#4f46e5",
                "fontFamily": "Inter",
                "fontSize": "16px",
                "fontWeight": "500",
                "lineHeight": "25.6px",
                "textDecoration": "none",
            },
            token_refs={"textColor": "{colors.primary}"},
            states={"hover": {"textColor": "#4338ca", "textDecoration": "underline"}},
            contrast_ratio=6.29,
            class_hints=["text-link"],
            samples=["See full pricing →"],
        ),
        ComponentSpec(
            name="nav-link",
            kind=ComponentKind.LINK,
            variant="nav",
            count=5,
            properties={
                "textColor": "#475569",
                "rounded": "6px",
                "padding": "8px 12px",
                "height": "36px",
                **UI_TEXT,
                "textDecoration": "none",
            },
            token_refs={
                "textColor": "{colors.muted}",
                "rounded": "{rounded.sm}",
                "typography": "{typography.ui}",
            },
            states={"hover": {"textColor": "#0f172a"}},
            state_token_refs={"hover": {"textColor": "{colors.ink}"}},
            contrast_ratio=7.58,
            class_hints=["nav-link"],
            samples=["Product", "Solutions", "Pricing"],
        ),
        ComponentSpec(
            name="text-input",
            kind=ComponentKind.INPUT,
            count=1,
            properties={
                "backgroundColor": "#ffffff",
                "textColor": "#0f172a",
                "borderColor": "#cbd5e1",
                "borderWidth": "1px",
                "rounded": "6px",
                "padding": "10px 14px",
                "height": "44px",
                "fontFamily": "Inter",
                "fontSize": "15px",
                "fontWeight": "400",
                "lineHeight": "22px",
                "placeholderColor": "#94a3b8",
                "shadow": SHADOW_SM,
            },
            token_refs={
                "backgroundColor": "{colors.canvas}",
                "textColor": "{colors.ink}",
                "rounded": "{rounded.sm}",
                "placeholderColor": "{colors.faint}",
            },
            contrast_ratio=17.85,
            class_hints=["input"],
            samples=["input[type=email]"],
        ),
        ComponentSpec(
            name="card",
            kind=ComponentKind.CARD,
            count=3,
            properties={
                "backgroundColor": "#ffffff",
                "borderColor": "#e2e8f0",
                "borderWidth": "1px",
                "rounded": "16px",
                "padding": "32px",
                "shadow": SHADOW_MD,
            },
            token_refs={
                "backgroundColor": "{colors.canvas}",
                "borderColor": "{colors.hairline}",
                "rounded": "{rounded.xl}",
                "padding": "{spacing.xxl}",
            },
            class_hints=["card"],
            samples=["Deploy in seconds", "Scale automatically", "Observe everything"],
        ),
        ComponentSpec(
            name="badge",
            kind=ComponentKind.BADGE,
            variant="pill",
            count=1,
            properties={
                "backgroundColor": "#eef2ff",
                "textColor": "#4f46e5",
                "rounded": "9999px",
                "padding": "4px 12px",
                "height": "28px",
                "fontFamily": "Inter",
                "fontSize": "13px",
                "fontWeight": "600",
                "lineHeight": "20px",
            },
            token_refs={"textColor": "{colors.primary}", "rounded": "{rounded.full}"},
            contrast_ratio=5.62,
            class_hints=["badge", "badge-pill"],
            samples=["New · Acme Workflows 2.0"],
        ),
        ComponentSpec(
            name="top-nav",
            kind=ComponentKind.NAVIGATION,
            count=1,
            properties={
                "backgroundColor": "#ffffffcc",
                "borderColor": "#e2e8f0",
                "borderWidth": "0px 0px 1px",
                "height": "64px",
                "padding": "0px 24px",
                "position": "sticky",
                "backdropFilter": "blur(12px)",
            },
            token_refs={"borderColor": "{colors.hairline}"},
            class_hints=["site-header", "nav-inner", "main-nav"],
            samples=["Acme Product Solutions Pricing Docs Sign in Get st"],
        ),
        ComponentSpec(
            name="footer",
            kind=ComponentKind.FOOTER,
            count=1,
            properties={
                "backgroundColor": "#0b1020",
                "textColor": "#94a3b8",
                "padding": "64px 0px 48px",
            },
            token_refs={
                "backgroundColor": "{colors.surface-inverse}",
                "textColor": "{colors.faint}",
                "typography": "{typography.body-sm}",
            },
            contrast_ratio=7.38,
            class_hints=["site-footer", "footer-link", "footer-col"],
            samples=["Product Features Pricing Company About Blog Careers"],
        ),
    ]


def _step(name: str, value: float, count: int, used_on: list[str]) -> ScaleStep:
    # A single main page weighs 1.0 per element, so the weighted usage equals the count.
    return ScaleStep(name=name, value_px=value, count=count, weight=float(count), used_on=used_on)


def _change(
    widths: tuple[int, int], kind: ResponsiveChangeKind, params: dict[str, float], evidence: str
) -> ResponsiveChange:
    return ResponsiveChange(
        from_width=widths[0], to_width=widths[1], kind=kind, params=params, evidence=evidence
    )


def build_layout() -> LayoutInfo:
    kind = ResponsiveChangeKind
    desktop_to_tablet = (1440, 768)
    tablet_to_mobile = (768, 390)
    return LayoutInfo(
        container_max_width_px=1200,
        gutter_px=24,
        shell="top-nav",
        snapshots=LAYOUTS,
        changes=[
            _change(
                desktop_to_tablet,
                kind.NAV_COLLAPSES_TO_HAMBURGER,
                {"below_px": 1024, "links": 5},
                "5 visible nav links at 1440px, hamburger at 768px",
            ),
            _change(
                desktop_to_tablet,
                kind.COLUMNS_REDUCE,
                {"from": 3, "to": 2},
                "main grid 3 -> 2 columns",
            ),
            _change(
                desktop_to_tablet,
                kind.HEADING_SCALES_DOWN,
                {"from_px": 64, "to_px": 48},
                "largest heading 64px -> 48px",
            ),
            _change(
                desktop_to_tablet,
                kind.CONTAINER_BECOMES_FLUID,
                {"max_width_px": 1200},
                "container 1200px -> full width",
            ),
            _change(
                tablet_to_mobile, kind.COLUMNS_REDUCE, {"from": 2, "to": 1}, "grid 2 -> 1 column"
            ),
            _change(
                tablet_to_mobile,
                kind.HEADING_SCALES_DOWN,
                {"from_px": 48, "to_px": 40},
                "largest heading 48px -> 40px",
            ),
            _change(
                tablet_to_mobile,
                kind.GUTTER_SHRINKS,
                {"from_px": 24, "to_px": 20},
                "container padding 24px -> 20px",
            ),
        ],
    )


def build_analysis(raw: RawCapture) -> DesignAnalysis:
    main_page = raw.pages[0]
    dark_page = raw.dark
    assert dark_page is not None
    palette = build_palette(main_page, "light")
    components = build_components()
    variable_names = {v.name for page in raw.pages for v in page.css_variables}
    variable_names.update(v.name for sheet in raw.stylesheets for v in sheet.variables)
    return DesignAnalysis(
        tool_version=TOOL_VERSION,
        analyzed_at=ANALYZED_AT,
        site=build_site_info(raw),
        colors=palette,
        typography=build_typography(),
        spacing=SpacingSystem(
            base_unit_px=4,
            steps=[
                _step("xxs", 4, 2, ["badge"]),
                _step("xs", 8, 9, ["nav-link", "button-ghost"]),
                _step("sm", 12, 14, ["button-primary", "gap"]),
                _step("md", 16, 8, ["p", "code-sample"]),
                _step("lg", 20, 6, ["button-primary"]),
                _step("xl", 24, 16, ["container", "grid gap"]),
                _step("xxl", 32, 17, ["card", "section gap"]),
                _step("xxxl", 48, 5, ["section content gap"]),
                _step("4xl", 64, 1, ["footer"]),
            ],
            container_max_width_px=1200,
            section_gap_px=96,
            common_paddings=["12px 20px", "32px", "8px 12px", "0px 24px"],
        ),
        radii=[
            _step("sm", 6, 7, ["nav-link", "text-input"]),
            _step("md", 8, 5, ["button-primary", "button-ghost"]),
            _step("lg", 12, 4, ["card-icon", "code-sample"]),
            _step("xl", 16, 3, ["card"]),
            _step("full", 9999, 1, ["badge"]),
        ],
        shadows=[
            ShadowLevel(
                name="sm", css=SHADOW_SM, layers=1, count=3, used_on=["button-primary", "input"]
            ),
            ShadowLevel(name="md", css=SHADOW_MD, layers=2, count=3, used_on=["card"]),
        ],
        breakpoints=[
            Breakpoint(name="sm", min_width_px=640, occurrences=14),
            Breakpoint(name="md", min_width_px=768, occurrences=40),
            Breakpoint(name="lg", min_width_px=1024, occurrences=44),
            Breakpoint(name="xl", min_width_px=1280, occurrences=9),
        ],
        layout=build_layout(),
        components=components,
        dark=ThemeAnalysis(colors=build_palette(dark_page, "dark")),
        screenshots=[*main_page.screenshots, *dark_page.screenshots],
        stats=AnalysisStats(
            pages=len(raw.pages),
            elements=sum(len(page.elements) for page in raw.pages),
            stylesheets=len(raw.stylesheets),
            css_variables=len(variable_names),
            hover_states=sum(len(page.hover_states) for page in raw.pages),
            color_clusters=len(palette.clusters),
            components=len(components),
        ),
        warnings=[f"[{main_page.name}] {warning}" for warning in main_page.warnings],
    )


# ─── Site contract (tests/fixtures/site implements it) ───────────────────────


def build_site_contract(analysis: DesignAnalysis) -> dict[str, Any]:
    dark = analysis.dark
    assert dark is not None
    return {
        "description": (
            "Design contract of the static fixture site tests/fixtures/site/ (Acme Cloud). "
            "index.html must render the same design as raw_sample.json; e2e tests assert it."
        ),
        "palette": {token.name: token.value for token in analysis.colors.tokens},
        "dark_palette": {token.name: token.value for token in dark.colors.tokens},
        "decorative_colors": ["#6366f1", "#ec4899"],
        "overlay_colors": ["#f59e0b", "#111827"],
        "fonts": {"primary": "Inter", "heading": "Space Grotesk", "mono": "JetBrains Mono"},
        "type_levels_px": {level.name: level.size_px for level in analysis.typography.levels},
        "spacing": {
            "base_unit_px": 4,
            "container_max_width_px": 1200,
            "section_gap_px": 96,
            "required_steps_px": [4, 8, 12, 16, 24, 32, 48],
        },
        "radii_px": [step.value_px for step in analysis.radii],
        "shadow_levels": len(analysis.shadows),
        "breakpoints_px": [640, 768, 1024, 1280],
        "components": [component.name for component in analysis.components],
        "hover": {"button-primary": {"backgroundColor": "#4338ca"}},
        "low_contrast_pairs": [
            {"foreground": pair.foreground, "background": pair.background, "ratio": pair.ratio}
            for pair in analysis.colors.contrast_pairs
            if not pair.passes_aa
        ],
        "layout": {
            "shell": "top-nav",
            "hamburger_below_px": 1024,
            "columns": {"1440": 3, "768": 2, "390": 1},
        },
        "pages": {
            "index.html": "home",
            "pricing.html": "pricing",
            "about.html": "about",
            "docs.html": "docs",
        },
        "discoverable_from_nav": ["pricing.html", "about.html", "docs.html"],
        "special_pages": {
            "login.html": "sign-in wall (password form only) -> LoginRequiredError",
            "challenge.html": "bot challenge, served with ?status=403 -> BotBlockedError",
            "never-idle.html": "polls fetch() forever -> networkidle fallback, still captured",
            "lazy.html": "content appended on scroll -> must be captured after scrolling",
        },
    }


def render_samples() -> dict[Path, str]:
    raw = build_raw()
    analysis = build_analysis(raw)
    contract = json.dumps(build_site_contract(analysis), indent=2, ensure_ascii=False)
    return {
        RAW_SAMPLE_PATH: raw.model_dump_json(indent=2) + "\n",
        ANALYSIS_SAMPLE_PATH: analysis.model_dump_json(indent=2) + "\n",
        SITE_CONTRACT_PATH: contract + "\n",
    }


def main() -> None:
    for path, text in render_samples().items():
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print(f"wrote {path.name}")


if __name__ == "__main__":
    main()
