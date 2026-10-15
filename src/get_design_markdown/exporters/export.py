"""Exports orchestration (frozen glue): analysis.json -> tokens.json + preview.html."""

from __future__ import annotations

from pathlib import Path

from get_design_markdown.console import print_success, print_warning
from get_design_markdown.errors import NotImplementedYetError
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
    """Write every available export; raise NotImplementedYetError only when none exists."""
    analysis = load_model(DesignAnalysis, folder / ANALYSIS_FILE)
    written: list[Path] = []
    missing: list[str] = []
    try:
        data = tokens.build_tokens(analysis)
    except NotImplementedYetError as error:
        missing.append(error.message)
    else:
        written.append(save_json(data, folder / TOKENS_FILE))
    try:
        html = preview.render_preview(analysis)
    except NotImplementedYetError as error:
        missing.append(error.message)
    else:
        written.append(save_text(html, folder / PREVIEW_FILE))
    if not written:
        raise NotImplementedYetError(
            "Chưa có bộ xuất tokens.json / preview.html nào được xây dựng (giai đoạn 8)."
        )
    for message in missing:
        print_warning(message)
    for path in written:
        print_success(f"Đã ghi {path}")
    return written
