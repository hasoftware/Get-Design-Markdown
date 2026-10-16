from __future__ import annotations

from pathlib import Path

import pytest

from get_design_markdown.config import Settings, load_settings
from get_design_markdown.errors import ConfigError


def test_defaults() -> None:
    assert load_settings({}) == Settings()


def test_environment_overrides() -> None:
    settings = load_settings(
        {"GDM_OUTPUT_DIR": "out", "GDM_MAX_ELEMENTS": "500", "GDM_TIMEOUT": "12.5"}
    )
    assert settings == Settings(output_dir=Path("out"), max_elements=500, timeout_s=12.5)


@pytest.mark.parametrize(
    ("name", "value"),
    [("GDM_MAX_ELEMENTS", "abc"), ("GDM_MAX_ELEMENTS", "0"), ("GDM_TIMEOUT", "-1")],
)
def test_invalid_values_raise_config_error(name: str, value: str) -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({name: value})
    assert name in info.value.message
    assert info.value.hint is not None and info.value.hint.startswith(f"Ví dụ: {name}=")


def test_non_positive_values_suggest_the_default() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({"GDM_MAX_ELEMENTS": "0"})
    assert info.value.hint == "Ví dụ: GDM_MAX_ELEMENTS=3000"
    with pytest.raises(ConfigError) as info:
        load_settings({"GDM_TIMEOUT": "0"})
    assert info.value.hint == "Ví dụ: GDM_TIMEOUT=45"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", "auto"),
        ("auto", "auto"),
        ("chromium", "chromium"),
        (" MSEdge ", "msedge"),
        ("Chrome", "chrome"),
    ],
)
def test_browser_choice_from_environment(value: str, expected: str) -> None:
    assert load_settings({"GDM_BROWSER": value}).browser == expected


def test_default_browser_is_auto() -> None:
    assert Settings().browser == "auto"


def test_unknown_browser_raises_config_error_with_choices() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({"GDM_BROWSER": "firefox"})
    assert "GDM_BROWSER" in info.value.message
    assert info.value.hint is not None
    for choice in ("auto", "chromium", "msedge", "chrome"):
        assert choice in info.value.hint


def test_env_example_documents_every_variable_with_its_default() -> None:
    example = Path(__file__).resolve().parents[1] / ".env.example"
    values = dict(
        line.split("=", 1)
        for line in example.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert set(values) == {"GDM_OUTPUT_DIR", "GDM_MAX_ELEMENTS", "GDM_TIMEOUT", "GDM_BROWSER"}
    assert load_settings(values) == Settings()
