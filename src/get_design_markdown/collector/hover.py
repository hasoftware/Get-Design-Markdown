"""Hover states of prominent buttons and links
(stream: collector — internal module, signatures may be refined by the collector stream).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from playwright.async_api import Page

from get_design_markdown.errors import NotImplementedYetError
from get_design_markdown.models.raw import ComputedStyle, ElementSample, HoverState

HoverKind = Literal["button", "link"]


def select_hover_candidates(
    elements: Sequence[ElementSample], *, max_buttons: int = 10, max_links: int = 10
) -> list[tuple[int, HoverKind]]:
    """Pick the most prominent buttons and links (element index, kind); pure."""
    raise NotImplementedYetError("Chọn phần tử hover chưa được xây dựng (giai đoạn 2).")


def diff_styles(before: ComputedStyle, after: ComputedStyle) -> dict[str, tuple[str, str]]:
    """Changed ComputedStyle fields (four-sided fields via models.raw.join_sides); pure."""
    raise NotImplementedYetError("So sánh style hover chưa được xây dựng (giai đoạn 2).")


async def capture_hover_states(
    page: Page,
    elements: Sequence[ElementSample],
    *,
    max_buttons: int = 10,
    max_links: int = 10,
) -> list[HoverState]:
    """Hover each candidate ([data-gdm-index]) and record the style diff."""
    raise NotImplementedYetError("Thu trạng thái hover chưa được xây dựng (giai đoạn 2).")
