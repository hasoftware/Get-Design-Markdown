"""Frozen glue (analyze orchestrator, synthesize/export I/O, pipeline) tested with fakes, so these
tests stay valid whatever the state of the parallel streams."""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from get_design_markdown import cli, pipeline
from get_design_markdown.analyzer import (
    analyze,
    breakpoints,
    colors,
    components,
    layout,
    report,
    shapes,
    spacing,
    typography,
)
from get_design_markdown.collector import collect
from get_design_markdown.errors import (
    AnalysisError,
    NotImplementedYetError,
    SynthesisValidationError,
)
from get_design_markdown.exporters import export, preview, tokens
from get_design_markdown.models.analysis import ColorPalette, DesignAnalysis
from get_design_markdown.models.raw import CaptureOptions, PageCapture, RawCapture, RobotsCheck
from get_design_markdown.models.synthesis import SynthesisResult, ValidationIssue
from get_design_markdown.storage import (
    ANALYSIS_FILE,
    DESIGN_FILE,
    PREVIEW_FILE,
    PROMPT_FILE,
    RAW_FILE,
    SYNTHESIS_FILE,
    TOKENS_FILE,
    load_model,
    save_model,
)
from get_design_markdown.synthesizer import prompt_export, render, synthesize, validate
from tests.conftest import FIXED_TIME
from tests.fixture_data import load_analysis_sample, load_raw_sample

runner = CliRunner()

DESIGN_TEXT = """---
version: alpha
name: Acme Cloud Analysis
colors:
  primary: "#4f46e5"
  canvas: "#ffffff"
rounded:
  md: 8px
---
# Acme Cloud — Design Reference

## Overview
Text.

```md
## Not a section
```

## Colors
More.
"""


def _not_implemented(*args: Any, **kwargs: Any) -> Any:
    raise NotImplementedYetError("chưa có")


@pytest.fixture
def sample_analyzers(monkeypatch: pytest.MonkeyPatch) -> DesignAnalysis:
    """Every analyzer returns the matching part of analysis_sample.json."""
    sample = load_analysis_sample()
    assert sample.dark is not None
    dark_palette = sample.dark.colors

    def fake_colors(pages: list[PageCapture], *args: Any, **kwargs: Any) -> ColorPalette:
        return dark_palette if pages[0].color_scheme == "dark" else sample.colors

    monkeypatch.setattr(colors, "analyze_colors", fake_colors)
    monkeypatch.setattr(typography, "analyze_typography", lambda *a, **k: sample.typography)
    monkeypatch.setattr(spacing, "analyze_spacing", lambda *a, **k: sample.spacing)
    monkeypatch.setattr(shapes, "analyze_radii", lambda *a, **k: sample.radii)
    monkeypatch.setattr(shapes, "analyze_shadows", lambda *a, **k: sample.shadows)
    monkeypatch.setattr(breakpoints, "analyze_breakpoints", lambda *a, **k: sample.breakpoints)
    monkeypatch.setattr(layout, "analyze_layout", lambda *a, **k: sample.layout)
    monkeypatch.setattr(components, "analyze_components", lambda *a, **k: sample.components)
    return sample


# ─── analyzer/analyze.py ─────────────────────────────────────────────────────


def test_analyze_capture_assembles_sample(sample_analyzers: DesignAnalysis) -> None:
    raw = load_raw_sample()
    result = analyze.analyze_capture(raw, now=sample_analyzers.analyzed_at)
    assert result == sample_analyzers


