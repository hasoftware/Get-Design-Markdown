"""Color analyzer (GĐ3): clusters per channel, semantic tokens, WCAG contrast pairs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from get_design_markdown.analyzer.colors import DEFAULT_DELTA_E, analyze_colors
from get_design_markdown.analyzer.colors_roles import (
    is_chromatic,
    is_neutralish,
    neutral_chroma_limit,
)
from get_design_markdown.analyzer.css_values import RGBA, contrast_ratio, parse_color
from get_design_markdown.analyzer.naming import COLOR_TOKEN_ORDER, COLOR_TOKEN_ROLES
from get_design_markdown.models.analysis import ColorPalette
from get_design_markdown.models.raw import (
    BoundingBox,
    ComputedStyle,
    CssVariable,
    ElementSample,
    HoverState,
    Landmark,
    PageCapture,
    StylesheetInfo,
)
from tests.fixture_data import load_raw_sample, load_site_contract

EXTRA_LIGHT_TOKENS = {"hairline-strong", "surface-raised", "on-inverse"}


# ─── Builders ────────────────────────────────────────────────────────────────


def _el(
    index: int,
    tag: str = "div",
    *,
    parent: int | None = None,
    box: tuple[float, float, float, float] = (0, 0, 100, 20),
    text: str = "",
    own_text: bool | None = None,
    href: str | None = None,
    classes: Sequence[str] = (),
    role: str | None = None,
    landmark: Landmark | None = None,
    in_overlay: bool = False,
    child_count: int = 0,
    placeholder_color: str | None = None,
    input_type: str | None = None,
    **styles: Any,
) -> ElementSample:
    x, y, width, height = box
    return ElementSample(
        index=index,
        tag=tag,
        role=role,
        classes=list(classes),
        text=text,
        has_own_text=bool(text) if own_text is None else own_text,
        href=href,
        input_type=input_type,
        placeholder_color=placeholder_color,
        box=BoundingBox(x=x, y=y, width=width, height=height),
        parent_index=parent,
        child_count=child_count,
        landmark=landmark,
        in_overlay=in_overlay,
        styles=ComputedStyle(**styles),
    )


def _page(
    elements: Sequence[ElementSample],
    *,
    name: str = "home",
    height: float = 2000,
    variables: Sequence[CssVariable] = (),
    body_bg: str = "rgb(255, 255, 255)",
    text_color: str = "rgb(17, 17, 17)",
) -> PageCapture:
    """A page with html (index 0) and body (index 1); `elements` use indexes >= 2."""
    root = [
        _el(0, "html", box=(0, 0, 1440, height), color=text_color),
        _el(
            1,
            "body",
            parent=0,
            box=(0, 0, 1440, height),
            background_color=body_bg,
            color=text_color,
        ),
    ]
    return PageCapture(
        url=f"https://example.test/{name}",
        final_url=f"https://example.test/{name}",
        name=name,
        document_height=height,
        css_variables=list(variables),
        elements=[*root, *elements],
    )


def _text(
    index: int,
    color: str,
    *,
    parent: int | None = 1,
    y: float = 100,
    size: str = "16px",
    text: str = "Some body copy for the page",
    **kwargs: Any,
) -> ElementSample:
    return _el(
        index,
        kwargs.pop("tag", "p"),
        parent=parent,
        box=kwargs.pop("box", (100, y, 600, 24)),
        text=text,
        color=color,
        font_size=size,
        **kwargs,
    )


def _button(
    index: int,
    bg: str,
    *,
    parent: int = 1,
    color: str = "rgb(255, 255, 255)",
    y: float = 300,
    text: str = "Get started",
    **kwargs: Any,
) -> ElementSample:
    return _el(
        index,
        kwargs.pop("tag", "a"),
        parent=parent,
        box=kwargs.pop("box", (100, y, 160, 44)),
        text=text,
        href="/start",
        background_color=bg,
        color=color,
        padding=("12px", "20px", "12px", "20px"),
        cursor="pointer",
        **kwargs,
    )


def _tokens(palette: ColorPalette) -> dict[str, str]:
    return {token.name: token.value for token in palette.tokens}


def _cluster_ids(palette: ColorPalette) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for cluster in palette.clusters:
        result.setdefault(cluster.channel, []).append(cluster.hex)
    return result


# ─── Reference fixture (Acme Cloud) ──────────────────────────────────────────


@pytest.fixture(scope="module")
def light() -> ColorPalette:
    raw = load_raw_sample()
    return analyze_colors(raw.pages, raw.stylesheets)


@pytest.fixture(scope="module")
def dark() -> ColorPalette:
    raw = load_raw_sample()
    assert raw.dark is not None
    return analyze_colors([raw.dark], raw.stylesheets)


def test_default_threshold_is_spec_value() -> None:
    assert DEFAULT_DELTA_E == 3.0


def test_fixture_palette_matches_contract(light: ColorPalette) -> None:
    contract = load_site_contract()
    tokens = _tokens(light)
    assert {name: tokens[name] for name in contract.palette if name in tokens} == contract.palette
    extras = set(tokens) - set(contract.palette)
    assert extras <= EXTRA_LIGHT_TOKENS
    assert 8 <= len(tokens) <= 14
    assert light.scheme == "light"


def test_fixture_tokens_are_ordered_named_and_backed_by_clusters(light: ColorPalette) -> None:
    names = [token.name for token in light.tokens]
    assert names == [name for name in COLOR_TOKEN_ORDER if name in names]
    clusters = {cluster.id: cluster for cluster in light.clusters}
    for token in light.tokens:
        assert token.role == COLOR_TOKEN_ROLES[token.name]
        assert token.evidence
        assert token.cluster_id in clusters
        cluster = clusters[token.cluster_id]
        assert cluster.hex == token.value
        assert not cluster.decorative


def test_fixture_css_variable_hints(light: ColorPalette) -> None:
    by_name = {token.name: token for token in light.tokens}
    assert by_name["primary"].css_variable == "--color-primary"
    assert by_name["surface"].css_variable == "--color-surface"
    assert by_name["hairline"].css_variable == "--color-border"
    assert by_name["ink"].css_variable == "--color-ink"
    assert by_name["muted"].css_variable == "--color-muted"
    assert by_name["success"].css_variable == "--color-success"
    assert by_name["canvas"].css_variable is None
    # --color-danger is declared but never used: no danger token.
    assert "danger" not in by_name


def test_fixture_overlay_and_decorative_colors_are_not_tokens(light: ColorPalette) -> None:
    contract = load_site_contract()
    values = {token.value[:7] for token in light.tokens}
    for color in [*contract.overlay_colors, *contract.decorative_colors]:
        assert color not in values
    gradients = [cluster for cluster in light.clusters if cluster.channel == "gradient"]
    assert gradients and all(cluster.decorative for cluster in gradients)
    assert {cluster.hex[:7] for cluster in gradients} == set(contract.decorative_colors)
    # Overlay (cookie banner) colors are not even sampled.
    assert all(cluster.hex[:7] != "#f59e0b" for cluster in light.clusters)


def test_fixture_clusters_are_per_channel_and_consistent(light: ColorPalette) -> None:
    channels = [cluster.channel for cluster in light.clusters]
    order = ["background", "text", "border", "gradient"]
    assert channels == sorted(channels, key=order.index)
    assert len({cluster.id for cluster in light.clusters}) == len(light.clusters)
    for channel in order:
        shares = [cluster.share for cluster in light.clusters if cluster.channel == channel]
        assert sum(shares) == pytest.approx(1.0, abs=0.01)
        weights = [cluster.weight for cluster in light.clusters if cluster.channel == channel]
        assert weights == sorted(weights, reverse=True)
    by_hex = _cluster_ids(light)
    # Same color, different channels: #e2e8f0 is both a border and a text color.
    assert "#e2e8f0" in by_hex["border"] and "#e2e8f0" in by_hex["text"]
    # Translucent header background keeps its alpha.
    assert "#ffffffcc" in by_hex["background"]
    # White and #f8fafc (ΔE 1.62) are both significant: never merged.
    assert "#ffffff" in by_hex["background"] and "#f8fafc" in by_hex["background"]
    border = next(cluster for cluster in light.clusters if cluster.hex == "#e2e8f0")
    assert border.usage.text > 0 and border.usage.border > 0
    assert "--color-border" in border.css_variables


def test_fixture_failing_contrast_pairs(light: ColorPalette) -> None:
    failing = {
        (pair.foreground, pair.background): pair
        for pair in light.contrast_pairs
        if not pair.passes_aa
    }
    for expected in load_site_contract().low_contrast_pairs:
        pair = failing[(expected["foreground"], expected["background"])]
        assert pair.ratio == pytest.approx(expected["ratio"], abs=0.01)
        assert pair.passes_aa is False
    assert failing[("#94a3b8", "#ffffff")].foreground_token == "faint"
    assert failing[("#94a3b8", "#ffffff")].background_token == "canvas"
    assert failing[("#16a34a", "#ffffff")].foreground_token == "success"
    assert failing[("#64748b", "#0b1020")].background_token == "surface-inverse"


def test_fixture_contrast_pairs_are_exact_and_sorted(light: ColorPalette) -> None:
    pairs = light.contrast_pairs
    assert pairs
    for pair in pairs:
        assert pair.ratio == pytest.approx(
            contrast_ratio(pair.foreground, pair.background), abs=0.01
        )
        assert pair.occurrences >= 1
        assert pair.context
    occurrences = [pair.occurrences for pair in pairs]
    assert occurrences == sorted(occurrences, reverse=True)
    top = pairs[0]
    assert (top.foreground, top.background, top.foreground_token) == ("#475569", "#ffffff", "muted")
    button = next(pair for pair in pairs if pair.background == "#4f46e5")
    assert (button.foreground_token, button.background_token) == ("on-primary", "primary")
    assert button.context == "button"
    # Foreground tokens are never borders or backgrounds (#e2e8f0 code text is not "hairline").
    code = next(pair for pair in pairs if pair.foreground == "#e2e8f0")
    assert code.foreground_token is None


def test_fixture_dark_palette(dark: ColorPalette) -> None:
    contract = load_site_contract()
    tokens = _tokens(dark)
    assert dark.scheme == "dark"
    for name in ("canvas", "surface", "ink", "muted", "primary", "on-primary"):
        assert tokens[name] == contract.dark_palette[name]
    assert tokens == contract.dark_palette
    assert any(
        (pair.foreground, pair.background) == ("#64748b", "#0b1020") and not pair.passes_aa
        for pair in dark.contrast_pairs
    )


# ─── Clustering ──────────────────────────────────────────────────────────────


def test_near_duplicate_colors_merge() -> None:
    page = _page(
        [
            _text(2, "rgb(99, 91, 255)", y=100),
            _text(3, "rgb(98, 90, 255)", y=140),
            _text(4, "rgb(17, 17, 17)", y=180, text="x" * 50),
        ]
    )
    palette = analyze_colors([page])
    texts = [cluster for cluster in palette.clusters if cluster.channel == "text"]
    brand = [cluster for cluster in texts if cluster.hex in ("#635bff", "#625aff")]
    assert len(brand) == 1
    assert set(brand[0].members) == {"#635bff", "#625aff"}
    assert brand[0].count == 2


def test_significant_subtle_surfaces_stay_separate() -> None:
    page = _page(
        [
            _el(2, "section", parent=1, box=(0, 0, 1440, 800), background_color="#f8fafc"),
            _text(3, "rgb(17, 17, 17)", parent=2),
        ],
    )
    palette = analyze_colors([page])
    backgrounds = _cluster_ids(palette)["background"]
    assert "#ffffff" in backgrounds and "#f8fafc" in backgrounds
    assert _tokens(palette)["surface"] == "#f8fafc"
    assert _tokens(palette)["canvas"] == "#ffffff"


def test_insignificant_near_duplicate_background_merges() -> None:
    page = _page(
        [
            _el(2, "div", parent=1, box=(100, 100, 20, 20), background_color="#fdfdfd"),
            _text(3, "rgb(17, 17, 17)"),
        ]
    )
    backgrounds = _cluster_ids(analyze_colors([page]))["background"]
    assert backgrounds == ["#ffffff"]


def test_border_never_merges_into_background() -> None:
    page = _page(
        [
            _el(
                2,
                "section",
                parent=1,
                box=(0, 0, 1440, 600),
                background_color="#e2e8f0",
            ),
            _el(
                3,
                "div",
                parent=1,
                box=(100, 700, 400, 200),
                border_width=("1px", "1px", "1px", "1px"),
                border_style=("solid", "solid", "solid", "solid"),
                border_color=("#e3e8ef", "#e3e8ef", "#e3e8ef", "#e3e8ef"),
            ),
            _text(4, "rgb(17, 17, 17)"),
        ]
    )
    palette = analyze_colors([page])
    clusters = _cluster_ids(palette)
    assert "#e3e8ef" in clusters["border"]
    assert "#e2e8f0" in clusters["background"]
    assert "#e3e8ef" not in clusters["background"]


def test_alpha_variants_stay_separate() -> None:
    page = _page(
        [
            _el(2, "div", parent=1, box=(0, 0, 1440, 300), background_color="rgba(0, 0, 0, 0.1)"),
            _el(3, "div", parent=1, box=(0, 400, 1440, 300), background_color="rgb(0, 0, 0)"),
            _text(4, "rgb(17, 17, 17)"),
        ]
    )
    backgrounds = _cluster_ids(analyze_colors([page]))["background"]
    assert "#0000001a" in backgrounds and "#000000" in backgrounds


def test_threshold_parameter_is_respected() -> None:
    # #3b82f6 vs #4f8ff7: ΔE2000 ~ 4.1 — merged at threshold 5, separate at 3.
    page = _page(
        [
            _text(2, "#3b82f6", text="x" * 50, size="32px"),
            _text(3, "#4f8ff7", text="tiny", size="12px", y=200),
            _text(4, "rgb(17, 17, 17)", text="y" * 50, size="32px", y=300),
        ]
    )
    loose = [c.hex for c in analyze_colors([page], threshold=5).clusters if c.channel == "text"]
    strict = [c.hex for c in analyze_colors([page], threshold=3).clusters if c.channel == "text"]
    assert "#4f8ff7" in strict and "#4f8ff7" not in loose
    assert "#3b82f6" in loose


def test_sub_pages_weigh_half() -> None:
    main = _page([_text(2, "#123456", text="x" * 40)])
    sub = _page([_text(2, "#654321", text="x" * 40)], name="pricing")
    palette = analyze_colors([main, sub])
    weights = {c.hex: c.weight for c in palette.clusters if c.channel == "text"}
    assert weights["#123456"] == pytest.approx(2 * weights["#654321"])


def test_many_unique_colors_are_bucketed() -> None:
    elements = [
        _text(
            index,
            f"rgb({(index * 37) % 256}, {(index * 91) % 256}, {(index * 53) % 256})",
            y=index * 30,
            text="z" * (1 + index % 40),
        )
        for index in range(2, 702)
    ]
    palette = analyze_colors([_page(elements, height=25000)])
    texts = [cluster for cluster in palette.clusters if cluster.channel == "text"]
    token_clusters = {token.cluster_id for token in palette.tokens}
    assert 0 < len([c for c in texts if c.id not in token_clusters]) <= 40
    assert sum(cluster.share for cluster in texts) <= 1.0 + 1e-6


# ─── Robustness ──────────────────────────────────────────────────────────────


def test_empty_input_gives_empty_palette() -> None:
    assert analyze_colors([]) == ColorPalette()
    empty = PageCapture(url="https://x.test/", final_url="https://x.test/", elements=[])
    assert analyze_colors([empty]) == ColorPalette()


def test_transparent_page_has_white_canvas() -> None:
    page = _page([_text(2, "rgb(30, 30, 30)")], body_bg="rgba(0, 0, 0, 0)")
    palette = analyze_colors([page])
    assert _tokens(palette)["canvas"] == "#ffffff"
    assert palette.scheme == "light"
    canvas = next(token for token in palette.tokens if token.name == "canvas")
    assert "browser default" in canvas.evidence


def test_rootless_page_without_backgrounds_still_has_canvas() -> None:
    page = PageCapture(
        url="https://x.test/",
        final_url="https://x.test/",
        elements=[_text(0, "rgb(30, 30, 30)", parent=None)],
    )
    palette = analyze_colors([page])
    canvas = next(token for token in palette.tokens if token.name == "canvas")
    assert canvas.value == "#ffffff" and canvas.cluster_id is None
    assert _tokens(palette)["ink"] == "#1e1e1e"


def test_modern_color_syntaxes_and_currentcolor() -> None:
    page = _page(
        [
            _text(2, "oklch(0.3 0.02 260)"),
            _text(3, "color(srgb 0.1 0.1 0.1)", y=200),
            _el(
                4,
                "div",
                parent=1,
                box=(100, 400, 300, 100),
                color="rgb(200, 200, 200)",
                border_width=("1px", "0px", "1px", "0px"),
                border_style=("solid", "none", "solid", "none"),
                border_color=("currentcolor", "currentcolor", "currentcolor", "currentcolor"),
            ),
            _text(5, "transparent", y=600),
            _el(6, "div", parent=1, box=(0, 700, 1440, 10), background_color="transparent"),
        ]
    )
    palette = analyze_colors([page])
    clusters = _cluster_ids(palette)
    assert "#c8c8c8" in clusters["border"]
    assert all(cluster.hex.startswith("#") for cluster in palette.clusters)
    assert len(clusters["text"]) >= 1


def test_overlay_elements_are_ignored() -> None:
    page = _page(
        [
            _text(2, "rgb(20, 20, 20)", text="x" * 50),
            _el(
                3,
                "div",
                parent=1,
                box=(0, 0, 1440, 900),
                background_color="rgb(255, 0, 128)",
                in_overlay=True,
            ),
        ]
    )
    palette = analyze_colors([page])
    assert all(cluster.hex != "#ff0080" for cluster in palette.clusters)


# ─── Roles ───────────────────────────────────────────────────────────────────


def _marketing_page(**overrides: Any) -> PageCapture:
    """Light page: white body, slate surface band, dark footer, indigo CTA buttons."""
    elements = [
        _el(
            2,
            "header",
            parent=1,
            box=(0, 0, 1440, 64),
            landmark="header",
            border_width=("0px", "0px", "1px", "0px"),
            border_style=("none", "none", "solid", "none"),
            border_color=("#000", "#000", "#e5e7eb", "#000"),
        ),
        _button(3, "#4f46e5", parent=2, y=12),
        _button(4, "#4f46e5", y=500),
        _text(5, "#111827", tag="h1", size="56px", text="Ship faster with less", y=200),
        _text(6, "#4b5563", text="Body copy that explains the product in detail.", y=300),
        _text(7, "#4b5563", text="More body copy that explains the product.", y=340),
        _text(8, "#9ca3af", text="Small print", y=380, size="13px"),
        _text(9, "#9ca3af", text="Another caption", y=420, size="13px"),
        _el(
            10,
            "section",
            parent=1,
            box=(0, 700, 1440, 700),
            background_color="#f9fafb",
            child_count=3,
        ),
        _el(
            11,
            "article",
            parent=10,
            box=(120, 760, 360, 300),
            background_color="#ffffff",
            border_width=("1px", "1px", "1px", "1px"),
            border_style=("solid", "solid", "solid", "solid"),
            border_color=("#e5e7eb", "#e5e7eb", "#e5e7eb", "#e5e7eb"),
            border_radius=("12px", "12px", "12px", "12px"),
            child_count=2,
            text="Card",
        ),
        _el(
            12,
            "article",
            parent=10,
            box=(520, 760, 360, 300),
            background_color="#ffffff",
            border_width=("1px", "1px", "1px", "1px"),
            border_style=("solid", "solid", "solid", "solid"),
            border_color=("#e5e7eb", "#e5e7eb", "#e5e7eb", "#e5e7eb"),
            border_radius=("12px", "12px", "12px", "12px"),
            child_count=2,
            text="Card",
        ),
        _text(13, "#111827", parent=11, tag="h3", size="20px", text="Feature one", y=780),
        _text(14, "#4b5563", parent=12, text="Feature two copy", y=820),
        _el(
            15,
            "footer",
            parent=1,
            box=(0, 1600, 1440, 400),
            background_color="#111827",
            landmark="footer",
            child_count=4,
        ),
        _text(
            16, "#d1d5db", parent=15, tag="a", href="/a", text="About us", y=1650, landmark="footer"
        ),
        _text(
            17, "#d1d5db", parent=15, tag="a", href="/b", text="Careers", y=1690, landmark="footer"
        ),
        _text(
            18,
            "#d1d5db",
            parent=15,
            tag="a",
            href="/c",
            text="Press kit",
            y=1730,
            landmark="footer",
        ),
    ]
    elements.extend(overrides.pop("extra", []))
    return _page(elements, **overrides)


def test_marketing_page_roles() -> None:
    palette = analyze_colors([_marketing_page()])
    tokens = _tokens(palette)
    assert tokens["canvas"] == "#ffffff"
    assert tokens["surface"] == "#f9fafb"
    assert tokens["surface-inverse"] == "#111827"
    assert tokens["ink"] == "#111827"
    assert tokens["muted"] == "#4b5563"
    assert tokens["faint"] == "#9ca3af"
    assert tokens["hairline"] == "#e5e7eb"
    assert tokens["primary"] == "#4f46e5"
    assert tokens["on-primary"] == "#ffffff"
    # Footer links are a distinct color used only on the dark band.
    assert tokens["on-inverse"] == "#d1d5db"
    assert palette.scheme == "light"
    for name in ("success", "warning", "danger", "info", "secondary"):
        assert name not in tokens


def test_red_brand_button_is_not_danger_without_evidence() -> None:
    page = _marketing_page(
        extra=[
            _button(20, "#dc2626", y=900, box=(600, 900, 200, 48)),
            _button(21, "#dc2626", y=980, box=(600, 980, 200, 48)),
            _button(22, "#dc2626", y=1060, box=(600, 1060, 200, 48)),
        ]
    )
    tokens = _tokens(analyze_colors([page]))
    assert "danger" not in tokens
    assert tokens["primary"] == "#dc2626"
    # Indigo still drives actions with a different hue: secondary.
    assert tokens["secondary"] == "#4f46e5"


def test_status_needs_evidence_from_classes_or_variables() -> None:
    page = _marketing_page(
        extra=[
            _text(20, "#dc2626", classes=["form-error"], text="Email is required", y=1100),
            _text(21, "#d97706", text="Heads up", y=1140, classes=["note"]),
            _el(
                22,
                "div",
                parent=1,
                box=(100, 1180, 300, 40),
                classes=["alert-warning"],
                child_count=1,
            ),
            _text(23, "#b45309", parent=22, text="Trial ends soon", y=1185),
            _text(24, "#0ea5e9", text="Fresh news", y=1240),
        ],
        variables=[
            CssVariable(
                name="--color-info", declared="#0ea5e9", resolved="rgb(14, 165, 233)", source="root"
            ),
        ],
    )
    palette = analyze_colors([page])
    tokens = _tokens(palette)
    assert tokens["danger"] == "#dc2626"
    assert tokens["warning"] == "#b45309"  # hinted by the parent's class, not #d97706
    assert tokens["info"] == "#0ea5e9"
    info = next(token for token in palette.tokens if token.name == "info")
    assert info.css_variable == "--color-info"


def test_brand_variable_hint_breaks_ties() -> None:
    page = _marketing_page(
        extra=[
            _button(20, "#0d9488", y=900),
            _button(21, "#0d9488", y=980),
            _button(22, "#0d9488", y=1060),
        ],
        variables=[
            CssVariable(
                name="--brand-500", declared="#4f46e5", resolved="rgb(79, 70, 229)", source="root"
            ),
        ],
    )
    palette = analyze_colors([page])
    by_name = {token.name: token for token in palette.tokens}
    assert by_name["primary"].value == "#4f46e5"
    assert by_name["primary"].css_variable == "--brand-500"
    assert by_name["secondary"].value == "#0d9488"


def test_monochrome_site_has_no_primary() -> None:
    page = _marketing_page(
        extra=[_button(20, "#000000", y=900), _button(21, "#000000", y=980)],
    )
    page.elements[3] = _button(3, "#111111", parent=2, y=12)
    page.elements[4] = _button(4, "#111111", y=500)
    tokens = _tokens(analyze_colors([page]))
    assert "primary" not in tokens and "on-primary" not in tokens


def test_primary_falls_back_to_link_color() -> None:
    page = _marketing_page(
        extra=[
            _text(20, "#2563eb", tag="a", href="/x", text="Read the docs", y=1100),
            _text(21, "#2563eb", tag="a", href="/y", text="See pricing", y=1140),
        ]
    )
    page.elements[3] = _button(3, "#111111", parent=2, y=12)
    page.elements[4] = _button(4, "#111111", y=500)
    palette = analyze_colors([page])
    primary = next(token for token in palette.tokens if token.name == "primary")
    assert primary.value == "#2563eb"
    assert "link" in primary.evidence


def test_decorative_blob_and_photo_backgrounds_are_not_tokens() -> None:
    page = _marketing_page(
        extra=[
            _el(20, "div", parent=1, box=(40, 900, 24, 24), background_color="#22c55e"),
            _el(21, "div", parent=1, box=(80, 900, 24, 24), background_color="#22c55e"),
            _el(
                22,
                "section",
                parent=1,
                box=(0, 1400, 1440, 200),
                background_color="#7c3aed",
                background_image='url("hero.jpg")',
                child_count=1,
            ),
        ]
    )
    palette = analyze_colors([page])
    clusters = {cluster.hex: cluster for cluster in palette.clusters}
    assert clusters["#22c55e"].decorative
    assert clusters["#7c3aed"].decorative
    values = set(_tokens(palette).values())
    assert "#22c55e" not in values and "#7c3aed" not in values


def test_dark_site_with_translucent_hairlines() -> None:
    hairline = "rgba(255, 255, 255, 0.08)"
    sides = ("1px", "1px", "1px", "1px")
    solid = ("solid", "solid", "solid", "solid")
    elements = [
        _text(2, "#f7f8f8", tag="h1", size="56px", text="Plan and build products", y=150),
        _text(3, "#8a8f98", text="Purpose-built tool for planning and building products", y=260),
        _text(4, "#8a8f98", text="Meet the system for modern software development", y=300),
        _text(5, "#62666d", text="Small caption", size="13px", y=340),
        _text(6, "#62666d", text="Another caption", size="13px", y=370),
        _el(
            7,
            "div",
            parent=1,
            box=(120, 500, 560, 300),
            background_color="#0f1011",
            border_width=sides,
            border_style=solid,
            border_color=(hairline,) * 4,
            border_radius=("8px",) * 4,
            child_count=2,
            text="Card",
        ),
        _el(
            8,
            "div",
            parent=1,
            box=(760, 500, 560, 300),
            background_color="#0f1011",
            border_width=sides,
            border_style=solid,
            border_color=(hairline,) * 4,
            border_radius=("8px",) * 4,
            child_count=2,
            text="Card",
        ),
        _button(9, "#e6e6e6", color="#08090a", y=420),
        _button(10, "#e6e6e6", color="#08090a", y=40),
        _el(
            11,
            "section",
            parent=1,
            box=(0, 900, 1440, 600),
            background_color="#141516",
            child_count=1,
        ),
    ]
    page = _page(elements, body_bg="#08090a", text_color="#f7f8f8")
    palette = analyze_colors([page])
    tokens = _tokens(palette)
    assert palette.scheme == "dark"
    assert tokens["canvas"] == "#08090a"
    assert tokens["ink"] == "#f7f8f8"
    assert tokens["muted"] == "#8a8f98"
    assert tokens["faint"] == "#62666d"
    assert tokens["hairline"] == "#ffffff14"
    assert tokens["surface"] == "#141516"
    assert tokens["surface-raised"] == "#0f1011"
    assert "primary" not in tokens


def test_text_on_photos_is_skipped_in_contrast_pairs() -> None:
    page = _marketing_page(
        extra=[
            _el(
                20,
                "section",
                parent=1,
                box=(0, 1400, 1440, 200),
                background_image='url("photo.jpg")',
                child_count=1,
            ),
            _text(21, "#fafafa", parent=20, text="Text over a photo", y=1450),
        ]
    )
    palette = analyze_colors([page])
    assert all(pair.foreground != "#fafafa" for pair in palette.contrast_pairs)


def test_hover_colors_are_not_tokens() -> None:
    page = _marketing_page()
    page.hover_states.append(
        HoverState(
            element_index=4,
            kind="button",
            changed={"background_color": ("rgb(79, 70, 229)", "rgb(67, 56, 202)")},
        )
    )
    values = set(_tokens(analyze_colors([page])).values())
    assert "#4338ca" not in values


def test_stylesheet_variables_used_when_pages_lack_them() -> None:
    sheet = StylesheetInfo(
        url="https://example.test/app.css",
        variables=[
            CssVariable(name="--text-muted", declared="#4b5563", source="stylesheet"),
            CssVariable(name="--accent-rgb", declared="79, 70, 229", source="stylesheet"),
        ],
    )
    palette = analyze_colors([_marketing_page()], [sheet])
    by_name = {token.name: token for token in palette.tokens}
    assert by_name["muted"].css_variable == "--text-muted"
    assert by_name["primary"].css_variable == "--accent-rgb"


def test_contrast_pairs_keep_every_failing_pair() -> None:
    extra = [
        _text(
            20 + offset,
            f"rgb({200 + offset}, {200 + offset}, {200 + offset})",
            text=f"Faint {offset}",
            y=1100 + offset * 10,
        )
        for offset in range(30)
    ]
    palette = analyze_colors([_marketing_page(extra=extra)])
    failing = [pair for pair in palette.contrast_pairs if not pair.passes_aa]
    assert len(failing) >= 30
    assert len(palette.contrast_pairs) <= 20 + len(failing)


def test_parse_color_compatibility() -> None:
    # Token values are canonical lowercase hex and parse back to the same color.
    palette = analyze_colors([_marketing_page()])
    for token in palette.tokens:
        color = parse_color(token.value)
        assert color is not None and color.css_hex == token.value


# ─── Heuristics tuned on stripe.com / linear.app ─────────────────────────────


def test_neutral_and_chromatic_predicates() -> None:
    def color(value: str) -> RGBA:
        parsed = parse_color(value)
        assert parsed is not None
        return parsed

    for neutral in ("#ffffff", "#f8fafd", "#e5edf5", "#e2e8f0", "#64748d", "#0a2540", "#0d1738"):
        assert is_neutralish(color(neutral)), neutral
    for tinted in ("#ffd7ef", "#d6d9fc", "#839bc8", "#2b408a", "#635bff"):
        assert not is_neutralish(color(tinted)), tinted
    assert is_chromatic(color("#635bff")) and is_chromatic(color("#16a34a"))
    # Navy ink and pale pink are neither gray enough nor saturated enough to be a brand color.
    assert not is_chromatic(color("#0a2540")) and not is_chromatic(color("#ffd7ef"))
    assert neutral_chroma_limit(0.2) > neutral_chroma_limit(0.6) > neutral_chroma_limit(0.97)


def test_line_like_backgrounds_count_as_borders() -> None:
    page = _marketing_page(
        extra=[
            # Mask-drawn card outline: a childless "...__border" box filled with the line color.
            _el(
                20,
                "div",
                parent=1,
                box=(120, 1100, 400, 300),
                classes=["bento-card__border"],
                background_color="#d1d5db",
            ),
            _el(21, "hr", parent=1, box=(0, 1450, 1200, 1), background_color="#cbd5e1"),
            # 1px padding ring around a child that hides everything else.
            _el(
                22,
                "div",
                parent=1,
                box=(600, 1100, 300, 100),
                background_color="#a5b4fc",
                padding=("1px", "1px", "1px", "1px"),
                child_count=1,
            ),
            _el(23, "div", parent=22, box=(601, 1101, 298, 98), background_color="#ffffff"),
        ]
    )
    palette = analyze_colors([page])
    clusters = _cluster_ids(palette)
    for line in ("#d1d5db", "#cbd5e1", "#a5b4fc"):
        assert line in clusters["border"], line
        assert line not in clusters["background"], line
    assert "surface-raised" not in _tokens(palette)


def test_images_hide_the_backgrounds_behind_them() -> None:
    page = _marketing_page(
        extra=[
            _el(
                20,
                "div",
                parent=1,
                box=(120, 1100, 600, 400),
                background_color="#e2e8f0",
                child_count=1,
            ),
            _el(21, "img", parent=20, box=(120, 1100, 600, 400)),
        ]
    )
    palette = analyze_colors([page])
    assert "#e2e8f0" not in _cluster_ids(palette)["background"]


def test_text_over_media_and_invisible_text_are_not_contrast_pairs() -> None:
    page = _marketing_page(
        extra=[
            _el(20, "video", parent=1, box=(0, 1420, 1440, 160)),
            _text(21, "#fafafa", text="Caption over the video", y=1450),
            _text(22, "#fefefe", text="Hidden text drawn over a canvas", y=1500),
        ]
    )
    palette = analyze_colors([page])
    foregrounds = {pair.foreground for pair in palette.contrast_pairs}
    assert "#fafafa" not in foregrounds
    # #fefefe on the white body is 1.0:1 — an artifact, not a design fact.
    assert "#fefefe" not in foregrounds
    assert all(pair.ratio >= 1.2 for pair in palette.contrast_pairs)


def _dark_app_page(**overrides: Any) -> PageCapture:
    """Linear-like page: near-black canvas, panel cards with translucent hairlines, four text
    grays named by CSS variables, a neutral (inverted) CTA."""
    sides = ("1px", "1px", "1px", "1px")
    solid = ("solid", "solid", "solid", "solid")
    hairline = ("rgba(255, 255, 255, 0.08)",) * 4
    elements: list[ElementSample] = [
        _text(2, "#f7f8f8", tag="h1", size="56px", text="Plan and build products", y=150),
        _text(3, "#f7f8f8", tag="h2", size="32px", text="Made for modern teams", y=230),
    ]
    for offset in range(6):
        y = 300 + offset * 40
        elements.append(_text(4 + offset, "#8a8f98", text="Description copy " * 3, y=y))
        elements.append(_text(10 + offset, "#d0d6e0", text="Mock UI label text " * 2, y=y + 5))
        elements.append(_text(16 + offset, "#62666d", text="Timestamp 2m", y=y + 10))
    for offset in range(6):
        elements.append(
            _el(
                22 + offset,
                "div",
                parent=1,
                box=(120 + offset * 200, 700, 180, 300),
                background_color="#0f1011",
                border_width=sides,
                border_style=solid,
                border_color=hairline,
                border_radius=("8px",) * 4,
                child_count=2,
                text="Card",
            )
        )
    elements.append(
        _el(
            28,
            "footer",
            parent=1,
            box=(0, 1800, 1440, 200),
            landmark="footer",
            border_width=("1px", "0px", "0px", "0px"),
            border_style=("solid", "none", "none", "none"),
            border_color=("#23252a", "#23252a", "#23252a", "#23252a"),
        )
    )
    elements.append(
        _el(
            31,
            "header",
            parent=1,
            box=(420, 0, 600, 64),
            landmark="header",
            border_width=("0px", "0px", "1px", "0px"),
            border_style=("none", "none", "solid", "none"),
            border_color=("#23252a", "#23252a", "#23252a", "#23252a"),
        )
    )
    elements.append(_button(29, "#e5e5e6", color="#08090a", y=40))
    elements.append(_button(30, "#e5e5e6", color="#08090a", y=1200))
    elements.extend(overrides.pop("extra", []))
    variables = [
        CssVariable(name=name, declared=value, source="root")
        for name, value in (
            ("--color-bg-primary", "#08090a"),
            ("--color-bg-panel", "#0f1011"),
            ("--color-text-primary", "#f7f8f8"),
            ("--color-text-secondary", "#d0d6e0"),
            ("--color-text-tertiary", "#8a8f98"),
            ("--color-text-quaternary", "#62666d"),
            ("--color-border-primary", "#23252a"),
            ("--color-brand-bg", "#5e6ad2"),
        )
    ]
    return _page(
        elements,
        body_bg="#08090a",
        text_color="#f7f8f8",
        variables=variables,
        **overrides,
    )


def test_dark_app_text_and_hairline_roles() -> None:
    palette = analyze_colors([_dark_app_page()])
    by_name = {token.name: token for token in palette.tokens}
    assert palette.scheme == "dark"
    assert by_name["canvas"].value == "#08090a"
    assert by_name["canvas"].css_variable == "--color-bg-primary"
    assert by_name["surface"].value == "#0f1011"
    assert by_name["ink"].value == "#f7f8f8"
    # Four text tiers: "--color-text-secondary" (#d0d6e0) is close to ink but named and heavily
    # used, so it is the muted tier; the tertiary gray is faint, the quaternary one is dropped.
    assert by_name["muted"].value == "#d0d6e0"
    assert by_name["muted"].css_variable == "--color-text-secondary"
    assert by_name["faint"].value == "#8a8f98"
    assert by_name["faint"].css_variable == "--color-text-tertiary"
    # The translucent hairline carries far more weight than the hinted "--color-border-primary".
    assert by_name["hairline"].value == "#ffffff14"
    assert by_name["hairline"].css_variable is None
    assert by_name["hairline-strong"].value == "#23252a"
    # "--color-border-primary" hints a plain hairline, but its value is exactly this token.
    assert by_name["hairline-strong"].css_variable == "--color-border-primary"
    # The brand variable is never used by an action: no primary, the CTA is neutral.
    assert "primary" not in by_name and "on-primary" not in by_name


def test_large_chromatic_card_is_not_primary() -> None:
    page = _marketing_page(
        extra=[
            _el(
                20,
                "a",
                parent=1,
                box=(900, 1100, 432, 480),
                href="/customers/ramp",
                background_color="#e4f222",
                padding=("24px", "32px", "24px", "32px"),
                cursor="pointer",
                child_count=2,
                text="Our speed is intense",
                own_text=False,
            ),
        ]
    )
    page.elements[3] = _button(3, "#111111", parent=2, y=12)
    page.elements[4] = _button(4, "#111111", y=500)
    assert "primary" not in _tokens(analyze_colors([page]))


def test_single_chromatic_button_needs_a_brand_hint() -> None:
    page = _marketing_page()
    page.elements[3] = _button(3, "#111111", parent=2, y=12)
    assert "primary" not in _tokens(analyze_colors([page]))
    hinted = page.model_copy(
        update={
            "css_variables": [
                CssVariable(name="--color-brand", declared="#4f46e5", source="root"),
            ]
        }
    )
    assert _tokens(analyze_colors([hinted]))["primary"] == "#4f46e5"


def test_on_inverse_is_the_highest_contrast_text_on_the_band() -> None:
    page = _marketing_page(
        extra=[
            _text(20, "#f3f4f6", parent=15, tag="h4", text="Company", y=1620, landmark="footer"),
            _text(21, "#f3f4f6", parent=15, tag="h4", text="Product", y=1620, landmark="footer"),
        ]
    )
    page.elements[3] = _button(3, "#4f46e5", parent=2, y=12, color="#eef2ff")
    page.elements[4] = _button(4, "#4f46e5", y=500, color="#eef2ff")
    tokens = _tokens(analyze_colors([page]))
    assert tokens["on-primary"] == "#eef2ff"
    # Footer links (#d1d5db) are heavier, but the headings are the main text of the band.
    assert tokens["on-inverse"] == "#f3f4f6"
    # White text on the band would repeat the canvas value: no on-inverse token then.
    page.elements[-1] = _text(
        21, "#ffffff", parent=15, tag="h4", text="Product", y=1620, landmark="footer"
    )
    page.elements[-2] = _text(
        20, "#ffffff", parent=15, tag="h4", text="Company", y=1620, landmark="footer"
    )
    assert "on-inverse" not in _tokens(analyze_colors([page]))
