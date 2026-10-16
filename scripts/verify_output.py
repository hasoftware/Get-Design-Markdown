"""Independent structural verifier of a gdm output folder (output/<domain>/).

A second opinion on the whole pipeline: it re-reads every file a run produced and checks the
schemas, the cross-file consistency, the DESIGN.md contract (front matter, token references,
section order, reference note, "no invented numbers" in the front matter and in the body text,
documented WCAG failures), that every stage was built, the W3C Design Tokens file and the
preview page. It deliberately never imports the synthesizer or the
exporters -- only the frozen models, analyzer.css_values and analyzer.naming -- so a bug in a
stage cannot vouch for itself.

Usage:
    python scripts/verify_output.py <site-folder> [--strict] [--json] [--partial]

Exit status: 0 without errors (and without warnings when --strict), 1 otherwise, 2 for invalid
arguments. Findings are printed in Vietnamese; check ids are "<file>.<rule>".
"""

from __future__ import annotations

import argparse
import io
import itertools
import json
import re
import struct
import sys
import textwrap
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal, ParamSpec, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from get_design_markdown.analyzer import css_values, naming
from get_design_markdown.analyzer.css_values import RGBA
from get_design_markdown.models.analysis import (
    ANALYSIS_SCHEMA_VERSION,
    ColorPalette,
    ColorRole,
    ComponentSpec,
    DesignAnalysis,
    TypeLevel,
)
from get_design_markdown.models.raw import (
    DEFAULT_SCREENSHOT_WIDTHS,
    MAX_TEXT_LENGTH,
    RAW_SCHEMA_VERSION,
    PageCapture,
    RawCapture,
    Screenshot,
)
from get_design_markdown.models.synthesis import SYNTHESIS_SCHEMA_VERSION, SynthesisResult

Severity = Literal["error", "warning", "info"]
SEVERITY_ORDER: Final[dict[str, int]] = {"error": 0, "warning": 1, "info": 2}
SEVERITY_LABELS: Final[dict[str, str]] = {"error": "LỖI", "warning": "CẢNH BÁO", "info": "GHI CHÚ"}

RAW_FILE: Final = "raw.json"
ANALYSIS_FILE: Final = "analysis.json"
SYNTHESIS_FILE: Final = "synthesis.json"
DESIGN_FILE: Final = "DESIGN.md"
PROMPT_FILE: Final = "prompt.md"
TOKENS_FILE: Final = "tokens.json"
PREVIEW_FILE: Final = "preview.html"

#: "## " sections of DESIGN.md in the mandatory order (Google Labs spec + Agent Prompt Guide).
DESIGN_SECTIONS: Final = (
    "Overview",
    "Colors",
    "Typography",
    "Layout",
    "Elevation & Depth",
    "Shapes",
    "Components",
    "Do's and Don'ts",
    "Agent Prompt Guide",
)
REQUIRED_SECTIONS: Final = ("Overview", "Colors", "Typography")
DOS_AND_DONTS: Final = "Do's and Don'ts"
AGENT_PROMPT_GUIDE: Final = "Agent Prompt Guide"
#: Aliases the official linter accepts; this project writes the canonical names.
SECTION_ALIASES: Final[dict[str, str]] = {
    "Brand & Style": "Overview",
    "Layout & Spacing": "Layout",
    "Elevation": "Elevation & Depth",
}
FRONT_MATTER_KEYS: Final = ("version", "name", "description", "omitted", *naming.TOKEN_GROUPS)
#: Component properties defined by the spec (others are accepted with a linter warning).
COMPONENT_SUB_TOKENS: Final = (
    "backgroundColor",
    "textColor",
    "typography",
    "rounded",
    "padding",
    "size",
    "height",
    "width",
)
TYPOGRAPHY_PROPERTIES: Final = (
    "fontFamily",
    "fontSize",
    "fontWeight",
    "lineHeight",
    "letterSpacing",
    "fontFeature",
    "fontVariation",
)
COLOR_PROPERTIES: Final = ("backgroundColor", "textColor", "borderColor", "placeholderColor")
#: ComponentSpec.properties / states keys documented in models/analysis.py (frozen contract).
#: The single external stylesheet preview.html may load (Google Fonts for the site's families).
GOOGLE_FONTS_CSS2: Final = "https://fonts.googleapis.com/css2?"

COMPONENT_PROPERTY_KEYS: Final = (
    "backgroundColor",
    "textColor",
    "borderColor",
    "borderWidth",
    "rounded",
    "padding",
    "height",
    "width",
    "gap",
    "fontFamily",
    "fontSize",
    "fontWeight",
    "lineHeight",
    "letterSpacing",
    "textTransform",
    "textDecoration",
    "shadow",
    "backdropFilter",
    "placeholderColor",
    "position",
    "opacity",
)
DIMENSION_PROPERTIES: Final = ("rounded", "padding", "size", "height", "width", "gap")

AA_MINIMUM: Final = 4.5
CONTRAST_TOLERANCE: Final = 0.02
COMPONENT_CONTRAST_TOLERANCE: Final = 0.05
PX_TOLERANCE: Final = 0.5
BREAKPOINT_TOLERANCE_PX: Final = 1.0
COLOR_MATCH_DELTA_E: Final = 3.0
COLOR_MATCH_ALPHA: Final = 0.05
LINE_HEIGHT_TOLERANCE: Final = 0.011
MAX_COLOR_TOKENS: Final = 14
MAX_PROMPT_IMAGE_SIDE: Final = 1568
#: Words of the NotImplementedYetError messages ("… chưa được xây dựng …"): a stage that is
#: missing from the build.
STAGE_MISSING_MARKER: Final = "chưa được xây dựng"
ROOT_FONT_SIZE_PX: Final = 16.0
MAX_REF_DEPTH: Final = 10
EXAMPLES_SHOWN: Final = 5

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
_DIMENSION_RE = re.compile(r"^(-?(?:\d+(?:\.\d*)?|\.\d+))(px|em|rem)$")
_NUMBER_RE = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)$")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
#: Brace expressions that look like a token reference, including common mistakes
#: ({color.primary}, {colors.Primary}, {radius.md}...).
_LOOSE_REF_RE = re.compile(
    r"\{\s*(colors?|typography|rounded|radius|radii|spacing|components?|fonts?)\s*\.\s*([^{}\s]*)\s*\}",
    re.IGNORECASE,
)
_DECIMAL_RE = re.compile(r"(?<![\w.#])(\d+)[.,](\d+)")
_INTEGER_LIKE_RE = re.compile(r"(?<![\w#.])(\d+(?:\.\d+)?)")
_PROMPT_IMAGE_RE = re.compile(r"prompt-images/[\w./-]+")

M = TypeVar("M", bound=BaseModel)
P = ParamSpec("P")
R = TypeVar("R")


# ─── Findings ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Finding:
    """One verification result. `check` is "<file>.<rule>", e.g. "design.broken-ref"."""

    check: str
    severity: Severity
    message: str


@dataclass
class _Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: Severity, check: str, message: str) -> None:
        self.findings.append(Finding(check=check, severity=severity, message=message))

    def error(self, check: str, message: str) -> None:
        self.add("error", check, message)

    def warning(self, check: str, message: str) -> None:
        self.add("warning", check, message)

    def info(self, check: str, message: str) -> None:
        self.add("info", check, message)


def _examples(items: Sequence[str], limit: int = EXAMPLES_SHOWN) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" … (+{len(items) - limit})" if len(items) > limit else "")


def _report_missing(
    report: _Report,
    check: str,
    filename: str,
    producer: str,
    *,
    partial: bool,
    severity: Severity = "error",
) -> None:
    if partial:
        report.info(check, f"Chưa có {filename} (tạo bằng '{producer}').")
    else:
        report.add(severity, check, f"Thiếu {filename} — chạy '{producer}'.")


# ─── Small parsing helpers ───────────────────────────────────────────────────


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def dimension_px(value: Any) -> float | None:
    """DESIGN.md Dimension ("16px", "1.5rem", "-0.02em") in px (em/rem on a 16px root)."""
    if not isinstance(value, str):
        return None
    match = _DIMENSION_RE.match(value.strip())
    if match is None:
        return None
    number = float(match.group(1))
    return number if match.group(2) == "px" else number * ROOT_FONT_SIZE_PX


def _is_dimension(value: Any) -> bool:
    return isinstance(value, str) and _DIMENSION_RE.match(value.strip()) is not None


def _is_ref(value: Any) -> bool:
    return isinstance(value, str) and naming.parse_token_ref(value) is not None


def _canonical_hex(value: Any) -> str | None:
    return css_values.to_hex(value) if isinstance(value, str) else None


def _colors_close(first: str | RGBA | None, second: str | RGBA | None) -> bool:
    one = first if isinstance(first, RGBA) else css_values.parse_color(first)
    two = second if isinstance(second, RGBA) else css_values.parse_color(second)
    if one is None or two is None:
        return False
    if abs(one.alpha - two.alpha) > COLOR_MATCH_ALPHA:
        return False
    if one.is_transparent and two.is_transparent:
        return True
    return css_values.delta_e_2000(one, two) <= COLOR_MATCH_DELTA_E


def _px_values(value: Any) -> list[float] | None:
    """ "12px 20px" -> [12, 20, 12, 20] (CSS shorthand expanded); None when not all lengths."""
    if _is_number(value):
        return [float(value)] * 4
    if not isinstance(value, str) or not value.strip():
        return None
    parts = value.split()
    values: list[float] = []
    for part in parts[:4]:
        px = css_values.parse_px(part)
        if px is None:
            return None
        values.append(px)
    return list(css_values.parse_sides_px([f"{number}px" for number in values]))


def _normalize_text(value: Any) -> str:
    return " ".join(str(value).strip().strip("\"'").lower().split())


def ratio_mentioned(ratio: float, text: str) -> bool:
    """True when `text` contains a decimal number equal to `ratio` up to its own precision."""
    for match in _DECIMAL_RE.finditer(text):
        decimals = match.group(2)
        number = float(f"{match.group(1)}.{decimals}")
        if abs(number - ratio) <= CONTRAST_TOLERANCE + 0.5 * 10 ** -len(decimals):
            return True
    return False


def _numbers_in(text: str) -> list[float]:
    return [float(match.group(1)) for match in _INTEGER_LIKE_RE.finditer(text)]


def _utc_date(moment: datetime) -> date:
    return (moment.astimezone(UTC) if moment.tzinfo else moment).date()


def _date_variants(moment: datetime) -> set[str]:
    day = _utc_date(moment)
    month_name = day.strftime("%B")
    return {
        day.isoformat(),
        f"{day.day}/{day.month}/{day.year}",
        f"{day:%d/%m/%Y}",
        f"{day.day}.{day.month}.{day.year}",
        f"{month_name} {day.day}, {day.year}",
        f"{day.day} {month_name} {day.year}",
        f"{day:%b} {day.day}, {day.year}",
        f"ngày {day.day} tháng {day.month} năm {day.year}",
        f"{day.day} tháng {day.month}, {day.year}",
    }


# ─── Images (stdlib only) ────────────────────────────────────────────────────