def test_analyze_capture_skips_unimplemented_parts(
    sample_analyzers: DesignAnalysis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(components, "analyze_components", _not_implemented)
    monkeypatch.setattr(shapes, "analyze_shadows", _not_implemented)
    result = analyze.analyze_capture(load_raw_sample(), now=FIXED_TIME)
    assert result.components == [] and result.shadows == []
    assert result.colors.tokens
    assert any("Component (GĐ5)" in warning for warning in result.warnings)
    assert any("Đổ bóng (GĐ4)" in warning for warning in result.warnings)


def test_analyze_capture_all_unimplemented_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for module, name in (
        (colors, "analyze_colors"),
        (typography, "analyze_typography"),
        (spacing, "analyze_spacing"),
        (shapes, "analyze_radii"),
        (shapes, "analyze_shadows"),
        (breakpoints, "analyze_breakpoints"),
        (layout, "analyze_layout"),
        (components, "analyze_components"),
    ):
        monkeypatch.setattr(module, name, _not_implemented)
    with pytest.raises(NotImplementedYetError):
        analyze.analyze_capture(load_raw_sample())


def test_analyze_capture_drops_identical_dark_theme(
    sample_analyzers: DesignAnalysis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(colors, "analyze_colors", lambda *a, **k: sample_analyzers.colors)
    result = analyze.analyze_capture(load_raw_sample())
    assert result.dark is None
    assert any("dark mode" in warning for warning in result.warnings)


def test_analyze_capture_drops_a_dark_capture_with_a_light_canvas(
    sample_analyzers: DesignAnalysis, monkeypatch: pytest.MonkeyPatch
) -> None:
    # stripe.com: the dark capture swaps canvas and surface but stays white.
    light = sample_analyzers.colors
    swapped = [
        token.model_copy(update={"value": "#f8fafc" if token.name == "canvas" else "#ffffff"})
        if token.name in ("canvas", "surface")
        else token
        for token in light.tokens
    ]
    noisy = light.model_copy(update={"tokens": swapped})

    def fake_colors(pages: list[PageCapture], *args: Any, **kwargs: Any) -> ColorPalette:
        return noisy if pages[0].color_scheme == "dark" else light

    monkeypatch.setattr(colors, "analyze_colors", fake_colors)
    result = analyze.analyze_capture(load_raw_sample())
    assert result.dark is None
    assert any("nền sáng (canvas #f8fafc)" in warning for warning in result.warnings)


def test_analyze_capture_drops_a_dark_capture_of_a_dark_only_site(
    sample_analyzers: DesignAnalysis, monkeypatch: pytest.MonkeyPatch
) -> None:
    # linear.app: dark by default; the dark capture only lost the primary token.
    assert sample_analyzers.dark is not None
    dark = sample_analyzers.dark.colors
    without_primary = dark.model_copy(
        update={"tokens": [token for token in dark.tokens if token.name != "primary"]}
    )

    def fake_colors(pages: list[PageCapture], *args: Any, **kwargs: Any) -> ColorPalette:
        return without_primary if pages[0].color_scheme == "dark" else dark

    monkeypatch.setattr(colors, "analyze_colors", fake_colors)
    result = analyze.analyze_capture(load_raw_sample())
    assert result.dark is None
    assert any("vốn dùng nền tối" in warning for warning in result.warnings)


def test_analyze_capture_rejects_empty_capture() -> None:
    raw = load_raw_sample()
    with pytest.raises(AnalysisError):
        analyze.analyze_capture(raw.model_copy(update={"pages": []}))
    empty_page = raw.pages[0].model_copy(update={"elements": []})
    with pytest.raises(AnalysisError):
        analyze.analyze_capture(raw.model_copy(update={"pages": [empty_page]}))


@pytest.mark.parametrize(
    ("title", "domain", "expected"),
    [
        ("Stripe | Financial Infrastructure to Grow Your Revenue", "stripe.com", "Stripe"),
        ("Linear – Plan and build products", "linear.app", "Linear"),
        ("Acme Cloud — Ship cloud apps without the ops", "acme.example", "Acme Cloud"),
        ("Fixture home", "127.0.0.1_8000", "Fixture home"),
        ("", "docs.github.com", "Github"),
    ],
)
def test_site_name(title: str, domain: str, expected: str) -> None:
    assert analyze.site_name(title, domain) == expected


def test_run_analyze_writes_file_and_falls_back_to_summary(
    sample_analyzers: DesignAnalysis, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_model(load_raw_sample(), tmp_path / RAW_FILE)
    monkeypatch.setattr(report, "build_report", _not_implemented)
    result = analyze.run_analyze(tmp_path, now=FIXED_TIME)
    assert load_model(DesignAnalysis, tmp_path / ANALYSIS_FILE) == result


# ─── synthesizer/synthesize.py ───────────────────────────────────────────────


def _analysis_folder(tmp_path: Path) -> Path:
    save_model(load_analysis_sample(), tmp_path / ANALYSIS_FILE)
    return tmp_path


def test_run_synthesize_writes_design_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _analysis_folder(tmp_path)
    calls: dict[str, Any] = {}

    def fake_render(analysis: DesignAnalysis, *, lang: str, examples: bool) -> str:
        calls.update(lang=lang, examples=examples)
        return DESIGN_TEXT

    monkeypatch.setattr(render, "render_design_md", fake_render)
    monkeypatch.setattr(
        validate,
        "validate_design_md",
        lambda text: [ValidationIssue(rule="contrast-ratio", severity="warning", message="2.56")],
    )
    result = synthesize.run_synthesize(folder, lang="vi", examples=True, now=FIXED_TIME)
    assert calls == {"lang": "vi", "examples": True}
    assert (folder / DESIGN_FILE).read_text(encoding="utf-8") == DESIGN_TEXT
    assert result.sections == ["Overview", "Colors"]
    assert result.token_counts == {"colors": 2, "rounded": 1}
    assert result.mode == "rules" and result.prompt_path is None
    assert load_model(SynthesisResult, folder / SYNTHESIS_FILE) == result


def test_run_synthesize_refuses_invalid_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _analysis_folder(tmp_path)
    monkeypatch.setattr(render, "render_design_md", lambda *a, **k: DESIGN_TEXT)
    monkeypatch.setattr(
        validate,
        "validate_design_md",
        lambda text: [ValidationIssue(rule="broken-ref", severity="error", message="x")],
    )
    with pytest.raises(SynthesisValidationError) as info:
        synthesize.run_synthesize(folder)
    assert info.value.issues[0].rule == "broken-ref"
    assert not (folder / DESIGN_FILE).exists()


def test_run_synthesize_prompt_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _analysis_folder(tmp_path)
    monkeypatch.setattr(render, "render_design_md", lambda *a, **k: DESIGN_TEXT)
    monkeypatch.setattr(validate, "validate_design_md", _not_implemented)
    monkeypatch.setattr(
        prompt_export, "prepare_prompt_images", lambda *a, **k: ["prompt-images/desktop.png"]
    )
    monkeypatch.setattr(prompt_export, "render_prompt_md", lambda *a, **k: "# Prompt\n")
    result = synthesize.run_synthesize(folder, prompt_only=True)
    assert result.mode == "prompt-only"
    assert result.prompt_path == PROMPT_FILE
    assert result.image_paths == ["prompt-images/desktop.png"]
    assert result.issues[0].rule == "self-check"
    assert (folder / PROMPT_FILE).read_text(encoding="utf-8") == "# Prompt\n"


def test_run_synthesize_requires_renderer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render, "render_design_md", _not_implemented)
    with pytest.raises(NotImplementedYetError):
        synthesize.run_synthesize(_analysis_folder(tmp_path))


# ─── exporters/export.py ─────────────────────────────────────────────────────


def test_run_export_writes_available_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _analysis_folder(tmp_path)
    monkeypatch.setattr(tokens, "build_tokens", lambda analysis: {"color": {"$type": "color"}})
    monkeypatch.setattr(preview, "render_preview", _not_implemented)
    written = export.run_export(folder)
    assert written == [folder / TOKENS_FILE]
    assert json.loads((folder / TOKENS_FILE).read_text(encoding="utf-8")) == {
        "color": {"$type": "color"}
    }
    monkeypatch.setattr(tokens, "build_tokens", _not_implemented)
    with pytest.raises(NotImplementedYetError):
        export.run_export(folder)


# ─── pipeline.py + cli.py ────────────────────────────────────────────────────


@pytest.fixture
def fake_collect(monkeypatch: pytest.MonkeyPatch) -> list[CaptureOptions]:
    seen: list[CaptureOptions] = []

    async def fake_robots(url: str, **_: object) -> RobotsCheck:
        return RobotsCheck(robots_url=url + "robots.txt", allowed=True, note="ok robots")

    async def fake_collect_site(
        url: str, *, out_dir: Path, options: CaptureOptions, progress: Any = None
    ) -> RawCapture:
        seen.append(options)
        if progress is not None:
            progress("đang chụp")
        return load_raw_sample().model_copy(update={"robots": None, "options": None})

    monkeypatch.setattr(pipeline, "check_robots", fake_robots)
    monkeypatch.setattr(collect, "collect_site", fake_collect_site)
    return seen


def test_run_collect_saves_raw_with_robots_and_options(
    fake_collect: list[CaptureOptions], tmp_path: Path
) -> None:
    options = CaptureOptions(max_elements=500, dark=True, pages=2)
    folder = pipeline.run_collect("https://acme.example", out_root=tmp_path, options=options)
    assert folder == tmp_path / "acme.example"
    raw = load_model(RawCapture, folder / RAW_FILE)
    assert raw.robots is not None and raw.robots.note == "ok robots"
    assert raw.options == options
    assert fake_collect == [options]


def test_run_get_full_pipeline(
    fake_collect: list[CaptureOptions],
    sample_analyzers: DesignAnalysis,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(render, "render_design_md", lambda *a, **k: DESIGN_TEXT)
    monkeypatch.setattr(validate, "validate_design_md", lambda text: [])
    monkeypatch.setattr(tokens, "build_tokens", _not_implemented)
    monkeypatch.setattr(preview, "render_preview", lambda analysis: "<html></html>")
    opened: list[str] = []

    def fake_open(uri: str) -> bool:
        opened.append(uri)
        return True

    monkeypatch.setattr(webbrowser, "open", fake_open)
    result = pipeline.run_get(
        "acme.example", out_root=tmp_path, options=CaptureOptions(), open_browser=True
    )
    names = {path.name for path in result.files}
    assert {DESIGN_FILE, PREVIEW_FILE, ANALYSIS_FILE, RAW_FILE, SYNTHESIS_FILE} <= names
    assert TOKENS_FILE not in names
    assert result.opened == result.folder / PREVIEW_FILE
    assert opened and opened[0].startswith("file:")


def test_open_result_falls_back_to_design_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webbrowser, "open", lambda uri: True)
    assert pipeline.open_result(tmp_path) is None
    (tmp_path / DESIGN_FILE).write_text("# x\n", encoding="utf-8")
    assert pipeline.open_result(tmp_path) == tmp_path / DESIGN_FILE


def test_cli_collect_passes_options(
    fake_collect: list[CaptureOptions], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GDM_TIMEOUT", "12")
    result = runner.invoke(
        cli.app,
        ["collect", "acme.example", "--out", str(tmp_path), "--dark", "--pages", "2"],
    )
    assert result.exit_code == 0, result.output
    assert fake_collect[0] == CaptureOptions(dark=True, pages=2, timeout_s=12.0)
    result = runner.invoke(
        cli.app, ["collect", "acme.example", "--out", str(tmp_path), "--max-elements", "50"]
    )
    assert fake_collect[1].max_elements == 50


def test_cli_analyze_synthesize_export(
    sample_analyzers: DesignAnalysis, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_model(load_raw_sample(), tmp_path / RAW_FILE)
    seen: dict[str, Any] = {}

    def fake_colors(pages: list[PageCapture], *args: Any, threshold: float) -> ColorPalette:
        seen["threshold"] = threshold
        return sample_analyzers.colors

    monkeypatch.setattr(colors, "analyze_colors", fake_colors)
    result = runner.invoke(cli.app, ["analyze", str(tmp_path), "--delta-e", "5"])
    assert result.exit_code == 0, result.output
    assert seen["threshold"] == 5.0

    monkeypatch.setattr(render, "render_design_md", lambda *a, **k: DESIGN_TEXT)
    monkeypatch.setattr(validate, "validate_design_md", lambda text: [])
    result = runner.invoke(cli.app, ["synthesize", str(tmp_path), "--lang", "vi"])
    assert result.exit_code == 0, result.output
    assert load_model(SynthesisResult, tmp_path / SYNTHESIS_FILE).lang == "vi"

    monkeypatch.setattr(tokens, "build_tokens", _not_implemented)
    monkeypatch.setattr(preview, "render_preview", _not_implemented)
    result = runner.invoke(cli.app, ["export", str(tmp_path)])
    assert result.exit_code == 1
    assert "giai đoạn 8" in " ".join(result.output.split())


def test_cli_rejects_unknown_lang(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["synthesize", str(tmp_path), "--lang", "fr"])
    assert result.exit_code == 2
