"""Command-line interface: gdm / get-design-markdown (frozen glue)."""

from __future__ import annotations

import functools
import io
import sys
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, ParamSpec, TypeVar

import typer

from get_design_markdown import __version__
from get_design_markdown.analyzer.analyze import DEFAULT_DELTA_E, run_analyze
from get_design_markdown.config import Settings, load_settings
from get_design_markdown.console import console, err_console, print_error
from get_design_markdown.errors import GdmError
from get_design_markdown.exporters.export import run_export
from get_design_markdown.models.raw import CaptureOptions
from get_design_markdown.pipeline import open_result, run_collect, run_get
from get_design_markdown.storage import resolve_site_dir
from get_design_markdown.synthesizer.synthesize import run_synthesize

P = ParamSpec("P")
R = TypeVar("R")

app = typer.Typer(
    name="gdm",
    help=(
        "Trích xuất ngôn ngữ thiết kế của một trang web thành DESIGN.md "
        "(spec DESIGN.md của Google Labs) để AI coding agent tạo UI theo phong cách tương tự."
    ),
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)


class Lang(StrEnum):
    EN = "en"
    VI = "vi"


class _State:
    debug = False


def handle_errors(func: Callable[P, R]) -> Callable[P, R]:
    """Print expected errors in Vietnamese without a traceback (unless --debug)."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except GdmError as error:
            if _State.debug:
                raise
            print_error(error)
            raise typer.Exit(code=1) from None
        except KeyboardInterrupt:
            err_console.print("[yellow]Đã hủy.[/]")
            raise typer.Exit(code=130) from None

    return wrapper


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"get-design-markdown {__version__}")
        raise typer.Exit()


@app.callback()
def _main_options(
    debug: Annotated[
        bool, typer.Option("--debug", help="Hiện traceback đầy đủ khi gặp lỗi.")
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="In phiên bản rồi thoát."
        ),
    ] = False,
) -> None:
    _State.debug = debug


def capture_options(
    settings: Settings, *, dark: bool, pages: int, max_elements: int | None
) -> CaptureOptions:
    return CaptureOptions(
        max_elements=max_elements or settings.max_elements,
        dark=dark,
        pages=pages,
        timeout_s=settings.timeout_s,
    )


TargetArgument = Annotated[
    str, typer.Argument(help="Thư mục output/<domain> hoặc URL/tên miền đã thu thập.")
]
OutOption = Annotated[
    Path | None,
    typer.Option("--out", "-o", help="Thư mục gốc chứa kết quả (mặc định: output/)."),
]
DarkOption = Annotated[
    bool, typer.Option("--dark", help="Thu thêm bản dark mode (prefers-color-scheme: dark).")
]
PagesOption = Annotated[
    int,
    typer.Option(
        "--pages", min=0, max=10, help="Số trang con quan trọng (từ navigation) phân tích thêm."
    ),
]
MaxElementsOption = Annotated[
    int | None,
    typer.Option("--max-elements", min=1, help="Số phần tử tối đa lấy style (mặc định 3000)."),
]
LangOption = Annotated[Lang, typer.Option("--lang", help="Ngôn ngữ phần mô tả trong DESIGN.md.")]
PromptOnlyOption = Annotated[
    bool,
    typer.Option(
        "--prompt-only",
        help="Xuất thêm prompt.md + ảnh để tự dán vào Claude Code/claude.ai (không gọi API).",
    ),
]
ExamplesOption = Annotated[
    bool, typer.Option("--examples", help="Thêm các surface minh họa ex-* vào DESIGN.md.")
]
OpenOption = Annotated[
    bool, typer.Option("--open", help="Mở preview.html (hoặc DESIGN.md) trong trình duyệt.")
]
DeltaEOption = Annotated[
    float,
    typer.Option("--delta-e", min=0.5, max=20.0, help="Ngưỡng gom cụm màu CIEDE2000 (mặc định 3)."),
]


@app.command()
@handle_errors
def collect(
    url: Annotated[str, typer.Argument(help="URL trang cần phân tích, ví dụ https://stripe.com")],
    out: OutOption = None,
    dark: DarkOption = False,
    pages: PagesOption = 0,
    max_elements: MaxElementsOption = None,
) -> None:
    """Tầng 1: mở trang bằng trình duyệt, thu thập style → output/<domain>/raw.json."""
    settings = load_settings()
    options = capture_options(settings, dark=dark, pages=pages, max_elements=max_elements)
    run_collect(url, out_root=out or settings.output_dir, options=options)


@app.command()
@handle_errors
def analyze(target: TargetArgument, delta_e: DeltaEOption = DEFAULT_DELTA_E) -> None:
    """Tầng 2: phân tích raw.json → analysis.json (thuần code, không dùng AI)."""
    folder = resolve_site_dir(target, load_settings().output_dir)
    run_analyze(folder, threshold=delta_e)


@app.command()
@handle_errors
def synthesize(
    target: TargetArgument,
    lang: LangOption = Lang.EN,
    prompt_only: PromptOnlyOption = False,
    examples: ExamplesOption = False,
) -> None:
    """Tầng 3: dựng DESIGN.md từ analysis.json bằng luật + template."""
    folder = resolve_site_dir(target, load_settings().output_dir)
    run_synthesize(folder, lang=lang.value, prompt_only=prompt_only, examples=examples)


@app.command()
@handle_errors
def export(target: TargetArgument, open_browser: OpenOption = False) -> None:
    """Xuất tokens.json (W3C Design Tokens) và preview.html từ analysis.json."""
    folder = resolve_site_dir(target, load_settings().output_dir)
    run_export(folder)
    if open_browser:
        open_result(folder)


@app.command()
@handle_errors
def get(
    url: Annotated[str, typer.Argument(help="URL trang cần phân tích.")],
    out: OutOption = None,
    dark: DarkOption = False,
    open_browser: OpenOption = False,
    pages: PagesOption = 0,
    lang: LangOption = Lang.EN,
    prompt_only: PromptOnlyOption = False,
    examples: ExamplesOption = False,
    max_elements: MaxElementsOption = None,
) -> None:
    """Chạy trọn gói collect → analyze → synthesize → tokens.json + preview.html."""
    settings = load_settings()
    options = capture_options(settings, dark=dark, pages=pages, max_elements=max_elements)
    run_get(
        url,
        out_root=out or settings.output_dir,
        options=options,
        lang=lang.value,
        prompt_only=prompt_only,
        examples=examples,
        open_browser=open_browser,
    )


def _ensure_utf8_stdio() -> None:
    """Piped output on Windows defaults to a legacy code page; Vietnamese needs UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower() not in (
            "utf-8",
            "utf8",
        ):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _ensure_utf8_stdio()
    app()