_JPEG_SOF_MARKERS: Final = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def image_size(path: Path) -> tuple[int, int] | None:
    """(width, height) of a PNG, JPEG or WebP file read from its header; None otherwise."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if data.startswith(b"\xff\xd8"):
        return _jpeg_size(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_size(data)
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker == 0xFF:
            index += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        length = int.from_bytes(data[index + 2 : index + 4], "big")
        if marker in _JPEG_SOF_MARKERS:
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        if length < 2:
            return None
        index += 2 + length
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        return (
            1 + int.from_bytes(data[24:27], "little"),
            1 + int.from_bytes(data[27:30], "little"),
        )
    if chunk == b"VP8 " and len(data) >= 30:
        return (
            int.from_bytes(data[26:28], "little") & 0x3FFF,
            int.from_bytes(data[28:30], "little") & 0x3FFF,
        )
    if chunk == b"VP8L" and len(data) >= 25:
        bits = data[21:25]
        width = 1 + (((bits[1] & 0x3F) << 8) | bits[0])
        height = 1 + (((bits[3] & 0x0F) << 10) | (bits[2] << 2) | ((bits[1] & 0xC0) >> 6))
        return width, height
    return None


# ─── JSON models ─────────────────────────────────────────────────────────────


def _load_model(
    path: Path, model_type: type[M], check: str, report: _Report, schema_version: int
) -> M | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        report.error(check, f"Không đọc được {path.name}: {error}.")
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        report.error(
            check,
            f"{path.name} không phải JSON hợp lệ (dòng {error.lineno}, cột {error.colno}): "
            f"{error.msg}.",
        )
        return None
    if isinstance(data, dict) and data.get("schema_version", schema_version) != schema_version:
        report.error(
            check,
            f"{path.name} có schema_version {data.get('schema_version')!r}, "
            f"phiên bản hiện tại là {schema_version} — hãy chạy lại bước tạo ra file.",
        )
    try:
        return model_type.model_validate_json(text)
    except ValidationError as error:
        first = error.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "(gốc)"
        report.error(
            check,
            f"{path.name} không đúng schema: {location} — {first['msg']} "
            f"({error.error_count()} lỗi).",
        )
        return None


# ─── Screenshots ─────────────────────────────────────────────────────────────


def _check_unreferenced_screenshots(
    folder: Path, pages: list[PageCapture], report: _Report
) -> None:
    """PNGs directly under screenshots/ that raw.json does not reference (left over from an
    earlier capture of other sub-pages, or added by hand)."""
    shots_dir = folder / "screenshots"
    if not shots_dir.is_dir():
        return
    referenced = {shot.path for page in pages for shot in page.screenshots}
    for file in sorted(shots_dir.glob("*.png")):
        relative = f"screenshots/{file.name}"
        if relative not in referenced:
            report.warning(
                "raw.screenshots",
                f"Ảnh chụp {relative} không được raw.json tham chiếu "
                "(còn sót từ lần thu thập trước?).",
            )


def _expected_screenshot_path(page_name: str, shot: Screenshot) -> str:
    scheme = "-dark" if shot.color_scheme == "dark" else ""
    return f"screenshots/{page_name}{scheme}-{shot.viewport_width}.png"


def _check_screenshot(
    folder: Path,
    shot: Screenshot,
    report: _Report,
    check: str,
    *,
    page: PageCapture | None = None,
) -> None:
    relative = shot.path
    pure = PurePosixPath(relative)
    if (
        "\\" in relative
        or pure.is_absolute()
        or ".." in pure.parts
        or re.match(r"^[A-Za-z]:", relative)
    ):
        report.error(
            check,
            f"Ảnh '{shot.name}': đường dẫn '{relative}' phải là đường dẫn tương đối (dấu /) "
            "bên trong thư mục output.",
        )
        return
    file = folder / relative
    if not file.is_file():
        report.error(check, f"Ảnh '{shot.name}': không tìm thấy file {relative}.")
        return
    size = image_size(file)
    if size is None:
        report.warning(check, f"Ảnh {relative} không phải PNG/JPEG/WebP hợp lệ.")
    elif shot.viewport_width > 0 and (size[0] == 0 or size[0] % shot.viewport_width != 0):
        report.warning(
            check,
            f"Ảnh {relative} rộng {size[0]}px, không khớp viewport {shot.viewport_width}px.",
        )
    if page is None:
        return
    if shot.color_scheme != page.color_scheme:
        report.warning(
            check,
            f"Ảnh {relative} ghi color_scheme '{shot.color_scheme}' nhưng trang "
            f"'{page.name}' là bản '{page.color_scheme}'.",
        )
    expected = _expected_screenshot_path(page.name, shot)
    if relative != expected:
        report.warning(check, f"Ảnh '{relative}' không theo quy ước tên '{expected}'.")


# ─── raw.json ────────────────────────────────────────────────────────────────


def _check_elements(
    page: PageCapture, label: str, report: _Report, max_elements: int | None
) -> None:
    count = len(page.elements)
    bad_index = [
        str(position) for position, element in enumerate(page.elements) if element.index != position
    ]
    if bad_index:
        report.error(
            "raw.elements",
            f"{label}: {len(bad_index)} phần tử có index khác vị trí trong danh sách "
            f"(vị trí {_examples(bad_index)}).",
        )
    bad_parent = [
        str(element.index)
        for element in page.elements
        if element.parent_index is not None and not 0 <= element.parent_index < element.index
    ]
    if bad_parent:
        report.error(
            "raw.elements",
            f"{label}: {len(bad_parent)} phần tử có parent_index không trỏ tới phần tử đứng "
            f"trước (index {_examples(bad_parent)}).",
        )
    bad_hover = [
        str(state.element_index)
        for state in page.hover_states
        if not 0 <= state.element_index < count
    ]
    if bad_hover:
        report.error(
            "raw.hover",
            f"{label}: {len(bad_hover)} trạng thái hover trỏ tới phần tử không tồn tại "
            f"(element_index {_examples(bad_hover)}).",
        )
    if max_elements is not None and count > max_elements:
        report.warning(
            "raw.elements",
            f"{label}: {count} phần tử, vượt giới hạn max_elements={max_elements}.",
        )
    if count and page.elements_total < count:
        report.warning(
            "raw.elements",
            f"{label}: elements_total={page.elements_total} nhỏ hơn số phần tử đã lưu ({count}).",
        )


def _check_raw(folder: Path, report: _Report, *, partial: bool) -> RawCapture | None:
    path = folder / RAW_FILE
    if not path.is_file():
        _report_missing(report, "raw.missing", RAW_FILE, "gdm collect <url>", partial=partial)
        return None
    raw = _load_model(path, RawCapture, "raw.schema", report, RAW_SCHEMA_VERSION)
    if raw is None:
        return None
    if not raw.pages:
        report.error("raw.pages", "raw.json không có trang nào (pages rỗng).")
        return raw
    if raw.domain != folder.name:
        report.warning(
            "raw.domain",
            f"raw.json ghi domain '{raw.domain}' nhưng thư mục tên '{folder.name}'.",
        )
    if raw.robots is None:
        report.warning("raw.robots", "raw.json thiếu kết quả kiểm tra robots.txt.")
    names = [page.name for page in raw.pages]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        report.error(
            "raw.pages",
            f"Trùng tên trang: {', '.join(duplicates)} — ảnh chụp sẽ ghi đè lên nhau.",
        )
    main = raw.pages[0]
    if not main.elements:
        report.error(
            "raw.elements",
            f"Trang chính '{main.name}' không có phần tử nào được lấy mẫu — trang có thể bị "
            "chặn hoặc chưa tải xong.",
        )
    for page in raw.pages[1:]:
        if not page.elements:
            report.warning("raw.elements", f"Trang con '{page.name}' không có phần tử nào.")

    options = raw.options
    captures: list[tuple[str, PageCapture]] = [(f"Trang '{page.name}'", page) for page in raw.pages]
    if raw.dark is not None:
        captures.append((f"Bản dark '{raw.dark.name}'", raw.dark))
        if raw.dark.color_scheme != "dark":
            report.warning("raw.dark", "Bản dark mode trong raw.json có color_scheme khác 'dark'.")
        if not raw.dark.elements:
            report.warning("raw.dark", "Bản dark mode không có phần tử nào.")
    elif options is not None and options.dark:
        report.warning("raw.dark", "Đã yêu cầu --dark nhưng raw.json không có bản dark mode.")
    if options is not None and options.pages > len(raw.pages) - 1:
        report.info(
            "raw.pages",
            f"Yêu cầu {options.pages} trang con nhưng chỉ thu được {len(raw.pages) - 1}.",
        )
    max_elements = options.max_elements if options is not None else None
    for label, page in captures:
        _check_elements(page, label, report, max_elements)
        for shot in page.screenshots:
            _check_screenshot(folder, shot, report, "raw.screenshots", page=page)
    _check_unreferenced_screenshots(folder, [page for _, page in captures], report)

    widths = list(options.screenshot_widths) if options is not None else []
    widths = widths or list(DEFAULT_SCREENSHOT_WIDTHS)
    shot_widths = {shot.viewport_width for shot in main.screenshots}
    missing_shots = [str(width) for width in widths if width not in shot_widths]
    if missing_shots:
        report.warning(
            "raw.screenshots",
            f"Trang chính thiếu ảnh chụp ở viewport {', '.join(missing_shots)}px.",
        )
    layout_widths = {snapshot.viewport_width for snapshot in main.layouts}
    missing_layouts = [str(width) for width in widths if width not in layout_widths]
    if missing_layouts:
        report.warning(
            "raw.layouts",
            f"Trang chính thiếu layout snapshot ở viewport {', '.join(missing_layouts)}px — "
            "không so sánh được hành vi responsive.",
        )
    if not raw.stylesheets:
        report.warning(
            "raw.stylesheets",
            "Không bắt được stylesheet nào — @media, @font-face và biến CSS sẽ thiếu.",
        )
    return raw


# ─── analysis.json ───────────────────────────────────────────────────────────


ROLE_LOOKUP: Final = "color-roles"


def _token_lookup(analysis: DesignAnalysis) -> dict[str, dict[str, Any]]:
    return {
        ROLE_LOOKUP: {token.name: token.role for token in analysis.colors.tokens},
        naming.COLORS: {token.name: token.value for token in analysis.colors.tokens},
        naming.TYPOGRAPHY: {level.name: level for level in analysis.typography.levels},
        naming.ROUNDED: {step.name: step.value_px for step in analysis.radii},
        naming.SPACING: {step.name: step.value_px for step in analysis.spacing.steps},
        naming.COMPONENTS: {component.name: component for component in analysis.components},
    }


def _check_palette(palette: ColorPalette, label: str, report: _Report, *, required: bool) -> None:
    tokens = palette.tokens
    if not tokens:
        report.add(
            "warning" if required else "info", "analysis.tokens", f"{label}: chưa có token màu."
        )
    names = [token.name for token in tokens]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        report.error("analysis.tokens", f"{label}: trùng tên token {', '.join(duplicates)}.")
    for token in tokens:
        role = naming.COLOR_TOKEN_ROLES.get(token.name)
        if role is None:
            report.warning(
                "analysis.tokens",
                f"{label}: token '{token.name}' không thuộc bộ tên chuẩn "
                "(naming.COLOR_TOKEN_ROLES).",
            )
        elif token.role != role:
            report.warning(
                "analysis.tokens",
                f"{label}: token '{token.name}' có vai trò '{token.role.value}' nhưng tên chuẩn "
                f"này mang vai trò '{role.value}'.",
            )
    if len(tokens) > MAX_COLOR_TOKENS:
        report.warning(
            "analysis.tokens",
            f"{label}: {len(tokens)} token màu — bảng màu nên gọn 8–14 token "
            "(cụm đầy đủ chỉ nằm trong clusters).",
        )
    cluster_ids = {cluster.id for cluster in palette.clusters}
    for token in tokens:
        if token.cluster_id is not None and token.cluster_id not in cluster_ids:
            report.warning(
                "analysis.tokens",
                f"{label}: token '{token.name}' trỏ tới cụm '{token.cluster_id}' không tồn tại.",
            )

    by_name = {token.name: token for token in tokens}
    canvas = by_name.get("canvas")
    canvas_color = css_values.parse_color(canvas.value) if canvas is not None else None
    for pair in palette.contrast_pairs:
        context = pair.context or f"{pair.foreground} / {pair.background}"
        measured = [css_values.contrast_ratio(pair.foreground, pair.background)]
        if canvas_color is not None:
            measured.append(
                css_values.contrast_ratio(pair.foreground, pair.background, base=canvas_color)
            )
        if all(abs(value - pair.ratio) > CONTRAST_TOLERANCE for value in measured):
            report.error(
                "analysis.contrast",
                f"{label}: cặp '{context}' ({pair.foreground} trên {pair.background}) ghi "
                f"{pair.ratio}:1 nhưng tính lại được {measured[0]:.2f}:1.",
            )
        flags = (pair.passes_aa, pair.passes_aa_large, pair.passes_aaa)
        if all(css_values.contrast_levels(value) != flags for value in [pair.ratio, *measured]):
            report.error(
                "analysis.contrast",
                f"{label}: cờ AA/AA-large/AAA của cặp '{context}' không khớp tỉ lệ {pair.ratio}:1.",
            )
        for role_label, token_name, color in (
            ("chữ", pair.foreground_token, pair.foreground),
            ("nền", pair.background_token, pair.background),
        ):
            if token_name is None:
                continue
            matched = by_name.get(token_name)
            if matched is None:
                report.error(
                    "analysis.contrast",
                    f"{label}: cặp '{context}' tham chiếu token '{token_name}' không tồn tại.",
                )
            elif not _colors_close(matched.value, color):
                report.warning(
                    "analysis.contrast",
                    f"{label}: token '{token_name}' ({matched.value}) khác màu {role_label} "
                    f"{color} của cặp '{context}'.",
                )


def _check_type_levels(analysis: DesignAnalysis, report: _Report) -> None:
    levels = analysis.typography.levels
    names = [level.name for level in levels]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        report.error("analysis.typography", f"Trùng tên cấp chữ: {', '.join(duplicates)}.")
    families = {family.name.lower() for family in analysis.typography.families}
    for level in levels:
        if level.name not in naming.TYPE_LEVEL_ORDER:
            report.warning(
                "analysis.typography",
                f"Cấp chữ '{level.name}' không thuộc tên chuẩn (naming.TYPE_LEVEL_ORDER).",
            )
        if level.line_height is not None and level.line_height_px is not None and level.size_px:
            expected = level.line_height_px / level.size_px
            if abs(expected - level.line_height) > 0.02:
                report.warning(
                    "analysis.typography",
                    f"Cấp chữ '{level.name}': line_height {level.line_height} không khớp "
                    f"{level.line_height_px:g}px / {level.size_px:g}px.",
                )
        if families and level.font_family.lower() not in families:
            report.warning(
                "analysis.typography",
                f"Cấp chữ '{level.name}' dùng font '{level.font_family}' không có trong families.",
            )
    for larger, smaller in itertools.pairwise(levels):
        if smaller.size_px > larger.size_px + 0.01:
            report.warning(
                "analysis.typography",
                f"Cấp chữ phải xếp từ lớn đến nhỏ: '{smaller.name}' ({smaller.size_px:g}px) "
                f"đứng sau '{larger.name}' ({larger.size_px:g}px).",
            )
            break


def _check_scale(
    steps: Sequence[Any],
    label: str,
    check: str,
    names_allowed: Sequence[str],
    report: _Report,
) -> None:
    names = [step.name for step in steps]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        report.error(check, f"{label}: trùng tên bước {', '.join(duplicates)}.")
    for step in steps:
        if step.name not in names_allowed and re.fullmatch(r"\d+xl", step.name) is None:
            report.warning(check, f"{label}: tên bước '{step.name}' không thuộc tên chuẩn.")
    values = [float(step.value_px) for step in steps]
    if any(second <= first for first, second in itertools.pairwise(values)):
        report.warning(check, f"{label}: các bước phải tăng dần ({_examples(names)}).")


def _check_scales(analysis: DesignAnalysis, report: _Report) -> None:
    _check_scale(
        analysis.spacing.steps,
        "Thang spacing",
        "analysis.spacing",
        naming.SPACING_STEP_NAMES,
        report,
    )
    _check_scale(
        analysis.radii,
        "Thang bo góc",
        "analysis.radii",
        (*naming.RADIUS_STEP_NAMES, naming.FULL_RADIUS_NAME),
        report,
    )
    for step in analysis.radii:
        if step.name == naming.FULL_RADIUS_NAME and step.value_px < naming.PILL_RADIUS_PX:
            report.warning(
                "analysis.radii",
                f"Bước 'full' chỉ {step.value_px:g}px — dành cho pill/hình tròn (≥ "
                f"{naming.PILL_RADIUS_PX:g}px).",
            )
    shadow_names = [level.name for level in analysis.shadows]
    duplicates = sorted({name for name in shadow_names if shadow_names.count(name) > 1})
    if duplicates:
        report.error("analysis.shadows", f"Trùng tên mức đổ bóng: {', '.join(duplicates)}.")
    for level in analysis.shadows:
        if level.name not in naming.SHADOW_LEVEL_NAMES:
            report.warning("analysis.shadows", f"Mức đổ bóng '{level.name}' không thuộc tên chuẩn.")
        if not css_values.visible_shadow_layers(level.css):
            report.warning(
                "analysis.shadows",
                f"Mức đổ bóng '{level.name}' không có lớp bóng nhìn thấy được: '{level.css}'.",
            )


def _check_breakpoints(analysis: DesignAnalysis, raw: RawCapture | None, report: _Report) -> None:
    measured: list[float] = []
    if raw is not None:
        for sheet in raw.stylesheets:
            for query in sheet.media_queries:
                measured.extend(
                    value for value in (query.min_width_px, query.max_width_px) if value is not None
                )
    previous: float | None = None
    for breakpoint in analysis.breakpoints:
        values = [
            value
            for value in (breakpoint.min_width_px, breakpoint.max_width_px)
            if value is not None
        ]
        if not values:
            report.warning(
                "analysis.breakpoints", f"Breakpoint '{breakpoint.name}' không có độ rộng nào."
            )
            continue
        if raw is not None and raw.stylesheets:
            for value in values:
                if not any(abs(value - other) <= BREAKPOINT_TOLERANCE_PX for other in measured):
                    report.warning(
                        "analysis.breakpoints",
                        f"Breakpoint '{breakpoint.name}' ({value:g}px) không xuất hiện trong "
                        "@media nào của raw.json — chỉ ghi breakpoint đo được.",
                    )
        current = values[0]
        if previous is not None and current < previous:
            report.warning("analysis.breakpoints", "Breakpoint phải xếp theo độ rộng tăng dần.")
        previous = current


def _check_analysis_ref(
    owner: str,
    key: str,
    ref: str,
    measured: str | None,
    component: ComponentSpec,
    lookup: Mapping[str, Mapping[str, Any]],
    report: _Report,
) -> None:
    parsed = naming.parse_token_ref(ref)
    if parsed is None:
        report.error(
            "analysis.refs",
            f"{owner}.{key}: '{ref}' không phải tham chiếu {{nhóm.tên}} hợp lệ.",
        )
        return
    group, name = parsed
    target = lookup.get(group, {}).get(name)
    if target is None:
        report.error(
            "analysis.refs", f"{owner}.{key}: {ref} không trỏ tới token nào trong analysis.json."
        )
        return
    role = lookup.get(ROLE_LOOKUP, {}).get(name) if group == naming.COLORS else None
    if key in ("backgroundColor", "textColor") and role == ColorRole.BORDER:
        report.warning(
            "analysis.ref-role",
            f"{owner}.{key} tham chiếu {ref} là token màu viền — nên dùng token nền/chữ.",
        )
    if group == naming.TYPOGRAPHY and isinstance(target, TypeLevel):
        size = css_values.parse_px(component.properties.get("fontSize"))
        if size is not None and abs(size - target.size_px) > PX_TOLERANCE:
            report.warning(
                "analysis.refs",
                f"{owner}: fontSize {size:g}px nhưng {ref} có cỡ {target.size_px:g}px.",
            )
        return
    if measured is None:
        return
    if group == naming.COLORS:
        if not _colors_close(str(target), measured):
            report.warning("analysis.refs", f"{owner}.{key} = {measured} nhưng {ref} = {target}.")
    elif group in (naming.ROUNDED, naming.SPACING) and isinstance(target, float):
        values = _px_values(measured)
        if values is None or len(set(values)) != 1:
            return
        value = values[0]
        pill = target >= naming.PILL_RADIUS_PX and (
            value >= naming.PILL_RADIUS_PX
            or (key == "rounded" and _rounds_the_ends(value, component.properties))
        )
        if not pill and abs(value - target) > PX_TOLERANCE:
            report.warning(
                "analysis.refs", f"{owner}.{key} = {measured} nhưng {ref} = {target:g}px."
            )


def _check_analysis_components(analysis: DesignAnalysis, report: _Report) -> None:
    lookup = _token_lookup(analysis)
    names = [component.name for component in analysis.components]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        report.error("analysis.components", f"Trùng tên component: {', '.join(duplicates)}.")
    for component in analysis.components:
        for key, ref in component.token_refs.items():
            _check_analysis_ref(
                component.name,
                key,
                ref,
                component.properties.get(key),
                component,
                lookup,
                report,
            )
        for state, refs in component.state_token_refs.items():
            if state not in component.states:
                report.warning(
                    "analysis.refs",
                    f"{component.name}: state_token_refs có trạng thái '{state}' nhưng states "
                    "không có.",
                )
            values = component.states.get(state, {})
            for key, ref in refs.items():
                _check_analysis_ref(
                    naming.state_variant_name(component.name, state),
                    key,
                    ref,
                    values.get(key),
                    component,
                    lookup,
                    report,
                )
        groups = [("", component.properties)] + [
            (f" ({state})", values) for state, values in component.states.items()
        ]
        for suffix, properties in groups:
            for key in COLOR_PROPERTIES:
                value = properties.get(key)
                if value is not None and css_values.parse_color(value) is None:
                    report.warning(
                        "analysis.components",
                        f"{component.name}{suffix}.{key} = '{value}' không phải màu hợp lệ.",
                    )
            unknown = [key for key in properties if key not in COMPONENT_PROPERTY_KEYS]
            if unknown:
                report.warning(
                    "analysis.components",
                    f"{component.name}{suffix}: khóa {', '.join(unknown)} không có trong danh "
                    "sách thuộc tính của ComponentSpec — synthesizer/exporters có thể bỏ qua.",
                )
        text_color = css_values.parse_color(component.properties.get("textColor"))
        if text_color is not None and text_color.is_transparent:
            report.warning(
                "analysis.components",
                f"{component.name}: textColor trong suốt — chữ không nhìn thấy, có thể đã đo nhầm "
                "phần tử.",
            )
        for state, values in component.states.items():
            unchanged = [
                key
                for key, value in values.items()
                if key in component.properties
                and _normalize_text(component.properties[key]) == _normalize_text(value)
            ]
            if unchanged:
                report.warning(
                    "analysis.components",
                    f"{component.name}: trạng thái '{state}' không đổi {', '.join(unchanged)} "
                    "so với trạng thái thường.",
                )
        background = css_values.parse_color(component.properties.get("backgroundColor"))
        text = css_values.parse_color(component.properties.get("textColor"))
        if (
            component.contrast_ratio is not None
            and background is not None
            and text is not None
            and background.is_opaque
            and text.is_opaque
        ):
            ratio = css_values.contrast_ratio(text, background)
            if abs(ratio - component.contrast_ratio) > COMPONENT_CONTRAST_TOLERANCE:
                report.warning(
                    "analysis.components",
                    f"{component.name}: contrast_ratio {component.contrast_ratio} nhưng "
                    f"{text.hex} trên {background.hex} tính được {ratio:.2f}.",
                )


def _check_analysis_vs_raw(analysis: DesignAnalysis, raw: RawCapture, report: _Report) -> None:
    page_names = [page.name for page in raw.pages]
    if analysis.site.pages_analyzed != page_names:
        report.warning(
            "analysis.site",
            f"site.pages_analyzed {analysis.site.pages_analyzed} khác các trang trong raw.json "
            f"{page_names}.",
        )
    if analysis.site.url != raw.source_url:
        report.warning(
            "analysis.site",
            f"site.url '{analysis.site.url}' khác source_url '{raw.source_url}' của raw.json.",
        )
    if analysis.site.collected_at is not None and analysis.site.collected_at != raw.collected_at:
        report.warning("analysis.site", "site.collected_at khác collected_at của raw.json.")
    elements = sum(len(page.elements) for page in raw.pages)
    if analysis.stats.pages != len(raw.pages) or analysis.stats.elements != elements:
        report.warning(
            "analysis.stats",
            f"stats ghi {analysis.stats.pages} trang / {analysis.stats.elements} phần tử nhưng "
            f"raw.json có {len(raw.pages)} trang / {elements} phần tử — analysis.json có thể "
            "đã cũ.",
        )
    if analysis.dark is not None and raw.dark is None:
        report.warning("analysis.dark", "analysis.json có bản dark nhưng raw.json không có.")


def _check_analysis(
    folder: Path, report: _Report, raw: RawCapture | None, *, partial: bool
) -> DesignAnalysis | None:
    path = folder / ANALYSIS_FILE
    if not path.is_file():
        _report_missing(
            report, "analysis.missing", ANALYSIS_FILE, "gdm analyze <thư-mục>", partial=partial
        )
        return None
    analysis = _load_model(path, DesignAnalysis, "analysis.schema", report, ANALYSIS_SCHEMA_VERSION)
    if analysis is None:
        return None
    skipped = [warning for warning in analysis.warnings if warning.startswith("Chưa phân tích")]
    for warning in skipped:
        # Every analyzer exists: a skipped part means a stage went missing (re-stubbed).
        if partial:
            report.info("analysis.skipped", warning)
        else:
            report.error(
                "analysis.skipped",
                f"{warning} — phần phân tích này bị bỏ qua vì chưa được xây dựng; analysis.json "
                "thiếu dữ liệu.",
            )
    colors_skipped = any("Màu sắc" in warning for warning in skipped)
    _check_palette(analysis.colors, "Bảng màu chính", report, required=not colors_skipped)
    if analysis.colors.tokens and "primary" not in {t.name for t in analysis.colors.tokens}:
        report.warning(
            "analysis.tokens",
            "Bảng màu chính không có token 'primary' — DESIGN.md sẽ thiếu colors.primary "
            "(màu hành động/thương hiệu chưa được nhận ra).",
        )
    if analysis.dark is not None:
        _check_palette(analysis.dark.colors, "Bảng màu dark", report, required=True)
        canvas = next((t.value for t in analysis.dark.colors.tokens if t.name == "canvas"), None)
        if analysis.dark.colors.scheme == "light" and analysis.colors.scheme == "light":
            report.warning(
                "analysis.dark",
                f"Bảng màu dark có nền sáng (canvas {canvas}) — trang có thể không có giao diện "
                "tối riêng; bản dark này không nên được ghi.",
            )
    _check_type_levels(analysis, report)
    _check_scales(analysis, report)
    _check_breakpoints(analysis, raw, report)
    _check_analysis_components(analysis, report)
    for shot in analysis.screenshots:
        _check_screenshot(folder, shot, report, "analysis.screenshots")
    if raw is not None and raw.pages:
        _check_analysis_vs_raw(analysis, raw, report)
    return analysis


# ─── DESIGN.md parsing ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class DesignDocument:
    """A parsed DESIGN.md: front matter, body, preamble (before the first "## ") and sections."""

    text: str
    front_matter: dict[str, Any] | None
    front_matter_error: str | None
    body: str
    preamble: str
    sections: list[tuple[str, str]]

    @property
    def section_titles(self) -> list[str]:
        return [title for title, _ in self.sections]

    def section(self, title: str) -> str | None:
        """Content of the first "## <title>" section (subsections included)."""
        for name, content in self.sections:
            if name == title:
                return content
        return None


def split_front_matter(text: str) -> tuple[dict[str, Any] | None, str, str | None]:
    """(front matter mapping or None, markdown body, problem in Vietnamese or None)."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return (
            None,
            text,
            "DESIGN.md phải bắt đầu bằng front matter YAML (dòng đầu tiên là '---').",
        )
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        return None, "", "Front matter không có dòng '---' đóng."
    body = "\n".join(lines[end + 1 :])
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" (dòng {mark.line + 2})" if mark is not None else ""
        problem = getattr(error, "problem", None) or str(error)
        return None, body, f"Front matter không phải YAML hợp lệ{where}: {problem}."
    if not isinstance(data, dict):
        return None, body, "Front matter phải là một mapping YAML (khóa: giá trị)."
    return {str(key): value for key, value in data.items()}, body, None


