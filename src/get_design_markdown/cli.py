"""Command-line interface: gdm / get-design-markdown (frozen glue)."""

from __future__ import annotations

import functools
import io
import re
import sys
from collections.abc import Callable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ParamSpec, TypeVar

import typer
from typer import rich_utils
from typer._click.core import Context, Parameter
from typer._click.types import StringParamType
from typer.core import TyperCommand, TyperGroup

from get_design_markdown import __version__
from get_design_markdown.analyzer.analyze import DEFAULT_DELTA_E, run_analyze
from get_design_markdown.config import Settings, load_settings
from get_design_markdown.console import console, err_console, print_error
from get_design_markdown.errors import ConfigError, GdmError
from get_design_markdown.exporters.export import run_export
from get_design_markdown.models.raw import CaptureOptions
from get_design_markdown.pipeline import open_result, run_collect, run_get
from get_design_markdown.storage import resolve_site_dir
from get_design_markdown.synthesizer.synthesize import run_synthesize

if TYPE_CHECKING:
    from get_design_markdown.ui.server import UiMode

P = ParamSpec("P")
R = TypeVar("R")

# ─── Vietnamese help screens ─────────────────────────────────────────────────

HELP_OPTION_TEXT = "Hiện hướng dẫn này rồi thoát."
USAGE_PREFIX = "Cách dùng: "
#: Usage-line placeholders (Click's defaults are "[OPTIONS]" and "COMMAND [ARGS]...").
OPTIONS_METAVAR = "[TÙY_CHỌN]"
SUBCOMMAND_METAVAR = "LỆNH [THAM_SỐ]..."


def _localize_help_option(option: Any) -> Any:
    if option is not None:
        option.help = HELP_OPTION_TEXT
    return option


class VietnameseGroup(TyperGroup):
    """The gdm group: Vietnamese --help/-h text, usage prefix and placeholders."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.options_metavar = OPTIONS_METAVAR
        self.subcommand_metavar = SUBCOMMAND_METAVAR

    def get_help_option(self, ctx: Any) -> Any:
        return _localize_help_option(super().get_help_option(ctx))

    def format_usage(self, ctx: Any, formatter: Any) -> None:
        pieces = self.collect_usage_pieces(ctx)
        formatter.write_usage(ctx.command_path, " ".join(pieces), prefix=USAGE_PREFIX)


class VietnameseCommand(TyperCommand):
    """A gdm sub-command: Vietnamese --help/-h text, usage prefix and placeholders."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.options_metavar = OPTIONS_METAVAR

    def get_help_option(self, ctx: Any) -> Any:
        return _localize_help_option(super().get_help_option(ctx))

    def format_usage(self, ctx: Any, formatter: Any) -> None:
        # Typer writes a required argument as "{URL}"; the plain name reads better.
        pieces = [_unbrace(piece) for piece in self.collect_usage_pieces(ctx)]
        formatter.write_usage(ctx.command_path, " ".join(pieces), prefix=USAGE_PREFIX)


def _unbrace(piece: str) -> str:
    return piece[1:-1] if piece.startswith("{") and piece.endswith("}") else piece


class PlainText(StringParamType):
    """A text argument whose help row shows only its Vietnamese name, not Typer's "<str>"."""

    def get_metavar(self, param: Parameter, ctx: Context) -> str | None:
        return ""


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


class Browser(StrEnum):
    """--browser values (models.raw.BrowserChoice)."""

    AUTO = "auto"
    CHROMIUM = "chromium"
    MSEDGE = "msedge"
    CHROME = "chrome"


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
    settings: Settings,
    *,
    dark: bool,
    pages: int,
    max_elements: int | None,
    browser: Browser | None = None,
) -> CaptureOptions:
    return CaptureOptions(
        max_elements=max_elements or settings.max_elements,
        dark=dark,
        pages=pages,
        timeout_s=settings.timeout_s,
        browser=browser.value if browser is not None else settings.browser,
    )


#: Placeholders shown in usage lines and help screens instead of Typer's <str>, <path>...
URL_METAVAR = "URL"
FOLDER_METAVAR = "THƯ_MỤC"
NUMBER_METAVAR = "SỐ"
BROWSER_METAVAR = "TRÌNH_DUYỆT"

