"""Exports orchestration (frozen glue): analysis.json -> tokens.json + preview.html."""

from __future__ import annotations

from pathlib import Path

from get_design_markdown.console import print_success
from get_design_markdown.exporters import preview, tokens
from get_design_markdown.models.analysis import DesignAnalysis
from get_design_markdown.storage import (
    ANALYSIS_FILE,
    PREVIEW_FILE,
    TOKENS_FILE,
    load_model,
    save_json,
    save_text,
)


def run_export(folder: Path) -> list[Path]:
    """Write tokens.json and preview.html; an exporter that fails stops the command."""
    analysis = load_model(DesignAnalysis, folder / ANALYSIS_FILE)
    data = tokens.build_tokens(analysis)
    html = preview.render_preview(analysis)
    written = [
        save_json(data, folder / TOKENS_FILE),
        save_text(html, folder / PREVIEW_FILE),
    ]
    for path in written:
        print_success(f"Đã ghi {path}")
    return written
