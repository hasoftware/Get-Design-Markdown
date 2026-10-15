from __future__ import annotations

import pytest

from get_design_markdown.analyzer.css_values import (
    BLACK,
    RGBA,
    TRANSPARENT,
    WHITE,
    ShadowLayer,
    composite,
    contrast_levels,
    contrast_ratio,
    css_shorthand,
    delta_e_2000,
    flatten,
    format_box_shadow,
    format_px,
    format_sides_px,
    gradient_colors,
    is_generic_family,
    is_gradient,
    is_large_text,
    is_monospace_stack,
    is_neutral,
    parse_box_shadow,
    parse_color,
    parse_font_family,
    parse_font_weight,
    parse_letter_spacing,
    parse_line_height,
    parse_media_query,
    parse_number,
    parse_opacity,
    parse_px,
    parse_sides_px,
    primary_font_family,
    relative_luminance,
    rgba,
    split_top_level,
    to_hex,
    to_oklch,
    visible_shadow_layers,
)

# ─── Lengths ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12px", 12.0),
        ("  -4.5px ", -4.5),
        ("0", 0.0),
        ("1.5em", 24.0),
        ("2rem", 32.0),
        ("12pt", 16.0),
        ("10vw", 144.0),
        ("50vh", 450.0),
        ("1E1px", 10.0),
    ],
)
def test_parse_px(value: str, expected: float) -> None:
    assert parse_px(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value", [None, "", "auto", "normal", "none", "1.5", "calc(100% - 8px)", "12furlongs"]
)
def test_parse_px_rejects_non_lengths(value: str | None) -> None:
    assert parse_px(value) is None


def test_parse_px_relative_units_use_context() -> None:
    assert parse_px("2em", font_size_px=20) == 40
    assert parse_px("2rem", root_font_size_px=10) == 20
    assert parse_px("50%") is None
    assert parse_px("50%", percent_of=300) == 150


def test_parse_number() -> None:
    assert parse_number("1.25") == 1.25
    assert parse_number(".5") == 0.5
    assert parse_number("12px") is None
    assert parse_number(None) is None


def test_parse_sides_px_expands_shorthands() -> None:
    assert parse_sides_px(("8px", "16px", "8px", "16px")) == (8, 16, 8, 16)
    assert parse_sides_px(["1em"], font_size_px=10) == (10, 10, 10, 10)
    assert parse_sides_px(["4px", "8px"]) == (4, 8, 4, 8)
    assert parse_sides_px(["auto", "8px", "0px", "8px"]) == (0, 8, 0, 8)
    assert parse_sides_px([]) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(16.0, "16px"), (0.5, "0.5px"), (12.345, "12.35px"), (-0.0, "0px"), (-4, "-4px")],
)
def test_format_px(value: float, expected: str) -> None:
    assert format_px(value) == expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (("8px", "8px", "8px", "8px"), "8px"),
        (("12px", "20px", "12px", "20px"), "12px 20px"),
        (("64px", "0px", "48px", "0px"), "64px 0px 48px"),
        (("1px", "2px", "3px", "4px"), "1px 2px 3px 4px"),
    ],
)
def test_css_shorthand(values: tuple[str, str, str, str], expected: str) -> None:
    assert css_shorthand(values) == expected


def test_format_sides_px() -> None:
    assert format_sides_px((12, 20, 12, 20)) == "12px 20px"


def test_split_top_level() -> None:
    assert split_top_level("a, rgb(1, 2, 3), 'x,y'") == ["a", "rgb(1, 2, 3)", "'x,y'"]
    assert split_top_level("rgba(0, 0, 0, 0.1)  0px 1px", " ") == [
        "rgba(0, 0, 0, 0.1)",
        "0px",
        "1px",
    ]
    assert split_top_level("") == []


