"""Command-line interface: gdm / get-design-markdown."""

from __future__ import annotations

import asyncio
import functools
import io
import sys
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, ParamSpec, TypeVar

import typer

from get_design_markdown import __version__
from get_design_markdown.collector.robots import check_robots
from get_design_markdown.config import load_settings
from get_design_markdown.console import (
    console,
    err_console,
    print_error,
    print_info,
    print_success,
    print_warning,
)
from get_design_markdown.errors import GdmError, NotImplementedYetError
from get_design_markdown.models.raw import RobotsCheck
from get_design_markdown.storage import normalize_url, resolve_site_dir, site_dir

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


OutOption = Annotated[
    Path | None,
    typer.Option("--out", "-o", help="Thư mục gốc chứa kết quả (mặc định: output/)."),
]
DarkOption = Annotated[
    bool, typer.Option("--dark", help="Thu thêm bản dark mode (prefers-color-scheme: dark).")
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


@app.command()
@handle_errors
def collect(
    url: Annotated[str, typer.Argument(help="URL trang cần phân tích, ví dụ https://stripe.com")],
    out: OutOption = None,
    dark: DarkOption = False,
    max_elements: Annotated[
        int | None,
        typer.Option("--max-elements", min=1, help="Số phần tử tối đa lấy style (mặc định 3000)."),
    ] = None,
) -> None:
    """Tầng 1: mở trang bằng trình duyệt, thu thập style → output/<domain>/raw.json."""
    settings = load_settings()
    page_url = normalize_url(url)
    target = site_dir(out or settings.output_dir, page_url)
    console.print(f"[bold]Thu thập[/] {page_url} → {target}")
    report_robots(asyncio.run(check_robots(page_url, timeout_s=min(settings.timeout_s, 15.0))))
    raise NotImplementedYetError(
        "Phần thu thập bằng trình duyệt chưa được xây dựng (giai đoạn 1).",
        hint="Hiện mới có bước kiểm tra robots.txt.",
    )


@app.command()
@handle_errors
def analyze(
    target: Annotated[str, typer.Argument(help="Thư mục output/<domain> hoặc URL/tên miền.")],
) -> None:
    """Tầng 2: phân tích raw.json → analysis.json (thuần code, không dùng AI)."""
    folder = resolve_site_dir(target, load_settings().output_dir)
    print_info(f"Thư mục dữ liệu: {folder}")
    raise NotImplementedYetError("Lệnh analyze chưa được xây dựng (giai đoạn 3–5).")


@app.command()
@handle_errors
def synthesize(
    target: Annotated[str, typer.Argument(help="Thư mục output/<domain> hoặc URL/tên miền.")],
    lang: LangOption = Lang.EN,
    prompt_only: PromptOnlyOption = False,
    examples: ExamplesOption = False,
) -> None:
    """Tầng 3: dựng DESIGN.md từ analysis.json bằng luật + template."""
    folder = resolve_site_dir(target, load_settings().output_dir)
    print_info(f"Thư mục dữ liệu: {folder}")
    raise NotImplementedYetError("Lệnh synthesize chưa được xây dựng (giai đoạn 6).")


@app.command()
@handle_errors
def get(
    url: Annotated[str, typer.Argument(help="URL trang cần phân tích.")],
    out: OutOption = None,
    dark: DarkOption = False,
    open_browser: Annotated[
        bool, typer.Option("--open", help="Mở preview.html trong trình duyệt khi xong.")
    ] = False,
    pages: Annotated[
        int,
        typer.Option("--pages", min=0, max=10, help="Số trang con quan trọng phân tích thêm."),
    ] = 0,
    lang: LangOption = Lang.EN,
    prompt_only: PromptOnlyOption = False,
    examples: ExamplesOption = False,
) -> None:
    """Chạy trọn gói collect → analyze → synthesize (+ tokens.json, preview.html)."""
    normalize_url(url)
    raise NotImplementedYetError("Lệnh get chưa được xây dựng (giai đoạn 7).")


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
