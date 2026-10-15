"""Loaders for the shared, read-only fixtures (frozen; regenerate with
`python -m uv run python -m tests.fixtures.build_samples`).

- raw_sample.json      RawCapture of "Acme Cloud" (light page + dark capture, 3 stylesheets)
- analysis_sample.json the reference DesignAnalysis of that page (what the analyzers should find)
- site_contract.json   design contract of the static fixture site tests/fixtures/site/
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from get_design_markdown.models.analysis import DesignAnalysis
from get_design_markdown.models.raw import RawCapture

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SITE_DIR = FIXTURES_DIR / "site"
RAW_SAMPLE_PATH = FIXTURES_DIR / "raw_sample.json"
ANALYSIS_SAMPLE_PATH = FIXTURES_DIR / "analysis_sample.json"
SITE_CONTRACT_PATH = FIXTURES_DIR / "site_contract.json"


class SiteContract(BaseModel):
    """Expected design of tests/fixtures/site (same design as raw_sample.json)."""

    model_config = ConfigDict(extra="forbid")

    description: str
    palette: dict[str, str]
    dark_palette: dict[str, str]
    decorative_colors: list[str]
    overlay_colors: list[str]
    fonts: dict[str, str]
    type_levels_px: dict[str, float]
    spacing: dict[str, Any]
    radii_px: list[float]
    shadow_levels: int
    breakpoints_px: list[int]
    components: list[str]
    hover: dict[str, dict[str, str]]
    low_contrast_pairs: list[dict[str, Any]]
    layout: dict[str, Any]
    pages: dict[str, str]
    discoverable_from_nav: list[str]
    special_pages: dict[str, str]


@lru_cache(maxsize=1)
def _raw_text() -> str:
    return RAW_SAMPLE_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _analysis_text() -> str:
    return ANALYSIS_SAMPLE_PATH.read_text(encoding="utf-8")


def load_raw_sample() -> RawCapture:
    """A fresh RawCapture (safe to mutate)."""
    return RawCapture.model_validate_json(_raw_text())


def load_analysis_sample() -> DesignAnalysis:
    """A fresh DesignAnalysis (safe to mutate)."""
    return DesignAnalysis.model_validate_json(_analysis_text())


def load_site_contract() -> SiteContract:
    return SiteContract.model_validate(json.loads(SITE_CONTRACT_PATH.read_text(encoding="utf-8")))