# ─── Colors ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("rgb(79, 70, 229)", "#4f46e5"),
        ("#4F46E5", "#4f46e5"),
        ("#fff", "#ffffff"),
        ("rgba(15, 23, 42, 0.06)", "#0f172a0f"),
        ("rgb(0 0 0 / 50%)", "#00000080"),
        ("hsl(240 100% 50% / 25%)", "#0000ff40"),
        ("hwb(120 10% 20%)", "#19cc19"),
        ("rebeccapurple", "#663399"),
        ("transparent", "#00000000"),
        ("oklch(0.6 0.2 280)", "#6e69f3"),
        ("lab(50% 40 59.5)", "#bf5700"),
        ("color(display-p3 1 0 0)", "#ff3428"),
    ],
)
def test_to_hex(value: str, expected: str) -> None:
    assert to_hex(value) == expected


@pytest.mark.parametrize("value", [None, "", "none", "inherit", "not-a-color", "12px", "url(x)"])
def test_parse_color_rejects(value: str | None) -> None:
    assert parse_color(value) is None


def test_currentcolor_needs_context() -> None:
    assert parse_color("currentcolor") is None
    assert parse_color("currentColor", current_color="rgb(1, 2, 3)") == RGBA(1, 2, 3, 1.0)
    assert parse_color("currentcolor", current_color=WHITE) == WHITE
    assert parse_color("currentcolor", current_color="currentcolor") is None


def test_rgba_properties() -> None:
    color = rgba(15.4, 23, 42, 0.06)
    assert color == RGBA(15, 23, 42, 0.06)
    assert color.hex == "#0f172a"
    assert color.hex8 == "#0f172a0f"
    assert color.css_hex == "#0f172a0f"
    assert not color.is_opaque and not color.is_transparent
    assert color.to_css() == "rgba(15, 23, 42, 0.06)"
    assert WHITE.to_css() == "rgb(255, 255, 255)"
    assert TRANSPARENT.is_transparent
    assert color.with_alpha(1).css_hex == "#0f172a"
    assert rgba(300, -5, 10, 2).alpha == 1.0


def test_composite_and_flatten() -> None:
    half_black = RGBA(0, 0, 0, 0.5)
    assert composite(half_black, WHITE) == RGBA(128, 128, 128, 1.0)
    assert composite(TRANSPARENT, TRANSPARENT) == TRANSPARENT
    glass = parse_color("rgba(255, 255, 255, 0.8)")
    navy = parse_color("#0f172a")
    assert glass is not None and navy is not None
    assert composite(glass, navy) == RGBA(207, 209, 212, 1.0)
    assert flatten(half_black) == RGBA(128, 128, 128, 1.0)
    assert flatten(WHITE, TRANSPARENT) == WHITE


def test_gradient_colors() -> None:
    value = (
        "radial-gradient(60% 60% at 50% 0%, rgba(99, 102, 241, 0.18) 0%, "
        'rgba(236, 72, 153, 0.12) 40%, rgba(0, 0, 0, 0) 70%), url("hero-#fff-red.png")'
    )
    assert [color.css_hex for color in gradient_colors(value)] == [
        "#6366f12e",
        "#ec48991f",
        "#00000000",
    ]
    assert [c.hex for c in gradient_colors("linear-gradient(to right, red, #00f 50%)")] == [
        "#ff0000",
        "#0000ff",
    ]
    stops = gradient_colors("repeating-conic-gradient(from 0deg, white 0 10%, black 10% 20%)")
    assert stops == [WHITE, BLACK]
    assert gradient_colors("url(a.png)") == []
    assert gradient_colors(None) == []


def test_is_gradient() -> None:
    assert is_gradient("linear-gradient(red, blue)")
    assert is_gradient("url(a.png), repeating-radial-gradient(red, blue)")
    assert not is_gradient("none")
    assert not is_gradient(None)


# ─── Shadows ─────────────────────────────────────────────────────────────────


