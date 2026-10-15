"""--prompt-only: prompt.md + resized desktop/mobile screenshots for manual use in Claude Code /
claude.ai (no API call) (stream: synthesizer, phase GĐ6).

PUBLIC ENTRY POINTS (frozen signatures, called by synthesizer/synthesize.py):
    prepare_prompt_images(folder, screenshots, max_side=...) -> list[str]
    render_prompt_md(analysis, design_md, image_paths, lang=...) -> str
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.analysis import DesignAnalysis
from get_design_markdown.models.raw import Screenshot

#: Longest side of images referenced by prompt.md.
MAX_IMAGE_SIDE = 1568


def prepare_prompt_images(
    folder: Path, screenshots: Sequence[Screenshot], *, max_side: int = MAX_IMAGE_SIDE
) -> list[str]:
    """Copy the light desktop (1440) and mobile (390) screenshots into
    folder/prompt-images/, cropped to at most 3 screens tall and resized so the longest side
    is <= max_side (Pillow). Returns paths relative to `folder`; missing files are skipped."""
    raise NotImplementedYetError("Xuất ảnh cho prompt chưa được xây dựng (giai đoạn 6).")


def render_prompt_md(
    analysis: DesignAnalysis,
    design_md: str,
    image_paths: Sequence[str],
    *,
    lang: str = "en",
) -> str:
    """prompt.md: instructions ("rewrite the prose only, never change numbers, token names or
    references"), the rule-built DESIGN.md, a compact data summary and the image list."""
    raise NotImplementedYetError("Xuất prompt chưa được xây dựng (giai đoạn 6).")
