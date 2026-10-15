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