def markdown_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """(preamble, [(title, content)]) for "## " headings outside fenced code blocks."""
    preamble: list[str] = []
    sections: list[tuple[str, str]] = []
    title: str | None = None
    content: list[str] = []
    fence: str | None = None
    for line in body.splitlines():
        match = _FENCE_RE.match(line)
        if match is not None:
            marker = match.group(1)[0]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
        elif fence is None and line.startswith("## "):
            if title is not None:
                sections.append((title, "\n".join(content)))
            title = line[3:].strip().rstrip("#").strip()
            content = []
            continue
        (content if title is not None else preamble).append(line)
    if title is not None:
        sections.append((title, "\n".join(content)))
    return "\n".join(preamble), sections


def parse_design_md(text: str) -> DesignDocument:
    front_matter, body, problem = split_front_matter(text)
    preamble, sections = markdown_sections(body)
    return DesignDocument(
        text=text,
        front_matter=front_matter,
        front_matter_error=problem,
        body=body,
        preamble=preamble,
        sections=sections,
    )


# ─── DESIGN.md checks ────────────────────────────────────────────────────────

FrontMatterTokens = dict[tuple[str, str], Any]


def _front_matter_tokens(front_matter: Mapping[str, Any]) -> FrontMatterTokens:
    tokens: FrontMatterTokens = {}
    for group in naming.TOKEN_GROUPS:
        values = front_matter.get(group)
        if isinstance(values, dict):
            for name, value in values.items():
                tokens[(group, str(name))] = value
    return tokens


def _group(front_matter: Mapping[str, Any], group: str) -> dict[str, Any]:
    values = front_matter.get(group)
    return {str(key): value for key, value in values.items()} if isinstance(values, dict) else {}


def _resolve(tokens: FrontMatterTokens, value: Any, depth: int = 0) -> Any:
    """Follow {group.name} references through the front matter (None when broken/circular)."""
    if not isinstance(value, str):
        return value
    parsed = naming.parse_token_ref(value)
    if parsed is None:
        return value
    if depth >= MAX_REF_DEPTH or parsed not in tokens:
        return None
    return _resolve(tokens, tokens[parsed], depth + 1)


def _resolved_color(tokens: FrontMatterTokens, value: Any) -> RGBA | None:
    resolved = _resolve(tokens, value)
    return css_values.parse_color(resolved) if isinstance(resolved, str) else None


def _walk_strings(value: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, f"{path}[{index}]")


def _check_front_matter_shape(front_matter: Mapping[str, Any], report: _Report) -> None:
    version = front_matter.get("version")
    if version != "alpha":
        report.error("design.version", f"version phải là 'alpha' (đang là {version!r}).")
    name = front_matter.get("name")
    if not isinstance(name, str) or not name.strip():
        report.error("design.name", "Front matter thiếu 'name'.")
    description = front_matter.get("description")
    if description is not None and not isinstance(description, str):
        report.warning("design.description", "'description' phải là một chuỗi.")
    for key in front_matter:
        if key not in FRONT_MATTER_KEYS:
            report.warning(
                "design.unknown-key",
                f"Khóa '{key}' không thuộc spec DESIGN.md — linter và công cụ export sẽ bỏ qua.",
            )
    for group in naming.TOKEN_GROUPS:
        values = front_matter.get(group)
        if values is None:
            continue
        if not isinstance(values, dict):
            report.error(
                "design.groups",
                f"Nhóm '{group}' phải là mapping (tên: giá trị), đang là {type(values).__name__}.",
            )
            continue
        for token_name in values:
            if not naming.is_token_name(str(token_name)):
                report.warning(
                    "design.token-name",
                    f"Tên token '{group}.{token_name}' không ở dạng kebab-case chữ thường.",
                )

    for token_name, value in _group(front_matter, naming.COLORS).items():
        if _is_ref(value):
            continue
        if not isinstance(value, str) or _HEX_COLOR_RE.match(value.strip()) is None:
            report.error(
                "design.colors",
                f"colors.{token_name} = {value!r} phải là mã hex #rrggbb hoặc #rrggbbaa.",
            )
    for level_name, entry in _group(front_matter, naming.TYPOGRAPHY).items():
        _check_typography_entry(level_name, entry, report)
    for step, value in _group(front_matter, naming.ROUNDED).items():
        if not (_is_ref(value) or _is_dimension(value)):
            report.error(
                "design.rounded",
                f"rounded.{step} = {value!r} phải là Dimension (px, em hoặc rem).",
            )
    for step, value in _group(front_matter, naming.SPACING).items():
        if not (_is_ref(value) or _is_dimension(value) or _is_number(value)):
            report.error(
                "design.spacing",
                f"spacing.{step} = {value!r} phải là Dimension (px, em, rem) hoặc số.",
            )
    for component, properties in _group(front_matter, naming.COMPONENTS).items():
        if not isinstance(properties, dict):
            report.error(
                "design.components",
                f"components.{component} phải là mapping thuộc tính: giá trị.",
            )
            continue
        for key, value in properties.items():
            key = str(key)
            if not (isinstance(value, str) or _is_number(value)):
                report.error(
                    "design.components",
                    f"components.{component}.{key} phải là chuỗi hoặc số, đang là "
                    f"{type(value).__name__}.",
                )
                continue
            if key not in COMPONENT_SUB_TOKENS:
                report.info(
                    "design.components",
                    f"components.{component}.{key} không thuộc sub-token chuẩn của spec "
                    "(linter chính thức sẽ cảnh báo).",
                )
            if (
                key in COLOR_PROPERTIES
                and not _is_ref(value)
                and css_values.parse_color(str(value)) is None
            ):
                report.error(
                    "design.components",
                    f"components.{component}.{key} = {value!r} không phải màu hợp lệ.",
                )


def _check_typography_entry(level_name: str, entry: Any, report: _Report) -> None:
    where = f"typography.{level_name}"
    if not isinstance(entry, dict):
        report.error("design.typography", f"{where} phải là mapping các thuộc tính chữ.")
        return
    for key, value in entry.items():
        key = str(key)
        if key not in TYPOGRAPHY_PROPERTIES:
            report.warning("design.typography", f"{where}.{key} không phải thuộc tính chữ hợp lệ.")
            continue
        if _is_ref(value):
            continue
        valid = True
        if key == "fontFamily":
            valid = isinstance(value, str) and bool(value.strip())
        elif key == "fontSize":
            valid = _is_dimension(value)
        elif key == "fontWeight":
            number = float(value) if _is_number(value) else css_values.parse_number(str(value))
            valid = number is not None and 1 <= number <= 1000
        elif key == "lineHeight":
            valid = (
                _is_number(value)
                or _is_dimension(value)
                or (isinstance(value, str) and _NUMBER_RE.match(value.strip()) is not None)
            )
        elif key == "letterSpacing":
            valid = _is_dimension(value) or value in (0, "0")
        elif key in ("fontFeature", "fontVariation"):
            valid = isinstance(value, str)
        if not valid:
            report.error("design.typography", f"{where}.{key} = {value!r} không hợp lệ.")
    for key in ("fontFamily", "fontSize"):
        if key not in entry:
            report.warning("design.typography", f"{where} thiếu {key}.")


