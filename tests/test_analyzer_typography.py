"""Tests of analyzer/typography.py: families, semantic type levels, scale ratio."""

from __future__ import annotations

from typing import Any

import pytest

from get_design_markdown.analyzer.common import element_index
from get_design_markdown.analyzer.naming import TYPE_LEVEL_ORDER
from get_design_markdown.analyzer.typography import (
    analyze_typography,
    collect_samples,
    family_of_stack,
    fit_scale_ratio,
    round_half,
    semantic_tag,
)
from get_design_markdown.models.analysis import FontRole, Typography
from get_design_markdown.models.raw import (
    BoundingBox,
    ComputedStyle,
    ElementSample,
    FontFace,
    PageCapture,
    StylesheetInfo,
)
from tests.fixture_data import load_analysis_sample, load_raw_sample, load_site_contract

SANS = "Inter, system-ui, sans-serif"


def _el(
    index: int,
    tag: str = "p",
    text: str = "",
    *,
    parent: int | None = None,
    box: tuple[float, float, float, float] = (0, 0, 400, 24),
    own: bool | None = None,
    classes: tuple[str, ...] = (),
    role: str | None = None,
    landmark: str | None = None,
    in_overlay: bool = False,
    **styles: Any,
) -> ElementSample:
    styles.setdefault("font_family", SANS)
    x, y, width, height = box
    return ElementSample(
        index=index,
        tag=tag,
        text=text,
        has_own_text=bool(text) if own is None else own,
        parent_index=parent,
        box=BoundingBox(x=x, y=y, width=width, height=height),
        classes=list(classes),
        role=role,
        landmark=landmark,
        in_overlay=in_overlay,
        styles=ComputedStyle(**styles),
    )


def _page(elements: list[ElementSample], name: str = "home") -> PageCapture:
    return PageCapture(
        url=f"https://x.test/{name}",
        final_url=f"https://x.test/{name}",
        name=name,
        elements=elements,
    )


def _texts(
    start: int, count: int, tag: str, size: str, text: str = "Some sample text here", **styles: Any
) -> list[ElementSample]:
    return [_el(start + i, tag, text, font_size=size, **styles) for i in range(count)]


# ─── Fixture (golden answers) ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fixture_typography() -> Typography:
    raw = load_raw_sample()
    return analyze_typography(raw.pages, raw.stylesheets)


def test_fixture_families(fixture_typography: Typography) -> None:
    expected = load_analysis_sample().typography.families
    got = fixture_typography.families
    assert [(f.name, f.role) for f in got] == [
        ("Inter", FontRole.PRIMARY),
        ("Space Grotesk", FontRole.HEADING),
        ("JetBrains Mono", FontRole.MONO),
    ]
    for family, reference in zip(got, expected, strict=True):
        assert family.stack == reference.stack
        assert family.category == reference.category
        assert family.weights == reference.weights
        assert family.is_google_font is True
        assert family.substitutes == []
        assert 0 < family.share <= 1
    assert got[0].share > got[1].share > got[2].share
    contract = load_site_contract().fonts
    assert {f.role.value: f.name for f in got} == contract


def test_fixture_levels_match_contract(fixture_typography: Typography) -> None:
    levels = fixture_typography.levels
    assert {level.name: level.size_px for level in levels} == load_site_contract().type_levels_px
    sizes = [level.size_px for level in levels]
    assert sizes == sorted(sizes, reverse=True)
    assert len({level.name for level in levels}) == len(levels)
    assert all(level.name in TYPE_LEVEL_ORDER for level in levels)


def test_fixture_levels_match_reference(fixture_typography: Typography) -> None:
    expected = {level.name: level for level in load_analysis_sample().typography.levels}
    for level in fixture_typography.levels:
        reference = expected[level.name]
        assert level.font_family == reference.font_family, level.name
        assert level.weight == reference.weight, level.name
        assert level.line_height_px == reference.line_height_px, level.name
        assert level.line_height == reference.line_height, level.name
        assert level.letter_spacing_px == reference.letter_spacing_px, level.name
        assert level.text_transform == reference.text_transform, level.name
        assert level.tags == reference.tags, level.name
        assert level.count == reference.count, level.name
        assert 1 <= len(level.samples) <= 3
    assert [level.name for level in fixture_typography.levels] == list(expected)


