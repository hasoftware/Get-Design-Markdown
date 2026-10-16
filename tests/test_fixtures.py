"""The shared fixtures are valid, in sync with their generator and internally consistent."""

from __future__ import annotations

from get_design_markdown.analyzer.css_values import contrast_ratio, parse_color
from get_design_markdown.analyzer.naming import (
    COLOR_TOKEN_ROLES,
    TOKEN_GROUPS,
    TYPE_LEVEL_ORDER,
    find_token_refs,
)
from tests.fixture_data import (
    load_analysis_sample,
    load_raw_sample,
    load_site_contract,
)
from tests.fixtures import build_samples


def test_fixture_files_match_generator() -> None:
    for path, text in build_samples.render_samples().items():
        assert path.read_text(encoding="utf-8") == text, (
            f"{path.name} drifted — run: python -m uv run python -m tests.fixtures.build_samples"
        )


def test_raw_sample_shape() -> None:
    raw = load_raw_sample()
    page = raw.pages[0]
    assert 60 <= len(page.elements) <= 100
    assert raw.dark is not None and raw.dark.color_scheme == "dark"
    assert [element.index for element in page.elements] == list(range(len(page.elements)))
    for element in page.elements:
        if element.parent_index is not None:
            assert element.parent_index < element.index
    assert {layout.viewport_width for layout in page.layouts} == {1440, 768, 390}
    assert any(element.in_overlay for element in page.elements)
    assert len(raw.stylesheets) == 3
    kinds = {state.kind for state in page.hover_states}
    assert kinds == {"button", "link"}
    for state in page.hover_states:
        assert 0 <= state.element_index < len(page.elements)
        assert state.changed


def test_loaders_return_fresh_copies() -> None:
    first = load_raw_sample()
    first.pages.clear()
    assert load_raw_sample().pages


def test_analysis_sample_is_complete() -> None:
    analysis = load_analysis_sample()
    assert analysis.site.name == "Acme Cloud"
    assert 8 <= len(analysis.colors.tokens) <= 14
    assert analysis.typography.levels and analysis.spacing.steps and analysis.radii
    assert analysis.shadows and analysis.breakpoints and analysis.layout.changes
    assert analysis.components and analysis.dark is not None
    assert analysis.dark.colors.scheme == "dark"
    assert analysis.screenshots
    assert any(component.states.get("hover") for component in analysis.components)
    assert any(not pair.passes_aa for pair in analysis.colors.contrast_pairs)


def test_analysis_sample_token_refs_resolve() -> None:
    analysis = load_analysis_sample()
    names = {
        "colors": {token.name for token in analysis.colors.tokens},
        "typography": {level.name for level in analysis.typography.levels},
        "rounded": {step.name for step in analysis.radii},
        "spacing": {step.name for step in analysis.spacing.steps},
        "components": {component.name for component in analysis.components},
    }
    assert set(names) == set(TOKEN_GROUPS)
    for component in analysis.components:
        refs = list(component.token_refs.values())
        refs += [ref for state in component.state_token_refs.values() for ref in state.values()]
        for group, name in find_token_refs(" ".join(refs)):
            assert name in names[group], f"{component.name}: {{{group}.{name}}}"
        for key in component.token_refs:
            assert key == "typography" or key in component.properties


def test_analysis_sample_is_consistent_with_css_values() -> None:
    analysis = load_analysis_sample()
    for token in analysis.colors.tokens:
        assert COLOR_TOKEN_ROLES[token.name] is token.role
    for pair in analysis.colors.contrast_pairs:
        expected = contrast_ratio(pair.foreground, pair.background)
        assert abs(pair.ratio - expected) < 0.01
    for component in analysis.components:
        background = component.properties.get("backgroundColor", "#ffffff")
        text = component.properties.get("textColor")
        if component.contrast_ratio is not None and text and background != "transparent":
            assert parse_color(background) is not None
            assert abs(component.contrast_ratio - contrast_ratio(text, background)) < 0.01
    sizes = [level.size_px for level in analysis.typography.levels]
    assert sizes == sorted(sizes, reverse=True)
    assert all(level.name in TYPE_LEVEL_ORDER for level in analysis.typography.levels)


def test_site_contract_matches_analysis_sample() -> None:
    contract = load_site_contract()
    analysis = load_analysis_sample()
    assert contract.palette == {token.name: token.value for token in analysis.colors.tokens}
    assert contract.components == [component.name for component in analysis.components]
    assert contract.breakpoints_px == [
        int(point.min_width_px or 0) for point in analysis.breakpoints
    ]
    assert "index.html" in contract.pages


def test_analysis_sample_component_property_rules() -> None:
    """Property-inclusion rules analyzer/components.py must follow."""
    font_keys = {"fontFamily", "fontSize", "fontWeight", "lineHeight"}
    for component in load_analysis_sample().components:
        keys = set(component.properties)
        if component.kind in {"button", "link", "input", "badge"}:
            assert {"textColor", *font_keys} <= keys, component.name
        else:
            assert not keys & font_keys, component.name
        if component.kind in {"button", "input", "card", "badge", "navigation", "footer"}:
            assert "backgroundColor" in keys, component.name
        if component.kind == "link":
            assert "textDecoration" in keys, component.name
