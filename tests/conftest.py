from __future__ import annotations

from datetime import UTC, datetime

import pytest

from get_design_markdown.models.analysis import (
    ColorCluster,
    ColorPalette,
    ColorRole,
    ColorToken,
    ComponentKind,
    ComponentSpec,
    ContrastPair,
    DesignAnalysis,
    FontFamilyInfo,
    FontRole,
    ScaleStep,
    SiteInfo,
    TypeLevel,
    Typography,
)
from get_design_markdown.models.raw import (
    BoundingBox,
    ComputedStyle,
    CssVariable,
    ElementSample,
    HoverState,
    LayoutSnapshot,
    PageCapture,
    RawCapture,
    RobotsCheck,
    Screenshot,
)

FIXED_TIME = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)


@pytest.fixture
def raw_capture() -> RawCapture:
    button = ElementSample(
        index=0,
        tag="a",
        classes=["btn", "btn-primary"],
        text="Start now",
        href="/start",
        box=BoundingBox(x=100, y=40, width=120, height=40),
        styles=ComputedStyle(
            color="rgb(255, 255, 255)",
            background_color="rgb(99, 91, 255)",
            font_family='"Sohne", sans-serif',
            font_size="15px",
            font_weight="500",
            padding=("8px", "16px", "8px", "16px"),
            border_radius=("999px", "999px", "999px", "999px"),
            display="inline-flex",
            cursor="pointer",
        ),
    )
    page = PageCapture(
        url="https://stripe.com/",
        final_url="https://stripe.com/",
        title="Stripe",
        meta_description="Financial infrastructure to grow your revenue.",
        lang="en",
        document_height=9000,
        css_variables=[
            CssVariable(name="--accent", declared="#635bff", resolved="#635bff", source="root")
        ],
        elements=[button],
        hover_states=[
            HoverState(
                element_index=0,
                kind="button",
                changed={"background_color": ("rgb(99, 91, 255)", "rgb(10, 37, 64)")},
            )
        ],
        layouts=[LayoutSnapshot(viewport_width=390, hamburger_visible=True, min_tap_target_px=40)],
        screenshots=[
            Screenshot(
                name="home-1440",
                viewport_width=1440,
                viewport_height=900,
                path="screenshots/home-1440.png",
            )
        ],
    )
    return RawCapture(
        tool_version="0.1.0",
        source_url="https://stripe.com/",
        domain="stripe.com",
        collected_at=FIXED_TIME,
        robots=RobotsCheck(robots_url="https://stripe.com/robots.txt", allowed=True, note="ok"),
        pages=[page],
    )


@pytest.fixture
def design_analysis() -> DesignAnalysis:
    return DesignAnalysis(
        tool_version="0.1.0",
        analyzed_at=FIXED_TIME,
        site=SiteInfo(url="https://stripe.com/", name="Stripe", pages_analyzed=["home"]),
        colors=ColorPalette(
            clusters=[
                ColorCluster(id="c1", hex="#635bff", weight=1200.0, share=0.04, chroma=0.2, hue=280)
            ],
            tokens=[
                ColorToken(name="primary", value="#635bff", role=ColorRole.BRAND, cluster_id="c1")
            ],
            contrast_pairs=[
                ContrastPair(
                    foreground="#ffffff",
                    background="#635bff",
                    ratio=4.63,
                    passes_aa=True,
                    passes_aa_large=True,
                    passes_aaa=False,
                    occurrences=3,
                )
            ],
        ),
        typography=Typography(
            families=[FontFamilyInfo(name="Sohne", role=FontRole.PRIMARY, share=0.9)],
            levels=[TypeLevel(name="body", size_px=16, font_family="Sohne", weight=400)],
        ),
        radii=[ScaleStep(name="full", value_px=999, count=12)],
        components=[
            ComponentSpec(
                name="button-primary",
                kind=ComponentKind.BUTTON,
                count=3,
                properties={"backgroundColor": "#635bff"},
                token_refs={"backgroundColor": "{colors.primary}"},
                states={"hover": {"backgroundColor": "#0a2540"}},
            )
        ],
    )
