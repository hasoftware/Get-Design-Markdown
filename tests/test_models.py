from __future__ import annotations

import pytest
from pydantic import ValidationError

from get_design_markdown.models.analysis import ColorToken, ComponentSpec, DesignAnalysis
from get_design_markdown.models.raw import (
    BoundingBox,
    ElementSample,
    RawCapture,
    join_sides,
    split_sides,
)
from get_design_markdown.models.synthesis import SynthesisResult, ValidationIssue
from tests.conftest import FIXED_TIME


def test_raw_capture_round_trip(raw_capture: RawCapture) -> None:
    restored = RawCapture.model_validate_json(raw_capture.model_dump_json())
    assert restored == raw_capture
    assert restored.main_page is not None
    assert restored.main_page.hover_states[0].changed["background_color"][1] == "rgb(10, 37, 64)"


def test_design_analysis_round_trip(design_analysis: DesignAnalysis) -> None:
    restored = DesignAnalysis.model_validate_json(design_analysis.model_dump_json())
    assert restored == design_analysis


def test_synthesis_result_round_trip() -> None:
    result = SynthesisResult(
        tool_version="0.1.0",
        mode="rules",
        generated_at=FIXED_TIME,
        issues=[ValidationIssue(rule="contrast-ratio", severity="warning", message="2.81:1")],
        design_md_path="DESIGN.md",
    )
    assert SynthesisResult.model_validate_json(result.model_dump_json()) == result


def test_unknown_fields_are_rejected(raw_capture: RawCapture) -> None:
    data = raw_capture.model_dump(mode="json")
    data["unexpected"] = 1
    with pytest.raises(ValidationError):
        RawCapture.model_validate(data)


def test_element_text_is_collapsed_and_truncated() -> None:
    element = ElementSample(
        index=0,
        tag="p",
        text="  Lorem   ipsum\n" + "x" * 100,
        box=BoundingBox(x=0, y=0, width=10, height=10),
    )
    assert element.text.startswith("Lorem ipsum x")
    assert len(element.text) == 50


def test_bounding_box_area_ignores_negative_sizes() -> None:
    assert BoundingBox(x=0, y=0, width=20, height=5).area == 100
    assert BoundingBox(x=0, y=0, width=-3, height=5).area == 0


@pytest.mark.parametrize("value", ["#635bff", "#635bff80"])
def test_hex_color_accepts_lowercase_hex(value: str) -> None:
    assert ColorToken(name="primary", value=value, role="brand").value == value


@pytest.mark.parametrize("value", ["#635BFF", "635bff", "#fff", "rgb(0,0,0)"])
def test_hex_color_rejects_other_formats(value: str) -> None:
    with pytest.raises(ValidationError):
        ColorToken(name="primary", value=value, role="brand")


@pytest.mark.parametrize("name", ["Button Primary", "button_primary", "-button", "button-"])
def test_token_names_must_be_kebab_case(name: str) -> None:
    with pytest.raises(ValidationError):
        ComponentSpec(name=name, kind="button")


def test_join_and_split_sides() -> None:
    assert join_sides(("8px", "8px", "8px", "8px")) == "8px"
    joined = join_sides(("rgb(0, 0, 0)", "red", "rgb(0, 0, 0)", "red"))
    assert joined == "rgb(0, 0, 0) | red | rgb(0, 0, 0) | red"
    assert split_sides(joined) == ("rgb(0, 0, 0)", "red", "rgb(0, 0, 0)", "red")
    assert split_sides("8px") == ("8px", "8px", "8px", "8px")


def test_color_cluster_requires_a_channel() -> None:
    from get_design_markdown.models.analysis import ColorCluster

    with pytest.raises(ValidationError):
        ColorCluster.model_validate({"id": "c1", "hex": "#ffffff"})
    cluster = ColorCluster(id="c1", channel="border", hex="#e2e8f0")
    assert cluster.usage.border == 0.0