def test_parse_box_shadow_computed_order() -> None:
    layers = parse_box_shadow(
        "rgba(0, 0, 0, 0.1) 0px 1px 2px 0px, rgb(238, 238, 238) 0px 0px 0px 1px inset"
    )
    assert layers == [
        ShadowLayer(0, 1, 2, 0, RGBA(0, 0, 0, 0.1), False),
        ShadowLayer(0, 0, 0, 1, RGBA(238, 238, 238, 1.0), True),
    ]


def test_parse_box_shadow_author_order_and_format() -> None:
    layers = parse_box_shadow("0 8px 24px -4px rgba(15,23,42,.12)")
    assert layers == [ShadowLayer(0, 8, 24, -4, RGBA(15, 23, 42, 0.12), False)]
    assert format_box_shadow(layers) == "0px 8px 24px -4px rgba(15, 23, 42, 0.12)"
    assert format_box_shadow([]) == "none"
    inset = ShadowLayer(0, 0, 0, 1, None, True)
    assert inset.to_css() == "inset 0px 0px 0px 1px"


def test_parse_box_shadow_edge_cases() -> None:
    assert parse_box_shadow("none") == []
    assert parse_box_shadow(None) == []
    assert parse_box_shadow("red") == []
    assert parse_box_shadow("currentcolor 0 1px", current_color="#fff")[0].color == WHITE
    assert visible_shadow_layers("rgba(0, 0, 0, 0) 0px 4px 8px 0px, black 0px 0px 0px 0px") == []


# ─── Fonts ───────────────────────────────────────────────────────────────────


def test_parse_font_family() -> None:
    stack = parse_font_family('Inter, system-ui, -apple-system, "Segoe  UI", sans-serif')
    assert stack == ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"]
    assert parse_font_family("") == []
    assert parse_font_family(None) == []


def test_primary_font_family() -> None:
    assert primary_font_family('"Sohne", "SF Pro", sans-serif') == "Sohne"
    assert primary_font_family("-apple-system, BlinkMacSystemFont, sans-serif") == "-apple-system"
    assert primary_font_family(["system-ui", "Roboto", "sans-serif"]) == "Roboto"
    assert primary_font_family(None) is None
    assert is_generic_family("Sans-Serif")
    assert is_generic_family("BlinkMacSystemFont")
    assert not is_generic_family("Inter")


@pytest.mark.parametrize(
    ("stack", "expected"),
    [
        ('"JetBrains Mono", ui-monospace, monospace', True),
        ("SFMono-Regular, Menlo, monospace", True),
        ("Consolas, monospace", True),
        ("monospace", True),
        ("Inter, sans-serif", False),
        ("Montserrat, sans-serif", False),
    ],
)
def test_is_monospace_stack(stack: str, expected: bool) -> None:
    assert is_monospace_stack(stack) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("normal", 400), ("bold", 700), ("600", 600), ("550.4", 550), ("weird", 400), (None, 400)],
)
def test_parse_font_weight(value: str | None, expected: int) -> None:
    assert parse_font_weight(value) == expected


def test_parse_line_height() -> None:
    assert parse_line_height("24px", 16) == 24
    assert parse_line_height("1.5", 16) == 24
    assert parse_line_height("150%", 16) == 24
    assert parse_line_height("1.5em", 16) == 24
    assert parse_line_height("normal", 16) is None
    assert parse_line_height("normal", 20, normal=1.2) == pytest.approx(24)
    assert parse_line_height(None, 16) is None


def test_parse_letter_spacing() -> None:
    assert parse_letter_spacing("normal", 16) == 0
    assert parse_letter_spacing("-1.5px", 64) == -1.5
    assert parse_letter_spacing("-0.02em", 50) == pytest.approx(-1.0)


def test_is_large_text() -> None:
    assert is_large_text(24, 400)
    assert is_large_text(19, 700)
    assert not is_large_text(19, 600)


# ─── Contrast, OKLCH, ΔE ─────────────────────────────────────────────────────


def test_relative_luminance_bounds() -> None:
    assert relative_luminance(WHITE) == pytest.approx(1.0)
    assert relative_luminance(BLACK) == 0.0


