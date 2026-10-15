"""Command-line interface: gdm / get-design-markdown (frozen glue)."""

from __future__ import annotations

import functools
import io
import re
import sys
from collections.abc import Callable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, ParamSpec, TypeVar

import typer
from typer import rich_utils
from typer.core import TyperCommand, TyperGroup

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

# ─── Vietnamese help screens ─────────────────────────────────────────────────

HELP_OPTION_TEXT = "Hiện hướng dẫn này rồi thoát."
USAGE_PREFIX = "Cách dùng: "


def _localize_help_option(option: Any) -> Any:
    if option is not None:
        option.help = HELP_OPTION_TEXT
    return option


class VietnameseGroup(TyperGroup):
    """The gdm group: Vietnamese --help/-h text and usage prefix."""

    def get_help_option(self, ctx: Any) -> Any:
        return _localize_help_option(super().get_help_option(ctx))

    def format_usage(self, ctx: Any, formatter: Any) -> None:
        pieces = self.collect_usage_pieces(ctx)
        formatter.write_usage(ctx.command_path, " ".join(pieces), prefix=USAGE_PREFIX)


class VietnameseCommand(TyperCommand):
    """A gdm sub-command: Vietnamese --help/-h text and usage prefix."""

    def get_help_option(self, ctx: Any) -> Any:
        return _localize_help_option(super().get_help_option(ctx))

    def format_usage(self, ctx: Any, formatter: Any) -> None:
        pieces = self.collect_usage_pieces(ctx)
        formatter.write_usage(ctx.command_path, " ".join(pieces), prefix=USAGE_PREFIX)


def _localize_help_screen() -> None:
    """Panel titles and labels of Typer's Rich help screens (read when a screen is drawn)."""
    rich_utils.ARGUMENTS_PANEL_TITLE = "Tham số"
    rich_utils.OPTIONS_PANEL_TITLE = "Tùy chọn"
    rich_utils.COMMANDS_PANEL_TITLE = "Lệnh"
    rich_utils.ERRORS_PANEL_TITLE = "Lỗi"
    rich_utils.ABORTED_TEXT = "Đã hủy."
    rich_utils.DEFAULT_STRING = "[mặc định: {}]"
    rich_utils.REQUIRED_LONG_STRING = "[bắt buộc]"
    rich_utils.RICH_HELP = "Chạy [blue]'{command_path} {help_option}'[/] để xem hướng dẫn."


_localize_help_screen()