def test_fixture_scale(fixture_typography: Typography) -> None:
    assert fixture_typography.base_size_px == 16
    assert set(fixture_typography.weights_used) >= {400, 500, 600, 700}
    assert fixture_typography.weights_used == sorted(fixture_typography.weights_used)
    assert fixture_typography.scale_ratio == 1.25
    assert fixture_typography.scale_ratio_name == "major third"


def test_fixture_overlay_text_is_ignored() -> None:
    raw = load_raw_sample()
    samples = collect_samples(raw.pages)
    texts = {sample.text for sample in samples}
    assert "Accept" not in texts
    assert not any("cookies" in text for text in texts)


# ─── Unit tests ──────────────────────────────────────────────────────────────


def test_empty_inputs() -> None:
    assert analyze_typography([]) == Typography()
    assert analyze_typography([_page([_el(0, "div", "", own=False)])]) == Typography()


def test_round_half() -> None:
    assert round_half(15.33) == 15.5
    assert round_half(13.2) == 13.0
    assert round_half(16) == 16.0


@pytest.mark.parametrize(
    ("stack", "family"),
    [
        (["Inter", "sans-serif"], "Inter"),
        (["system-ui", "-apple-system", "Segoe UI", "Roboto"], "system-ui"),
        (["-apple-system", "BlinkMacSystemFont", "Segoe UI"], "system-ui"),
        (["ui-sans-serif", "system-ui", "sans-serif", "Apple Color Emoji"], "system-ui"),
        (["Apple Color Emoji", "Inter"], None),
        (["Material Icons"], None),
        (["Inter", "Apple Color Emoji"], "Inter"),
        (["sans-serif"], "sans-serif"),
        ([], None),
    ],
)
def test_family_of_stack(stack: list[str], family: str | None) -> None:
    assert family_of_stack(stack) == family


def test_semantic_tag_resolves_inline_wrappers() -> None:
    elements = [
        _el(0, "h1", own=False),
        _el(1, "span", "Hero", parent=0),
        _el(2, "button", own=False),
        _el(3, "span", "Buy", parent=2),
        _el(4, "a", own=False),
        _el(5, "div", own=False, parent=4),
        _el(6, "span", "Card", parent=5),
        _el(7, "div", own=False),
        _el(8, "span", "Plain", parent=7),
        _el(9, "div", "Div text", parent=4),
    ]
    index = element_index(_page(elements))
    assert semantic_tag(elements[1], index) == "h1"
    assert semantic_tag(elements[3], index) == "button"
    assert semantic_tag(elements[6], index) == "a"
    assert semantic_tag(elements[8], index) == "span"
    assert semantic_tag(elements[9], index) == "div"


def test_samples_skip_hidden_icon_and_decorative_text() -> None:
    elements = [
        _el(0, "p", "Visible paragraph text", font_size="16px"),
        _el(1, "span", "Skip to content", box=(0, 0, 1, 1)),
        _el(2, "i", "arrow_forward", font_family='"Material Icons"'),
        _el(3, "div", own=False, classes=("payments-graphic",)),
        _el(4, "span", "Mock label", parent=3, font_size="10px"),
        _el(5, "div", own=False, role="img"),
        _el(6, "span", "Chart label", parent=5),
        _el(7, "p", "Faded out", opacity="0"),
        _el(8, "p", "Tiny", font_size="4px"),
    ]
    assert [sample.text for sample in collect_samples([_page(elements)])] == [
        "Visible paragraph text"
    ]


def test_proprietary_font_with_google_fallback_in_stack() -> None:
    stack = 'Graphik, "Work Sans", sans-serif'
    elements = _texts(0, 4, "p", "16px", font_family=stack)
    families = analyze_typography([_page(elements)]).families
    assert families[0].name == "Graphik"
    assert not families[0].is_google_font
    assert families[0].substitutes == ["Work Sans", "Inter", "Figtree"]
    assert families[0].stack == ["Graphik", "Work Sans", "sans-serif"]