def _check_refs(document: DesignDocument, tokens: FrontMatterTokens, report: _Report) -> None:
    front_matter = document.front_matter or {}
    for path, text in _walk_strings(front_matter, ""):
        group_of_path = path.split(".", 1)[0]
        for group, name in naming.find_token_refs(text):
            if (group, name) not in tokens:
                report.error(
                    "design.broken-ref",
                    f"{path}: {{{group}.{name}}} không trỏ tới token nào trong front matter.",
                )
            elif group_of_path in (naming.COLORS, naming.ROUNDED, naming.SPACING) and (
                group != group_of_path
            ):
                report.error(
                    "design.ref-type",
                    f"{path}: {{{group}.{name}}} phải tham chiếu cùng nhóm '{group_of_path}'.",
                )
        _check_loose_refs(text, f"{path}: ", report)
    reported: set[tuple[str, str]] = set()
    for group, name in naming.find_token_refs(document.body):
        if (group, name) not in tokens and (group, name) not in reported:
            reported.add((group, name))
            report.error(
                "design.broken-ref",
                f"Phần thân nhắc tới {{{group}.{name}}} nhưng front matter không có token này.",
            )
    _check_loose_refs(document.body, "Phần thân: ", report)


def _check_loose_refs(text: str, prefix: str, report: _Report) -> None:
    for match in _LOOSE_REF_RE.finditer(text):
        if naming.parse_token_ref(match.group(0)) is None:
            report.warning(
                "design.ref-syntax",
                f"{prefix}'{match.group(0)}' không đúng cú pháp tham chiếu {{nhóm.tên}} "
                f"(nhóm hợp lệ: {', '.join(naming.TOKEN_GROUPS)}; tên kebab-case chữ thường).",
            )


def _check_sections(document: DesignDocument, report: _Report) -> None:
    titles = document.section_titles
    order = {title: index for index, title in enumerate(DESIGN_SECTIONS)}
    seen: set[str] = set()
    for title in titles:
        if title in seen:
            report.error("design.sections", f"Mục '## {title}' bị trùng.")
        seen.add(title)
        if title not in order:
            if title in SECTION_ALIASES:
                report.warning(
                    "design.sections",
                    f"Dùng tên chuẩn '## {SECTION_ALIASES[title]}' thay cho '## {title}'.",
                )
            else:
                report.warning(
                    "design.sections", f"Mục '## {title}' không thuộc danh sách mục chuẩn."
                )
    known = [title for title in dict.fromkeys(titles) if title in order]
    for previous, current in itertools.pairwise(known):
        if order[previous] > order[current]:
            report.error(
                "design.sections",
                f"Mục '## {current}' phải đứng trước '## {previous}' (thứ tự chuẩn: "
                f"{', '.join(DESIGN_SECTIONS)}).",
            )
            break
    for required in REQUIRED_SECTIONS:
        if required not in seen:
            report.error("design.sections", f"Thiếu mục bắt buộc '## {required}'.")
    if AGENT_PROMPT_GUIDE in seen and titles[-1] != AGENT_PROMPT_GUIDE:
        report.warning("design.sections", f"Mục '## {AGENT_PROMPT_GUIDE}' phải là mục cuối cùng.")
    first_line = next((line for line in document.preamble.splitlines() if line.strip()), "")
    if not first_line.startswith("# "):
        report.warning(
            "design.title",
            "Thiếu tiêu đề '# <Tên site> — Design Reference' ngay sau front matter.",
        )


def _check_reference_note(
    document: DesignDocument,
    analysis: DesignAnalysis | None,
    raw: RawCapture | None,
    report: _Report,
) -> None:
    note = document.preamble
    source_url = analysis.site.url if analysis is not None else raw.source_url if raw else None
    if source_url:
        candidates = {source_url, source_url.rstrip("/")}
        if not any(candidate and candidate in note for candidate in candidates):
            report.error(
                "design.reference-note",
                f"Ghi chú đầu file (trước mục '## ' đầu tiên) phải ghi URL nguồn {source_url}.",
            )
    collected = None
    if analysis is not None and analysis.site.collected_at is not None:
        collected = analysis.site.collected_at
    elif raw is not None:
        collected = raw.collected_at
    if collected is not None and not any(variant in note for variant in _date_variants(collected)):
        report.warning(
            "design.reference-note",
            f"Ghi chú đầu file chưa ghi ngày trích xuất ({_utc_date(collected).isoformat()}).",
        )
    pages: list[str] = []
    if analysis is not None:
        pages = list(analysis.site.pages_analyzed)
    elif raw is not None:
        pages = [page.name for page in raw.pages]
    missing = [page for page in pages if page not in note]
    if missing:
        report.warning(
            "design.reference-note",
            f"Ghi chú đầu file chưa liệt kê trang đã phân tích: {', '.join(missing)}.",
        )
    lowered = note.lower()
    if "official" not in lowered and "chính thức" not in lowered:
        report.warning(
            "design.reference-note",
            "Ghi chú đầu file cần nói rõ đây là tài liệu tham khảo, không phải design system "
            "chính thức.",
        )


def _measured_colors(analysis: DesignAnalysis) -> set[str]:
    palettes = [analysis.colors] + ([analysis.dark.colors] if analysis.dark else [])
    values: set[str] = set()
    for palette in palettes:
        values.update(token.value for token in palette.tokens)
        values.update(cluster.hex for cluster in palette.clusters)
        for cluster in palette.clusters:
            values.update(cluster.members)
        for pair in palette.contrast_pairs:
            values.update((pair.foreground, pair.background))
    for component in analysis.components:
        for properties in [component.properties, *component.states.values()]:
            for key in COLOR_PROPERTIES:
                color = _canonical_hex(properties.get(key))
                if color is not None:
                    values.add(color)
    return values


def _spacing_values(analysis: DesignAnalysis) -> list[float]:
    values = [step.value_px for step in analysis.spacing.steps]
    for extra in (
        analysis.spacing.base_unit_px,
        analysis.spacing.container_max_width_px,
        analysis.spacing.section_gap_px,
        analysis.layout.container_max_width_px,
        analysis.layout.gutter_px,
    ):
        if extra is not None:
            values.append(float(extra))
    for padding in analysis.spacing.common_paddings:
        sides = _px_values(padding)
        if sides is not None:
            values.extend(sides)
    return values


def _matches_any(
    value: float, candidates: Iterable[float], tolerance: float = PX_TOLERANCE
) -> bool:
    return any(abs(value - candidate) <= tolerance for candidate in candidates)


def _check_invented_typography(
    front_matter: Mapping[str, Any],
    tokens: FrontMatterTokens,
    analysis: DesignAnalysis,
    report: _Report,
) -> None:
    levels = analysis.typography.levels
    sizes = [level.size_px for level in levels]
    weights = {level.weight for level in levels} | set(analysis.typography.weights_used)
    for family in analysis.typography.families:
        weights.update(family.weights)
    families = {family.name.lower() for family in analysis.typography.families}
    families.update(level.font_family.lower() for level in levels)
    for level_name, entry in _group(front_matter, naming.TYPOGRAPHY).items():
        if not isinstance(entry, dict):
            continue
        where = f"typography.{level_name}"
        size = dimension_px(_resolve(tokens, entry.get("fontSize")))
        if size is not None and not _matches_any(size, sizes):
            report.error(
                "design.invented-typography",
                f"{where}.fontSize = {entry.get('fontSize')} không có trong các cấp chữ của "
                "analysis.json.",
            )
        weight_value = _resolve(tokens, entry.get("fontWeight"))
        weight = (
            float(weight_value)
            if _is_number(weight_value)
            else css_values.parse_number(str(weight_value))
            if weight_value is not None
            else None
        )
        if weight is not None and not _matches_any(weight, weights, 0.0):
            report.error(
                "design.invented-typography",
                f"{where}.fontWeight = {weight_value} không có trong analysis.json.",
            )
        family_value = _resolve(tokens, entry.get("fontFamily"))
        if isinstance(family_value, str):
            primary = css_values.primary_font_family(family_value) or family_value
            if primary.lower() not in families:
                report.error(
                    "design.invented-typography",
                    f"{where}.fontFamily = {family_value} không có trong analysis.json.",
                )
        line_height = _resolve(tokens, entry.get("lineHeight"))
        if line_height is not None and not _line_height_matches(line_height, size, levels):
            report.warning(
                "design.invented-typography",
                f"{where}.lineHeight = {line_height} không khớp cấp chữ nào trong analysis.json.",
            )
        spacing = _resolve(tokens, entry.get("letterSpacing"))
        if spacing is not None and not _letter_spacing_matches(spacing, size, levels):
            report.warning(
                "design.invented-typography",
                f"{where}.letterSpacing = {spacing} không khớp cấp chữ nào trong analysis.json.",
            )


def _line_height_matches(value: Any, size_px: float | None, levels: Sequence[TypeLevel]) -> bool:
    px = dimension_px(value)
    if px is None:
        ratio = float(value) if _is_number(value) else css_values.parse_number(str(value))
        if ratio is None:
            return True  # format problems are reported by the shape check
        for level in levels:
            if level.line_height is not None and abs(level.line_height - ratio) <= (
                LINE_HEIGHT_TOLERANCE
            ):
                return True
            if (
                level.line_height_px is not None
                and size_px is not None
                and abs(ratio * size_px - level.line_height_px) <= PX_TOLERANCE
            ):
                return True
        return False
    for level in levels:
        if level.line_height_px is not None and abs(px - level.line_height_px) <= PX_TOLERANCE:
            return True
        if level.line_height is not None and abs(px - level.line_height * level.size_px) <= (
            PX_TOLERANCE
        ):
            return True
    return False


def _letter_spacing_matches(value: Any, size_px: float | None, levels: Sequence[TypeLevel]) -> bool:
    if value in (0, "0", "normal"):
        px: float | None = 0.0
    elif (
        isinstance(value, str)
        and value.strip().endswith("em")
        and not value.strip().endswith("rem")
    ):
        number = css_values.parse_number(value.strip()[:-2])
        px = None if number is None or size_px is None else number * size_px
    else:
        px = dimension_px(value)
    if px is None:
        return True
    tolerance = max(0.05, 0.01 * (size_px or 0.0))
    candidates = [level.letter_spacing_px for level in levels] + [0.0]
    return _matches_any(px, candidates, tolerance)


def _check_invented_values(
    front_matter: Mapping[str, Any],
    tokens: FrontMatterTokens,
    analysis: DesignAnalysis,
    report: _Report,
) -> None:
    token_colors = {token.value for token in analysis.colors.tokens}
    if analysis.dark is not None:
        token_colors.update(token.value for token in analysis.dark.colors.tokens)
    light = {token.name: token.value for token in analysis.colors.tokens}
    for name, value in _group(front_matter, naming.COLORS).items():
        if _is_ref(value):
            continue
        color = _canonical_hex(value)
        if color is None:
            continue
        if color not in token_colors:
            report.error(
                "design.invented-color",
                f"colors.{name} = {value} không có trong token màu của analysis.json "
                "(sáng hoặc dark) — không được bịa màu.",
            )
        elif name in light and light[name] != color:
            report.warning(
                "design.color-mismatch",
                f"colors.{name} = {value} nhưng analysis.json ghi {name} = {light[name]}.",
            )
    measured = _measured_colors(analysis)
    for component, properties in _group(front_matter, naming.COMPONENTS).items():
        if not isinstance(properties, dict):
            continue
        for key in COLOR_PROPERTIES:
            value = properties.get(key)
            if value is None or _is_ref(value):
                continue
            parsed = css_values.parse_color(str(value))
            if parsed is not None and not parsed.is_transparent and parsed.css_hex not in measured:
                report.error(
                    "design.invented-color",
                    f"components.{component}.{key} = {value} không đo được trong "
                    "analysis.json — không được bịa màu.",
                )
    radii = [step.value_px for step in analysis.radii]
    for name, value in _group(front_matter, naming.ROUNDED).items():
        px = dimension_px(value)
        if px is None:
            continue
        pill = px >= naming.PILL_RADIUS_PX and any(r >= naming.PILL_RADIUS_PX for r in radii)
        if not pill and not _matches_any(px, radii):
            report.error(
                "design.invented-dimension",
                f"rounded.{name} = {value} không có trong thang bo góc của analysis.json.",
            )
    spacing_values = _spacing_values(analysis)
    for name, value in _group(front_matter, naming.SPACING).items():
        px = dimension_px(value)
        if px is None:
            continue
        if not _matches_any(px, spacing_values):
            report.error(
                "design.invented-dimension",
                f"spacing.{name} = {value} không có trong số đo spacing của analysis.json.",
            )
    _check_invented_typography(front_matter, tokens, analysis, report)


def _component_target(
    name: str, components: Mapping[str, ComponentSpec]
) -> tuple[ComponentSpec, Mapping[str, str]] | None:
    if name in components:
        spec = components[name]
        return spec, spec.properties
    for state in {state for spec in components.values() for state in spec.states}:
        suffix = f"-{state}"
        if name.endswith(suffix) and name[: -len(suffix)] in components:
            spec = components[name[: -len(suffix)]]
            return spec, spec.states.get(state, {})
    return None


def _rounds_the_ends(radius_px: float, properties: Mapping[str, str]) -> bool:
    """A radius of at least half the measured height (1px tolerance) renders as a pill, so
    {rounded.full} describes it (the components analyzer maps such radii to "full")."""
    height = css_values.parse_px(properties.get("height"))
    return height is not None and radius_px > 0 and radius_px >= height / 2.0 - 1.0


def _same_component_value(
    key: str,
    written: Any,
    resolved: Any,
    measured: str,
    from_ref: bool,
    properties: Mapping[str, str] | None = None,
) -> bool:
    if key in COLOR_PROPERTIES:
        if from_ref:
            return _colors_close(str(resolved), measured)
        return _canonical_hex(str(resolved)) == _canonical_hex(measured)
    written_px = _px_values(resolved)
    measured_px = _px_values(measured)
    if written_px is not None and measured_px is not None:
        if key == "rounded" and min(written_px) >= naming.PILL_RADIUS_PX:
            return min(measured_px) >= naming.PILL_RADIUS_PX or (
                properties is not None and _rounds_the_ends(min(measured_px), properties)
            )
        return all(abs(a - b) <= PX_TOLERANCE for a, b in zip(written_px, measured_px, strict=True))
    return _normalize_text(resolved) == _normalize_text(measured)


def _check_components_vs_analysis(
    front_matter: Mapping[str, Any],
    tokens: FrontMatterTokens,
    analysis: DesignAnalysis,
    report: _Report,
) -> None:
    components = {spec.name: spec for spec in analysis.components}
    written = _group(front_matter, naming.COMPONENTS)
    for name, properties in written.items():
        if name.startswith("ex-") or not isinstance(properties, dict):
            continue
        target = _component_target(name, components)
        if target is None:
            report.warning(
                "design.component-unknown",
                f"components.{name} không có trong analysis.json (component bịa thêm?).",
            )
            continue
        spec, measured_values = target
        for key, value in properties.items():
            key = str(key)
            resolved = _resolve(tokens, value)
            if resolved is None:
                continue  # broken reference, reported elsewhere
            if key == "typography":
                size = css_values.parse_px(spec.properties.get("fontSize"))
                written_size = (
                    dimension_px(_resolve(tokens, resolved.get("fontSize")))
                    if isinstance(resolved, dict)
                    else None
                )
                if (
                    size is not None
                    and written_size is not None
                    and (abs(size - written_size) > PX_TOLERANCE)
                ):
                    report.warning(
                        "design.component-value",
                        f"components.{name}.typography = {value} (cỡ {written_size:g}px) nhưng "
                        f"analysis.json đo được {size:g}px.",
                    )
                continue
            measured = measured_values.get(key)
            if measured is None:
                report.warning(
                    "design.component-value",
                    f"components.{name}.{key} = {value} không có số đo tương ứng trong "
                    "analysis.json.",
                )
                continue
            if not _same_component_value(
                key, value, resolved, measured, _is_ref(value), spec.properties
            ):
                report.warning(
                    "design.component-value",
                    f"components.{name}.{key} = {value} nhưng analysis.json đo được {measured}.",
                )
    for spec in analysis.components:
        hover = spec.states.get(naming.HOVER_STATE)
        variant = naming.state_variant_name(spec.name)
        if hover and spec.name in written and variant not in written:
            report.warning(
                "design.hover",
                f"analysis.json có trạng thái hover của {spec.name} nhưng front matter thiếu "
                f"components.{variant}.",
            )


