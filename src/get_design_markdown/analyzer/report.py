"""Rich terminal summary of an analysis (stream: analyzer-components, phase GĐ5).

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    build_report(analysis) -> RenderableType

Every section tolerates missing data (an analyzer not merged yet, an empty page), so the report
never raises; headings are Vietnamese, token names and CSS values stay as measured.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from rich import box
from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from get_design_markdown.analyzer.css_values import AA_NORMAL, AAA_NORMAL, format_px, parse_color
from get_design_markdown.models.analysis import (
    Breakpoint,
    ColorPalette,
    ColorRole,
    ComponentSpec,
    ContrastPair,
    DesignAnalysis,
    LayoutInfo,
    ResponsiveChangeKind,
    ScaleStep,
    Typography,
)

SWATCH = "      "
MAX_CLUSTER_ROWS = 12
EMPTY = "—"

ROLE_LABELS: dict[ColorRole, str] = {
    ColorRole.BACKGROUND: "nền",
    ColorRole.SURFACE: "bề mặt",
    ColorRole.TEXT: "chữ",
    ColorRole.TEXT_MUTED: "chữ phụ",
    ColorRole.NEUTRAL: "trung tính",
    ColorRole.BRAND: "thương hiệu",
    ColorRole.ACCENT: "nhấn",
    ColorRole.SUCCESS: "thành công",
    ColorRole.WARNING: "cảnh báo",
    ColorRole.DANGER: "nguy hiểm",
    ColorRole.INFO: "thông tin",
    ColorRole.BORDER: "viền",
    ColorRole.DECORATIVE: "trang trí",
}
CHANNEL_LABELS = {"text": "chữ", "background": "nền", "border": "viền", "gradient": "gradient"}
CHANGE_LABELS: dict[ResponsiveChangeKind, str] = {
    ResponsiveChangeKind.NAV_COLLAPSES_TO_HAMBURGER: "Nav thu thành nút hamburger",
    ResponsiveChangeKind.NAV_HIDDEN: "Ẩn thanh điều hướng",
    ResponsiveChangeKind.SIDEBAR_HIDDEN: "Ẩn sidebar",
    ResponsiveChangeKind.COLUMNS_REDUCE: "Giảm số cột",
    ResponsiveChangeKind.HEADING_SCALES_DOWN: "Tiêu đề nhỏ lại",
    ResponsiveChangeKind.CONTAINER_BECOMES_FLUID: "Container tràn màn hình",
    ResponsiveChangeKind.GUTTER_SHRINKS: "Lề hẹp lại",
    ResponsiveChangeKind.HORIZONTAL_SCROLL_APPEARS: "Xuất hiện cuộn ngang",
    ResponsiveChangeKind.TAP_TARGETS_SMALL: "Vùng chạm nhỏ",
}
SHELL_LABELS = {
    "top-nav": "thanh điều hướng trên cùng",
    "sidebar": "sidebar",
    "top-nav+sidebar": "thanh điều hướng trên + sidebar",
    "unknown": "chưa xác định",
}
STATE_LABELS = {
    "backgroundColor": "nền",
    "textColor": "chữ",
    "borderColor": "viền",
    "shadow": "bóng",
    "textDecoration": "gạch chân",
    "opacity": "độ mờ",
}


def build_report(analysis: DesignAnalysis) -> RenderableType:
    """Tables with Vietnamese headings: palette (swatch + hex + role + share), contrast
    warnings, type levels, spacing/radius/shadow scales, breakpoints, components (pure)."""
    parts: list[RenderableType] = [
        Rule(Text(f"Phân tích thiết kế — {analysis.site.name or analysis.site.url}", "bold")),
        _overview(analysis),
        _palette_section(analysis.colors),
        _contrast_section(analysis.colors),
        _typography_section(analysis.typography),
        _scales_section(analysis),
        _breakpoint_section(analysis.breakpoints),
        _responsive_section(analysis.layout),
        _component_section(analysis.components),
    ]
    if analysis.dark is not None:
        parts.append(_dark_section(analysis.colors, analysis.dark.colors))
    if analysis.warnings:
        parts.append(_warning_section(analysis.warnings))
    return Group(*parts)


# ─── Building blocks ─────────────────────────────────────────────────────────


def _table(title: str, *columns: str) -> Table:
    table = Table(
        title=title,
        title_justify="left",
        title_style="bold cyan",
        box=box.SIMPLE_HEAD,
        header_style="bold",
        pad_edge=False,
    )
    for column in columns:
        table.add_column(column)
    return table


def _empty(title: str, message: str = "chưa có dữ liệu.") -> Text:
    text = Text()
    text.append(f"{title}: ", style="bold cyan")
    text.append(message, style="dim")
    return text


def _swatch(value: str | None) -> Text:
    color = parse_color(value) if value else None
    if color is None or color.is_transparent:
        return Text(SWATCH)
    return Text(SWATCH, style=f"on {color.hex}")


def _sample(foreground: str, background: str) -> Text:
    front = parse_color(foreground)
    back = parse_color(background)
    if front is None or back is None:
        return Text(" Aa ")
    return Text(" Aa ", style=f"{front.hex} on {back.hex}")


def _ratio(ratio: float | None) -> Text:
    if ratio is None:
        return Text(EMPTY, style="dim")
    if ratio < AA_NORMAL:
        style = "bold red"
    elif ratio < AAA_NORMAL:
        style = "yellow"
    else:
        style = "green"
    return Text(f"{ratio:.2f}", style=style)


def _px(value: float | None) -> str:
    return EMPTY if value is None else format_px(value)


def _percent(share: float) -> str:
    return f"{share * 100:.1f}%"


def _named(token: str | None, value: str) -> str:
    return f"{token} {value}" if token else value


def _scale(steps: Sequence[ScaleStep]) -> str:
    return " · ".join(f"{step.name} {format_px(step.value_px)}" for step in steps) or EMPTY


# ─── Sections ────────────────────────────────────────────────────────────────


def _overview(analysis: DesignAnalysis) -> Text:
    stats = analysis.stats
    pages = ", ".join(analysis.site.pages_analyzed) or EMPTY
    parts = [
        f"Nguồn: {analysis.site.url}",
        f"Trang: {stats.pages} ({pages})",
        f"Phần tử: {stats.elements}",
        f"Stylesheet: {stats.stylesheets}",
        f"Biến CSS: {stats.css_variables}",
        f"Hover: {stats.hover_states}",
        f"Cụm màu: {stats.color_clusters}",
        f"Component: {stats.components}",
    ]
    return Text(" · ".join(parts), style="dim")


def _palette_section(palette: ColorPalette) -> RenderableType:
    scheme = "tối" if palette.scheme == "dark" else "sáng"
    if palette.tokens:
        table = _table(
            f"Bảng màu ({len(palette.tokens)} token, giao diện {scheme})",
            "",
            "Token",
            "Hex",
            "Vai trò",
            "Bằng chứng",
        )
        table.columns[4].overflow = "fold"
        table.columns[4].max_width = 48
        for token in palette.tokens:
            evidence = token.evidence
            if token.css_variable:
                evidence = f"{token.css_variable} · {evidence}" if evidence else token.css_variable
            table.add_row(
                _swatch(token.value),
                token.name,
                token.value,
                ROLE_LABELS.get(token.role, str(token.role)),
                Text(evidence or EMPTY, style="dim"),
            )
        return table
    if palette.clusters:
        table = _table(
            f"Bảng màu (chưa đặt token — {len(palette.clusters)} cụm)",
            "",
            "Cụm",
            "Kênh",
            "Hex",
            "Tỉ lệ",
            "Số phần tử",
        )
        clusters = sorted(palette.clusters, key=lambda cluster: -cluster.share)
        for cluster in clusters[:MAX_CLUSTER_ROWS]:
            table.add_row(
                _swatch(cluster.hex),
                cluster.id,
                CHANNEL_LABELS.get(cluster.channel, cluster.channel),
                cluster.hex + (" (trang trí)" if cluster.decorative else ""),
                _percent(cluster.share),
                str(cluster.count),
            )
        return table
    return _empty("Bảng màu")


def _failing_pairs(pairs: Iterable[ContrastPair]) -> list[ContrastPair]:
    return sorted((pair for pair in pairs if not pair.passes_aa), key=lambda pair: pair.ratio)


def _contrast_section(palette: ColorPalette) -> RenderableType:
    title = "Tương phản chưa đạt AA"
    if not palette.contrast_pairs:
        return _empty(title, "chưa đo cặp chữ/nền nào.")
    failing = _failing_pairs(palette.contrast_pairs)
    if not failing:
        return _empty(title, "không có — mọi cặp chữ/nền đo được đều đạt AA (4.5:1).")
    table = _table(title, "", "Chữ", "Nền", "Tỉ lệ", "AA chữ lớn", "Ngữ cảnh", "Số lần")
    for pair in failing:
        table.add_row(
            _sample(pair.foreground, pair.background),
            _named(pair.foreground_token, pair.foreground),
            _named(pair.background_token, pair.background),
            _ratio(pair.ratio),
            Text("đạt", style="yellow") if pair.passes_aa_large else Text("không", style="red"),
            pair.context or EMPTY,
            str(pair.occurrences),
        )
    return table


def _typography_section(typography: Typography) -> RenderableType:
    if not typography.families and not typography.levels:
        return _empty("Typography")
    parts: list[RenderableType] = []
    if typography.families:
        fonts = _table(
            "Typography — font", "Font", "Vai trò", "Loại", "Tỉ lệ", "Weight", "Thay thế"
        )
        fonts.columns[5].overflow = "fold"
        for family in typography.families:
            google = " (Google Fonts)" if family.is_google_font else ""
            fonts.add_row(
                family.name + google,
                str(family.role),
                family.category,
                _percent(family.share),
                ", ".join(str(weight) for weight in family.weights) or EMPTY,
                ", ".join(family.substitutes) or EMPTY,
            )
        parts.append(fonts)
    if typography.levels:
        ratio = ""
        if typography.scale_ratio:
            name = f" {typography.scale_ratio_name}" if typography.scale_ratio_name else ""
            ratio = f", tỉ lệ {typography.scale_ratio:g}{name}"
        base = f"cỡ gốc {_px(typography.base_size_px)}{ratio}"
        levels = _table(
            f"Typography — cấp chữ ({base})",
            "Cấp",
            "Cỡ",
            "Weight",
            "Line-height",
            "Letter-spacing",
            "Font",
            "Thẻ",
            "Số lần",
        )
        for level in typography.levels:
            line_height = _px(level.line_height_px)
            if level.line_height is not None:
                line_height += f" ({level.line_height:g})"
            levels.add_row(
                level.name,
                format_px(level.size_px),
                str(level.weight),
                line_height,
                format_px(level.letter_spacing_px) if level.letter_spacing_px else EMPTY,
                level.font_family,
                ", ".join(level.tags) or EMPTY,
                str(level.count),
            )
        parts.append(levels)
    return Group(*parts)


def _scales_section(analysis: DesignAnalysis) -> RenderableType:
    spacing = analysis.spacing
    if not (spacing.steps or analysis.radii or analysis.shadows or spacing.base_unit_px):
        return _empty("Spacing / Bo góc / Đổ bóng")
    table = _table("Spacing / Bo góc / Đổ bóng", "Thang", "Giá trị")
    table.columns[1].overflow = "fold"
    unit = f"{spacing.base_unit_px}px" if spacing.base_unit_px else EMPTY
    table.add_row("Đơn vị cơ sở", unit)
    table.add_row("Spacing", _scale(spacing.steps))
    if spacing.container_max_width_px or spacing.section_gap_px:
        table.add_row(
            "Container / section",
            f"{_px(spacing.container_max_width_px)} / {_px(spacing.section_gap_px)}",
        )
    if spacing.common_paddings:
        table.add_row("Padding hay dùng", ", ".join(spacing.common_paddings))
    table.add_row("Bo góc", _scale(analysis.radii))
    if analysis.shadows:
        for level in analysis.shadows:
            table.add_row(f"Đổ bóng {level.name}", level.css)
    else:
        table.add_row("Đổ bóng", Text("không có", style="dim"))
    return table


def _breakpoint_section(breakpoints: Sequence[Breakpoint]) -> RenderableType:
    if not breakpoints:
        return _empty("Breakpoint", "không đo được @media theo chiều rộng.")
    table = _table("Breakpoint", "Tên", "min-width", "max-width", "Số lần")
    for point in breakpoints:
        table.add_row(
            point.name,
            _px(point.min_width_px),
            _px(point.max_width_px),
            str(point.occurrences),
        )
    return table


def _responsive_section(layout: LayoutInfo) -> RenderableType:
    if not layout.changes and layout.shell == "unknown" and not layout.snapshots:
        return _empty("Responsive")
    summary = (
        f"Khung: {SHELL_LABELS.get(layout.shell, layout.shell)} · container "
        f"{_px(layout.container_max_width_px)} · lề {_px(layout.gutter_px)}"
    )
    if not layout.changes:
        return Group(_empty("Responsive", summary + " · không thấy thay đổi bố cục."))
    table = _table(f"Responsive ({summary})", "Viewport", "Thay đổi", "Chi tiết")
    table.columns[2].overflow = "fold"
    for change in layout.changes:
        detail = change.evidence or ", ".join(
            f"{key}={value:g}" for key, value in change.params.items()
        )
        table.add_row(
            f"{change.from_width} → {change.to_width}",
            CHANGE_LABELS.get(change.kind, str(change.kind)),
            detail or EMPTY,
        )
    return table


def _background_cell(component: ComponentSpec) -> Text:
    properties = component.properties
    background = properties.get("backgroundColor")
    border = properties.get("borderColor")
    cell = Text()
    if background and background != "transparent":
        color = parse_color(background)
        cell.append("  ", style="" if color is None else f"on {color.hex}")
        cell.append(f" {background}")
    elif background:
        cell.append("trong suốt", style="dim")
    if border:
        if cell:
            cell.append("\n")
        cell.append(f"viền {border}", style="dim")
    return cell if cell else Text(EMPTY, style="dim")


def _text_cell(component: ComponentSpec) -> Text:
    value = component.properties.get("textColor")
    if not value:
        return Text(EMPTY, style="dim")
    color = parse_color(value)
    return Text(value, style="" if color is None else color.hex)


def _hover_cell(component: ComponentSpec) -> str:
    hover = component.states.get("hover", {})
    if not hover:
        return EMPTY
    return ", ".join(f"{STATE_LABELS.get(key, key)} → {value}" for key, value in hover.items())


def _component_section(components: Sequence[ComponentSpec]) -> RenderableType:
    if not components:
        return _empty("Component", "không nhận diện được component nào.")
    table = _table(
        f"Component ({len(components)})",
        "Tên",
        "SL",
        "Nền",
        "Chữ",
        "Hover",
        "Tương phản",
    )
    table.columns[1].justify = "right"
    table.columns[4].overflow = "fold"
    table.columns[5].justify = "right"
    for component in components:
        table.add_row(
            component.name,
            str(component.count),
            _background_cell(component),
            _text_cell(component),
            _hover_cell(component),
            _ratio(component.contrast_ratio),
        )
    return table


def _dark_section(light: ColorPalette, dark: ColorPalette) -> RenderableType:
    light_values = {token.name: token.value for token in light.tokens}
    dark_values = {token.name: token.value for token in dark.tokens}
    names = list(light_values) + [name for name in dark_values if name not in light_values]
    if not names:
        return _empty("Dark mode", "có bản tối nhưng chưa đặt token màu.")
    table = _table("Dark mode — token sáng ↔ tối", "Token", "", "Sáng", "", "Tối")
    for name in names:
        light_value = light_values.get(name)
        dark_value = dark_values.get(name)
        table.add_row(
            name,
            _swatch(light_value),
            light_value or EMPTY,
            _swatch(dark_value),
            dark_value or EMPTY,
        )
    return table


def _warning_section(warnings: Sequence[str]) -> RenderableType:
    table = _table(f"Cảnh báo ({len(warnings)})", "")
    table.show_header = False
    table.columns[0].overflow = "fold"
    for warning in warnings:
        table.add_row(Text(f"• {warning}", style="yellow"))
    return table
