"""Stage 3 orchestration (frozen glue): analysis.json -> DESIGN.md (+ prompt.md), synthesis.json.

The pure functions live in render.py, validate.py and prompt_export.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from get_design_markdown import __version__
from get_design_markdown.console import console, err_console, print_info, print_success
from get_design_markdown.errors import SynthesisValidationError
from get_design_markdown.models.analysis import DesignAnalysis
from get_design_markdown.models.synthesis import SynthesisResult, ValidationIssue
from get_design_markdown.storage import (
    ANALYSIS_FILE,
    DESIGN_FILE,
    PROMPT_FILE,
    SYNTHESIS_FILE,
    load_model,
    save_model,
    save_text,
)
from get_design_markdown.synthesizer import prompt_export, render, validate

_MAX_ISSUES_SHOWN = 20
_SEVERITY_STYLE = {"error": "bold red", "warning": "yellow", "info": "dim"}
_SEVERITY_LABEL = {"error": "Lỗi", "warning": "Cảnh báo", "info": "Ghi chú"}


def _front_matter(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def section_titles(text: str) -> list[str]:
    """Titles of "## " headings outside the front matter and code fences."""
    titles: list[str] = []
    in_fence = False
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        start = next((i + 1 for i in range(1, len(lines)) if lines[i].strip() == "---"), 0)
    for line in lines[start:]:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("## "):
            titles.append(line[3:].strip())
    return titles


def token_counts(text: str) -> dict[str, int]:
    """Number of entries per front-matter group (colors, typography, rounded...)."""
    data = _front_matter(text)
    return {key: len(value) for key, value in data.items() if isinstance(value, dict)}


def print_issues(issues: Sequence[ValidationIssue]) -> None:
    order = {"error": 0, "warning": 1, "info": 2}
    shown = sorted(issues, key=lambda issue: order[issue.severity])[:_MAX_ISSUES_SHOWN]
    for issue in shown:
        style = _SEVERITY_STYLE[issue.severity]
        target = err_console if issue.severity == "error" else console
        target.print(
            f"[{style}]{_SEVERITY_LABEL[issue.severity]} [{issue.rule}][/] {issue.message}"
        )
    if len(issues) > len(shown):
        console.print(f"[dim]… và {len(issues) - len(shown)} mục khác (xem synthesis.json).[/]")


def run_synthesize(
    folder: Path,
    *,
    lang: str = "en",
    prompt_only: bool = False,
    examples: bool = False,
    now: datetime | None = None,
) -> SynthesisResult:
    """Build DESIGN.md from analysis.json; refuse to write it when the self-check finds errors."""
    analysis = load_model(DesignAnalysis, folder / ANALYSIS_FILE)
    print_info(f"Dựng DESIGN.md cho {analysis.site.name} (ngôn ngữ: {lang}).")
    text = render.render_design_md(analysis, lang=lang, examples=examples)
    issues = validate.validate_design_md(text)
    print_issues(issues)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        raise SynthesisValidationError(
            f"DESIGN.md không qua bước tự kiểm ({len(errors)} lỗi) — chưa ghi file.",
            hint="Xem danh sách lỗi ở trên; nếu analysis.json đã cũ, chạy lại 'gdm analyze'.",
            issues=errors,
        )
    design_path = save_text(text, folder / DESIGN_FILE)
    print_success(f"Đã ghi {design_path}")

    prompt_path: str | None = None
    images: list[str] = []
    if prompt_only:
        images = prompt_export.prepare_prompt_images(folder, analysis.screenshots)
        prompt = prompt_export.render_prompt_md(analysis, text, images, lang=lang)
        print_success(f"Đã ghi {save_text(prompt, folder / PROMPT_FILE)}")
        prompt_path = PROMPT_FILE
        for image in images:
            print_info(f"Ảnh cho prompt: {folder / image}")

    result = SynthesisResult(
        tool_version=__version__,
        mode="prompt-only" if prompt_only else "rules",
        lang=lang,
        examples=examples,
        generated_at=now or datetime.now(UTC),
        issues=issues,
        sections=section_titles(text),
        token_counts=token_counts(text),
        design_md_path=DESIGN_FILE,
        prompt_path=prompt_path,
        image_paths=images,
    )
    save_model(result, folder / SYNTHESIS_FILE)
    return result