app = typer.Typer(
    name="gdm",
    help=(
        "Trích xuất ngôn ngữ thiết kế của một trang web thành DESIGN.md "
        "(spec DESIGN.md của Google Labs) để AI coding agent tạo UI theo phong cách tương tự."
    ),
    cls=VietnameseGroup,
    context_settings={"help_option_names": ["--help", "-h"]},
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


@app.command(cls=VietnameseCommand)
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


@app.command(cls=VietnameseCommand)
@handle_errors
def analyze(target: TargetArgument, delta_e: DeltaEOption = DEFAULT_DELTA_E) -> None:
    """Tầng 2: phân tích raw.json → analysis.json (thuần code, không dùng AI)."""
    folder = resolve_site_dir(target, load_settings().output_dir)
    run_analyze(folder, threshold=delta_e)


@app.command(cls=VietnameseCommand)
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


@app.command(cls=VietnameseCommand)
@handle_errors
def export(target: TargetArgument, open_browser: OpenOption = False) -> None:
    """Xuất tokens.json (W3C Design Tokens) và preview.html từ analysis.json."""
    folder = resolve_site_dir(target, load_settings().output_dir)
    run_export(folder)
    if open_browser:
        open_result(folder)


@app.command(cls=VietnameseCommand)
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


# ─── Vietnamese usage errors ─────────────────────────────────────────────────

_Table = list[tuple["re.Pattern[str]", str]]

#: Click/Typer usage messages (English, hard-coded) -> Vietnamese; full matches, in order.
_USAGE_MESSAGES: _Table = [
    (re.compile(r"Missing argument (?P<name>.+)\."), "Thiếu tham số bắt buộc {name}."),
    (re.compile(r"Missing option (?P<name>.+)\."), "Thiếu tùy chọn bắt buộc {name}."),
    (re.compile(r"Missing parameter:? ?(?P<name>.*?)\.?"), "Thiếu tham số {name}."),
    (re.compile(r"Missing command\."), "Thiếu lệnh."),
    (
        re.compile(r"No such command (?P<name>.+?)\. Did you mean (?P<suggestion>.+)\?"),
        "Không có lệnh {name}. Có phải ý bạn là {suggestion}?",
    ),
    (re.compile(r"No such command (?P<name>.+)\."), "Không có lệnh {name}."),
    (
        re.compile(r"No such option: (?P<name>\S+) \(Possible options: (?P<suggestion>.+)\)"),
        "Không có tùy chọn {name}. Có phải ý bạn là {suggestion}?",
    ),
    (re.compile(r"No such option: (?P<name>\S+)"), "Không có tùy chọn {name}."),
    (re.compile(r"Option (?P<name>.+) requires an argument\."), "Tùy chọn {name} cần một giá trị."),
    (
        re.compile(r"Option (?P<name>.+) requires (?P<count>\d+) arguments\."),
        "Tùy chọn {name} cần {count} giá trị.",
    ),
    (
        re.compile(r"Option (?P<name>.+) does not take a value\."),
        "Tùy chọn {name} không nhận giá trị.",
    ),
    (
        re.compile(r"Got unexpected extra arguments? ?(\(s\))? \((?P<args>.*)\)"),
        "Thừa tham số không dùng tới: {args}.",
    ),
    (
        re.compile(r"Invalid value for (?P<name>.+?): (?P<detail>.+)", re.DOTALL),
        "Giá trị không hợp lệ cho {name}: {detail}",
    ),
    (re.compile(r"Invalid value: (?P<detail>.+)", re.DOTALL), "Giá trị không hợp lệ: {detail}"),
]

#: Reasons after "Invalid value for X:" (parameter types).
_VALUE_MESSAGES: _Table = [
    (
        re.compile(r"(?P<value>.+) is not in the range (?P<range>.+)\."),
        "{value} nằm ngoài khoảng {range}.",
    ),
    (
        re.compile(r"(?P<value>.+) is not a valid integer( range)?\."),
        "{value} không phải số nguyên.",
    ),
    (re.compile(r"(?P<value>.+) is not a valid float( range)?\."), "{value} không phải số thực."),
    (
        re.compile(r"(?P<value>.+) is not a valid boolean\..*", re.DOTALL),
        "{value} không phải giá trị đúng/sai.",
    ),
    (
        re.compile(r"(?P<value>.+) is not one of (?P<choices>.+)\."),
        "{value} không thuộc các giá trị {choices}.",
    ),
]


def _translate(message: str, table: _Table) -> str | None:
    for pattern, template in table:
        match = pattern.fullmatch(message.strip())
        if match is not None:
            return template.format(**{k: v for k, v in match.groupdict().items() if v is not None})
    return None


def translate_usage_error(message: str) -> str:
    """Vietnamese text of a Click/Typer usage error; unknown messages are kept as they are."""
    for pattern, template in _USAGE_MESSAGES:
        match = pattern.fullmatch(message.strip())
        if match is None:
            continue
        fields = {key: value for key, value in match.groupdict().items() if value is not None}
        if "detail" in fields:
            fields["detail"] = _translate(fields["detail"], _VALUE_MESSAGES) or fields["detail"]
        return template.format(**fields)
    return message


def _print_usage_error(error: typer.TyperException) -> None:
    ctx = getattr(error, "ctx", None)
    hint = "chạy lệnh kèm --help để xem hướng dẫn."
    if ctx is not None:
        err_console.print(ctx.get_usage(), markup=False, highlight=False)
        if ctx.command.get_help_option(ctx) is not None:
            hint = f"chạy '{ctx.command_path} {ctx.help_option_names[0]}' để xem hướng dẫn."
    print_error(GdmError(translate_usage_error(error.format_message()), hint=hint))


def run(argv: Sequence[str] | None = None) -> int:
    """Run gdm and return its exit code; usage errors are printed in Vietnamese."""
    try:
        result = app(args=None if argv is None else list(argv), standalone_mode=False)
    except typer.TyperException as error:
        # Click usage errors: missing argument, unknown command or option, bad value...
        # For a bare `gdm` (NoArgsIsHelpError) Typer has already drawn the help screen.
        if type(error).__name__ != "NoArgsIsHelpError":
            _print_usage_error(error)
        return error.exit_code
    except typer.Abort:
        err_console.print("[yellow]Đã hủy.[/]")
        return 1
    return result if isinstance(result, int) else 0


def main() -> None:
    _ensure_utf8_stdio()
    sys.exit(run())
