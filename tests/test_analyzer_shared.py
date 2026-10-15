"""Tests of the frozen helpers shared by all analyzers: analyzer/common.py and naming.py."""

from __future__ import annotations

import pytest

from get_design_markdown.analyzer.common import (
    ancestors,
    clean_class_name,
    effective_background,
    element_index,
    has_visible_border,
    is_hashed_class,
    is_heading,
    is_interactive,
    is_rendered,
    iter_elements,
    meaningful_classes,
    page_weight,
    short_selector,
    top_class_hints,
    visible_area,
)
from get_design_markdown.analyzer.css_values import RGBA, WHITE
from get_design_markdown.analyzer.naming import (
    COLOR_TOKEN_ORDER,
    COLOR_TOKEN_ROLES,
    COMPONENT_NAME_ORDER,
    RADIUS_STEP_NAMES,
    SPACING_STEP_NAMES,
    TYPE_LEVEL_ORDER,
    find_token_refs,
    is_token_name,
    match_color_token,
    match_scale_step,
    match_type_level,
    name_scale,
    parse_token_ref,
    slugify,
    state_variant_name,
    token_ref,
)
from get_design_markdown.models.analysis import ColorRole, ColorToken, ScaleStep, TypeLevel
from get_design_markdown.models.raw import BoundingBox, ComputedStyle, ElementSample, PageCapture
from tests.fixture_data import load_raw_sample


def _element(index: int, **kwargs: object) -> ElementSample:
    data: dict[str, object] = {
        "index": index,
        "tag": "div",
        "box": BoundingBox(x=0, y=0, width=100, height=10),
    }
    data.update(kwargs)
    return ElementSample.model_validate(data)


# ─── common.py ───────────────────────────────────────────────────────────────


def test_page_weight() -> None:
    assert page_weight(0) == 1.0
    assert page_weight(3) == 0.5


def test_iter_elements_skips_overlays_and_invisible() -> None:
    raw = load_raw_sample()
    page = raw.pages[0]
    refs = list(iter_elements([page]))
    assert all(not ref.element.in_overlay for ref in refs)
    assert len(refs) == len([e for e in page.elements if not e.in_overlay])
    with_overlays = list(iter_elements([page], include_overlays=True))
    assert len(with_overlays) == len(page.elements)
    second = page.model_copy(update={"name": "pricing"})
    weights = {ref.page.name: ref.weight for ref in iter_elements([page, second])}
    assert weights == {"home": 1.0, "pricing": 0.5}


def test_is_rendered() -> None:
    assert is_rendered(_element(0))
    assert not is_rendered(_element(0, box=BoundingBox(x=0, y=0, width=0, height=10)))
    assert not is_rendered(_element(0, styles=ComputedStyle(opacity="0")))
    assert not is_rendered(_element(0, styles=ComputedStyle(display="none")))


def test_visible_area_is_clipped_to_viewport() -> None:
    wide = _element(0, box=BoundingBox(x=-100, y=0, width=2000, height=10))
    assert visible_area(wide) == 1440 * 10
    assert visible_area(wide, max_height=5) == 1440 * 5


def test_ancestors_and_effective_background() -> None:
    page = PageCapture(
        url="https://x.test/",
        final_url="https://x.test/",
        elements=[
            _element(0, styles=ComputedStyle(background_color="rgb(15, 23, 42)")),
            _element(1, parent_index=0, styles=ComputedStyle(background_color="rgba(0,0,0,0)")),
            _element(
                2, parent_index=1, styles=ComputedStyle(background_color="rgba(255,255,255,0.8)")
            ),
            _element(3, parent_index=2),
            _element(4, parent_index=99),
        ],
    )
    index = element_index(page)
    assert [node.index for node in ancestors(page.elements[3], index)] == [2, 1, 0]
    assert effective_background(page.elements[1], index) == RGBA(15, 23, 42, 1.0)
    assert effective_background(page.elements[3], index) == RGBA(207, 209, 212, 1.0)
    assert effective_background(page.elements[4], index) == WHITE


def test_has_visible_border() -> None:
    bordered = ComputedStyle(
        border_width=("0px", "0px", "1px", "0px"),
        border_style=("none", "none", "solid", "none"),
        border_color=("red", "red", "rgb(226, 232, 240)", "red"),
    )
    assert has_visible_border(_element(0, styles=bordered))
    invisible = ComputedStyle(
        border_width=("1px", "1px", "1px", "1px"),
        border_style=("solid", "solid", "solid", "solid"),
        border_color=("transparent",) * 4,
    )
    assert not has_visible_border(_element(0, styles=invisible))
    assert not has_visible_border(_element(0))


def test_element_predicates() -> None:
    assert is_heading(_element(0, tag="h2"))
    assert is_heading(_element(0, role="heading"))
    assert is_interactive(_element(0, tag="button"))
    assert is_interactive(_element(0, styles=ComputedStyle(cursor="pointer")))
    assert not is_interactive(_element(0, tag="p"))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("btn-primary", "btn-primary"),
        ("Button_primary__3xK9z", "button-primary"),
        ("css-1q2w3e4", None),
        ("css-1q2w3e4-Button", "button"),
        ("sc-bdVaJa", None),
        ("kKDMPh", None),
        ("jsx-1234567890", None),
        ("navLink", "navLink"),
        ("MyButton", "MyButton"),
        ("md:px-4", "md:px-4"),
        ("col-12", "col-12"),
        ("card__title--large", "card__title--large"),
        ("", None),
    ],
)
def test_clean_class_name(name: str, expected: str | None) -> None:
    assert clean_class_name(name) == expected
    assert is_hashed_class(name) is (expected is None)


