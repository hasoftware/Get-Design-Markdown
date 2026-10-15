"""Stage glue for the CLI (frozen): `gdm collect` and the all-in-one `gdm get`.

collect -> analyze -> synthesize -> export (tokens.json + preview.html) -> optional browser.
"""

from __future__ import annotations

import asyncio
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from rich.markup import escape
from rich.table import Table

from get_design_markdown.analyzer.analyze import DEFAULT_DELTA_E, run_analyze
from get_design_markdown.collector import collect
from get_design_markdown.collector.robots import check_robots
from get_design_markdown.console import (
    console,
    print_info,
    print_step,
    print_success,
    print_warning,
    progress_status,
)
from get_design_markdown.exporters.export import run_export
from get_design_markdown.models.raw import CaptureOptions, RawCapture, RobotsCheck
from get_design_markdown.storage import (
    ANALYSIS_FILE,
    DESIGN_FILE,
    PREVIEW_FILE,
    PROMPT_FILE,
    RAW_FILE,
    SYNTHESIS_FILE,
    TOKENS_FILE,
    normalize_url,
    save_model,
    site_dir,
)
from get_design_markdown.synthesizer.synthesize import run_synthesize

ROBOTS_TIMEOUT_S = 15.0


def report_robots(result: RobotsCheck) -> None:
    if result.allowed is True:
        print_success(result.note)
    elif result.allowed is False:
        print_warning(
            f"{result.note} Công cụ vẫn tiếp tục vì mục đích tham khảo — "
            "hãy tôn trọng điều khoản sử dụng của trang."
        )
    else:
        print_warning(result.note)


def summarize_capture(raw: RawCapture) -> None:
    for page in raw.pages:
        truncated = (
            f" (giới hạn {len(page.elements)}/{page.elements_total})"
            if page.elements_total > len(page.elements)
            else ""
        )
        print_info(
            f"Trang '{page.name}': {len(page.elements)} phần tử{truncated}, "
            f"{len(page.css_variables)} biến CSS, {len(page.hover_states)} trạng thái hover, "
            f"{len(page.screenshots)} ảnh chụp."
        )
    if raw.dark is not None:
        print_info(f"Dark mode: {len(raw.dark.elements)} phần tử.")
    print_info(f"Stylesheet: {len(raw.stylesheets)} file.")
    for warning in raw.warnings:
        print_warning(warning)
    for page in raw.pages:
        for warning in page.warnings:
            print_warning(f"[{page.name}] {warning}")


def run_collect(url: str, *, out_root: Path, options: CaptureOptions) -> Path:
    """robots.txt check, browser capture, raw.json. Returns the site folder."""
    page_url = normalize_url(url)
    folder = site_dir(out_root, page_url)
    console.print(f"[bold]Thu thập[/] {escape(page_url)} → {escape(str(folder))}")
    robots = asyncio.run(check_robots(page_url, timeout_s=min(options.timeout_s, ROBOTS_TIMEOUT_S)))
    report_robots(robots)
    with progress_status("Đang thu thập") as progress:
        raw = asyncio.run(
            collect.collect_site(page_url, out_dir=folder, options=options, progress=progress)
        )
    raw = raw.model_copy(update={"robots": robots, "options": options})
    path = save_model(raw, folder / RAW_FILE)
    summarize_capture(raw)
    print_success(f"Đã ghi {path}")
    return folder


def open_result(folder: Path) -> Path | None:
    """Open preview.html (or DESIGN.md when there is no preview) in the default browser."""
    for name in (PREVIEW_FILE, DESIGN_FILE):
        path = folder / name
        if path.is_file():
            webbrowser.open(path.resolve().as_uri())
            print_info(f"Đã mở {path} trong trình duyệt.")
            return path
    print_warning("Không có preview.html hay DESIGN.md để mở.")
    return None


@dataclass(frozen=True)
class GetResult:
    folder: Path
    files: list[Path] = field(default_factory=list)
    opened: Path | None = None


def run_get(
    url: str,
    *,
    out_root: Path,
    options: CaptureOptions,
    lang: str = "en",
    prompt_only: bool = False,
    examples: bool = False,
    open_browser: bool = False,
    threshold: float = DEFAULT_DELTA_E,
) -> GetResult:
    """The whole pipeline: collect, analyze, synthesize, export; any stage error stops it."""
    print_step(1, 4, "Thu thập dữ liệu trang (Tầng 1)")
    folder = run_collect(url, out_root=out_root, options=options)
    print_step(2, 4, "Phân tích thiết kế (Tầng 2)")
    run_analyze(folder, threshold=threshold)
    print_step(3, 4, "Dựng DESIGN.md (Tầng 3)")
    run_synthesize(folder, lang=lang, prompt_only=prompt_only, examples=examples)
    print_step(4, 4, "Xuất tokens.json và preview.html")
    run_export(folder)

    files = [
        folder / name
        for name in (
            DESIGN_FILE,
            TOKENS_FILE,
            PREVIEW_FILE,
            PROMPT_FILE,
            ANALYSIS_FILE,
            SYNTHESIS_FILE,
            RAW_FILE,
        )
        if (folder / name).is_file()
    ]
    table = Table(title="Kết quả", show_header=False)
    table.add_column("File", style="bold")
    for path in files:
        table.add_row(str(path))
    console.print(table)
    opened = open_result(folder) if open_browser else None
    return GetResult(folder=folder, files=files, opened=opened)
