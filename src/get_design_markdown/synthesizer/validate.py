"""Self-check of a DESIGN.md text before it is written.

PUBLIC ENTRY POINT (frozen signature, called by synthesizer/synthesize.py):
    validate_design_md(text) -> list[ValidationIssue]

Rule ids follow the official linter (`npx @google/design.md lint`) where one exists:

- "front-matter" (error): front matter missing, not valid YAML, not a mapping, version not
  "alpha", name missing, or a token group that is not a mapping.
- "token-value" (error): a color, dimension or font weight the official linter rejects.
- "broken-ref" (error): a {group.name} reference — in the front matter or anywhere in the body —
  that the front matter does not define; front-matter values may never reference components.
- "section-order" / "duplicate-section" (error): "## " sections out of the canonical order or
  repeated (aliases such as "Brand & Style" count as their canonical section).
- "yaml-block" (error/warning): a fenced ```yaml block in the body — the official linter merges
  it into the tokens, so redefining a front-matter key makes it reject the file.
- "missing-primary", "missing-typography", "unknown-property", "contrast-ratio" (warning).
- "contrast-claim" (warning): a ratio written right after two color tokens that is not the
  contrast of those token values (a member's ratio quoted next to the token's hex).
- "unknown-section", "missing-sections", "orphaned-tokens", "token-summary" (info).

Fenced code blocks (``` or ~~~): "## " lines inside them are not sections. Token references are
checked everywhere — prose, inline code and fenced blocks — because agents read all of it.
Messages are Vietnamese (shown to the user by the CLI).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, Final

import yaml

from get_design_markdown.analyzer.css_values import (
    AA_NORMAL,
    RGBA,
    WHITE,
    contrast_ratio,
    parse_color,
)
from get_design_markdown.analyzer.naming import (
    COLORS,
    COMPONENTS,
    ROUNDED,
    SPACING,
    TOKEN_GROUPS,
    TOKEN_REF_RE,
    TYPOGRAPHY,
    parse_token_ref,
)
from get_design_markdown.models.synthesis import ValidationIssue

#: Canonical "## " sections in order (render.DESIGN_SECTIONS; duplicated to avoid an import
#: cycle — a test keeps both equal).
SECTION_ORDER: Final = (
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
SECTION_ALIASES: Final = {
    "Brand & Style": "Overview",
    "Layout & Spacing": "Layout",
    "Elevation": "Elevation & Depth",
}
SPEC_COMPONENT_KEYS: Final = (
    "backgroundColor",
    "textColor",
    "typography",
    "rounded",
    "padding",
    "size",
    "height",
    "width",
)
TYPOGRAPHY_KEYS: Final = frozenset(
    {
        "fontFamily",
        "fontSize",
        "fontWeight",
        "lineHeight",
        "letterSpacing",
        "fontFeature",
        "fontVariation",
    }
)
#: Suffixes of state variants that inherit missing colors from their base component.
STATE_SUFFIXES: Final = ("hover", "focus", "active", "pressed", "visited", "disabled")
MAX_REFERENCE_DEPTH: Final = 10

_DIMENSION_RE = re.compile(r"^-?(?:\d+(?:\.\d+)?|\.\d+)(px|em|rem)$")
_NUMBER_RE = re.compile(r"^-?(?:\d+(?:\.\d+)?|\.\d+)$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
#: A color token reference (optionally marked "≈ " as a member's nearest token) or a ratio.
_CLAIM_RE = re.compile(
    r"(?P<approx>≈ )?`\{colors\.(?P<name>[A-Za-z0-9_-]+)\}`|(?P<ratio>\d+(?:\.\d+)?):1(?!\d)"
)
_GROUP_LABELS: Final = {
    COLORS: "màu",
    TYPOGRAPHY: "cấp chữ",
    ROUNDED: "mức bo góc",
    SPACING: "bước khoảng cách",
    COMPONENTS: "component",
}


@dataclass
class _Parsed:
    found: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    #: 1-based line number of the first body line in the full text.
    body_start: int = 1
    error: str | None = None


def _parse(text: str) -> _Parsed:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        return _Parsed(body=normalized)
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        return _Parsed(
            body=normalized, error="Front matter mở bằng '---' nhưng không có dòng '---' đóng."
        )
    body = "\n".join(lines[end + 1 :])
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as error:
        detail = str(error).strip().splitlines()[0] if str(error).strip() else "lỗi cú pháp"
        return _Parsed(
            found=True,
            body=body,
            body_start=end + 2,
            error=f"Front matter không phải YAML hợp lệ: {detail}.",
        )
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return _Parsed(
            found=True,
            body=body,
            body_start=end + 2,
            error="Front matter phải là một bảng khóa–giá trị (mapping) YAML.",
        )
    return _Parsed(found=True, data=data, body=body, body_start=end + 2)


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """(parsed YAML front matter, markdown body); missing/invalid front matter -> ({}, text)."""
    parsed = _parse(text)
    if not parsed.found or parsed.error is not None:
        return {}, text
    return parsed.data, parsed.body


def _issue(rule: str, severity: str, message: str) -> ValidationIssue:
    return ValidationIssue.model_validate({"rule": rule, "severity": severity, "message": message})


def _groups(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {group: data[group] for group in TOKEN_GROUPS if isinstance(data.get(group), dict)}


def _check_header(data: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    version = data.get("version")
    if version != "alpha":
        issues.append(
            _issue(
                "front-matter",
                "error",
                f"Trường version phải là 'alpha' (đang là {version!r}).",
            )
        )
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        issues.append(_issue("front-matter", "error", "Front matter thiếu trường name."))
    for group in TOKEN_GROUPS:
        if group in data and not isinstance(data[group], dict):
            issues.append(
                _issue(
                    "front-matter",
                    "error",
                    f"Nhóm '{group}' trong front matter phải là một mapping.",
                )
            )
    return issues


def _is_ref(value: Any) -> bool:
    return isinstance(value, str) and parse_token_ref(value) is not None


def _check_values(groups: dict[str, dict[str, Any]]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for name, value in groups.get(COLORS, {}).items():
        if _is_ref(value):
            continue
        if not isinstance(value, str) or parse_color(value) is None:
            issues.append(
                _issue(
                    "token-value", "error", f"colors.{name} = {value!r} không phải màu CSS hợp lệ."
                )
            )
    for name, value in groups.get(ROUNDED, {}).items():
        if _is_ref(value):
            continue
        if not isinstance(value, str) or not _DIMENSION_RE.match(value.strip()):
            issues.append(
                _issue(
                    "token-value",
                    "error",
                    f"rounded.{name} = {value!r} không phải kích thước hợp lệ (px, em hoặc rem).",
                )
            )
    for name, entry in groups.get(TYPOGRAPHY, {}).items():
        if not isinstance(entry, dict):
            issues.append(
                _issue(
                    "token-value", "error", f"typography.{name} phải là một mapping thuộc tính chữ."
                )
            )
            continue
        issues.extend(_check_typography(name, entry))
    return issues


def _check_typography(name: str, entry: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    weight = entry.get("fontWeight")
    numeric = isinstance(weight, int | float) and not isinstance(weight, bool)
    textual = isinstance(weight, str) and _NUMBER_RE.match(weight.strip()) is not None
    if weight is not None and not (numeric or textual):
        issues.append(
            _issue(
                "token-value",
                "error",
                f"typography.{name}.fontWeight = {weight!r} phải là một con số.",
            )
        )
    for key in ("fontSize", "letterSpacing", "lineHeight"):
        value = entry.get(key)
        if value is None or isinstance(value, int | float) or _is_ref(value):
            continue
        text = str(value).strip()
        if _DIMENSION_RE.match(text) or (key == "lineHeight" and _NUMBER_RE.match(text)):
            continue
        issues.append(
            _issue(
                "token-value",
                "error",
                f"typography.{name}.{key} = {value!r} không phải kích thước hợp lệ "
                "(px, em hoặc rem).",
            )
        )
    unknown = sorted(key for key in entry if key not in TYPOGRAPHY_KEYS)
    if unknown:
        issues.append(
            _issue(
                "unknown-property",
                "warning",
                f"typography.{name} có thuộc tính ngoài spec: {', '.join(unknown)}.",
            )
        )
    return issues


def _defined(groups: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    return {group: {str(name) for name in groups.get(group, {})} for group in TOKEN_GROUPS}


def _walk_strings(value: Any, path: str) -> list[tuple[str, str]]:
    if isinstance(value, dict):
        found: list[tuple[str, str]] = []
        for key, item in value.items():
            found.extend(_walk_strings(item, f"{path}.{key}"))
        return found
    if isinstance(value, list):
        return [
            pair
            for index, item in enumerate(value)
            for pair in _walk_strings(item, f"{path}.{index}")
        ]
    return [(path, value)] if isinstance(value, str) else []


def _check_refs(
    groups: dict[str, dict[str, Any]], body: str, body_start: int
) -> list[ValidationIssue]:
    defined = _defined(groups)
    issues: list[ValidationIssue] = []
    reported: set[str] = set()
    for group, entries in groups.items():
        for path, value in _walk_strings(entries, group):
            for match in TOKEN_REF_RE.finditer(value):
                ref_group, name = match.group(1), match.group(2)
                ref = match.group(0)
                if ref_group == COMPONENTS:
                    issues.append(
                        _issue(
                            "broken-ref",
                            "error",
                            f"{path}: tham chiếu {ref} trỏ tới một component — front matter chỉ "
                            "được tham chiếu token màu, chữ, bo góc hoặc khoảng cách.",
                        )
                    )
                elif name not in defined[ref_group] and f"{path}|{ref}" not in reported:
                    reported.add(f"{path}|{ref}")
                    issues.append(
                        _issue(
                            "broken-ref",
                            "error",
                            f"{path}: tham chiếu {ref} không trỏ tới token nào trong front matter.",
                        )
                    )
    for offset, line in enumerate(body.split("\n")):
        for match in TOKEN_REF_RE.finditer(line):
            ref = match.group(0)
            if match.group(2) in defined[match.group(1)] or ref in reported:
                continue
            reported.add(ref)
            issues.append(
                _issue(
                    "broken-ref",
                    "error",
                    f"Dòng {body_start + offset}: tham chiếu {ref} không trỏ tới token nào trong "
                    "front matter.",
                )
            )
    return issues


def _closes(match: re.Match[str] | None, fence: str) -> bool:
    """True when a fence line closes the open `fence` (same character, at least as long, no
    info string)."""
    if match is None:
        return False
    marker = match.group(1)
    return marker[0] == fence[0] and len(marker) >= len(fence) and not match.group(2).strip()


def body_sections(body: str) -> list[str]:
    """ "## " titles outside fenced code blocks, in order."""
    titles: list[str] = []
    fence: str | None = None
    for line in body.split("\n"):
        match = _FENCE_RE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
                continue
            if line.startswith("## "):
                titles.append(line[3:].strip().rstrip("#").strip())
        elif _closes(match, fence):
            fence = None
    return titles


def _yaml_blocks(body: str, body_start: int) -> list[tuple[int, str]]:
    blocks: list[tuple[int, str]] = []
    fence: str | None = None
    info = ""
    start = 0
    content: list[str] = []
    for offset, line in enumerate(body.split("\n")):
        match = _FENCE_RE.match(line)
        if fence is None:
            if match:
                fence, info, start, content = match.group(1), match.group(2).strip(), offset, []
            continue
        if _closes(match, fence):
            if info.split()[:1] in (["yaml"], ["yml"]):
                blocks.append((body_start + start, "\n".join(content)))
            fence = None
            continue
        content.append(line)
    return blocks


def _check_sections(body: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    titles = body_sections(body)
    canonical: list[str] = []
    for title in titles:
        resolved = SECTION_ALIASES.get(title, title)
        if resolved in SECTION_ORDER:
            canonical.append(resolved)
        else:
            issues.append(
                _issue(
                    "unknown-section",
                    "info",
                    f"Section '## {title}' không thuộc danh sách chuẩn — vẫn được giữ nguyên.",
                )
            )
    for title, count in Counter(canonical).items():
        if count > 1:
            issues.append(
                _issue(
                    "duplicate-section",
                    "error",
                    f"Section '## {title}' xuất hiện {count} lần — mỗi section chỉ được có một.",
                )
            )
    for previous, current in pairwise(canonical):
        if SECTION_ORDER.index(current) < SECTION_ORDER.index(previous):
            issues.append(
                _issue(
                    "section-order",
                    "error",
                    f"Section '## {current}' đứng sau '## {previous}' — sai thứ tự chuẩn: "
                    f"{', '.join(SECTION_ORDER)}.",
                )
            )
            break
    return issues


def _check_yaml_blocks(data: dict[str, Any], body: str, body_start: int) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for line, content in _yaml_blocks(body, body_start):
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError:
            parsed = None
        keys = sorted(str(key) for key in parsed) if isinstance(parsed, dict) else []
        overlap = [key for key in keys if key in data]
        if overlap:
            issues.append(
                _issue(
                    "yaml-block",
                    "error",
                    f"Khối ```yaml ở dòng {line} định nghĩa lại {', '.join(overlap)} — linter "
                    "chính thức sẽ từ chối file; dùng ```text hoặc ```css cho ví dụ.",
                )
            )
        else:
            issues.append(
                _issue(
                    "yaml-block",
                    "warning",
                    f"Khối ```yaml ở dòng {line} sẽ bị linter chính thức đọc như token; dùng "
                    "```text hoặc ```css cho ví dụ.",
                )
            )
    return issues


def _resolve_color(value: Any, colors: dict[str, Any], depth: int = 0) -> RGBA | None:
    if not isinstance(value, str) or depth > MAX_REFERENCE_DEPTH:
        return None
    parsed = parse_token_ref(value)
    if parsed is None:
        return parse_color(value)
    group, name = parsed
    if group != COLORS or name not in colors:
        return None
    return _resolve_color(colors[name], colors, depth + 1)


def _base_component(name: str, components: dict[str, Any]) -> str | None:
    for suffix in STATE_SUFFIXES:
        if name.endswith(f"-{suffix}"):
            base = name[: -len(suffix) - 1]
            if base in components:
                return base
    return None


def _check_components(groups: dict[str, dict[str, Any]]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    components = groups.get(COMPONENTS, {})
    colors = groups.get(COLORS, {})
    canvas = _canvas_of(colors)
    for name, props in components.items():
        if not isinstance(props, dict):
            issues.append(
                _issue(
                    "front-matter", "error", f"components.{name} phải là một mapping thuộc tính."
                )
            )
            continue
        unknown = sorted(str(key) for key in props if key not in SPEC_COMPONENT_KEYS)
        if unknown:
            issues.append(
                _issue(
                    "unknown-property",
                    "warning",
                    f"components.{name} có thuộc tính ngoài spec: {', '.join(unknown)} (hợp lệ: "
                    f"{', '.join(SPEC_COMPONENT_KEYS)}).",
                )
            )
        merged = dict(props)
        base = _base_component(str(name), components)
        if base is not None and isinstance(components[base], dict):
            merged = {**components[base], **props}
        background = _resolve_color(merged.get("backgroundColor"), colors)
        foreground = _resolve_color(merged.get("textColor"), colors)
        if background is None or foreground is None or background.is_transparent:
            continue
        ratio = contrast_ratio(foreground, background, base=canvas)
        if ratio < AA_NORMAL:
            inherited = " (kế thừa màu từ " + base + ")" if base is not None else ""
            issues.append(
                _issue(
                    "contrast-ratio",
                    "warning",
                    f"components.{name}{inherited}: chữ {foreground.css_hex} trên nền "
                    f"{background.css_hex} chỉ đạt {ratio:.2f}:1, dưới mức WCAG AA 4.5:1.",
                )
            )
    return issues


def _canvas_of(colors: dict[str, Any]) -> RGBA:
    canvas = _resolve_color(colors.get("canvas"), colors) or WHITE
    return canvas if canvas.is_opaque else canvas.with_alpha(1.0)


def _quoted_matches(quoted: str, ratios: tuple[float, ...]) -> bool:
    """True when a printed ratio ("4.45", "16.4") is one of `ratios` rounded the same way."""
    decimals = len(quoted.partition(".")[2])
    tolerance = 0.5 * 10.0**-decimals + 1e-6
    value = float(quoted)
    return any(abs(round(ratio, decimals) - value) <= tolerance for ratio in ratios)


def _check_contrast_claims(
    groups: dict[str, dict[str, Any]], body: str, body_start: int
) -> list[ValidationIssue]:
    """A ratio written right after two color tokens ("`{colors.ink}` — #061b31 on
    `{colors.canvas}` — #f6f9fc (16.44:1)") must be the contrast of those two token values:
    quoting a ratio measured on a slightly different member color next to the token's hex
    gives the same pair two AA verdicts. Tokens written as "≈ {colors.x}" (a measured member
    shown with its nearest token) are not claims about the token and are skipped."""
    colors = groups.get(COLORS, {})
    if not colors:
        return []
    canvas = _canvas_of(colors)
    issues: list[ValidationIssue] = []
    reported: set[tuple[str, str, str]] = set()
    for offset, line in enumerate(body.split("\n")):
        window: list[tuple[str, bool]] = []
        for match in _CLAIM_RE.finditer(line):
            quoted = match.group("ratio")
            if quoted is None:
                window.append((match.group("name"), match.group("approx") is not None))
                continue
            pair, window = window[-2:], []
            if len(pair) < 2 or any(approx for _, approx in pair):
                continue
            (fg_name, _), (bg_name, _) = pair
            foreground = _resolve_color(colors.get(fg_name), colors)
            background = _resolve_color(colors.get(bg_name), colors)
            if foreground is None or background is None or fg_name == bg_name:
                continue
            ratios = (
                contrast_ratio(foreground, background, base=canvas),
                contrast_ratio(background, foreground, base=canvas),
            )
            key = (fg_name, bg_name, quoted)
            if _quoted_matches(quoted, ratios) or key in reported:
                continue
            reported.add(key)
            issues.append(
                _issue(
                    "contrast-claim",
                    "warning",
                    f"Dòng {body_start + offset}: tỉ lệ {quoted}:1 ghi cho {{colors.{fg_name}}} "
                    f"trên {{colors.{bg_name}}} không khớp giá trị token (tính lại từ "
                    f"{foreground.css_hex} và {background.css_hex}: {ratios[0]:.2f}:1) — cùng "
                    "một cặp token sẽ có hai kết luận WCAG khác nhau.",
                )
            )
    return issues


def _referenced_colors(groups: dict[str, dict[str, Any]]) -> set[str]:
    colors = groups.get(COLORS, {})
    used: set[str] = set()
    for _, value in _walk_strings(groups.get(COMPONENTS, {}), COMPONENTS):
        parsed = parse_token_ref(value)
        depth = 0
        while parsed is not None and parsed[0] == COLORS and depth <= MAX_REFERENCE_DEPTH:
            used.add(parsed[1])
            target = colors.get(parsed[1])
            parsed = parse_token_ref(target) if isinstance(target, str) else None
            depth += 1
    return used


def _info(groups: dict[str, dict[str, Any]]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    colors = groups.get(COLORS, {})
    if colors:
        for group in (SPACING, ROUNDED):
            if not groups.get(group):
                issues.append(
                    _issue(
                        "missing-sections",
                        "info",
                        f"Front matter chưa có nhóm '{group}' — agent sẽ tự chọn giá trị mặc định.",
                    )
                )
    if colors and groups.get(COMPONENTS):
        orphans = [name for name in colors if name not in _referenced_colors(groups)]
        if orphans:
            issues.append(
                _issue(
                    "orphaned-tokens",
                    "info",
                    "Token màu chưa được component nào tham chiếu: "
                    f"{', '.join(map(str, orphans))}.",
                )
            )
    parts = [
        f"{len(groups[group])} {_GROUP_LABELS[group]}"
        for group in TOKEN_GROUPS
        if groups.get(group)
    ]
    if parts:
        issues.append(_issue("token-summary", "info", f"DESIGN.md định nghĩa {', '.join(parts)}."))
    return issues


def validate_design_md(text: str) -> list[ValidationIssue]:
    """Rules (ids aligned with the official linter): "front-matter" (error), "broken-ref"
    (error), "section-order" (error), "duplicate-section" (error), "missing-primary"
    (warning), "contrast-ratio" (warning, component text/background < 4.5:1),
    "contrast-claim" (warning, a quoted token-pair ratio that the token values do not give),
    "orphaned-tokens" (info), "token-summary" (info)."""
    parsed = _parse(text)
    issues: list[ValidationIssue] = []
    if not parsed.found and parsed.error is None:
        issues.append(
            _issue(
                "front-matter",
                "error",
                "DESIGN.md thiếu front matter YAML (khối nằm giữa hai dòng '---' ở đầu file).",
            )
        )
    elif parsed.error is not None:
        issues.append(_issue("front-matter", "error", parsed.error))
    data = parsed.data
    if parsed.found and parsed.error is None:
        issues.extend(_check_header(data))
    groups = _groups(data)
    issues.extend(_check_values(groups))
    issues.extend(_check_refs(groups, parsed.body, parsed.body_start))
    issues.extend(_check_sections(parsed.body))
    issues.extend(_check_yaml_blocks(data, parsed.body, parsed.body_start))
    colors = groups.get(COLORS, {})
    if colors and "primary" not in colors:
        issues.append(
            _issue(
                "missing-primary",
                "warning",
                "Front matter có màu nhưng thiếu token 'primary' — agent sẽ tự đoán màu chủ đạo.",
            )
        )
    if colors and not groups.get(TYPOGRAPHY):
        issues.append(
            _issue(
                "missing-typography",
                "warning",
                "Front matter có màu nhưng không có token typography — agent sẽ dùng font "
                "mặc định.",
            )
        )
    issues.extend(_check_components(groups))
    issues.extend(_check_contrast_claims(groups, parsed.body, parsed.body_start))
    issues.extend(_info(groups))
    return issues
