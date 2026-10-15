"""YAML front matter of DESIGN.md per the Google Labs DESIGN.md spec (version: alpha)
(stream: synthesizer, phase GĐ6).
"""

from __future__ import annotations

from typing import Any

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import DesignAnalysis


def build_front_matter(analysis: DesignAnalysis, *, examples: bool = False) -> dict[str, Any]:
    """{"version": "alpha", "name", "description", "colors", "typography", "rounded",
    "spacing", "components"} — components include "<name>-hover" state variants and only the
    spec properties (backgroundColor, textColor, typography, rounded, padding, size, height,
    width); values are token references when ComponentSpec.token_refs has one."""
    raise NotImplementedYetError("Front matter chưa được xây dựng (giai đoạn 6).")


def dump_front_matter(data: dict[str, Any]) -> str:
    """YAML text between "---" lines, keys in insertion order, hex colors quoted."""
    raise NotImplementedYetError("Front matter chưa được xây dựng (giai đoạn 6).")