def _check_design_contrast(
    document: DesignDocument,
    tokens: FrontMatterTokens,
    analysis: DesignAnalysis | None,
    report: _Report,
) -> None:
    front_matter = document.front_matter or {}
    donts = document.section(DOS_AND_DONTS)
    canvas = _resolved_color(tokens, _group(front_matter, naming.COLORS).get("canvas"))
    base = canvas if canvas is not None and canvas.is_opaque else css_values.WHITE
    components = _group(front_matter, naming.COMPONENTS)
    for name, properties in components.items():
        if not isinstance(properties, dict):
            continue
        effective = dict(properties)
        for state in ("-hover", "-active", "-focus", "-pressed"):
            base_name = name.removesuffix(state)
            if base_name != name and isinstance(components.get(base_name), dict):
                effective = {**components[base_name], **properties}
        background = _resolved_color(tokens, effective.get("backgroundColor"))
        text = _resolved_color(tokens, effective.get("textColor"))
        if background is None or text is None or background.is_transparent:
            continue
        ratio = css_values.contrast_ratio(text, background, base=base)
        if ratio < AA_MINIMUM and (donts is None or not ratio_mentioned(ratio, donts)):
            report.warning(
                "design.contrast",
                f"components.{name}: chữ {text.css_hex} trên nền {background.css_hex} chỉ đạt "
                f"{ratio:.2f}:1 (< 4.5:1) nhưng mục Do's and Don'ts không ghi tỉ lệ này.",
            )
    if analysis is None:
        return
    # Measured page pairs may be listed in the Colors contrast table or in Do's and Don'ts.
    documented = "\n".join(
        section for section in (document.section("Colors"), donts) if section is not None
    )
    seen: set[float] = set()
    for pair in analysis.colors.contrast_pairs:
        if pair.ratio >= AA_MINIMUM or pair.ratio in seen:
            continue
        seen.add(pair.ratio)
        if not ratio_mentioned(pair.ratio, documented):
            report.warning(
                "design.contrast-undocumented",
                f"Cặp màu '{pair.context or 'không rõ'}' ({pair.foreground} trên "
                f"{pair.background}) chỉ đạt {pair.ratio}:1 nhưng cả mục Colors lẫn Do's and "
                "Don'ts đều không nhắc tỉ lệ này.",
            )
    if analysis.dark is not None:
        for pair in analysis.dark.colors.contrast_pairs:
            if pair.ratio >= AA_MINIMUM or pair.ratio in seen:
                continue
            seen.add(pair.ratio)
            if not ratio_mentioned(pair.ratio, document.body):
                report.info(
                    "design.contrast-undocumented",
                    f"Dark mode: cặp '{pair.context or 'không rõ'}' chỉ đạt {pair.ratio}:1 "
                    "nhưng DESIGN.md không nhắc tới.",
                )


def _check_breakpoints_documented(
    document: DesignDocument, analysis: DesignAnalysis, report: _Report
) -> None:
    if not analysis.breakpoints:
        return
    layout = document.section("Layout")
    if layout is None:
        report.warning("design.breakpoints", "Thiếu mục '## Layout' để ghi breakpoint đo được.")
        return
    numbers = _numbers_in(layout)
    for breakpoint in analysis.breakpoints:
        for value in (breakpoint.min_width_px, breakpoint.max_width_px):
            if value is not None and not _matches_any(value, numbers, BREAKPOINT_TOLERANCE_PX):
                report.warning(
                    "design.breakpoints",
                    f"Breakpoint {value:g}px (đo từ @media) chưa được ghi trong mục Layout.",
                )


# ─── DESIGN.md body values ───────────────────────────────────────────────────
#
# A "--prompt-only" rewrite (or a hand edit) only touches the prose, so the body is checked on
# its own: outside fenced code, every "`{group.name}` — value" pair and every table row keyed by
# a token must repeat the front-matter value, and every color (hex, short hex or a CSS color
# function), length (px or rem) and "N:1" contrast ratio must come from analysis.json (or from
# the few guideline values the rules quote). Unitless numbers (font weights, line heights),
# em/% values and named colors are not checked.

#: Values the rules quote from guidelines rather than from measurements: the WCAG thresholds,
#: the WCAG large-text sizes (24px, or 18.66px bold), the 44px tap-target guideline, 1px rings.
GUIDELINE_RATIOS: Final = (3.0, 4.5, 7.0)
GUIDELINE_PX: Final = (0.0, 1.0, 18.66, 24.0, 44.0)
BODY_RATIO_TOLERANCE: Final = 0.02
#: A rem length is compared in px at the page's root font size and at the browser default.
DEFAULT_ROOT_FONT_PX: Final = 16.0
#: rgb()/hsl()/oklch()… spellings of a measured color round its channels (hsl percentages,
#: alpha decimals): within this distance they still name that color. Hex must match exactly.
FUNCTION_COLOR_DELTA_E: Final = 1.0
FUNCTION_COLOR_ALPHA: Final = 0.02
#: Trailing punctuation a rewrite may add inside a quotation of page text.
QUOTE_PUNCTUATION: Final = " .,;:!?…"

_BLOCK_START_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|\||#{1,6}\s|>)")
#: Table rows and headings never continue on the next line.
_SINGLE_LINE_BLOCK_RE = re.compile(r"^\s*(?:\||#{1,6}\s)")
#: #rrggbbaa, #rrggbb, #rgba and #rgb (never an entity "&#123;", an escaped "\#" or "a#b").
_HEX_DIGITS = r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])"
_BODY_HEX_RE = re.compile(r"(?<![\w&#\\])" + _HEX_DIGITS)
_COLOR_FUNCTION = r"(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\([^()]*\)"
_BODY_COLOR_FUNCTION_RE = re.compile(r"(?<![\w-])" + _COLOR_FUNCTION, re.IGNORECASE)
#: Optional space (also no-break and narrow no-break) between a number and its unit: "16 px".
_UNIT_SPACE = "[ \u00a0\u202f]?"
_BODY_PX_RE = re.compile(r"(?<![\w.,#\[/\\-])(-?\d+(?:\.\d+)?)" + _UNIT_SPACE + r"px(?![\w-])")
_BODY_REM_RE = re.compile(
    r"(?<![\w.,#\[/\\-])(-?(?:\d+(?:\.\d+)?|\.\d+))" + _UNIT_SPACE + r"rem(?![\w-])"
)
_BODY_RATIO_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?) ?: ?1(?!\d|[.,]\d)")
_CSS_PX_RE = re.compile(r"(-?\d+(?:\.\d+)?)px")
_URL_RE = re.compile(r"<?https?://[^\s>)\]]+>?")
_QUOTED_RE = re.compile(r"“([^”]*)”")
_MD_SPECIAL_RE = re.compile(r"([\\`*_\[\]<>|{}#~])")
_MD_UNESCAPE_RE = re.compile(r"\\([\\`*_\[\]<>|{}#~])")
_TABLE_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
_VALUE_REF_RE = re.compile(r"`\{(colors|typography|rounded|spacing)\.([a-z0-9]+(?:-[a-z0-9]+)*)\}`")
_DASH_RE = re.compile(r"\s*—\s*")
_PAREN_RE = re.compile(r"\s*\(\s*([^()]*?)\s*\)")
_LEADING_HEX_RE = re.compile(_HEX_DIGITS)
_LEADING_COLOR_FUNCTION_RE = re.compile(_COLOR_FUNCTION, re.IGNORECASE)
_LEADING_DIMENSION_RE = re.compile(r"-?(?:\d+(?:\.\d+)?|\.\d+)(?:px|rem|em)(?![\w-])")
#: "Inter 15px / 500 / 1.33" — how a typography token is written next to its reference.
_TYPE_SUMMARY_RE = re.compile(
    r"(?P<family>[^\s,;:()|/][^,;:()|/]{0,60}?)\s+(?P<size>\d+(?:\.\d+)?)px\s*/\s*"
    r"(?P<weight>\d{1,4})(?:\s*/\s*(?P<lh>\d+(?:\.\d+)?)(?P<lh_px>px)?)?"
)
#: "700 64px/1.05 Inter, sans-serif" — a CSS font shorthand (custom-properties table).
_FONT_SHORTHAND_RE = re.compile(
    r"^(?P<weight>\d{1,4})\s+(?P<size>\d+(?:\.\d+)?)px\s*/\s*(?P<lh>\d+(?:\.\d+)?)(?P<lh_px>px)?"
    r"\s+(?P<family>.+)$"
)
_PURE_PX_CELL_RE = re.compile(r"^-?\d+(?:\.\d+)?px$")
_PURE_INT_CELL_RE = re.compile(r"^\d{1,4}$")
_LEADING_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class BodyBlock:
    """One paragraph, list item, table row, heading or quote line of the DESIGN.md body
    (outside fenced code), its lines joined with spaces. `where` is "dòng N" or a label."""

    where: str
    text: str
    is_table_row: bool = False


def body_offset(text: str) -> int:
    """0-based index of the first body line of DESIGN.md (the line after the closing '---')."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return 0
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    return 0 if end is None else end + 1


def body_blocks(body: str, *, first_line: int = 1) -> list[BodyBlock]:
    """Paragraphs, list items, table rows, headings and quote lines outside fenced code.

    `first_line` is the 1-based line number of the body's first line in DESIGN.md."""
    blocks: list[BodyBlock] = []
    current: list[str] = []
    start = 0
    fence: str | None = None

    def flush() -> None:
        if current:
            text = " ".join(current)
            blocks.append(
                BodyBlock(f"dòng {first_line + start}", text, text.lstrip().startswith("|"))
            )
            current.clear()

    for index, line in enumerate(body.splitlines()):
        match = _FENCE_RE.match(line)
        if match is not None:
            flush()
            marker = match.group(1)[0]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is not None:
            continue
        if not line.strip():
            flush()
            continue
        if _BLOCK_START_RE.match(line) is not None:
            flush()
        if not current:
            start = index
        current.append(line.strip())
        if _SINGLE_LINE_BLOCK_RE.match(line) is not None:
            flush()
    flush()
    return blocks


def _table_cells(row: str) -> list[str]:
    cells = [cell.strip() for cell in _TABLE_CELL_SPLIT_RE.split(row.strip())]
    if cells and not cells[0]:
        cells = cells[1:]
    if cells and not cells[-1]:
        cells = cells[:-1]
    return cells


def _md_escape(text: str) -> str:
    return _MD_SPECIAL_RE.sub(r"\\\1", " ".join(text.split()))


def _site_strings(analysis: DesignAnalysis) -> list[str]:
    """Site-derived texts (page texts, selectors, names) that may contain digits by accident."""
    texts: list[str] = [
        analysis.site.name,
        analysis.site.title or "",
        analysis.site.description or "",
        *analysis.site.pages_analyzed,
    ]
    for family in analysis.typography.families:
        texts.extend([family.name, ", ".join(family.stack), *family.stack, *family.substitutes])
    for level in analysis.typography.levels:
        texts.extend(level.samples)
    palettes = [analysis.colors] + ([analysis.dark.colors] if analysis.dark else [])
    for palette in palettes:
        for cluster in palette.clusters:
            texts.extend(cluster.samples)
            texts.extend(cluster.css_variables)
        texts.extend(token.css_variable or "" for token in palette.tokens)
    for step in [*analysis.spacing.steps, *analysis.radii]:
        texts.extend(step.used_on)
    for shadow in analysis.shadows:
        texts.extend(shadow.used_on)
    for component in analysis.components:
        texts.extend(component.samples)
        texts.extend(component.class_hints)
    variants: set[str] = set()
    for text in texts:
        collapsed = " ".join(text.split())
        if len(collapsed) >= 2 and any(character.isdigit() for character in collapsed):
            variants.update({collapsed, _md_escape(collapsed)})
    return sorted(variants, key=len, reverse=True)


def _plain(text: str) -> str:
    """Markdown-unescaped, whitespace-collapsed, case-folded text, to compare page texts."""
    return " ".join(_MD_UNESCAPE_RE.sub(r"\1", text).split()).casefold()


def _may_hold_values(text: str) -> bool:
    return any(character.isdigit() or character == "#" for character in text)


def _page_texts(analysis: DesignAnalysis, raw: RawCapture | None) -> list[str]:
    """Plain texts of the captured pages (raw.json elements, titles, analysis samples) that a
    quotation holding a measurement-like value may come from: texts with a digit or "#", and
    texts the collector cut at MAX_TEXT_LENGTH (their continuation is unknown)."""
    texts: list[str] = [analysis.site.title or "", analysis.site.description or ""]
    for level in analysis.typography.levels:
        texts.extend(level.samples)
    for component in analysis.components:
        texts.extend(component.samples)
    if raw is not None:
        for page in [*raw.pages, *([raw.dark] if raw.dark else [])]:
            texts.extend((page.title or "", page.meta_description or ""))
            texts.extend(element.text for element in page.elements)
    return sorted(
        {
            plain
            for text in texts
            if _may_hold_values(plain := _plain(text)) or len(plain) >= MAX_TEXT_LENGTH
        }
    )


def _contains_words(text: str, part: str) -> bool:
    """`part` occurs in `text` without cutting a word or a number at either end."""
    start = text.find(part)
    while start >= 0:
        end = start + len(part)
        before = text[start - 1] if start > 0 else " "
        after = text[end] if end < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = text.find(part, start + 1)
    return False


def _is_page_quote(content: str, page_texts: Sequence[str]) -> bool:
    """A curly-quoted span is page text (exempt from the value checks) only when the page shows
    it: it occurs in a captured text, or starts with a text the capture cut at
    MAX_TEXT_LENGTH characters. Any other quotation (an example prompt, an invented label) is
    checked like the rest of the prose."""
    quote = _plain(content).strip(QUOTE_PUNCTUATION)
    if not _may_hold_values(quote):
        return True
    return any(
        _contains_words(text, quote) or (len(text) >= MAX_TEXT_LENGTH and quote.startswith(text))
        for text in page_texts
    )


def _scannable(
    text: str, site_pattern: re.Pattern[str] | None, page_texts: Sequence[str] = ()
) -> str:
    """`text` without URLs, quotations of page text and other site-derived strings."""
    text = _QUOTED_RE.sub(
        lambda match: " " if _is_page_quote(match.group(1), page_texts) else match.group(0), text
    )
    text = _URL_RE.sub(" ", text)
    return site_pattern.sub(" ", text) if site_pattern is not None else text


