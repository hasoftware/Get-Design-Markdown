"""Run the whole pipeline on the static fixture site and verify the result (CI job "e2e").

    python scripts/run_e2e.py [--out e2e-output] [--site tests/fixtures/site] [--timeout 900]

1. No fixture site yet (tests/fixtures/site/index.html) -> "::notice::" and exit 0.
2. Serve the site on http://127.0.0.1:<free port>/ and run
   `python -m get_design_markdown get <url> --out <out> --dark --pages 2` (PYTHONUTF8=1).
3. A stage that is not built yet (Vietnamese "... chưa ... được xây dựng" error) -> notice; the
   files that do exist are still verified (verify_output --partial) and exit 0 unless they are
   broken. Any other failure -> exit with gdm's code.
4. verify_output() on <out>/127.0.0.1_<port>; errors -> exit 1.
5. Print the DESIGN.md path and write design_md=<path> / site_dir=<path> to $GITHUB_OUTPUT.
"""

from __future__ import annotations

import argparse
import functools
import io
import os
import subprocess
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # `python scripts/run_e2e.py` only puts scripts/ on the path
    sys.path.insert(0, str(REPO_ROOT))

from scripts.verify_output import (  # noqa: E402
    DESIGN_FILE,
    exit_code,
    format_findings,
    verify_output,
)

DEFAULT_OUT: Final = Path("e2e-output")
DEFAULT_SITE: Final = REPO_ROOT / "tests" / "fixtures" / "site"
DEFAULT_TIMEOUT_S: Final = 900.0
E2E_PAGES: Final = 2
HOST: Final = "127.0.0.1"
ERROR_PREFIX: Final = "Lỗi:"
HINT_PREFIX: Final = "Gợi ý:"
MAX_ANNOTATIONS: Final = 20


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


@contextmanager
def serve_site(directory: Path) -> Iterator[str]:
    """Serve `directory` on 127.0.0.1:<free port> in a background thread; yields the base URL."""
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer((HOST, 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{HOST}:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def pipeline_command(url: str, out: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "get_design_markdown",
        "get",
        url,
        "--out",
        str(out),
        "--dark",
        "--pages",
        str(E2E_PAGES),
    ]


def run_pipeline(url: str, out: Path, *, timeout_s: float) -> subprocess.CompletedProcess[str]:
    """`gdm get` in a subprocess with UTF-8 output and wide (unwrapped) Rich lines."""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", COLUMNS="200")
    return subprocess.run(
        pipeline_command(url, out),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
    )


def stage_missing_message(output: str) -> str | None:
    """The CLI error message when gdm stopped because a stage is not built yet, else None.

    The CLI prints "Lỗi: <message>" (+ "Gợi ý: …"); unbuilt stages raise NotImplementedYetError
    whose message says the part "chưa được xây dựng" / "Chưa có … nào được xây dựng".
    """
    text = " ".join(output.split())
    start = text.rfind(ERROR_PREFIX)
    if start < 0:
        return None
    message = text[start + len(ERROR_PREFIX) :]
    hint = message.find(HINT_PREFIX)
    if hint >= 0:
        message = message[:hint]
    message = message.strip()
    lowered = message.lower()
    return message if "chưa" in lowered and "được xây dựng" in lowered else None


def annotation(kind: str, message: str) -> str:
    """GitHub Actions workflow command (::notice:: / ::warning:: / ::error::), one line."""
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::{kind}::{escaped}"


def write_github_output(values: Mapping[str, str], env: Mapping[str, str] | None = None) -> None:
    """Append key=value lines to the $GITHUB_OUTPUT file when running in GitHub Actions."""
    target = (os.environ if env is None else env).get("GITHUB_OUTPUT")
    if not target:
        return
    with Path(target).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower() not in (
            "utf-8",
            "utf8",
        ):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_e2e.py",
        description=(
            "Chạy trọn pipeline gdm trên trang fixture cục bộ rồi kiểm tra thư mục kết quả."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Thư mục gốc chứa kết quả (mặc định e2e-output).",
    )
    parser.add_argument(
        "--site",
        type=Path,
        default=DEFAULT_SITE,
        help="Thư mục trang fixture tĩnh (mặc định tests/fixtures/site).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help="Thời gian tối đa cho 'gdm get' (giây).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    args = _parser().parse_args(argv)
    site: Path = args.site
    out: Path = args.out
    if not (site / "index.html").is_file():
        print(annotation("notice", f"Chưa có trang fixture {site / 'index.html'} — bỏ qua e2e."))
        return 0
    out.mkdir(parents=True, exist_ok=True)
    with serve_site(site) as url:
        print(f"Trang fixture: {url}", flush=True)
        try:
            completed = run_pipeline(url, out, timeout_s=args.timeout)
        except subprocess.TimeoutExpired:
            print(annotation("error", f"'gdm get' chạy quá {args.timeout:g} giây."))
            return 1
    sys.stdout.write(completed.stdout)
    sys.stdout.write(completed.stderr)
    folder = out / f"{HOST}_{urlsplit(url).port}"

    partial = False
    if completed.returncode != 0:
        missing = stage_missing_message(completed.stderr) or stage_missing_message(
            completed.stdout + "\n" + completed.stderr
        )
        if missing is None:
            print(annotation("error", f"'gdm get' thất bại (mã thoát {completed.returncode})."))
            return completed.returncode or 1
        print(annotation("notice", f"Stage chưa xong: {missing}"))
        if not folder.is_dir():
            return 0
        partial = True
    elif not folder.is_dir():
        print(annotation("error", f"Không tìm thấy thư mục kết quả {folder}."))
        return 1

    findings = verify_output(folder, partial=partial)
    print(format_findings(findings, folder))
    for kind in ("error", "warning"):
        selected = [finding for finding in findings if finding.severity == kind]
        for finding in selected[:MAX_ANNOTATIONS]:
            print(annotation(kind, f"[{finding.check}] {finding.message}"))
    outputs = {"site_dir": str(folder)}
    design = folder / DESIGN_FILE
    if design.is_file():
        print(f"DESIGN.md: {design}")
        outputs["design_md"] = str(design)
    write_github_output(outputs)
    return exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
