"""End-to-end: the whole pipeline on the static fixture site (needs Chromium + tests/fixtures/site).

The pipeline runs once per module and every stage must be built: a NotImplementedYetError, a part
the analyzer skipped or an output file that was not written fails the run. The output must pass
the independent verifier and satisfy the site contract (tests/fixtures/site_contract.json).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.verify_output import dtcg_color_hex, parse_design_md, ratio_mentioned, verify_output

from get_design_markdown import pipeline
from get_design_markdown.analyzer import css_values
from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import DesignAnalysis
from get_design_markdown.models.raw import CaptureOptions, RawCapture
from tests.fixture_data import SiteContract, load_site_contract

pytestmark = [pytest.mark.e2e, pytest.mark.browser]

E2E_OPTIONS = CaptureOptions(dark=True, pages=2, timeout_s=30)
WIDTH_TOLERANCE_PX = 1.0
RATIO_TOLERANCE = 0.02
#: Every file `gdm get` writes (prompt.md only with --prompt-only).
OUTPUT_FILES = (
    "raw.json",
    "analysis.json",
    "DESIGN.md",
    "synthesis.json",
    "tokens.json",
    "preview.html",
)


@pytest.fixture(scope="module")
def e2e_folder(site_url: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out_root = tmp_path_factory.mktemp("e2e")
    try:
        result = pipeline.run_get(site_url, out_root=out_root, options=E2E_OPTIONS)
    except NotImplementedYetError as error:
        pytest.fail(f"Một tầng của pipeline chưa được xây dựng: {error.message}")
    return result.folder


@pytest.fixture(scope="module")
def contract() -> SiteContract:
    return load_site_contract()


def _require(folder: Path, filename: str) -> Path:
    path = folder / filename
    assert path.is_file(), f"'gdm get' không ghi {filename}"
    return path


def _close_to_any(value: float, candidates: list[float], tolerance: float) -> bool:
    return any(abs(value - candidate) <= tolerance for candidate in candidates)


def test_pipeline_writes_every_file(e2e_folder: Path) -> None:
    missing = [name for name in OUTPUT_FILES if not (e2e_folder / name).is_file()]
    assert missing == [], f"'gdm get' không ghi: {', '.join(missing)}"


def test_pipeline_output_passes_the_verifier(e2e_folder: Path) -> None:
    findings = verify_output(e2e_folder)
    errors = [f"[{f.check}] {f.message}" for f in findings if f.severity == "error"]
    assert errors == [], "\n".join(errors)


def test_raw_capture_matches_contract(e2e_folder: Path, contract: SiteContract) -> None:
    path = _require(e2e_folder, "raw.json")
    raw = RawCapture.model_validate_json(path.read_text(encoding="utf-8"))
    names = [page.name for page in raw.pages]
    assert names[0] == "home"
    assert len(names) == 1 + E2E_OPTIONS.pages, names
    assert set(names) <= set(contract.pages.values()), names
    assert raw.dark is not None and raw.dark.color_scheme == "dark"
    main = raw.pages[0]
    assert {shot.viewport_width for shot in main.screenshots} >= set(E2E_OPTIONS.screenshot_widths)
    snapshots = {snapshot.viewport_width: snapshot for snapshot in main.layouts}
    columns: dict[str, int] = contract.layout["columns"]
    for label, expected in columns.items():
        assert snapshots[int(label)].main_columns == expected, label
    hamburger_below = contract.layout["hamburger_below_px"]
    for viewport, snapshot in snapshots.items():
        assert snapshot.hamburger_visible == (viewport < hamburger_below), viewport
    media_widths = [
        value
        for sheet in raw.stylesheets
        for query in sheet.media_queries
        for value in (query.min_width_px, query.max_width_px)
        if value is not None
    ]
    for width in contract.breakpoints_px:
        assert _close_to_any(width, media_widths, WIDTH_TOLERANCE_PX), width


def test_analysis_matches_contract(e2e_folder: Path, contract: SiteContract) -> None:
    path = _require(e2e_folder, "analysis.json")
    analysis = DesignAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
    skipped = [warning for warning in analysis.warnings if warning.startswith("Chưa phân tích")]
    assert skipped == [], skipped
    tokens = {token.name: token.value for token in analysis.colors.tokens}
    assert tokens.get("primary") == contract.palette["primary"], tokens
    assert analysis.dark is not None and analysis.dark.colors.tokens
    dark = {token.name: token.value for token in analysis.dark.colors.tokens}
    assert dark.get("primary") == contract.dark_palette["primary"], dark
    families = {family.name for family in analysis.typography.families}
    assert {contract.fonts["primary"], contract.fonts["heading"]} <= families, families
    widths = [
        value
        for breakpoint in analysis.breakpoints
        for value in (breakpoint.min_width_px, breakpoint.max_width_px)
        if value is not None
    ]
    for width in contract.breakpoints_px:
        assert _close_to_any(width, widths, WIDTH_TOLERANCE_PX), (width, widths)
    components = {component.name: component for component in analysis.components}
    for name, expected in contract.hover.items():
        hover = components[name].states.get("hover", {})
        for key, value in expected.items():
            assert hover.get(key) == value, (name, hover)
    ratios = [pair.ratio for pair in analysis.colors.contrast_pairs]
    for pair in contract.low_contrast_pairs:
        assert _close_to_any(pair["ratio"], ratios, RATIO_TOLERANCE), pair


def test_design_md_matches_contract(e2e_folder: Path, contract: SiteContract) -> None:
    path = _require(e2e_folder, "DESIGN.md")
    document = parse_design_md(path.read_text(encoding="utf-8"))
    front_matter: dict[str, Any] = document.front_matter or {}
    assert front_matter.get("colors", {}).get("primary") == contract.palette["primary"]
    families = {
        css_values.primary_font_family(str(entry.get("fontFamily")))
        for entry in front_matter.get("typography", {}).values()
        if isinstance(entry, dict)
    }
    assert {contract.fonts["primary"], contract.fonts["heading"]} <= families, families
    layout = document.section("Layout") or ""
    for width in contract.breakpoints_px:
        assert str(width) in layout, width
    assert "button-primary-hover" in front_matter.get("components", {})
    donts = document.section("Do's and Don'ts") or ""
    for pair in contract.low_contrast_pairs:
        assert ratio_mentioned(pair["ratio"], donts), pair


def test_exports_match_contract(e2e_folder: Path, contract: SiteContract) -> None:
    primary = contract.palette["primary"]
    data = json.loads(_require(e2e_folder, "tokens.json").read_text(encoding="utf-8"))
    assert dtcg_color_hex(data["color"]["primary"]["$value"]) == primary
    preview = _require(e2e_folder, "preview.html").read_text(encoding="utf-8")
    assert primary in preview.lower()