def _measured_px(analysis: DesignAnalysis) -> list[float]:
    """Every length analysis.json measured (+ the guideline lengths the rules quote)."""
    values: set[float] = set(GUIDELINE_PX)
    typography = analysis.typography
    for level in typography.levels:
        values.update((level.size_px, level.letter_spacing_px))
        if level.line_height_px is not None:
            values.add(level.line_height_px)
        if level.line_height is not None:
            values.add(level.line_height * level.size_px)
    if typography.base_size_px is not None:
        values.add(typography.base_size_px)
    values.update(_spacing_values(analysis))
    values.update(step.value_px for step in analysis.radii)
    if any(step.value_px >= naming.PILL_RADIUS_PX for step in analysis.radii) or any(
        step.name == naming.FULL_RADIUS_NAME for step in analysis.radii
    ):
        values.add(naming.FULL_RADIUS_PX)
    for shadow in analysis.shadows:
        values.update(float(number) for number in _CSS_PX_RE.findall(shadow.css))
    for breakpoint in analysis.breakpoints:
        values.update(
            v for v in (breakpoint.min_width_px, breakpoint.max_width_px) if v is not None
        )
    for snapshot in analysis.layout.snapshots:
        values.update(
            float(v)
            for v in (
                snapshot.viewport_width,
                snapshot.viewport_height,
                snapshot.min_tap_target_px,
                snapshot.container_width_px,
                snapshot.gutter_px,
                snapshot.max_heading_px,
            )
            if v is not None
        )
    for change in analysis.layout.changes:
        values.update((float(change.from_width), float(change.to_width)))
        values.update(float(v) for v in change.params.values())
    for component in analysis.components:
        for properties in [component.properties, *component.states.values()]:
            for value in properties.values():
                values.update(float(number) for number in _CSS_PX_RE.findall(value))
    for shot in analysis.screenshots:
        values.update((float(shot.viewport_width), float(shot.viewport_height)))
    return sorted(values)


def _measured_ratios(analysis: DesignAnalysis) -> list[float]:
    """Contrast ratios analysis.json states (pairs, components, token evidence) + thresholds."""
    values: set[float] = set(GUIDELINE_RATIOS)
    palettes = [analysis.colors] + ([analysis.dark.colors] if analysis.dark else [])
    for palette in palettes:
        values.update(pair.ratio for pair in palette.contrast_pairs)
        for token in palette.tokens:
            for match in _BODY_RATIO_RE.finditer(token.evidence):
                values.add(float(match.group(1).replace(",", ".")))
    values.update(c.contrast_ratio for c in analysis.components if c.contrast_ratio is not None)
    return sorted(values)


def _component_colors(analysis: DesignAnalysis) -> list[RGBA]:
    colors: dict[str, RGBA] = {}
    for component in analysis.components:
        for properties in [component.properties, *component.states.values()]:
            for key in COLOR_PROPERTIES:
                parsed = css_values.parse_color(properties.get(key))
                if parsed is not None and not parsed.is_transparent:
                    colors[parsed.css_hex] = parsed
    return list(colors.values())


def _ratio_matches(written: str, ratio: float) -> bool:
    decimals = written.replace(",", ".").partition(".")[2]
    number = float(written.replace(",", "."))
    tolerance: float = BODY_RATIO_TOLERANCE + 0.5 * 10.0 ** -len(decimals)
    return abs(number - ratio) <= tolerance


def _block_ratios(block_colors: Sequence[RGBA], others: Sequence[RGBA], base: RGBA) -> list[float]:
    """Contrast of every color of a block against itself and the reference colors.

    When a translucent color is involved, the alpha-blind reading (both colors made opaque) is
    computed too: the prose may quote what checkers that ignore alpha report for it.
    """
    ratios: list[float] = []
    for first in block_colors:
        for second in [*block_colors, *others]:
            if first is second:
                continue
            ratios.append(css_values.contrast_ratio(first, second, base=base))
            ratios.append(css_values.contrast_ratio(second, first, base=base))
            if not (first.is_opaque and second.is_opaque):
                ratios.append(
                    css_values.contrast_ratio(first.with_alpha(1.0), second.with_alpha(1.0))
                )
    return ratios


def _flattened_colors(analysis: DesignAnalysis, measured: set[str]) -> set[str]:
    """Opaque colors the measured translucent colors show over their theme's canvas token: the
    prose may write "#ffffff0d (≈ #151616 over the canvas)"."""
    themes = [analysis.colors] + ([analysis.dark.colors] if analysis.dark else [])
    canvases = [
        color
        for palette in themes
        for token in palette.tokens
        if token.name == "canvas" and (color := css_values.parse_color(token.value)) is not None
    ] or [css_values.WHITE]
    values: set[str] = set()
    for value in measured:
        color = css_values.parse_color(value)
        if color is None or color.is_opaque or color.is_transparent:
            continue
        values.update(css_values.flatten(color, canvas).css_hex for canvas in canvases)
    return values


def _same_px(written: Any, expected: Any) -> bool | None:
    """Equal lengths (pill radii count as equal); None when either side is not a length."""
    one = dimension_px(written)
    two = dimension_px(expected) if not _is_number(expected) else float(expected)
    if one is None or two is None:
        return None
    if one >= naming.PILL_RADIUS_PX and two >= naming.PILL_RADIUS_PX:
        return True
    return abs(one - two) <= PX_TOLERANCE


def _same_family(written: str, expected: Any) -> bool:
    if not isinstance(expected, str):
        return True
    family = css_values.primary_font_family(expected) or expected
    return _normalize_text(written) == _normalize_text(family)


def _line_height_equal(
    written: float, written_px: bool, expected: Any, size_px: float | None
) -> bool:
    """A written line height ("1.6" or "24px") against the front-matter one (unitless or px)."""
    expected_px = dimension_px(expected)
    ratio = None
    if expected_px is None:
        ratio = float(expected) if _is_number(expected) else css_values.parse_number(str(expected))
    if written_px:
        target = expected_px
        if target is None and ratio is not None and size_px is not None:
            target = ratio * size_px
        return target is None or abs(written - target) <= PX_TOLERANCE
    if ratio is not None:
        return abs(written - ratio) <= LINE_HEIGHT_TOLERANCE
    if expected_px is not None and size_px:
        return abs(written * size_px - expected_px) <= PX_TOLERANCE
    return True


def _typography_mismatches(
    entry: Mapping[str, Any],
    tokens: FrontMatterTokens,
    *,
    family: str | None,
    size: str,
    weight: str | None,
    line_height: str | None,
    line_height_px: bool,
) -> list[str]:
    """Which parts of a written type summary differ from the front-matter typography entry."""
    wrong: list[str] = []
    expected_size = dimension_px(_resolve(tokens, entry.get("fontSize")))
    if expected_size is not None and abs(float(size) - expected_size) > PX_TOLERANCE:
        wrong.append(f"cỡ {size}px ≠ {expected_size:g}px")
    expected_weight = _resolve(tokens, entry.get("fontWeight"))
    if weight is not None and expected_weight is not None:
        number = css_values.parse_number(str(expected_weight))
        if number is not None and abs(float(weight) - number) > 0:
            wrong.append(f"độ đậm {weight} ≠ {expected_weight}")
    expected_line_height = _resolve(tokens, entry.get("lineHeight"))
    if (
        line_height is not None
        and expected_line_height is not None
        and not _line_height_equal(
            float(line_height), line_height_px, expected_line_height, expected_size
        )
    ):
        wrong.append(f"line-height {line_height} ≠ {expected_line_height}")
    if family is not None and not _same_family(family, _resolve(tokens, entry.get("fontFamily"))):
        wrong.append(f"font {family} ≠ {_resolve(tokens, entry.get('fontFamily'))}")
    return wrong


def _pair_mismatch(
    group: str, name: str, after: str, tokens: FrontMatterTokens
) -> tuple[str, str] | None:
    """(written value, front-matter value) when the value written right after a reference
    ("`{colors.canvas}` — #ffffff" or "`{rounded.md}` (8px)") contradicts the front matter."""
    expected = _resolve(tokens, tokens.get((group, name)))
    if expected is None:
        return None  # broken reference, reported by _check_refs
    dash = _DASH_RE.match(after)
    paren = None if dash is not None else _PAREN_RE.match(after)
    if dash is None and paren is None:
        return None
    value = after[dash.end() :] if dash is not None else paren.group(1) if paren else ""
    if group == naming.COLORS:
        hex_match = _LEADING_HEX_RE.match(value)
        color_match = hex_match or _LEADING_COLOR_FUNCTION_RE.match(value)
        if color_match is None or (paren is not None and color_match.end() != len(value)):
            return None
        written = " ".join(color_match.group(0).split())
        if hex_match is not None:
            same = _canonical_hex(written) == _canonical_hex(str(expected))
        else:
            color = css_values.parse_color(written)
            target = css_values.parse_color(str(expected))
            same = color is None or target is None or _same_function_color(color, [target])
        return None if same else (written, str(expected))
    if group in (naming.ROUNDED, naming.SPACING):
        dimension = _LEADING_DIMENSION_RE.match(value)
        if dimension is None or (paren is not None and dimension.end() != len(value)):
            return None
        if _same_px(dimension.group(0), expected) is False:
            return dimension.group(0), str(expected)
        return None
    if group == naming.TYPOGRAPHY and dash is not None and isinstance(expected, dict):
        summary = _TYPE_SUMMARY_RE.match(value)
        if summary is None:
            return None
        wrong = _typography_mismatches(
            expected,
            tokens,
            family=summary.group("family").strip(),
            size=summary.group("size"),
            weight=summary.group("weight"),
            line_height=summary.group("lh"),
            line_height_px=summary.group("lh_px") is not None,
        )
        if wrong:
            return summary.group(0), "; ".join(wrong)
    return None


def _row_mismatch(cells: Sequence[str], tokens: FrontMatterTokens) -> tuple[str, str] | None:
    """A table row keyed by a token ("| `{spacing.md}` | 16px | …") that contradicts it."""
    if not cells:
        return None
    key = _VALUE_REF_RE.fullmatch(cells[0])
    if key is None:
        return None
    group, name = key.group(1), key.group(2)
    expected = _resolve(tokens, tokens.get((group, name)))
    if expected is None:
        return None
    others = cells[1:]
    if group == naming.COLORS:
        target = css_values.parse_color(str(expected))
        for cell in others:
            for match in _BODY_HEX_RE.finditer(cell):
                if _canonical_hex(match.group(0)) != _canonical_hex(str(expected)):
                    return match.group(0), str(expected)
            for match in _BODY_COLOR_FUNCTION_RE.finditer(cell):
                color = css_values.parse_color(match.group(0))
                if (
                    color is not None
                    and target is not None
                    and not _same_function_color(color, [target])
                ):
                    return " ".join(match.group(0).split()), str(expected)
        return None
    if group in (naming.ROUNDED, naming.SPACING):
        for cell in others:
            if _PURE_PX_CELL_RE.match(cell) and _same_px(cell, expected) is False:
                return cell, str(expected)
        return None
    if group != naming.TYPOGRAPHY or not isinstance(expected, dict):
        return None
    for cell in others:
        shorthand = _FONT_SHORTHAND_RE.match(cell.strip("`"))
        if shorthand is not None:
            wrong = _typography_mismatches(
                expected,
                tokens,
                family=None,
                size=shorthand.group("size"),
                weight=shorthand.group("weight"),
                line_height=shorthand.group("lh"),
                line_height_px=shorthand.group("lh_px") is not None,
            )
            return (cell, "; ".join(wrong)) if wrong else None
    px_cells = [cell for cell in others if _PURE_PX_CELL_RE.match(cell)]
    if not px_cells:
        return None
    weight = next((cell for cell in others if _PURE_INT_CELL_RE.match(cell)), None)
    line_height = next(
        (
            number.group(1)
            for cell in others
            if not (_PURE_INT_CELL_RE.match(cell) or _PURE_PX_CELL_RE.match(cell))
            and (number := _LEADING_NUMBER_RE.match(cell)) is not None
        ),
        None,
    )
    wrong = _typography_mismatches(
        expected,
        tokens,
        family=None,
        size=px_cells[0][:-2],
        weight=weight,
        line_height=line_height,
        line_height_px=False,
    )
    if len(px_cells) > 1:
        spacing = _resolve(tokens, expected.get("letterSpacing"))
        written = dimension_px(px_cells[1])
        expected_spacing = 0.0 if spacing is None else dimension_px(spacing)
        if spacing in (0, "0"):
            expected_spacing = 0.0
        if (
            written is not None
            and expected_spacing is not None
            and abs(written - expected_spacing) > 0.05
        ):
            wrong.append(f"letter-spacing {px_cells[1]} ≠ {spacing or 0}")
    return (" | ".join(others), "; ".join(wrong)) if wrong else None


def _check_body_token_values(
    document: DesignDocument, tokens: FrontMatterTokens, report: _Report
) -> None:
    """Values written next to a token reference must be the front-matter values."""
    mismatches: dict[tuple[str, str, str], list[str]] = {}
    first_line = body_offset(document.text) + 1
    for block in body_blocks(document.body, first_line=first_line):
        if block.is_table_row:
            cells = _table_cells(block.text)
            mismatch = _row_mismatch(cells, tokens)
            if mismatch is not None:
                mismatches.setdefault((cells[0].strip("`"), *mismatch), []).append(block.where)
        for match in _VALUE_REF_RE.finditer(block.text):
            group, name = match.group(1), match.group(2)
            if (group, name) not in tokens:
                continue
            pair = _pair_mismatch(group, name, block.text[match.end() :], tokens)
            if pair is not None:
                mismatches.setdefault((f"{{{group}.{name}}}", *pair), []).append(block.where)
    for (ref, written, expected), places in mismatches.items():
        report.error(
            "design.body-value",
            f"Phần thân ({_examples(list(dict.fromkeys(places)), 3)}): {ref} được ghi là "
            f"{written} nhưng front matter ghi {expected} — giá trị viết cạnh tham chiếu phải "
            "đúng giá trị của token.",
        )


def _css_value_colors(analysis: DesignAnalysis) -> set[str]:
    """Colors written inside measured CSS values: shadow layers and every component property
    (gradients, shadows, borders), which the rules may quote verbatim."""
    values = [shadow.css for shadow in analysis.shadows]
    for component in analysis.components:
        for properties in [component.properties, *component.states.values()]:
            values.extend(str(value) for value in properties.values())
    colors: set[str] = set()
    for value in values:
        for match in itertools.chain(
            _BODY_HEX_RE.finditer(value), _BODY_COLOR_FUNCTION_RE.finditer(value)
        ):
            color = css_values.parse_color(match.group(0))
            if color is not None:
                colors.add(color.css_hex)
    return colors


def _same_function_color(color: RGBA, others: Iterable[RGBA]) -> bool:
    """`color` (written as rgb()/hsl()/…) names one of `others` up to channel rounding."""
    return any(
        abs(color.alpha - other.alpha) <= FUNCTION_COLOR_ALPHA
        and css_values.delta_e_2000(color, other) <= FUNCTION_COLOR_DELTA_E
        for other in others
    )


def _root_font_sizes(raw: RawCapture | None) -> list[float]:
    sizes = [DEFAULT_ROOT_FONT_PX]
    if raw is not None and raw.pages and raw.pages[0].root_font_size_px:
        sizes.append(float(raw.pages[0].root_font_size_px))
    return sizes


