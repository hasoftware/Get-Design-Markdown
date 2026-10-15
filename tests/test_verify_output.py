"""scripts/verify_output.py: a valid output folder passes, each corruption gives its finding."""

from __future__ import annotations

import ast
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from scripts import verify_output
from scripts.verify_output import Finding
from scripts.verify_output import verify_output as run_verify

from get_design_markdown.models.analysis import ComponentSpec
from get_design_markdown.synthesizer import render
from tests.fixture_data import load_analysis_sample, load_raw_sample

GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "verify"
GOLDEN_FILES = ("DESIGN.md", "tokens.json", "preview.html", "synthesis.json")
SITE_FOLDER = "acme.example"


def build_valid_folder(root: Path, name: str = SITE_FOLDER) -> Path:
    """A complete, valid output folder: fixture raw/analysis + golden files + tiny screenshots."""
    folder = root / name
    folder.mkdir(parents=True)
    raw = load_raw_sample()
    analysis = load_analysis_sample()
    (folder / "raw.json").write_text(raw.model_dump_json(indent=2), encoding="utf-8")
    (folder / "analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    for filename in GOLDEN_FILES:
        shutil.copyfile(GOLDEN_DIR / filename, folder / filename)
    pages = [*raw.pages, *([raw.dark] if raw.dark else [])]
    for page in pages:
        for shot in page.screenshots:
            target = folder / shot.path
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (shot.viewport_width, 8), "white").save(target)
    return folder


@pytest.fixture
def valid_folder(tmp_path: Path) -> Path:
    return build_valid_folder(tmp_path)


def checks(findings: list[Finding], severity: str | None = None) -> set[str]:
    return {f.check for f in findings if severity is None or f.severity == severity}


def edit_text(folder: Path, filename: str, old: str, new: str) -> None:
    path = folder / filename
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} không có trong {filename}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def edit_json(folder: Path, filename: str, mutate: Callable[[Any], None]) -> None:
    path = folder / filename
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def move_section(folder: Path, title: str, before: str) -> None:
    path = folder / "DESIGN.md"
    text = path.read_text(encoding="utf-8")
    start = text.index(f"## {title}\n")
    end = text.index("\n## ", start + 1) + 1
    block = text[start:end]
    text = text[:start] + text[end:]
    anchor = text.index(f"## {before}\n")
    path.write_text(text[:anchor] + block + text[anchor:], encoding="utf-8")


# ─── Valid folder ────────────────────────────────────────────────────────────


def test_valid_folder_has_no_findings(valid_folder: Path) -> None:
    assert run_verify(valid_folder) == []


def test_one_google_fonts_stylesheet_is_allowed(valid_folder: Path) -> None:
    link = (
        '<link rel="stylesheet" '
        'href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&amp;display=swap">'
    )
    edit_text(valid_folder, "preview.html", "</head>", f"{link}</head>")
    assert run_verify(valid_folder) == []
    edit_text(valid_folder, "preview.html", "</head>", f"{link}</head>")
    assert checks(run_verify(valid_folder), "warning") == {"preview.external"}


