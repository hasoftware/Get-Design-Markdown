"""Hover states of prominent buttons and links
(stream: collector — internal module).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from get_design_markdown.analyzer.common import (
    has_visible_border,
    is_rendered,
    meaningful_classes,
    own_background,
)
from get_design_markdown.analyzer.css_values import parse_px
from get_design_markdown.collector.dom import read_element_style
from get_design_markdown.models.raw import ComputedStyle, ElementSample, HoverState, join_sides
from get_design_markdown.resources import resource_text

HoverKind = Literal["button", "link"]

#: At most this many hovered elements share one visual signature (5 identical nav links
#: would otherwise use the whole budget).
MAX_PER_SIGNATURE = 2
#: Wait after moving the pointer: max(transition, MIN) capped at MAX (milliseconds).
MIN_HOVER_WAIT_MS = 150
MAX_HOVER_WAIT_MS = 1000
#: Pause after moving the pointer away again (milliseconds).
RESET_WAIT_MS = 100
#: Pause before retrying a covered element (menus close on a delay; milliseconds).
RETRY_WAIT_MS = 400

#: Two screens at the 1440x900 capture viewport.
PROMINENT_Y_PX = 1800.0
BUTTON_MIN_HEIGHT_PX = 24.0
BUTTON_MAX_HEIGHT_PX = 80.0
BUTTON_MIN_PADDING_X_PX = 8.0
LINK_MAX_HEIGHT_PX = 80.0
LINK_MAX_WIDTH_PX = 600.0

_BUTTON_CLASS_RE = re.compile(r"(^|[-_:])(btn|button|cta)($|[-_])", re.IGNORECASE)
_BUTTON_INPUT_TYPES = frozenset({"submit", "button", "reset"})
_LANDMARK_BONUS = {"header": 2.0, "nav": 2.0, "main": 1.0, "form": 1.0, "aside": 0.0}
_FOOTER_PENALTY = 3.0


@dataclass(frozen=True)
class _Candidate:
    index: int
    kind: HoverKind
    score: float
    signature: tuple[str, ...]


def _padding_x(element: ElementSample) -> float:
    right = parse_px(element.styles.padding[1]) or 0.0
    left = parse_px(element.styles.padding[3]) or 0.0
    return min(left, right)


def _looks_like_button_link(element: ElementSample) -> bool:
    """An <a> styled as a button: filled or outlined (or a btn/button class), padded,
    button-sized."""
    height = element.box.height
    if not BUTTON_MIN_HEIGHT_PX <= height <= BUTTON_MAX_HEIGHT_PX:
        return False
    if _padding_x(element) < BUTTON_MIN_PADDING_X_PX:
        return False
    if own_background(element) is not None or has_visible_border(element):
        return True
    return any(_BUTTON_CLASS_RE.search(name) for name in meaningful_classes(element.classes))


def hover_kind(element: ElementSample) -> HoverKind | None:
    """ "button", "link" or None (not a hover candidate); pure."""
    if element.in_overlay or not is_rendered(element):
        return None
    if element.box.width < 8 or element.box.height < 8:
        return None
    tag = element.tag
    if (
        tag == "button"
        or element.role == "button"
        or (tag == "input" and (element.input_type or "") in _BUTTON_INPUT_TYPES)
    ):
        return "button"
    if tag != "a" or not element.href:
        return None
    if element.href.lower().startswith(("javascript:", "mailto:", "tel:")) and not element.text:
        return None
    if _looks_like_button_link(element):
        return "button"
    if (
        element.text
        and element.box.height <= LINK_MAX_HEIGHT_PX
        and element.box.width <= LINK_MAX_WIDTH_PX
    ):
        return "link"
    return None


def _signature(element: ElementSample, kind: HoverKind) -> tuple[str, ...]:
    styles = element.styles
    return (
        kind,
        styles.background_color,
        styles.color,
        join_sides(styles.border_width),
        join_sides(styles.border_color),
        join_sides(styles.border_radius),
        styles.font_size,
        styles.font_weight,
        join_sides(styles.padding),
    )


def _score(element: ElementSample, kind: HoverKind) -> float:
    score = 0.0
    if element.box.y < PROMINENT_Y_PX:
        score += 3.0
    landmark = element.landmark
    if landmark == "footer":
        score -= _FOOTER_PENALTY
    else:
        score += _LANDMARK_BONUS.get(landmark or "", 0.5)
    area = max(element.box.width * element.box.height, 1.0)
    score += min(math.log10(area), 5.0) * 0.5
    if kind == "button":
        if element.text:
            score += 1.0
        if own_background(element) is not None:
            score += 1.0
    return score


def select_hover_candidates(
    elements: Sequence[ElementSample], *, max_buttons: int = 10, max_links: int = 10
) -> list[tuple[int, HoverKind]]:
    """Pick the most prominent buttons and links (element index, kind); pure.

    Buttons: <button>, [role=button], input[type=submit|button|reset] and <a> styled as
    buttons. Links: other <a href> with text. Overlay elements are excluded. Ranked by
    prominence (first two screens, header/nav/main before footer, area), at most
    MAX_PER_SIGNATURE per identical style; buttons first, then links.
    """
    candidates: list[_Candidate] = []
    for element in elements:
        kind = hover_kind(element)
        if kind is None:
            continue
        candidates.append(
            _Candidate(element.index, kind, _score(element, kind), _signature(element, kind))
        )
    result: list[tuple[int, HoverKind]] = []
    for kind, limit in (("button", max_buttons), ("link", max_links)):
        ranked = sorted(
            (item for item in candidates if item.kind == kind),
            key=lambda item: (-item.score, item.index),
        )
        per_signature: Counter[tuple[str, ...]] = Counter()
        chosen = 0
        for item in ranked:
            if chosen >= limit:
                break
            if per_signature[item.signature] >= MAX_PER_SIGNATURE:
                continue
            per_signature[item.signature] += 1
            result.append((item.index, item.kind))
            chosen += 1
    return result


def _has_border(styles: ComputedStyle) -> bool:
    return any(
        style not in ("none", "hidden") and (parse_px(width) or 0.0) > 0
        for width, style in zip(styles.border_width, styles.border_style, strict=True)
    )


def diff_styles(before: ComputedStyle, after: ComputedStyle) -> dict[str, tuple[str, str]]:
    """Changed ComputedStyle fields (four-sided fields via models.raw.join_sides); pure.

    border-color is ignored when neither state draws a border: it defaults to currentColor,
    so it only echoes a color change.
    """
    bordered = _has_border(before) or _has_border(after)
    changed: dict[str, tuple[str, str]] = {}
    for name in ComputedStyle.model_fields:
        old = getattr(before, name)
        new = getattr(after, name)
        if old == new or (name == "border_color" and not bordered):
            continue
        if isinstance(old, tuple):
            changed[name] = (join_sides(old), join_sides(new))
        else:
            changed[name] = (str(old), str(new))
    return changed


def hover_wait_ms(transition_ms: float) -> int:
    """How long to wait after hovering: max(transition, 150 ms), capped at 1 s; pure."""
    return int(min(max(transition_ms, MIN_HOVER_WAIT_MS), MAX_HOVER_WAIT_MS))


def execution_order(
    candidates: Sequence[tuple[int, HoverKind]], elements: Sequence[ElementSample]
) -> list[tuple[int, HoverKind]]:
    """Hover order: header/nav items last, because hovering mega-menu triggers can leave
    panels open that cover the rest of the page; otherwise document order; pure."""
    by_index = {element.index: element for element in elements}

    def key(item: tuple[int, HoverKind]) -> tuple[int, int]:
        element = by_index.get(item[0])
        in_header = element is not None and element.landmark in ("header", "nav")
        return (1 if in_header else 0, item[0])

    return sorted(candidates, key=key)


async def _park_pointer(page: Page) -> None:
    """Move the pointer to the bottom-left corner (away from headers and menus)."""
    viewport = page.viewport_size or {"width": 1440, "height": 900}
    await page.mouse.move(0, max(viewport["height"] - 1, 0))


async def _target(page: Page, index: int) -> dict[str, Any]:
    result = await page.evaluate(resource_text("collector", "js", "hover.js"), {"index": index})
    return dict(result) if isinstance(result, dict) else {"found": False, "ok": False}


async def _hover_one(page: Page, index: int, kind: HoverKind) -> HoverState | None:
    """Raises PlaywrightError / LookupError when the element cannot be hovered."""
    target = await _target(page, index)
    if target.get("found") and not target.get("ok"):
        # Something covers it (a menu opened by an earlier hover?): park and retry once.
        await _park_pointer(page)
        await page.wait_for_timeout(RETRY_WAIT_MS)
        target = await _target(page, index)
    if not target.get("ok"):
        raise LookupError("not hoverable")
    before = await read_element_style(page, index)
    if before is None:
        raise LookupError("element gone")
    await page.mouse.move(float(target["x"]), float(target["y"]))
    await page.wait_for_timeout(hover_wait_ms(before[1]))
    after = await read_element_style(page, index)
    await _park_pointer(page)
    await page.wait_for_timeout(RESET_WAIT_MS)
    if after is None:
        raise LookupError("element gone")
    changed = diff_styles(before[0], after[0])
    if not changed:
        return None
    return HoverState(element_index=index, kind=kind, changed=changed)


async def capture_hover_states(
    page: Page,
    elements: Sequence[ElementSample],
    *,
    max_buttons: int = 10,
    max_links: int = 10,
    warnings: list[str] | None = None,
) -> list[HoverState]:
    """Hover each candidate ([data-gdm-index]) and record the style diff.

    Elements that cannot be hovered (covered, detached, re-rendered) are skipped; one
    summary warning is appended to `warnings`. States are returned in candidate rank order.
    """
    candidates = select_hover_candidates(elements, max_buttons=max_buttons, max_links=max_links)
    rank = {item: position for position, item in enumerate(candidates)}
    results: list[tuple[int, HoverState]] = []
    failed = 0
    if candidates:
        with suppress(PlaywrightError):
            await _park_pointer(page)
    for index, kind in execution_order(candidates, elements):
        try:
            state = await _hover_one(page, index, kind)
        except (PlaywrightError, LookupError, KeyError, TypeError, ValueError):
            failed += 1
            continue
        if state is not None:
            results.append((rank[(index, kind)], state))
    states = [state for _, state in sorted(results, key=lambda item: item[0])]
    with suppress(PlaywrightError):
        await _park_pointer(page)
        await page.evaluate("() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' })")
        # Let menus opened by the last (header) hovers close before screenshots.
        await page.wait_for_timeout(RETRY_WAIT_MS)
    if failed and warnings is not None:
        warnings.append(
            f"Không hover được {failed}/{len(candidates)} phần tử (bị che khuất hoặc đã thay "
            "đổi trên trang) — đã bỏ qua."
        )
    return states