def _check_body_measurements(
    document: DesignDocument,
    tokens: FrontMatterTokens,
    analysis: DesignAnalysis,
    raw: RawCapture | None,
    report: _Report,
) -> None:
    """Colors, lengths and contrast ratios in the prose must come from analysis.json."""
    front_matter = document.front_matter or {}
    strings = _site_strings(analysis)
    site_pattern = re.compile("|".join(map(re.escape, strings))) if strings else None
    page_texts = _page_texts(analysis, raw)
    measured_colors = _measured_colors(analysis)
    measured_colors |= _flattened_colors(analysis, measured_colors)
    measured_colors |= _css_value_colors(analysis)
    measured_rgba: list[RGBA] | None = None
    measured_px = _measured_px(analysis)
    root_sizes = _root_font_sizes(raw)
    measured_ratios = _measured_ratios(analysis)
    reference_colors = [
        color
        for color in (
            _resolved_color(tokens, value) for value in _group(front_matter, naming.COLORS).values()
        )
        if color is not None
    ] + _component_colors(analysis)
    canvas = _resolved_color(tokens, _group(front_matter, naming.COLORS).get("canvas"))
    base = canvas if canvas is not None and canvas.is_opaque else css_values.WHITE

    blocks = body_blocks(document.body, first_line=body_offset(document.text) + 1)
    description = front_matter.get("description")
    if isinstance(description, str):
        blocks.insert(0, BodyBlock("front matter description", description))
    colors: dict[str, list[str]] = {}
    lengths: dict[str, list[str]] = {}
    ratios: dict[str, list[str]] = {}
    for block in blocks:
        text = _scannable(block.text, site_pattern, page_texts)
        block_colors: list[RGBA] = []
        for match in _BODY_HEX_RE.finditer(text):
            color = css_values.parse_color(match.group(0))
            if color is not None:
                block_colors.append(color)
            if _canonical_hex(match.group(0)) not in measured_colors:
                colors.setdefault(match.group(0).lower(), []).append(block.where)
        for match in _BODY_COLOR_FUNCTION_RE.finditer(text):
            color = css_values.parse_color(match.group(0))
            if color is None:
                continue
            block_colors.append(color)
            if color.css_hex in measured_colors:
                continue
            if measured_rgba is None:
                measured_rgba = [
                    parsed
                    for value in sorted(measured_colors)
                    if (parsed := css_values.parse_color(value)) is not None
                ]
            if not _same_function_color(color, measured_rgba):
                colors.setdefault(" ".join(match.group(0).split()), []).append(block.where)
        for match in _BODY_PX_RE.finditer(text):
            if not _matches_any(float(match.group(1)), measured_px):
                lengths.setdefault(match.group(0), []).append(block.where)
        for match in _BODY_REM_RE.finditer(text):
            rem = float(match.group(1))
            if not any(_matches_any(rem * size, measured_px) for size in root_sizes):
                lengths.setdefault(match.group(0), []).append(block.where)
        written_ratios = [match.group(1) for match in _BODY_RATIO_RE.finditer(text)]
        if not written_ratios:
            continue
        for group, name in naming.find_token_refs(block.text):
            if group == naming.COLORS:
                color = _resolved_color(tokens, tokens.get((group, name)))
                if color is not None:
                    block_colors.append(color)
        computed = _block_ratios(block_colors, reference_colors, base)
        for written in written_ratios:
            if not any(_ratio_matches(written, ratio) for ratio in [*measured_ratios, *computed]):
                ratios.setdefault(f"{written}:1", []).append(block.where)
    for value, places in colors.items():
        report.error(
            "design.invented-color",
            f"Phần thân ({_examples(list(dict.fromkeys(places)), 3)}) ghi màu {value} không có "
            "trong analysis.json — không được bịa hay đổi màu khi viết lại.",
        )
    for value, places in lengths.items():
        report.error(
            "design.invented-dimension",
            f"Phần thân ({_examples(list(dict.fromkeys(places)), 3)}) ghi {value} — không khớp "
            "số đo nào trong analysis.json (cỡ chữ, spacing, bo góc, đổ bóng, breakpoint, layout, "
            "component).",
        )
    for value, places in ratios.items():
        report.error(
            "design.invented-ratio",
            f"Phần thân ({_examples(list(dict.fromkeys(places)), 3)}) ghi tỉ lệ tương phản "
            f"{value} — không khớp cặp màu đo được nào trong analysis.json hay cặp màu nhắc "
            "cùng chỗ.",
        )


def _check_design(
    folder: Path,
    report: _Report,
    analysis: DesignAnalysis | None,
    raw: RawCapture | None,
    *,
    partial: bool,
) -> DesignDocument | None:
    path = folder / DESIGN_FILE
    if not path.is_file():
        _report_missing(
            report, "design.missing", DESIGN_FILE, "gdm synthesize <thư-mục>", partial=partial
        )
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        report.error("design.read", f"Không đọc được DESIGN.md (cần UTF-8): {error}.")
        return None
    document = parse_design_md(text)
    if document.front_matter_error is not None:
        report.error("design.front-matter", document.front_matter_error)
    front_matter = document.front_matter or {}
    tokens = _front_matter_tokens(front_matter)
    if document.front_matter is not None:
        _check_front_matter_shape(front_matter, report)
        if naming.COLORS in front_matter and "primary" not in _group(front_matter, naming.COLORS):
            report.warning(
                "design.missing-primary",
                "Front matter không có colors.primary — agent sẽ tự chọn màu chủ đạo.",
            )
    _check_refs(document, tokens, report)
    _check_sections(document, report)
    _check_reference_note(document, analysis, raw, report)
    if document.front_matter is not None:
        _check_body_token_values(document, tokens, report)
    if document.front_matter is not None and analysis is not None:
        _check_invented_values(front_matter, tokens, analysis, report)
        _check_components_vs_analysis(front_matter, tokens, analysis, report)
        _check_body_measurements(document, tokens, analysis, raw, report)
    _check_design_contrast(document, tokens, analysis, report)
    if analysis is not None:
        _check_breakpoints_documented(document, analysis, report)
    return document


# ─── synthesis.json / prompt.md ──────────────────────────────────────────────


def _check_synthesis(
    folder: Path, report: _Report, document: DesignDocument | None, *, partial: bool
) -> None:
    path = folder / SYNTHESIS_FILE
    if not path.is_file():
        if (folder / DESIGN_FILE).is_file():
            report.add(
                "info" if partial else "warning",
                "synthesis.missing",
                "Có DESIGN.md nhưng thiếu synthesis.json (kết quả tự kiểm của bước synthesize).",
            )
        return
    result = _load_model(
        path, SynthesisResult, "synthesis.schema", report, SYNTHESIS_SCHEMA_VERSION
    )
    if result is None:
        return
    errors = [issue for issue in result.issues if issue.severity == "error"]
    if errors:
        report.error(
            "synthesis.issues",
            f"synthesis.json ghi {len(errors)} lỗi tự kiểm: "
            f"{_examples([f'[{issue.rule}] {issue.message}' for issue in errors], 3)}",
        )
    for issue in result.issues:
        if STAGE_MISSING_MARKER in issue.message.lower():
            report.add(
                "info" if partial else "error",
                "synthesis.stage-missing",
                f"[{issue.rule}] {issue.message} — DESIGN.md được ghi mà không qua bước này.",
            )
    for label, relative in (
        ("design_md_path", result.design_md_path),
        ("prompt_path", result.prompt_path),
    ):
        if relative is not None and not (folder / relative).is_file():
            report.error("synthesis.paths", f"{label} = {relative} nhưng file không tồn tại.")
    for relative in result.image_paths:
        image = folder / relative
        if not image.is_file():
            report.error("synthesis.paths", f"Ảnh cho prompt {relative} không tồn tại.")
            continue
        size = image_size(image)
        if size is not None and max(size) > MAX_PROMPT_IMAGE_SIDE:
            report.warning(
                "synthesis.paths",
                f"Ảnh cho prompt {relative} có cạnh dài {max(size)}px (tối đa "
                f"{MAX_PROMPT_IMAGE_SIDE}px).",
            )
    if result.mode == "prompt-only" and result.prompt_path is None:
        report.warning("synthesis.mode", "Chế độ prompt-only nhưng không ghi prompt_path.")
    if result.mode == "rules" and (folder / PROMPT_FILE).is_file() and result.prompt_path is None:
        report.info("synthesis.mode", "Có prompt.md cũ từ lần chạy --prompt-only trước.")
    if result.lang not in ("en", "vi"):
        report.warning("synthesis.lang", f"Ngôn ngữ '{result.lang}' không được hỗ trợ (en, vi).")
    if document is None:
        return
    if result.sections and result.sections != document.section_titles:
        report.warning(
            "synthesis.sections",
            "Danh sách mục trong synthesis.json khác các mục '## ' thực tế của DESIGN.md.",
        )
    front_matter = document.front_matter or {}
    counts = {key: len(value) for key, value in front_matter.items() if isinstance(value, dict)}
    if result.token_counts and result.token_counts != counts:
        report.warning(
            "synthesis.token-counts",
            f"token_counts {result.token_counts} khác số token thực tế {counts}.",
        )
    examples = [name for name in _group(front_matter, naming.COMPONENTS) if name.startswith("ex-")]
    if examples and not result.examples:
        report.warning(
            "synthesis.examples",
            f"DESIGN.md có component minh họa ({_examples(examples)}) dù không bật --examples.",
        )


def _check_prompt(folder: Path, report: _Report) -> None:
    path = folder / PROMPT_FILE
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        report.error("prompt.read", f"Không đọc được prompt.md: {error}.")
        return
    if not text.strip():
        report.error("prompt.empty", "prompt.md rỗng.")
    for relative in dict.fromkeys(_PROMPT_IMAGE_RE.findall(text)):
        relative = relative.rstrip(".")
        if not (folder / relative).is_file():
            report.error("prompt.images", f"prompt.md nhắc tới ảnh {relative} nhưng file không có.")


# ─── tokens.json (W3C Design Tokens) ─────────────────────────────────────────

DTCG_TYPES: Final = frozenset(
    {
        "color",
        "dimension",
        "fontFamily",
        "fontWeight",
        "duration",
        "cubicBezier",
        "number",
        "strokeStyle",
        "border",
        "transition",
        "shadow",
        "gradient",
        "typography",
    }
)
DTCG_PROPERTIES: Final = frozenset(
    {
        "$value",
        "$type",
        "$description",
        "$extensions",
        "$deprecated",
        "$schema",
        "$extends",
        "$root",
    }
)
DTCG_COLOR_SPACES: Final = frozenset(
    {
        "srgb",
        "srgb-linear",
        "hsl",
        "hwb",
        "lab",
        "lch",
        "oklab",
        "oklch",
        "display-p3",
        "a98-rgb",
        "prophoto-rgb",
        "rec2020",
        "xyz-d65",
        "xyz-d50",
    }
)
DTCG_FONT_WEIGHTS: Final = frozenset(
    {
        "thin",
        "hairline",
        "extra-light",
        "ultra-light",
        "light",
        "normal",
        "regular",
        "book",
        "medium",
        "semi-bold",
        "demi-bold",
        "bold",
        "extra-bold",
        "ultra-bold",
        "black",
        "heavy",
        "extra-black",
        "ultra-black",
    }
)
_DTCG_ALIAS_RE = re.compile(r"^\{([^{}]+)\}$")


@dataclass(frozen=True)
class _DtcgToken:
    path: str
    type: str | None
    value: Any


def dtcg_alias(value: Any) -> str | None:
    """ "{color.primary}" -> "color.primary"; anything else -> None."""
    if not isinstance(value, str):
        return None
    match = _DTCG_ALIAS_RE.match(value.strip())
    return match.group(1) if match else None


def dtcg_color(value: Any) -> RGBA | None:
    """RGBA of a DTCG color value: 2025.10 object ({colorSpace, components, alpha, hex}) or a
    legacy CSS color string; None when invalid."""
    if isinstance(value, str):
        return css_values.parse_color(value)
    if not isinstance(value, dict):
        return None
    hex_value = value.get("hex")
    alpha = value.get("alpha", 1.0)
    alpha = float(alpha) if _is_number(alpha) else 1.0
    space = value.get("colorSpace")
    components = value.get("components")
    if (
        space == "srgb"
        and isinstance(components, list)
        and len(components) == 3
        and all(_is_number(channel) for channel in components)
    ):
        red, green, blue = (float(channel) * 255 for channel in components)
        return css_values.rgba(red, green, blue, alpha)
    if isinstance(hex_value, str):
        parsed = css_values.parse_color(hex_value)
        return None if parsed is None else parsed.with_alpha(alpha)
    if space in DTCG_COLOR_SPACES and isinstance(components, list) and len(components) == 3:
        first, second, third = (
            "none" if channel == "none" else str(channel) for channel in components
        )
        if space in ("hsl", "hwb"):
            css = f"{space}({first} {second}% {third}% / {alpha})"
        elif space in ("lab", "lch", "oklab", "oklch"):
            css = f"{space}({first} {second} {third} / {alpha})"
        else:
            css = f"color({space} {first} {second} {third} / {alpha})"
        return css_values.parse_color(css)
    return None


def dtcg_color_hex(value: Any) -> str | None:
    """Canonical #rrggbb / #rrggbbaa of a DTCG color value (None when invalid)."""
    color = dtcg_color(value)
    return None if color is None else color.css_hex


def _collect_dtcg(
    node: Any,
    path: list[str],
    inherited: str | None,
    tokens: dict[str, _DtcgToken],
    report: _Report,
) -> None:
    where = ".".join(path) or "(gốc)"
    if not isinstance(node, dict):
        report.error("tokens.structure", f"{where}: phải là object (nhóm hoặc token).")
        return
    for key in node:
        if key.startswith("$") and key not in DTCG_PROPERTIES:
            report.warning("tokens.structure", f"{where}: thuộc tính '{key}' không có trong spec.")
    own_type = node.get("$type")
    if own_type is not None and not isinstance(own_type, str):
        report.error("tokens.type", f"{where}: $type phải là chuỗi.")
        own_type = None
    if isinstance(own_type, str) and own_type not in DTCG_TYPES:
        report.warning("tokens.type", f"{where}: $type '{own_type}' không thuộc spec DTCG.")
    effective = own_type or inherited
    children = [key for key in node if not key.startswith("$")]
    if "$value" in node:
        if children:
            report.error(
                "tokens.structure",
                f"{where}: token (có $value) không được chứa token/nhóm con "
                f"({_examples(children)}).",
            )
        if effective is None:
            report.error(
                "tokens.type",
                f"{where}: token không có $type (cả ở token lẫn nhóm cha).",
            )
        tokens[where] = _DtcgToken(where, effective, node["$value"])
        return
    for key in children:
        if "." in key or "{" in key or "}" in key:
            report.error(
                "tokens.structure",
                f"{where}: tên '{key}' không được chứa '.', '{{' hoặc '}}'.",
            )
        _collect_dtcg(node[key], [*path, key], effective, tokens, report)


def _dtcg_aliases(value: Any) -> Iterator[str]:
    alias = dtcg_alias(value)
    if alias is not None:
        yield alias
    elif isinstance(value, dict):
        for child in value.values():
            yield from _dtcg_aliases(child)
    elif isinstance(value, list):
        for child in value:
            yield from _dtcg_aliases(child)


def _validate_dtcg_dimension(value: Any, where: str, report: _Report) -> None:
    if dtcg_alias(value) is not None:
        return
    if isinstance(value, dict):
        number, unit = value.get("value"), value.get("unit")
        if not _is_number(number) or unit not in ("px", "rem"):
            report.error(
                "tokens.dimension",
                f'{where}: dimension phải có dạng {{"value": số, "unit": "px"|"rem"}}.',
            )
    elif isinstance(value, str) and css_values.parse_px(value) is not None:
        report.warning(
            "tokens.dimension",
            f"{where}: dimension dạng chuỗi '{value}' là định dạng cũ — spec 2025.10 dùng "
            '{"value", "unit"}.',
        )
    elif value != 0:
        report.error("tokens.dimension", f"{where}: dimension {value!r} không hợp lệ.")


