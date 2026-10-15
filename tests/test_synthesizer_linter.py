"""Rendered DESIGN.md files must pass the official linter (`@google/design.md lint`) with 0 errors.

The linter is downloaded through npx (it needs npm and, on first use, network access); the
tests skip when npx is missing or the package cannot be fetched. PACKAGE pins the same exact
version as the DESIGN_MD_LINTER variable of .github/workflows/ci.yml, so local results do not
depend on the latest release of an alpha spec. The `designmd` bin alias is used instead of
`design.md`: on Windows a command named "design.md" resolves to the .md file association instead
of the package's .cmd shim.

Warnings recorded on the Acme sample: two "orphaned-tokens" (`hairline` is only used as a
component borderColor, which is not a spec property; `success` is used by no component).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from get_design_markdown.models.analysis import DesignAnalysis, SiteInfo
from get_design_markdown.synthesizer.render import render_design_md
from tests.fixture_data import load_analysis_sample

LINT_TIMEOUT_S = 180
#: Bumped by hand together with DESIGN_MD_LINTER in .github/workflows/ci.yml.
PACKAGE = "@google/design.md@0.4.0"


def _run_linter(path: Path) -> dict[str, Any]:
    npx = shutil.which("npx")
    if npx is None:
        pytest.skip("npx không có trên PATH — bỏ qua linter chính thức.")
    command = [npx, "--yes", "--package", PACKAGE, "designmd", "lint", str(path)]
    # Output goes to a file, not a pipe: on Windows a killed npx.cmd can leave node holding pipes.
    with tempfile.TemporaryFile() as output:
        try:
            subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=LINT_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            pytest.skip(f"Không chạy được linter chính thức: {error}")
        output.seek(0)
        raw = output.read().decode("utf-8", errors="replace")
    try:
        report = json.loads(raw)
    except json.JSONDecodeError:
        pytest.skip(f"Linter chính thức không trả JSON (mạng/npm?): {raw[:200]!r}")
    assert isinstance(report, dict)
    return report


def _empty() -> DesignAnalysis:
    return DesignAnalysis(
        tool_version="0.1.0",
        analyzed_at=datetime(2026, 10, 15, tzinfo=UTC),
        site=SiteInfo(url="https://empty.example/", name="Empty"),
    )


def _sample() -> DesignAnalysis:
    return load_analysis_sample()


def _colors_only() -> DesignAnalysis:
    analysis = load_analysis_sample()
    analysis.typography.levels = []
    analysis.typography.families = []
    analysis.components = []
    return analysis


CASES: list[tuple[str, Callable[[], DesignAnalysis], str, bool, set[str]]] = [
    ("acme-en", _sample, "en", False, {"orphaned-tokens"}),
    ("acme-vi", _sample, "vi", False, {"orphaned-tokens"}),
    ("acme-examples", _sample, "en", True, {"orphaned-tokens"}),
    ("empty", _empty, "en", False, set()),
    ("colors-only", _colors_only, "vi", False, {"missing-typography"}),
]


@pytest.mark.parametrize(
    ("name", "factory", "lang", "examples", "allowed_warnings"),
    CASES,
    ids=[case[0] for case in CASES],
)
def test_official_linter_reports_no_errors(
    tmp_path: Path,
    name: str,
    factory: Callable[[], DesignAnalysis],
    lang: str,
    examples: bool,
    allowed_warnings: set[str],
) -> None:
    path = tmp_path / f"{name}.md"
    path.write_text(render_design_md(factory(), lang=lang, examples=examples), encoding="utf-8")
    report = _run_linter(path)
    findings = report.get("findings", [])
    errors = [finding for finding in findings if finding.get("severity") == "error"]
    assert errors == []
    assert report["summary"]["errors"] == 0
    warnings = {finding.get("rule") for finding in findings if finding.get("severity") == "warning"}
    assert warnings <= allowed_warnings
    if name.startswith("acme"):
        orphans = sorted(
            finding["path"] for finding in findings if finding.get("rule") == "orphaned-tokens"
        )
        assert orphans == ["colors.hairline", "colors.success"]


def test_linter_version_matches_ci() -> None:
    import yaml

    ci_file = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    workflow = yaml.safe_load(ci_file.read_text(encoding="utf-8"))
    assert workflow["env"]["DESIGN_MD_LINTER"] == PACKAGE