def test_unknown_font_category_from_stack_and_google_face() -> None:
    serif = _texts(0, 3, "p", "18px", font_family='"Acme Text", Georgia, serif')
    result = analyze_typography([_page(serif)])
    assert result.families[0].category == "serif"
    assert result.families[0].substitutes == ["Source Serif 4", "Lora"]

    face = FontFace(family="Acme Text", src=["url(https://fonts.gstatic.com/s/acme.woff2)"])
    sheets = [StylesheetInfo(url="https://x.test/a.css", font_faces=[face])]
    with_face = analyze_typography([_page(serif)], sheets)
    assert with_face.families[0].is_google_font is True
    assert with_face.families[0].substitutes == []


def test_roles_heading_display_mono() -> None:
    elements = [
        _el(0, "h1", "Huge display headline", font_size="72px", font_family='"Big Shoulders"'),
        *_texts(1, 3, "h2", "40px", "Section heading text", font_family='"Space Grotesk"'),
        *_texts(10, 6, "p", "16px", "Body copy that is long enough to dominate the page"),
        _el(20, "code", "npm i thing", font_size="14px", font_family='"Fira Code", monospace'),
    ]
    families = analyze_typography([_page(elements)]).families
    assert [(f.name, f.role) for f in families] == [
        ("Inter", FontRole.PRIMARY),
        ("Space Grotesk", FontRole.HEADING),
        ("Big Shoulders", FontRole.DISPLAY),
        ("Fira Code", FontRole.MONO),
    ]


def test_heading_role_omitted_when_headings_use_primary() -> None:
    elements = [
        *_texts(0, 2, "h1", "48px", "Primary family headline"),
        *_texts(5, 6, "p", "16px"),
    ]
    roles = [f.role for f in analyze_typography([_page(elements)]).families]
    assert roles == [FontRole.PRIMARY]


def test_sub_pages_weigh_half() -> None:
    main = _page(_texts(0, 4, "p", "16px", "x" * 25), "home")
    sub = _page(_texts(0, 6, "p", "16px", "y" * 25, font_family="Lora, serif"), "blog")
    families = analyze_typography([main, sub]).families
    assert families[0].name == "Inter"  # 100 chars vs 6 x 25 x 0.5 = 75
    swapped = analyze_typography([sub, main]).families
    assert swapped[0].name == "Lora"


def test_below_body_names() -> None:
    elements = [
        *_texts(0, 8, "p", "16px", "Regular body paragraph text"),
        *_texts(10, 5, "a", "15px", "Nav link", font_weight="500"),
        *_texts(20, 4, "span", "12px", "LABEL", text_transform="uppercase", letter_spacing="1px"),
        *_texts(30, 3, "p", "14px", "Smaller paragraph"),
        *_texts(40, 3, "p", "13px", "Fine print text"),
        *_texts(50, 2, "code", "13px", "let x = 1", font_family="Menlo, monospace"),
    ]
    levels = analyze_typography([_page(elements)]).levels
    assert [(level.name, level.size_px) for level in levels] == [
        ("body", 16.0),
        ("ui", 15.0),
        ("body-sm", 14.0),
        ("caption", 13.0),
        ("code", 13.0),
        ("overline", 12.0),
    ]
    overline = next(level for level in levels if level.name == "overline")
    assert overline.text_transform == "uppercase" and overline.letter_spacing_px == 1.0
    code = next(level for level in levels if level.name == "code")
    assert code.font_family == "Menlo"


def test_middle_small_groups_merge_into_named_levels() -> None:
    elements = [
        *_texts(0, 8, "p", "16px", "Regular body paragraph text"),
        *_texts(10, 4, "p", "15px", "Slightly smaller text"),
        *_texts(20, 2, "p", "14px", "Even smaller"),
        *_texts(30, 3, "p", "12px", "Fine print"),
    ]
    levels = analyze_typography([_page(elements)]).levels
    assert [(level.name, level.size_px) for level in levels] == [
        ("body", 16.0),
        ("body-sm", 15.0),
        ("caption", 12.0),
    ]
    assert levels[1].count == 6


