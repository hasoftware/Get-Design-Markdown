"""tokens.json in the W3C Design Tokens Community Group format (stream: exporters, phase GĐ8).

PUBLIC ENTRY POINT (frozen signature, called by exporters/export.py):
    build_tokens(analysis) -> dict[str, Any]
"""

from __future__ import annotations

from typing import Any

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import DesignAnalysis


def build_tokens(analysis: DesignAnalysis) -> dict[str, Any]:
    """DTCG document: groups color / typography / dimension (spacing, rounded) / shadow /
    breakpoint / component aliases, every token with "$type" and "$value", aliases written
    "{color.primary}"; dark theme under a "dark" group when present (pure, JSON-serializable)."""
    raise NotImplementedYetError("Xuất tokens.json chưa được xây dựng (giai đoạn 8).")
