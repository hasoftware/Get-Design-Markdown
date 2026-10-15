"""Run js/extract.js in the page: meta, CSS variables, computed styles of visible elements
(stream: collector — internal module).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from pydantic import ValidationError

from get_design_markdown.errors import CollectError
from get_design_markdown.models.raw import ComputedStyle, CssVariable, ElementSample
from get_design_markdown.resources import resource_text

EXTRACT_SCRIPT = ("collector", "js", "extract.js")


@dataclass
class DomExtraction:
    title: str = ""
    meta_description: str | None = None
    lang: str | None = None
    document_height: float = 0.0
    root_font_size_px: float = 16.0
    css_variables: list[CssVariable] = field(default_factory=list)
    elements_total: int = 0
    elements: list[ElementSample] = field(default_factory=list)
    #: Milliseconds spent in the page per phase (walk, elements, variables); diagnostics only.
    timing_ms: dict[str, float] = field(default_factory=dict)

    @property
    def truncated(self) -> bool:
        return self.elements_total > len(self.elements)


def extract_script() -> str:
    return resource_text(*EXTRACT_SCRIPT)


async def extract_dom(page: Page, *, max_elements: int) -> DomExtraction:
    """Evaluate js/extract.js and validate its result into models."""
    try:
        payload = await page.evaluate(
            extract_script(), {"mode": "extract", "maxElements": max_elements}
        )
    except PlaywrightError as error:
        first_line = str(error).strip().splitlines()[0] if str(error).strip() else ""
        raise CollectError(
            f"Không trích xuất được style từ trang: {first_line}",
            hint="Trang có thể đã chuyển hướng hoặc đóng trong lúc thu thập — hãy chạy lại.",
        ) from None
    if not isinstance(payload, dict):
        raise CollectError(
            "Script trích xuất không trả về dữ liệu hợp lệ.",
            hint="Trang có thể đã chuyển hướng trong lúc thu thập — hãy chạy lại lệnh.",
        )
    return parse_extraction(payload)


async def read_element_style(page: Page, index: int) -> tuple[ComputedStyle, float] | None:
    """Computed style of [data-gdm-index=index] and its longest transition (ms), or None."""
    result = await page.evaluate(extract_script(), {"mode": "style", "index": index})
    if not isinstance(result, dict):
        return None
    styles = ComputedStyle.model_validate(_style_payload(result.get("styles") or {}))
    return styles, float(result.get("transition_ms") or 0.0)


def _style_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Four-sided lists -> tuples; unknown keys dropped (forward compatible)."""
    known = ComputedStyle.model_fields
    data: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in known or value is None:
            continue
        data[key] = tuple(str(part) for part in value) if isinstance(value, list) else str(value)
    return data


def _element_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    data = {key: value for key, value in raw.items() if key in ElementSample.model_fields}
    styles = raw.get("styles")
    if isinstance(styles, Mapping):
        data["styles"] = _style_payload(styles)
    return data


def _float(value: object, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return default


def _optional_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def parse_extraction(payload: Mapping[str, Any]) -> DomExtraction:
    """Validate the JSON payload returned by extract.js (pure, unit-testable)."""
    try:
        elements = [
            ElementSample.model_validate(_element_payload(item))
            for item in payload.get("elements") or []
        ]
        variables = [
            CssVariable.model_validate(
                {key: item.get(key) for key in ("name", "declared", "resolved", "source")}
            )
            for item in payload.get("css_variables") or []
            if isinstance(item, Mapping) and item.get("name")
        ]
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "(gốc)"
        raise CollectError(
            f"Dữ liệu trích xuất từ trang không hợp lệ: {location} — {first['msg']}.",
            hint="Đây là lỗi của công cụ; hãy báo lỗi kèm URL trang.",
        ) from None
    timing = payload.get("timing_ms")
    return DomExtraction(
        title=str(payload.get("title") or "").strip(),
        meta_description=_optional_text(payload.get("meta_description")),
        lang=_optional_text(payload.get("lang")),
        document_height=_float(payload.get("document_height"), 0.0),
        root_font_size_px=_float(payload.get("root_font_size_px"), 16.0) or 16.0,
        css_variables=variables,
        elements_total=max(int(_float(payload.get("elements_total"), 0.0)), len(elements)),
        elements=elements,
        timing_ms=(
            {str(key): _float(value, 0.0) for key, value in timing.items()}
            if isinstance(timing, Mapping)
            else {}
        ),
    )