def _validate_dtcg_color(value: Any, where: str, report: _Report) -> None:
    if dtcg_alias(value) is not None:
        return
    if isinstance(value, str):
        if css_values.parse_color(value) is None:
            report.error("tokens.color", f"{where}: màu '{value}' không hợp lệ.")
        else:
            report.warning(
                "tokens.color",
                f"{where}: màu dạng chuỗi '{value}' là định dạng cũ — spec 2025.10 dùng object "
                "{colorSpace, components, alpha, hex}.",
            )
        return
    if not isinstance(value, dict):
        report.error("tokens.color", f"{where}: màu phải là object {{colorSpace, components}}.")
        return
    space = value.get("colorSpace")
    components = value.get("components")
    if space not in DTCG_COLOR_SPACES:
        report.error("tokens.color", f"{where}: colorSpace {space!r} không hợp lệ.")
    if not (
        isinstance(components, list)
        and len(components) == 3
        and all(_is_number(channel) or channel == "none" for channel in components)
    ):
        report.error("tokens.color", f"{where}: components phải là danh sách 3 số.")
        return
    if space == "srgb" and not all(
        channel == "none" or -1e-6 <= float(channel) <= 1 + 1e-6 for channel in components
    ):
        report.error("tokens.color", f"{where}: components sRGB phải nằm trong 0..1.")
    alpha = value.get("alpha", 1.0)
    if not _is_number(alpha) or not 0.0 <= float(alpha) <= 1.0:
        report.error("tokens.color", f"{where}: alpha phải là số trong 0..1.")
    hex_value = value.get("hex")
    if hex_value is not None:
        if not isinstance(hex_value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", hex_value) is None:
            report.error("tokens.color", f"{where}: hex {hex_value!r} phải có dạng #rrggbb.")
        elif space == "srgb" and all(_is_number(channel) for channel in components):
            expected = css_values.parse_color(hex_value)
            channels = [round(float(channel) * 255) for channel in components]
            if expected is not None and any(
                abs(a - b) > 1
                for a, b in zip(channels, (expected.r, expected.g, expected.b), strict=True)
            ):
                report.warning(
                    "tokens.color", f"{where}: hex {hex_value} không khớp components {components}."
                )


def _validate_dtcg_value(token: _DtcgToken, report: _Report) -> None:
    value, where = token.value, token.path
    if dtcg_alias(value) is not None or token.type is None:
        return
    kind = token.type
    if kind == "color":
        _validate_dtcg_color(value, where, report)
    elif kind == "dimension":
        _validate_dtcg_dimension(value, where, report)
    elif kind == "fontFamily":
        if not (
            (isinstance(value, str) and value.strip())
            or (isinstance(value, list) and value and all(isinstance(item, str) for item in value))
        ):
            report.error("tokens.value", f"{where}: fontFamily phải là chuỗi hoặc danh sách chuỗi.")
    elif kind == "fontWeight":
        valid = (_is_number(value) and 1 <= float(value) <= 1000) or (
            isinstance(value, str) and value in DTCG_FONT_WEIGHTS
        )
        if not valid:
            report.error("tokens.value", f"{where}: fontWeight {value!r} không hợp lệ.")
    elif kind == "number":
        if not _is_number(value):
            report.error("tokens.value", f"{where}: number phải là số.")
    elif kind == "duration":
        if not (
            isinstance(value, dict)
            and _is_number(value.get("value"))
            and value.get("unit") in ("ms", "s")
        ):
            report.error("tokens.value", f"{where}: duration phải là {{value, unit: ms|s}}.")
    elif kind == "cubicBezier":
        if not (isinstance(value, list) and len(value) == 4 and all(_is_number(v) for v in value)):
            report.error("tokens.value", f"{where}: cubicBezier phải là 4 số.")
    elif kind == "shadow":
        layers = value if isinstance(value, list) else [value]
        for index, layer in enumerate(layers):
            label = f"{where}[{index}]" if isinstance(value, list) else where
            if not isinstance(layer, dict):
                report.error("tokens.value", f"{label}: lớp shadow phải là object.")
                continue
            missing = [
                key for key in ("color", "offsetX", "offsetY", "blur", "spread") if key not in layer
            ]
            if missing:
                report.error("tokens.value", f"{label}: shadow thiếu {', '.join(missing)}.")
            if "color" in layer:
                _validate_dtcg_color(layer["color"], f"{label}.color", report)
            for key in ("offsetX", "offsetY", "blur", "spread"):
                if key in layer:
                    _validate_dtcg_dimension(layer[key], f"{label}.{key}", report)
    elif kind == "typography":
        if not isinstance(value, dict):
            report.error("tokens.value", f"{where}: typography phải là object.")
            return
        missing = [
            key
            for key in ("fontFamily", "fontSize", "fontWeight", "letterSpacing", "lineHeight")
            if key not in value
        ]
        if missing:
            report.warning("tokens.value", f"{where}: typography thiếu {', '.join(missing)}.")
        for key in ("fontSize", "letterSpacing"):
            if key in value:
                _validate_dtcg_dimension(value[key], f"{where}.{key}", report)


def _resolve_dtcg_alias(
    root: Mapping[str, Any], alias: str, tokens: Mapping[str, _DtcgToken]
) -> tuple[_DtcgToken | None, str | None]:
    """(target token, problem) following alias chains with cycle detection."""
    seen: list[str] = []
    current = alias
    while True:
        if current in seen:
            return None, f"tham chiếu vòng {' -> '.join([*seen, current])}"
        seen.append(current)
        node: Any = root
        for part in current.split("."):
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                return None, f"{{{current}}} không tồn tại"
        if not isinstance(node, dict) or "$value" not in node:
            return None, f"{{{current}}} trỏ tới một nhóm, không phải token"
        token = tokens.get(current)
        if token is None:
            return None, f"{{{current}}} không phải token hợp lệ"
        following = dtcg_alias(token.value)
        if following is None:
            return token, None
        current = following


def _check_tokens(
    folder: Path, report: _Report, analysis: DesignAnalysis | None, *, partial: bool
) -> None:
    path = folder / TOKENS_FILE
    if not path.is_file():
        _report_missing(
            report,
            "tokens.missing",
            TOKENS_FILE,
            "gdm export <thư-mục>",
            partial=partial,
            severity="warning",
        )
        return
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        report.error("tokens.json", f"tokens.json không đọc được hoặc không phải JSON: {error}.")
        return
    if not isinstance(root, dict):
        report.error("tokens.structure", "tokens.json phải là một object JSON.")
        return
    tokens: dict[str, _DtcgToken] = {}
    _collect_dtcg(root, [], None, tokens, report)
    if not tokens:
        report.error("tokens.structure", "tokens.json không có token nào.")
    for token in tokens.values():
        _validate_dtcg_value(token, report)
        for alias in dict.fromkeys(_dtcg_aliases(token.value)):
            target, problem = _resolve_dtcg_alias(root, alias, tokens)
            if problem is not None:
                report.error("tokens.alias", f"{token.path}: {problem}.")
            elif (
                target is not None
                and dtcg_alias(token.value) == alias
                and token.type is not None
                and target.type is not None
                and target.type != token.type
            ):
                report.warning(
                    "tokens.alias",
                    f"{token.path} ({token.type}) tham chiếu {{{alias}}} kiểu {target.type}.",
                )
    if analysis is None or not analysis.colors.tokens:
        return
    color_group = root.get("color")
    if not isinstance(color_group, dict):
        report.warning("tokens.analysis", "tokens.json không có nhóm 'color' cho bảng màu.")
        return
    for color_token in analysis.colors.tokens:
        entry = tokens.get(f"color.{color_token.name}")
        if entry is None:
            report.warning("tokens.analysis", f"tokens.json thiếu color.{color_token.name}.")
            continue
        value = entry.value
        color_alias = dtcg_alias(value)
        if color_alias is not None:
            target, _ = _resolve_dtcg_alias(root, color_alias, tokens)
            value = target.value if target is not None else None
        color = dtcg_color(value)
        if color is None or color.css_hex != color_token.value:
            report.warning(
                "tokens.analysis",
                f"color.{color_token.name} = {dtcg_color_hex(value)} nhưng analysis.json ghi "
                f"{color_token.value}.",
            )
    if analysis.dark is not None and "dark" not in root:
        report.warning(
            "tokens.analysis", "analysis.json có bản dark nhưng tokens.json thiếu nhóm 'dark'."
        )


# ─── preview.html ────────────────────────────────────────────────────────────


class _PreviewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.doctype = False
        self.tags: set[str] = set()
        self.script_sources: list[str] = []
        self.stylesheets: list[str] = []
        self.remote_media: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_decl(self, decl: str) -> None:
        if decl.lower().startswith("doctype html"):
            self.doctype = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        attributes = {name: value or "" for name, value in attrs}
        if tag == "script" and attributes.get("src"):
            self.script_sources.append(attributes["src"])
        elif tag == "link" and "stylesheet" in attributes.get("rel", "").lower():
            self.stylesheets.append(attributes.get("href", ""))
        elif tag in ("img", "source", "video", "audio", "iframe", "embed"):
            source = attributes.get("src", "")
            if source.startswith(("http://", "https://", "//")):
                self.remote_media.append(source)
        if tag == "title":
            self._in_title = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def _check_preview(
    folder: Path, report: _Report, analysis: DesignAnalysis | None, *, partial: bool
) -> None:
    path = folder / PREVIEW_FILE
    if not path.is_file():
        _report_missing(
            report,
            "preview.missing",
            PREVIEW_FILE,
            "gdm export <thư-mục>",
            partial=partial,
            severity="warning",
        )
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        report.error("preview.read", f"Không đọc được preview.html (cần UTF-8): {error}.")
        return
    parser = _PreviewParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as error:  # html.parser is lenient; anything raised is a broken file
        report.error("preview.html", f"preview.html không phân tích được: {error}.")
        return
    if not parser.doctype:
        report.warning("preview.html", "preview.html thiếu <!doctype html>.")
    for tag in ("html", "head", "body"):
        if tag not in parser.tags:
            report.warning("preview.html", f"preview.html thiếu thẻ <{tag}>.")
    if not parser.title.strip():
        report.warning("preview.html", "preview.html thiếu <title>.")
    for source in parser.script_sources:
        report.error(
            "preview.script",
            f"preview.html nạp script ngoài '{source}' — trang phải tự chứa (JS viết inline).",
        )
    google_fonts = [href for href in parser.stylesheets if href.startswith(GOOGLE_FONTS_CSS2)]
    for href in parser.stylesheets:
        if href.startswith(GOOGLE_FONTS_CSS2) and len(google_fonts) == 1:
            # One optional Google Fonts stylesheet is allowed; offline the page falls back to
            # the font stacks.
            continue
        report.warning(
            "preview.external",
            f"preview.html nạp stylesheet ngoài '{href}' — nên viết CSS inline.",
        )
    for source in parser.remote_media:
        report.warning("preview.external", f"preview.html tải tài nguyên từ mạng: {source}.")
    lowered = text.lower()
    if re.search(r"@import\s+(url\()?\s*['\"]?(https?:)?//", lowered) or "url(http" in lowered:
        report.warning("preview.external", "preview.html tải CSS/font từ mạng (@import/url()).")
    if analysis is None:
        return
    missing = [
        token.value for token in analysis.colors.tokens if token.value.lower() not in lowered
    ]
    if missing:
        report.error(
            "preview.colors",
            f"preview.html thiếu mã màu của token: {_examples(missing)}.",
        )
    if analysis.dark is not None:
        dark_missing = [
            token.value
            for token in analysis.dark.colors.tokens
            if token.value.lower() not in lowered
        ]
        if dark_missing:
            report.warning(
                "preview.colors",
                f"preview.html thiếu mã màu dark mode: {_examples(dark_missing)}.",
            )


# ─── Entry points ────────────────────────────────────────────────────────────


def _guarded(
    report: _Report, label: str, check: Callable[P, R], *args: P.args, **kwargs: P.kwargs
) -> R | None:
    """Run one check; an unexpected exception becomes an error finding instead of a crash."""
    try:
        return check(*args, **kwargs)
    except Exception as error:
        report.error(
            "verify.internal", f"Lỗi nội bộ khi kiểm {label}: {type(error).__name__}: {error}"
        )
        return None


def verify_output(folder: Path, *, partial: bool = False) -> list[Finding]:
    """Every finding for an output/<domain>/ folder, most severe first.

    partial=True accepts a folder where later stages did not run yet: missing files become
    notes instead of errors (everything that exists is still fully checked).
    """
    report = _Report()
    if not folder.is_dir():
        report.error("folder.missing", f"Không tìm thấy thư mục {folder}.")
        return report.findings
    raw = _guarded(report, RAW_FILE, _check_raw, folder, report, partial=partial)
    analysis = _guarded(
        report, ANALYSIS_FILE, _check_analysis, folder, report, raw, partial=partial
    )
    document = _guarded(
        report, DESIGN_FILE, _check_design, folder, report, analysis, raw, partial=partial
    )
    _guarded(report, SYNTHESIS_FILE, _check_synthesis, folder, report, document, partial=partial)
    _guarded(report, PROMPT_FILE, _check_prompt, folder, report)
    _guarded(report, TOKENS_FILE, _check_tokens, folder, report, analysis, partial=partial)
    _guarded(report, PREVIEW_FILE, _check_preview, folder, report, analysis, partial=partial)
    return sorted(report.findings, key=lambda finding: SEVERITY_ORDER[finding.severity])


def summarize(findings: Sequence[Finding]) -> dict[str, int]:
    return {
        severity: sum(1 for f in findings if f.severity == severity) for severity in SEVERITY_ORDER
    }


def exit_code(findings: Sequence[Finding], *, strict: bool = False) -> int:
    counts = summarize(findings)
    return 1 if counts["error"] or (strict and counts["warning"]) else 0


def format_findings(findings: Sequence[Finding], folder: Path, *, width: int = 110) -> str:
    """Readable plain-text table (Vietnamese)."""
    lines = [f"Kiểm tra thư mục: {folder}"]
    if findings:
        label_width = max(len(SEVERITY_LABELS[f.severity]) for f in findings)
        check_width = max(len(f.check) for f in findings)
        indent = label_width + check_width + 4
        lines.append(f"{'MỨC ĐỘ'.ljust(label_width)}  {'KIỂM TRA'.ljust(check_width)}  NỘI DUNG")
        for finding in findings:
            wrapped = textwrap.wrap(finding.message, max(width - indent, 40)) or [""]
            prefix = (
                f"{SEVERITY_LABELS[finding.severity].ljust(label_width)}  "
                f"{finding.check.ljust(check_width)}  "
            )
            lines.append(prefix + wrapped[0])
            lines.extend(" " * indent + line for line in wrapped[1:])
    else:
        lines.append("Không có phát hiện nào.")
    counts = summarize(findings)
    lines.append(
        f"Kết quả: {counts['error']} lỗi, {counts['warning']} cảnh báo, {counts['info']} ghi chú."
    )
    return "\n".join(lines)


def findings_json(findings: Sequence[Finding], folder: Path, *, strict: bool = False) -> str:
    payload = {
        "folder": str(folder),
        "ok": exit_code(findings, strict=strict) == 0,
        "summary": summarize(findings),
        "findings": [asdict(finding) for finding in findings],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower() not in (
            "utf-8",
            "utf8",
        ):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_output.py",
        description=(
            "Kiểm tra độc lập cấu trúc một thư mục output/<domain> của gdm "
            "(raw.json, analysis.json, DESIGN.md, tokens.json, preview.html, synthesis.json)."
        ),
    )
    parser.add_argument("folder", type=Path, help="Thư mục output/<domain> cần kiểm tra.")
    parser.add_argument("--strict", action="store_true", help="Coi cảnh báo là lỗi (exit 1).")
    parser.add_argument("--json", action="store_true", help="In kết quả dạng JSON.")
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Chấp nhận thư mục chưa chạy hết các tầng (file thiếu chỉ là ghi chú).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    args = _parser().parse_args(argv)
    folder: Path = args.folder
    findings = verify_output(folder, partial=args.partial)
    if args.json:
        print(findings_json(findings, folder, strict=args.strict))
    else:
        print(format_findings(findings, folder))
    return exit_code(findings, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