def test_selectors_and_class_hints() -> None:
    element = _element(
        0, tag="a", id="cta", classes=["btn", "css-1q2w3e4", "btn-primary", "btn-lg"]
    )
    assert meaningful_classes(element.classes) == ["btn", "btn-primary", "btn-lg"]
    assert short_selector(element) == "a#cta.btn.btn-primary"
    assert short_selector(_element(1, tag="input", id="r1:abc")) == "input"
    other = _element(1, classes=["btn", "btn-secondary"])
    assert top_class_hints([element, other], limit=2) == ["btn", "btn-primary"]


# ─── naming.py ───────────────────────────────────────────────────────────────


def test_canonical_names_are_valid_token_names() -> None:
    for name in (
        *COLOR_TOKEN_ORDER,
        *TYPE_LEVEL_ORDER,
        *SPACING_STEP_NAMES,
        *RADIUS_STEP_NAMES,
        *COMPONENT_NAME_ORDER,
    ):
        assert is_token_name(name), name
    assert COLOR_TOKEN_ROLES["primary"] is ColorRole.BRAND
    assert COLOR_TOKEN_ROLES["hairline"] is ColorRole.BORDER


def test_token_refs() -> None:
    assert token_ref("colors", "on-primary") == "{colors.on-primary}"
    assert parse_token_ref(" {rounded.md} ") == ("rounded", "md")
    assert parse_token_ref("{shadows.md}") is None
    assert parse_token_ref("#ffffff") is None
    text = "Use `{colors.primary}` on {colors.canvas}; pad {spacing.md}."
    assert find_token_refs(text) == [("colors", "primary"), ("colors", "canvas"), ("spacing", "md")]
    with pytest.raises(ValueError):
        token_ref("shadows", "md")
    with pytest.raises(ValueError):
        token_ref("colors", "Primary")


def test_slugify_and_state_names() -> None:
    assert slugify("Button Primary!") == "button-primary"
    assert slugify("***") == "item"
    assert state_variant_name("button-primary") == "button-primary-hover"


def test_name_scale() -> None:
    assert name_scale([4, 8, 16, 24], SPACING_STEP_NAMES, anchor_value=16) == [
        "xs",
        "sm",
        "md",
        "lg",
    ]
    assert name_scale([6, 8, 12, 16], RADIUS_STEP_NAMES, anchor_value=8) == [
        "sm",
        "md",
        "lg",
        "xl",
    ]
    long_scale = name_scale(list(range(10)), SPACING_STEP_NAMES)
    assert long_scale[:2] == ["xxs", "xs"]
    assert long_scale[-2:] == ["4xl", "5xl"]
    assert name_scale([], SPACING_STEP_NAMES) == []


def test_match_color_token_prefers_roles_and_alpha() -> None:
    tokens = [
        ColorToken(name="canvas", value="#ffffff", role=ColorRole.BACKGROUND),
        ColorToken(name="on-primary", value="#ffffff", role=ColorRole.TEXT),
        ColorToken(name="primary", value="#4f46e5", role=ColorRole.BRAND),
        ColorToken(name="hairline", value="#0f172a1a", role=ColorRole.BORDER),
    ]
    assert match_color_token("rgb(255, 255, 255)", tokens) == tokens[0]
    prefer_text = match_color_token("#fff", tokens, prefer_roles={ColorRole.TEXT})
    assert prefer_text == tokens[1]
    assert match_color_token("#4e46e5", tokens) == tokens[2]
    assert match_color_token("#3b82f6", tokens) is None
    assert match_color_token("rgba(15, 23, 42, 0.1)", tokens) == tokens[3]
    assert match_color_token("#0f172a", tokens) is None
    assert match_color_token("nope", tokens) is None


def test_match_color_token_exact_match_beats_role_preference() -> None:
    tokens = [
        ColorToken(name="surface-inverse", value="#0b1020", role=ColorRole.SURFACE),
        ColorToken(name="ink", value="#0f172a", role=ColorRole.TEXT),
        ColorToken(name="on-primary", value="#ffffff", role=ColorRole.TEXT),
        ColorToken(name="canvas", value="#ffffff", role=ColorRole.BACKGROUND),
    ]
    background_roles = {ColorRole.BACKGROUND, ColorRole.SURFACE}
    assert match_color_token("#0f172a", tokens, prefer_roles=background_roles) == tokens[1]
    assert match_color_token("#0c1121", tokens, prefer_roles=background_roles) == tokens[0]
    assert match_color_token("#ffffff", tokens, prefer_roles=background_roles) == tokens[3]
    assert match_color_token("#ffffff", tokens, prefer_roles={ColorRole.TEXT}) == tokens[2]


def test_match_scale_step() -> None:
    steps = [
        ScaleStep(name="sm", value_px=6),
        ScaleStep(name="md", value_px=8),
        ScaleStep(name="full", value_px=9999),
    ]
    assert match_scale_step(8.2, steps) == steps[1]
    assert match_scale_step(7, steps) is None
    assert match_scale_step(999, steps) == steps[2]
    assert match_scale_step(999, steps[:2]) is None


def test_match_type_level() -> None:
    levels = [
        TypeLevel(name="ui", size_px=15, font_family="Inter", weight=500),
        TypeLevel(name="body", size_px=16, font_family="Inter", weight=400),
        TypeLevel(name="code", size_px=14, font_family="JetBrains Mono", weight=400),
        TypeLevel(name="body-sm", size_px=14, font_family="Inter", weight=400),
    ]
    assert match_type_level(15, 500, levels) == levels[0]
    assert match_type_level(14, 400, levels, family="Inter") == levels[3]
    assert match_type_level(14, 400, levels, family="JetBrains Mono") == levels[2]
    assert match_type_level(18, 400, levels) is None