@pytest.mark.parametrize(
    ("foreground", "background", "expected"),
    [
        ("#ffffff", "#000000", 21.0),
        ("#ffffff", "#4f46e5", 6.29),
        ("#94a3b8", "#ffffff", 2.56),
        ("#64748b", "#0b1020", 3.98),
        ("rgba(0, 0, 0, 0.5)", "#ffffff", 3.95),
        ("#000000", "rgba(255, 255, 255, 0)", 21.0),
    ],
)
def test_contrast_ratio(foreground: str, background: str, expected: float) -> None:
    assert contrast_ratio(foreground, background) == pytest.approx(expected, abs=0.01)


def test_contrast_ratio_translucent_background_uses_base() -> None:
    ratio = contrast_ratio("#ffffff", "rgba(0, 0, 0, 0.5)", base=BLACK)
    assert ratio == pytest.approx(21.0)
    with pytest.raises(ValueError):
        contrast_ratio("nope", "#fff")


def test_contrast_levels() -> None:
    assert contrast_levels(4.5) == (True, True, False)
    assert contrast_levels(3.2) == (False, True, False)
    assert contrast_levels(7.0) == (True, True, True)


def test_to_oklch() -> None:
    brand = to_oklch(RGBA(79, 70, 229))
    assert brand.lightness == pytest.approx(0.5106, abs=1e-3)
    assert brand.chroma == pytest.approx(0.2301, abs=1e-3)
    assert brand.hue == pytest.approx(276.97, abs=0.1)
    white = to_oklch(WHITE)
    assert white.hue is None and white.lightness == pytest.approx(1.0)
    assert to_oklch(RGBA(128, 128, 128)).hue is None


def test_is_neutral() -> None:
    assert is_neutral(RGBA(71, 85, 105))  # slate-600 is a tinted neutral
    assert is_neutral(WHITE)
    assert not is_neutral(RGBA(79, 70, 229))


def test_delta_e_2000() -> None:
    a = RGBA(99, 91, 255)
    assert delta_e_2000(a, a) == 0
    assert delta_e_2000(a, RGBA(98, 90, 255)) < 1
    assert delta_e_2000(a, RGBA(98, 90, 255)) == delta_e_2000(RGBA(98, 90, 255), a)
    assert delta_e_2000(WHITE, BLACK) > 90
    assert delta_e_2000(RGBA(0, 0, 0, 0.1), BLACK) == 0  # alpha is compared separately


# ─── Media queries & misc ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("(min-width: 768px)", (768, None)),
        ("screen and (min-width:48em)", (768, None)),
        ("(max-width: 767.98px)", (None, 767.98)),
        ("(min-width: 640px) and (max-width: 1023px)", (640, 1023)),
        ("(width >= 1024px)", (1024, None)),
        ("(width < 40rem)", (None, 640)),
        ("(640px <= width < 1024px)", (640, 1024)),
        ("(1280px > width)", (None, 1280)),
        ("print", (None, None)),
        ("not all and (min-width: 768px)", (None, None)),
        ("print, (min-width: 1280px)", (1280, None)),
    ],
)
def test_parse_media_query_widths(query: str, expected: tuple[float | None, float | None]) -> None:
    result = parse_media_query(query)
    assert (result.min_width_px, result.max_width_px) == expected


def test_parse_media_query_color_scheme() -> None:
    assert parse_media_query("(prefers-color-scheme: dark)").prefers_color_scheme == "dark"
    assert parse_media_query("(prefers-color-scheme:light)").prefers_color_scheme == "light"
    assert parse_media_query("(min-width: 1px)").prefers_color_scheme is None


@pytest.mark.parametrize(
    ("value", "expected"), [("0.5", 0.5), ("50%", 0.5), ("2", 1.0), ("x", 1.0), (None, 1.0)]
)
def test_parse_opacity(value: str | None, expected: float) -> None:
    assert parse_opacity(value) == expected