def test_h1_absorbs_one_off_larger_headline() -> None:
    # linear.app: a closing 72px h2 is larger than the 64px hero h1 (text in spans).
    elements = [
        _el(0, "h1", own=False, font_size="64px"),
        _el(1, "span", "The product development", parent=0, font_size="64px"),
        _el(2, "span", "system for teams and agents", parent=0, font_size="64px"),
        _el(3, "h2", "Built for the future.", font_size="72px"),
        *_texts(10, 4, "h2", "48px", "A new species of tool"),
        *_texts(20, 8, "p", "15px", "Purpose-built for planning and building products."),
    ]
    levels = analyze_typography([_page(elements)]).levels
    assert [(level.name, level.size_px) for level in levels] == [
        ("display", 64.0),
        ("heading", 48.0),
        ("body", 15.0),
    ]
    assert levels[0].tags == ["h1", "h2"]
    assert levels[0].count == 3


def test_rare_display_text_survives_crowded_small_sizes() -> None:
    # stripe.com: dozens of small text groups must not evict the 48px hero headline.
    elements: list[ElementSample] = [
        _el(0, "h1", own=False, font_size="48px"),
        _el(1, "em", "Financial infrastructure", parent=0, font_size="48px"),
    ]
    sizes = ["9px", "10px", "11px", "12px", "13px", "14px", "18px", "20px", "22px", "26px", "32px"]
    for position, size in enumerate(sizes):
        elements.extend(_texts(100 + position * 10, 6, "p", size, "z" * 50))
    elements.extend(_texts(300, 20, "p", "16px", "w" * 50))
    levels = analyze_typography([_page(elements)]).levels
    assert levels[0].name == "display" and levels[0].size_px == 48.0
    assert len(levels) <= 10
    assert len({level.name for level in levels}) == len(levels)


def test_body_is_never_renamed_code() -> None:
    elements = [
        _el(0, "p", "Lonely sans paragraph", font_size="16px"),
        *_texts(1, 4, "pre", "14px", "let value = compute()", font_family="Menlo, monospace"),
    ]
    levels = analyze_typography([_page(elements)]).levels
    assert [(level.name, level.font_family) for level in levels] == [("body", "Menlo")]


def test_one_off_small_groups_are_dropped() -> None:
    elements = [
        *_texts(0, 6, "p", "16px"),
        _el(10, "a", "Logo", font_size="20px", font_family='"Space Grotesk"'),
        _el(11, "h2", "Single h2", font_size="30px"),
    ]
    levels = analyze_typography([_page(elements)]).levels
    assert [(level.name, level.size_px) for level in levels] == [("heading", 30.0), ("body", 16.0)]


def test_line_height_normal_and_letter_spacing_em() -> None:
    elements = _texts(0, 3, "p", "20px", line_height="normal", letter_spacing="-0.02em")
    level = analyze_typography([_page(elements)]).levels[0]
    assert level.line_height_px == 24.0 and level.line_height == 1.2
    assert level.letter_spacing_px == -0.4


# ─── Scale ratio ─────────────────────────────────────────────────────────────


def test_fit_scale_ratio_fixture_sizes() -> None:
    fit = fit_scale_ratio([64, 40, 24, 20, 16, 15, 14, 13], 16)
    assert fit is not None and (fit.ratio, fit.name) == (1.25, "major third")


def test_fit_scale_ratio_exact_progression() -> None:
    sizes = [round_half(16 * 1.2**step) for step in range(1, 6)]
    fit = fit_scale_ratio(sizes, 16)
    assert fit is not None and (fit.ratio, fit.name) == (1.2, "minor third")
    fourth = [16 * 1.333**step for step in range(1, 5)]
    fit = fit_scale_ratio(fourth, 16)
    assert fit is not None and fit.name == "perfect fourth"


@pytest.mark.parametrize(
    "sizes",
    [
        [17, 21, 26.5, 35, 45, 57],
        [18, 20, 24, 30, 36, 48, 60, 72],  # Tailwind's text scale is not modular
        [18, 22, 26, 32, 48],  # stripe.com
        [24, 32, 48, 64],  # linear.app with base 15
    ],
)
def test_fit_scale_ratio_rejects_irregular_sizes(sizes: list[float]) -> None:
    base = 15 if sizes[0] == 24 else 16
    assert fit_scale_ratio(sizes, base) is None


def test_fit_scale_ratio_needs_three_sizes() -> None:
    assert fit_scale_ratio([20, 25], 16) is None
    assert fit_scale_ratio([12, 14], 16) is None
    assert fit_scale_ratio([20, 25, 31.25], 0) is None