TargetArgument = Annotated[
    str,
    typer.Argument(
        metavar=FOLDER_METAVAR,
        click_type=PlainText(),
        help="Thư mục output/<domain> hoặc URL/tên miền đã thu thập.",
    ),
]
OutOption = Annotated[
    Path | None,
    typer.Option(
        "--out",
        "-o",
        metavar=FOLDER_METAVAR,
        help="Thư mục gốc chứa kết quả (mặc định: output/).",
    ),
]
DarkOption = Annotated[
    bool, typer.Option("--dark", help="Thu thêm bản dark mode (prefers-color-scheme: dark).")
]
PagesOption = Annotated[
    int,
    typer.Option(
        "--pages",
        min=0,
        max=10,
        metavar=NUMBER_METAVAR,
        help="Số trang con quan trọng (từ navigation) phân tích thêm.",
    ),
]
MaxElementsOption = Annotated[
    int | None,
    typer.Option(
        "--max-elements",
        min=1,
        metavar=NUMBER_METAVAR,
        help="Số phần tử tối đa lấy style (mặc định 3000).",
    ),
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
BrowserOption = Annotated[
    Browser | None,
    typer.Option(
        "--browser",
        metavar=BROWSER_METAVAR,
        help=(
            "Trình duyệt dùng để mở trang: auto (Chromium của Playwright → Edge → Chrome), "
            "chromium, msedge hoặc chrome (mặc định: biến GDM_BROWSER, nếu không có thì auto)."
        ),
        show_default=False,
    ),
]
DeltaEOption = Annotated[
    float,
    typer.Option(
        "--delta-e",
        min=0.5,
        max=20.0,
        metavar=NUMBER_METAVAR,
        help="Ngưỡng gom cụm màu CIEDE2000 (mặc định 3).",
    ),
]


@app.command(cls=VietnameseCommand)
@handle_errors
def collect(
    url: Annotated[
        str,
        typer.Argument(
            metavar=URL_METAVAR,
            click_type=PlainText(),
            help="URL trang cần phân tích, ví dụ https://stripe.com",
        ),
    ],
    out: OutOption = None,
    dark: DarkOption = False,
    pages: PagesOption = 0,
    max_elements: MaxElementsOption = None,
    browser: BrowserOption = None,
) -> None:
    """Tầng 1: mở trang bằng trình duyệt, thu thập style → output/<domain>/raw.json."""
    settings = load_settings()
    options = capture_options(
        settings, dark=dark, pages=pages, max_elements=max_elements, browser=browser
    )
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
    url: Annotated[
        str,
        typer.Argument(
            metavar=URL_METAVAR, click_type=PlainText(), help="URL trang cần phân tích."
        ),
    ],
    out: OutOption = None,
    dark: DarkOption = False,
    open_browser: OpenOption = False,
    pages: PagesOption = 0,
    lang: LangOption = Lang.EN,
    prompt_only: PromptOnlyOption = False,
    examples: ExamplesOption = False,
    max_elements: MaxElementsOption = None,
    browser: BrowserOption = None,
) -> None:
    """Chạy trọn gói collect → analyze → synthesize → tokens.json + preview.html."""
    settings = load_settings()
    options = capture_options(
        settings, dark=dark, pages=pages, max_elements=max_elements, browser=browser
    )
    run_get(
        url,
        out_root=out or settings.output_dir,
        options=options,
        lang=lang.value,
        prompt_only=prompt_only,
        examples=examples,
        open_browser=open_browser,
    )


PORT_METAVAR = "CỔNG"


@app.command(cls=VietnameseCommand)
@handle_errors
def ui(
    port: Annotated[
        int,
        typer.Option(
            "--port",
            min=0,
            max=65535,
            metavar=PORT_METAVAR,
            help="Cổng của máy chủ cục bộ 127.0.0.1 (0 = tự chọn một cổng trống).",
        ),
    ] = 0,
    window: Annotated[
        bool,
        typer.Option(
            "--window",
            help=(
                "Mở trong cửa sổ riêng thay vì tab trình duyệt "
                '(cần gói pywebview: pip install "get-design-markdown\\[gui]").'
            ),
        ),
    ] = False,
    no_open: Annotated[
        bool,
        typer.Option("--no-open", help="Không tự mở giao diện, chỉ in địa chỉ để bạn tự mở."),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            "-o",
            metavar=FOLDER_METAVAR,
            help="Thư mục gốc chứa kết quả cho phiên này (mặc định: theo cấu hình giao diện).",
        ),
    ] = None,
) -> None:
    """Mở giao diện đồ họa cục bộ: nhập URL, chỉnh tùy chọn và xem kết quả."""
    if window and no_open:
        raise ConfigError(
            "Không dùng --window cùng với --no-open.",
            hint="Chọn --window để mở cửa sổ riêng, hoặc --no-open để chỉ in địa chỉ.",
        )
    from get_design_markdown.ui import app as ui_app

    mode: UiMode = "window" if window else "none" if no_open else "browser"
    ui_app.run_ui(port=port, mode=mode, out_root=out)


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
        re.compile(r"(?P<value>.+) is not a valid int(?:eger)?( range)?\."),
        "{value} không phải số nguyên.",
    ),
    (
        re.compile(r"(?P<value>.+) is not a valid float(?:ing point value)?( range)?\."),
        "{value} không phải số thực.",
    ),
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
