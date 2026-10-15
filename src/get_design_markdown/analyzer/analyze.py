"""Stage 2 orchestration (frozen glue): raw.json -> analysis.json.

Calls every analyzer module through its module attribute (so tests can monkeypatch them).
An analyzer that fails stops the command: analysis.json is never written with an empty part.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from get_design_markdown import __version__
from get_design_markdown.analyzer import (
    breakpoints,
    colors,
    components,
    layout,
    report,
    shapes,
    spacing,
    typography,
)
from get_design_markdown.console import (
    ProgressCallback,
    console,
    print_success,
    progress_status,
    silent_progress,
)
from get_design_markdown.errors import AnalysisError
from get_design_markdown.models.analysis import (
    AnalysisStats,
    ColorPalette,
    DesignAnalysis,
    SiteInfo,
    ThemeAnalysis,
)
from get_design_markdown.models.raw import RawCapture
from get_design_markdown.storage import ANALYSIS_FILE, RAW_FILE, load_model, save_model

DEFAULT_DELTA_E = colors.DEFAULT_DELTA_E
_TITLE_SEPARATORS = (" | ", " — ", " – ", " - ", " · ", ": ")


def _domain_label(domain: str) -> str:
    host = domain.split("_", 1)[0]
    parts = [part for part in host.split(".") if part]
    if not parts or all(part.isdigit() for part in parts):
        return ""
    if len(parts) == 1:
        return parts[0]
    return parts[-2]


def site_name(title: str, domain: str) -> str:
    """Brand name from the page title ("Stripe | Financial…" -> "Stripe"), else the domain."""
    label = _domain_label(domain)
    segments = [title.strip()]
    for separator in _TITLE_SEPARATORS:
        segments = [piece.strip() for segment in segments for piece in segment.split(separator)]
    segments = [segment for segment in segments if segment]
    if label:
        for segment in segments:
            if label.lower() in segment.lower().replace(" ", "") and len(segment) <= 40:
                return segment
    if segments and len(segments[0]) <= 30:
        return segments[0]
    return label.capitalize() if label else (urlsplit(f"//{domain}").hostname or domain)


def build_site_info(raw: RawCapture) -> SiteInfo:
    main = raw.pages[0]
    return SiteInfo(
        url=raw.source_url,
        name=site_name(main.title, raw.domain),
        title=main.title,
        description=main.meta_description,
        lang=main.lang,
        collected_at=raw.collected_at,
        pages_analyzed=[page.name for page in raw.pages],
    )


def _token_values(palette: ColorPalette) -> dict[str, str]:
    return {token.name: token.value for token in palette.tokens}


def _canvas_value(palette: ColorPalette) -> str:
    return _token_values(palette).get("canvas", "?")


def _no_separate_dark_theme(light: ColorPalette, dark: ColorPalette) -> str | None:
    """Why the prefers-color-scheme: dark capture is not a dark theme of its own (None if it is).

    Measured capture noise (canvas and surface swapping, a token missing) must not become a
    "dark theme": the dark capture counts only when its canvas is dark while the default capture's
    canvas is light.
    """
    if _token_values(dark) == _token_values(light):
        return "Bản dark mode giống hệt bản sáng — trang không có giao diện tối riêng, đã bỏ qua."
    if dark.scheme != "dark":
        return (
            f"Bản dark mode vẫn có nền sáng (canvas {_canvas_value(dark)}) — trang không có "
            "giao diện tối riêng, đã bỏ qua."
        )
    if light.scheme == "dark":
        return (
            f"Trang vốn dùng nền tối (canvas {_canvas_value(light)}) ở cả hai bản chụp — "
            "không có giao diện tối riêng, đã bỏ qua bản dark mode."
        )
    return None


def _build_stats(raw: RawCapture, palette: ColorPalette, component_count: int) -> AnalysisStats:
    variable_names = {variable.name for page in raw.pages for variable in page.css_variables}
    variable_names.update(
        variable.name for sheet in raw.stylesheets for variable in sheet.variables
    )
    return AnalysisStats(
        pages=len(raw.pages),
        elements=sum(len(page.elements) for page in raw.pages),
        stylesheets=len(raw.stylesheets),
        css_variables=len(variable_names),
        hover_states=sum(len(page.hover_states) for page in raw.pages),
        color_clusters=len(palette.clusters),
        components=component_count,
    )


def analyze_capture(
    raw: RawCapture,
    *,
    threshold: float = DEFAULT_DELTA_E,
    now: datetime | None = None,
    progress: ProgressCallback = silent_progress,
) -> DesignAnalysis:
    """Run every analyzer on a RawCapture and assemble the DesignAnalysis (no I/O).

    Raises AnalysisError when there is nothing to analyze; errors of an analyzer propagate.
    """
    if not raw.pages:
        raise AnalysisError(
            "raw.json không có trang nào để phân tích.",
            hint="Chạy lại 'gdm collect <url>'.",
        )
    if not any(page.elements for page in raw.pages):
        raise AnalysisError(
            "raw.json không có phần tử hiển thị nào — trang có thể bị chặn hoặc chưa tải xong.",
            hint="Chạy lại 'gdm collect <url>' (có thể tăng GDM_TIMEOUT).",
        )
    pages = raw.pages
    progress("Màu sắc")
    palette = colors.analyze_colors(pages, raw.stylesheets, threshold=threshold)
    progress("Typography")
    type_system = typography.analyze_typography(pages, raw.stylesheets)
    progress("Spacing")
    spacing_system = spacing.analyze_spacing(pages)
    progress("Bo góc")
    radii = shapes.analyze_radii(pages)
    progress("Đổ bóng")
    shadows = shapes.analyze_shadows(pages)
    progress("Breakpoint")
    breakpoint_list = breakpoints.analyze_breakpoints(raw.stylesheets)
    progress("Layout")
    layout_info = layout.analyze_layout(pages, breakpoint_list)
    progress("Component")
    component_list = components.analyze_components(
        pages,
        palette=palette,
        typography=type_system,
        spacing=spacing_system,
        radii=radii,
        shadows=shadows,
    )

    warnings = list(raw.warnings)
    for page in pages:
        warnings.extend(f"[{page.name}] {warning}" for warning in page.warnings)

    dark_theme: ThemeAnalysis | None = None
    dark_page = raw.dark
    if dark_page is not None:
        progress("Màu dark mode")
        dark_palette = colors.analyze_colors([dark_page], raw.stylesheets, threshold=threshold)
        if dark_palette.tokens:
            reason = _no_separate_dark_theme(palette, dark_palette)
            if reason is None:
                dark_theme = ThemeAnalysis(colors=dark_palette)
            else:
                warnings.append(reason)

    screenshots = list(pages[0].screenshots)
    if dark_page is not None:
        screenshots.extend(dark_page.screenshots)

    return DesignAnalysis(
        tool_version=__version__,
        analyzed_at=now or datetime.now(UTC),
        site=build_site_info(raw),
        colors=palette,
        typography=type_system,
        spacing=spacing_system,
        radii=radii,
        shadows=shadows,
        breakpoints=breakpoint_list,
        layout=layout_info,
        components=component_list,
        dark=dark_theme,
        screenshots=screenshots,
        stats=_build_stats(raw, palette, len(component_list)),
        warnings=warnings,
    )


def run_analyze(
    folder: Path,
    *,
    threshold: float = DEFAULT_DELTA_E,
    show_report: bool = True,
    now: datetime | None = None,
) -> DesignAnalysis:
    """Load raw.json, analyze, write analysis.json and print the Rich report."""
    raw = load_model(RawCapture, folder / RAW_FILE)
    with progress_status("Đang phân tích") as progress:
        analysis = analyze_capture(raw, threshold=threshold, now=now, progress=progress)
    path = save_model(analysis, folder / ANALYSIS_FILE)
    if show_report:
        console.print(report.build_report(analysis))
    print_success(f"Đã ghi {path}")
    return analysis