def test_cli_table_on_valid_folder(valid_folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert verify_output.main([str(valid_folder)]) == 0
    output = capsys.readouterr().out
    assert "Kết quả: 0 lỗi, 0 cảnh báo, 0 ghi chú." in output
    assert str(valid_folder) in output


def test_cli_json_on_valid_folder(valid_folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert verify_output.main([str(valid_folder), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["findings"] == []
    assert payload["summary"] == {"error": 0, "warning": 0, "info": 0}


def test_missing_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    findings = run_verify(tmp_path / "nope")
    assert checks(findings, "error") == {"folder.missing"}
    assert verify_output.main([str(tmp_path / "nope")]) == 1
    assert "Không tìm thấy thư mục" in capsys.readouterr().out


# ─── One corruption per check ────────────────────────────────────────────────


def _drop_screenshot(folder: Path) -> None:
    (folder / "screenshots" / "home-768.png").unlink()


def _narrow_screenshot(folder: Path) -> None:
    Image.new("RGB", (100, 8), "white").save(folder / "screenshots" / "home-390.png")


def _set(path: list[str | int], value: Any) -> Callable[[Any], None]:
    def mutate(data: Any) -> None:
        node = data
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mutate


def _delete(path: list[str | int]) -> Callable[[Any], None]:
    def mutate(data: Any) -> None:
        node = data
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return mutate


def _alias_cycle(data: Any) -> None:
    data["color"]["primary"]["$value"] = "{color.on-primary}"
    data["color"]["on-primary"]["$value"] = "{color.primary}"


def _add_error_issue(data: Any) -> None:
    data["issues"].append({"rule": "broken-ref", "severity": "error", "message": "Hỏng."})


def _add_breakpoint(data: Any) -> None:
    data["breakpoints"].append({"name": "xxl", "min_width_px": 1536.0, "occurrences": 1})


def _remove_primary(folder: Path) -> None:
    path = folder / "DESIGN.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace('  primary: "#4f46e5"\n', "").replace("{colors.primary}", "{colors.ink}")
    path.write_text(text, encoding="utf-8")


def _write(filename: str, content: str) -> Callable[[Path], None]:
    def mutate(folder: Path) -> None:
        (folder / filename).write_text(content, encoding="utf-8")

    return mutate


def _unlink(filename: str) -> Callable[[Path], None]:
    def mutate(folder: Path) -> None:
        (folder / filename).unlink()

    return mutate


def _text(filename: str, old: str, new: str) -> Callable[[Path], None]:
    return lambda folder: edit_text(folder, filename, old, new)


def _json(filename: str, mutate: Callable[[Any], None]) -> Callable[[Path], None]:
    return lambda folder: edit_json(folder, filename, mutate)


CORRUPTIONS: list[Any] = [
    # raw.json
    pytest.param(_drop_screenshot, "raw.screenshots", "error", id="missing-screenshot"),
    pytest.param(_narrow_screenshot, "raw.screenshots", "warning", id="screenshot-width"),
    pytest.param(_write("raw.json", "{"), "raw.schema", "error", id="raw-not-json"),
    pytest.param(
        _json("raw.json", _set(["pages"], "nope")), "raw.schema", "error", id="raw-schema"
    ),
    pytest.param(
        _json("raw.json", _set(["schema_version"], 99)), "raw.schema", "error", id="raw-version"
    ),
    pytest.param(
        _json("raw.json", _set(["pages", 0, "elements", 5, "parent_index"], 50)),
        "raw.elements",
        "error",
        id="raw-parent-index",
    ),
    pytest.param(
        _json("raw.json", _set(["pages", 0, "hover_states", 0, "element_index"], 999)),
        "raw.hover",
        "error",
        id="raw-hover-index",
    ),
    pytest.param(
        _json("raw.json", _set(["domain"], "other.example")),
        "raw.domain",
        "warning",
        id="raw-domain",
    ),
    pytest.param(
        _json("raw.json", _set(["pages", 0, "elements"], [])),
        "raw.elements",
        "error",
        id="raw-no-elements",
    ),
    # analysis.json
    pytest.param(
        _json("analysis.json", _set(["colors", "contrast_pairs", 0, "ratio"], 5.0)),
        "analysis.contrast",
        "error",
        id="analysis-contrast-ratio",
    ),
    pytest.param(
        _json(
            "analysis.json",
            _set(["components", 0, "token_refs", "backgroundColor"], "{colors.brand}"),
        ),
        "analysis.refs",
        "error",
        id="analysis-unresolved-ref",
    ),
    pytest.param(
        _json("analysis.json", _set(["colors", "tokens", 9, "name"], "positive")),
        "analysis.tokens",
        "warning",
        id="analysis-noncanonical-token",
    ),
    pytest.param(
        _json("analysis.json", _add_breakpoint),
        "analysis.breakpoints",
        "warning",
        id="analysis-breakpoint-not-measured",
    ),
    pytest.param(
        _json("analysis.json", _set(["typography", "levels", 0, "size_px"], 12.0)),
        "analysis.typography",
        "warning",
        id="analysis-level-order",
    ),
    # DESIGN.md: front matter
    pytest.param(
        _text("DESIGN.md", "---\nversion: alpha", "version: alpha"),
        "design.front-matter",
        "error",
        id="front-matter-missing",
    ),
    pytest.param(
        _text("DESIGN.md", "name: Acme Cloud Analysis", "name: [Acme"),
        "design.front-matter",
        "error",
        id="front-matter-yaml",
    ),
    pytest.param(
        _text("DESIGN.md", "version: alpha", "version: beta"),
        "design.version",
        "error",
        id="version",
    ),
    pytest.param(
        _text("DESIGN.md", '  success: "#16a34a"', '  success: "rgb(22, 163, 74)"'),
        "design.colors",
        "error",
        id="color-not-hex",
    ),
    pytest.param(
        _text("DESIGN.md", 'rounded: "{rounded.xl}"', 'rounded: "{rounded.huge}"'),
        "design.broken-ref",
        "error",
        id="broken-ref-front-matter",
    ),
    pytest.param(
        _text("DESIGN.md", "`{typography.body}`", "`{typography.paragraph}`"),
        "design.broken-ref",
        "error",
        id="broken-ref-body",
    ),
    pytest.param(
        _text("DESIGN.md", "Do keep `{colors.primary}`", "Do keep `{color.primary}`"),
        "design.ref-syntax",
        "warning",
        id="ref-syntax",
    ),
    pytest.param(_remove_primary, "design.missing-primary", "warning", id="missing-primary"),
    # DESIGN.md: sections and reference note
    pytest.param(
        lambda folder: move_section(folder, "Shapes", "Layout"),
        "design.sections",
        "error",
        id="section-order",
    ),
    pytest.param(
        _text("DESIGN.md", "## Agent Prompt Guide", "## Colors\n\nAgain.\n\n## Agent Prompt Guide"),
        "design.sections",
        "error",
        id="duplicate-section",
    ),
    pytest.param(
        _text("DESIGN.md", "## Overview\n", "## Intro\n"),
        "design.sections",
        "error",
        id="missing-overview",
    ),
    pytest.param(
        _text("DESIGN.md", "keep radii on the `rounded` scale.\n", "scale.\n\n## Notes\n\nx\n"),
        "design.sections",
        "warning",
        id="agent-guide-not-last",
    ),
    pytest.param(
        _text("DESIGN.md", "> Source: https://acme.example/", "> Source: https://example.org/"),
        "design.reference-note",
        "error",
        id="reference-note-url",
    ),
    pytest.param(
        _text("DESIGN.md", "not an official", "an internal"),
        "design.reference-note",
        "warning",
        id="reference-note-disclaimer",
    ),
    pytest.param(
        _text("DESIGN.md", "Extracted: 2026-10-15", "Extracted: soon"),
        "design.reference-note",
        "warning",
        id="reference-note-date",
    ),
    # DESIGN.md: no invented numbers
    pytest.param(
        _text("DESIGN.md", '  surface: "#f8fafc"', '  surface: "#f8fafd"'),
        "design.invented-color",
        "error",
        id="invented-color",
    ),
    pytest.param(
        _text("DESIGN.md", '    backgroundColor: "#1e293b"', '    backgroundColor: "#1e293c"'),
        "design.invented-color",
        "error",
        id="invented-component-color",
    ),
    pytest.param(
        _text("DESIGN.md", "  md: 8px\n", "  md: 10px\n"),
        "design.invented-dimension",
        "error",
        id="invented-rounded",
    ),
    pytest.param(
        _text("DESIGN.md", "  4xl: 64px", "  4xl: 72px"),
        "design.invented-dimension",
        "error",
        id="invented-spacing",
    ),
    pytest.param(
        _text("DESIGN.md", "    fontSize: 16px", "    fontSize: 17px"),
        "design.invented-typography",
        "error",
        id="invented-font-size",
    ),
    pytest.param(
        _text("DESIGN.md", "fontFamily: JetBrains Mono", "fontFamily: Fira Code"),
        "design.invented-typography",
        "error",
        id="invented-font-family",
    ),
    pytest.param(
        _text("DESIGN.md", "    fontWeight: 600", "    fontWeight: 800"),
        "design.invented-typography",
        "error",
        id="invented-font-weight",
    ),
    pytest.param(
        _text(
            "DESIGN.md",
            "    padding: 12px 20px\n    height: 44px\n  button-primary-hover",
            "    padding: 14px 20px\n    height: 44px\n  button-primary-hover",
        ),
        "design.component-value",
        "warning",
        id="component-value",
    ),
    pytest.param(
        _text("DESIGN.md", "  footer:\n", "  hero-banner:\n    height: 600px\n  footer:\n"),
        "design.component-unknown",
        "warning",
        id="unknown-component",
    ),
    pytest.param(
        _text("DESIGN.md", '  nav-link-hover:\n    textColor: "{colors.ink}"\n', ""),
        "design.hover",
        "warning",
        id="hover-variant-missing",
    ),
    # DESIGN.md: contrast and breakpoints
    pytest.param(
        _text(
            "DESIGN.md",
            '    backgroundColor: "{colors.canvas}"\n    textColor: "{colors.ink}"',
            '    backgroundColor: "{colors.surface}"\n    textColor: "{colors.faint}"',
        ),
        "design.contrast",
        "warning",
        id="failing-contrast-not-mentioned",
    ),
    pytest.param(
        _text("DESIGN.md", "it measures 2.56:1, below", "it measures too low, below"),
        "design.contrast-undocumented",
        "warning",
        id="analysis-pair-undocumented",
    ),
    pytest.param(
        _text("DESIGN.md", "640px, 768px", "768px"),
        "design.breakpoints",
        "warning",
        id="breakpoint-undocumented",
    ),
    # tokens.json
    pytest.param(_write("tokens.json", "not json"), "tokens.json", "error", id="tokens-not-json"),
    pytest.param(
        _json(
            "tokens.json",
            _set(["component", "button-primary", "background", "$value"], "{color.brand}"),
        ),
        "tokens.alias",
        "error",
        id="tokens-unresolved-alias",
    ),
    pytest.param(
        _json(
            "tokens.json",
            _set(["component", "button-primary", "radius", "$value"], "{dimension.rounded}"),
        ),
        "tokens.alias",
        "error",
        id="tokens-alias-to-group",
    ),
    pytest.param(_json("tokens.json", _alias_cycle), "tokens.alias", "error", id="alias-cycle"),
    pytest.param(
        _json("tokens.json", _delete(["color", "$type"])), "tokens.type", "error", id="no-type"
    ),
    pytest.param(
        _json("tokens.json", _set(["color", "primary", "$value", "components"], [3, 0, 0])),
        "tokens.color",
        "error",
        id="tokens-color-range",
    ),
    pytest.param(
        _json("tokens.json", _set(["color", "primary", "$value"], {"hex": "#4f46e5"})),
        "tokens.color",
        "error",
        id="tokens-color-object",
    ),
    pytest.param(
        _json(
            "tokens.json",
            _set(["dimension", "spacing", "md", "$value"], {"value": "16", "unit": "pt"}),
        ),
        "tokens.dimension",
        "error",
        id="tokens-dimension",
    ),
    pytest.param(
        _json(
            "tokens.json",
            _set(
                ["color", "primary", "$value"],
                {"colorSpace": "srgb", "components": [1, 0, 0], "hex": "#ff0000"},
            ),
        ),
        "tokens.analysis",
        "warning",
        id="tokens-differs-from-analysis",
    ),
    # preview.html
    pytest.param(
        _text(
            "preview.html",
            "</head>",
            '<script src="https://cdn.example.com/app.js"></script></head>',
        ),
        "preview.script",
        "error",
        id="preview-external-script",
    ),
    pytest.param(
        _text(
            "preview.html",
            "</head>",
            '<link rel="stylesheet" href="https://cdn.example.com/theme.css"></head>',
        ),
        "preview.external",
        "warning",
        id="preview-external-stylesheet",
    ),
    pytest.param(
        _text("preview.html", "#16a34a", "green"),
        "preview.colors",
        "error",
        id="preview-missing-color",
    ),
    pytest.param(_unlink("preview.html"), "preview.missing", "warning", id="preview-missing"),
    # synthesis.json / prompt.md
    pytest.param(
        _json("synthesis.json", _add_error_issue), "synthesis.issues", "error", id="synth-errors"
    ),
    pytest.param(
        _json("synthesis.json", _set(["sections"], ["Overview"])),
        "synthesis.sections",
        "warning",
        id="synth-sections",
    ),
    pytest.param(
        _text("DESIGN.md", "  footer:\n", "  ex-hero:\n    height: 600px\n  footer:\n"),
        "synthesis.examples",
        "warning",
        id="examples-without-flag",
    ),
    pytest.param(
        _write("prompt.md", "Ảnh: prompt-images/home-1440.png\n"),
        "prompt.images",
        "error",
        id="prompt-missing-image",
    ),
    # missing stages
    pytest.param(_unlink("analysis.json"), "analysis.missing", "error", id="analysis-missing"),
    pytest.param(_unlink("DESIGN.md"), "design.missing", "error", id="design-missing"),
]


@pytest.mark.parametrize(("corrupt", "check", "severity"), CORRUPTIONS)
def test_corruption_is_reported(
    valid_folder: Path, corrupt: Callable[[Path], None], check: str, severity: str
) -> None:
    corrupt(valid_folder)
    findings = run_verify(valid_folder)
    assert check in checks(findings, severity), findings
    assert "verify.internal" not in checks(findings)
    if severity == "error":
        assert verify_output.exit_code(findings) == 1
    assert verify_output.exit_code(findings, strict=True) == 1


def test_failing_page_pair_may_be_documented_in_colors(valid_folder: Path) -> None:
    edit_text(valid_folder, "DESIGN.md", "it measures 2.56:1, below", "it measures too low, below")
    edit_text(
        valid_folder,
        "DESIGN.md",
        "Dark theme: in the dark capture",
        "Captions in `{colors.faint}` on `{colors.canvas}` measure 2.56:1.\n\n"
        "Dark theme: in the dark capture",
    )
    assert run_verify(valid_folder) == []


def test_findings_are_sorted_by_severity(valid_folder: Path) -> None:
    _unlink("preview.html")(valid_folder)
    _drop_screenshot(valid_folder)
    severities = [finding.severity for finding in run_verify(valid_folder)]
    assert severities == sorted(severities, key=verify_output.SEVERITY_ORDER.__getitem__)
    assert severities[0] == "error"


def test_partial_mode_turns_missing_stages_into_notes(valid_folder: Path) -> None:
    for name in ("analysis.json", "DESIGN.md", "synthesis.json", "tokens.json", "preview.html"):
        (valid_folder / name).unlink()
    strict = run_verify(valid_folder)
    assert {"analysis.missing", "design.missing"} <= checks(strict, "error")
    partial = run_verify(valid_folder, partial=True)
    assert checks(partial, "error") == set()
    assert checks(partial, "warning") == set()
    assert {"analysis.missing", "design.missing", "tokens.missing"} <= checks(partial, "info")


def test_partial_mode_still_checks_existing_files(valid_folder: Path) -> None:
    _drop_screenshot(valid_folder)
    (valid_folder / "DESIGN.md").unlink()
    findings = run_verify(valid_folder, partial=True)
    assert "raw.screenshots" in checks(findings, "error")


def test_cli_strict_and_partial_flags(
    valid_folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    edit_json(valid_folder, "raw.json", _set(["domain"], "other.example"))
    assert verify_output.main([str(valid_folder)]) == 0
    assert verify_output.main([str(valid_folder), "--strict"]) == 1
    (valid_folder / "DESIGN.md").unlink()
    (valid_folder / "synthesis.json").unlink()
    assert verify_output.main([str(valid_folder)]) == 1
    assert verify_output.main([str(valid_folder), "--partial"]) == 0
    output = capsys.readouterr().out
    assert "CẢNH BÁO" in output and "raw.domain" in output


def test_cli_json_reports_findings(valid_folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _drop_screenshot(valid_folder)
    assert verify_output.main([str(valid_folder), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["summary"]["error"] >= 1
    first = payload["findings"][0]
    assert set(first) == {"check", "severity", "message"}
    assert first["severity"] == "error"


def test_unexpected_exception_becomes_a_finding(
    valid_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(verify_output, "_check_tokens", explode)
    findings = run_verify(valid_folder)
    internal = [f for f in findings if f.check == "verify.internal"]
    assert len(internal) == 1
    assert "tokens.json" in internal[0].message and "boom" in internal[0].message


# ─── Analyzer quality signals seen on real sites ─────────────────────────────

BUTTON_PRIMARY, TEXT_INPUT = 0, 6


def _rename_primary(data: Any) -> None:
    data["colors"]["tokens"][0]["name"] = "brand-accent"


@pytest.mark.parametrize(
    ("mutate", "check", "needle"),
    [
        pytest.param(
            _set(["dark", "colors", "scheme"], "light"),
            "analysis.dark",
            "không có giao diện tối riêng",
            id="dark-palette-is-light",  # stripe.com: no dark theme, dark palette still kept
        ),
        pytest.param(
            _rename_primary,
            "analysis.tokens",
            "không có token 'primary'",
            id="no-primary-token",  # linear.app: the indigo accent was not recognized
        ),
        pytest.param(
            _set(["components", TEXT_INPUT, "properties", "textColor"], "transparent"),
            "analysis.components",
            "textColor trong suốt",
            id="transparent-text",  # linear.app text-input
        ),
        pytest.param(
            _set(["components", BUTTON_PRIMARY, "states", "hover", "backgroundColor"], "#4f46e5"),
            "analysis.components",
            "không đổi backgroundColor",
            id="hover-unchanged",  # stripe.com nav-link
        ),
        pytest.param(
            _set(["components", BUTTON_PRIMARY, "states", "hover", "zIndex"], "10"),
            "analysis.components",
            "khóa zIndex",
            id="undocumented-key",
        ),
        pytest.param(
            _set(
                ["components", BUTTON_PRIMARY, "token_refs", "backgroundColor"], "{colors.hairline}"
            ),
            "analysis.ref-role",
            "token màu viền",
            id="background-from-border-token",  # linear.app button-primary
        ),
    ],
)
def test_analyzer_quality_signals(
    valid_folder: Path, mutate: Callable[[Any], None], check: str, needle: str
) -> None:
    edit_json(valid_folder, "analysis.json", mutate)
    messages = [f.message for f in run_verify(valid_folder) if f.check == check]
    assert any(needle in message for message in messages), messages


# ─── Helpers ─────────────────────────────────────────────────────────────────


def test_sections_constant_matches_synthesizer() -> None:
    assert verify_output.DESIGN_SECTIONS == render.DESIGN_SECTIONS


def test_component_keys_match_model_docstring() -> None:
    doc = " ".join((ComponentSpec.__doc__ or "").split())
    match = re.search(r"`properties` keys \(camelCase, raw measured values\): ([^.]+)\.", doc)
    assert match is not None
    keys = tuple(key.strip() for key in match.group(1).split(","))
    assert keys == verify_output.COMPONENT_PROPERTY_KEYS


def test_verifier_stays_independent_of_later_stages() -> None:
    source = Path(verify_output.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    project = {name for name in imported if name.startswith("get_design_markdown")}
    allowed = ("get_design_markdown.models", "get_design_markdown.analyzer")
    assert all(name.startswith(allowed) for name in project), project
    analyzer_modules = {
        name.split(".")[2]
        for name in project
        if name.startswith("get_design_markdown.analyzer.") and name.count(".") >= 2
    }
    assert analyzer_modules <= {"css_values", "naming", "RGBA"}, analyzer_modules


def test_split_front_matter_and_sections() -> None:
    text = (
        "---\nversion: alpha\nname: X\n---\n# X\n\nnote\n\n## Overview\nA\n"
        "```md\n## Not a section\n```\n### Sub\n## Colors\nB\n"
    )
    document = verify_output.parse_design_md(text)
    assert document.front_matter == {"version": "alpha", "name": "X"}
    assert document.front_matter_error is None
    assert document.section_titles == ["Overview", "Colors"]
    overview = document.section("Overview")
    assert overview is not None and "## Not a section" in overview and "### Sub" in overview
    assert document.preamble.strip() == "# X\n\nnote"
    assert verify_output.split_front_matter("# no front matter")[2] is not None
    assert verify_output.split_front_matter("---\nname: X\n")[2] == (
        "Front matter không có dòng '---' đóng."
    )
    problem = verify_output.split_front_matter("---\nname: [x\n---\n")[2]
    assert problem is not None and "dòng" in problem


@pytest.mark.parametrize(
    ("ratio", "text", "expected"),
    [
        (2.564, "measures 2.56:1", True),
        (3.3, "only 3.3:1", True),
        (3.2957, "only 3.3:1", True),
        (3.98, "tỉ lệ 3,98:1", True),
        (2.56, "measures 2.65:1", False),
        (4.0, "4:1 and #404040", False),
    ],
)
def test_ratio_mentioned(ratio: float, text: str, expected: bool) -> None:
    assert verify_output.ratio_mentioned(ratio, text) is expected


def test_image_size_reads_png_jpeg_webp(tmp_path: Path) -> None:
    image = Image.new("RGB", (37, 21), "red")
    image.save(tmp_path / "a.png")
    image.save(tmp_path / "a.jpg")
    image.save(tmp_path / "a.webp", lossless=False)
    image.save(tmp_path / "b.webp", lossless=True)
    for name in ("a.png", "a.jpg", "a.webp", "b.webp"):
        assert verify_output.image_size(tmp_path / name) == (37, 21), name
    garbage = tmp_path / "x.png"
    garbage.write_bytes(b"not an image")
    assert verify_output.image_size(garbage) is None
    assert verify_output.image_size(tmp_path / "missing.png") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            {"colorSpace": "srgb", "components": [0.3098, 0.2745, 0.898], "hex": "#4f46e5"},
            "#4f46e5",
        ),
        ({"colorSpace": "srgb", "components": [1, 1, 1], "alpha": 0.8}, "#ffffffcc"),
        ({"colorSpace": "oklch", "components": [1, 0, 0]}, "#ffffff"),
        ({"hex": "#0F172A"}, "#0f172a"),
        ("#4f46e5", "#4f46e5"),
        ({"colorSpace": "srgb"}, None),
        (12, None),
    ],
)
def test_dtcg_color_hex(value: Any, expected: str | None) -> None:
    assert verify_output.dtcg_color_hex(value) == expected


def test_dimension_px() -> None:
    assert verify_output.dimension_px("16px") == 16.0
    assert verify_output.dimension_px("1.5rem") == 24.0
    assert verify_output.dimension_px("-0.02em") == pytest.approx(-0.32)
    assert verify_output.dimension_px("12px 20px") is None
    assert verify_output.dimension_px(16) is None


def test_format_findings_wraps_long_messages(tmp_path: Path) -> None:
    findings = [
        Finding("design.broken-ref", "error", "x " * 120),
        Finding("raw.domain", "warning", "ngắn"),
        Finding("analysis.skipped", "info", "ghi chú"),
    ]
    text = verify_output.format_findings(findings, tmp_path, width=80)
    lines = text.splitlines()
    assert lines[0] == f"Kiểm tra thư mục: {tmp_path}"
    assert lines[1].startswith("MỨC ĐỘ")
    assert lines[2].startswith("LỖI")
    assert all(len(line) <= 80 for line in lines[2:-1])
    assert lines[-1] == "Kết quả: 1 lỗi, 1 cảnh báo, 1 ghi chú."
    assert verify_output.format_findings([], tmp_path).splitlines()[1] == "Không có phát hiện nào."
