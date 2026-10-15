"""Self-check of a DESIGN.md text before it is written (stream: synthesizer, phase GĐ6).

PUBLIC ENTRY POINT (frozen signature, called by synthesizer/synthesize.py):
    validate_design_md(text) -> list[ValidationIssue]
"""

from __future__ import annotations

from typing import Any

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.synthesis import ValidationIssue


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """(parsed YAML front matter, markdown body); missing/invalid front matter -> ({}, text)."""
    raise NotImplementedYetError("Tự kiểm DESIGN.md chưa được xây dựng (giai đoạn 6).")


def validate_design_md(text: str) -> list[ValidationIssue]:
    """Rules (ids aligned with the official linter): "front-matter" (error), "broken-ref"
    (error), "section-order" (error), "duplicate-section" (error), "missing-primary"
    (warning), "contrast-ratio" (warning, component text/background < 4.5:1),
    "orphaned-tokens" (info), "token-summary" (info)."""
    raise NotImplementedYetError("Tự kiểm DESIGN.md chưa được xây dựng (giai đoạn 6).")
