"""Expected errors carrying a Vietnamese, user-facing message."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from get_design_markdown.models.synthesis import ValidationIssue


class GdmError(Exception):
    """Base class for errors shown to the user without a traceback."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class InvalidUrlError(GdmError):
    """The URL given on the command line cannot be used."""


class ConfigError(GdmError):
    """An environment variable or .env value is invalid."""


class OutputNotFoundError(GdmError):
    """A data file expected from a previous stage is missing."""


class InvalidDataError(GdmError):
    """A data file exists but does not match its schema."""


class NotImplementedYetError(GdmError):
    """The command exists but its stage has not been built yet."""


class JobCancelledError(GdmError):
    """The user cancelled a running job (local web UI).

    Raised by the job's message sink (console.use_sink) at the next message the pipeline
    reports, so the stage unwinds through its normal cleanup (Playwright closes the browser).
    """

    def __init__(
        self,
        message: str = "Đã hủy phân tích theo yêu cầu của bạn.",
        hint: str | None = "Các file đã ghi trước khi hủy vẫn nằm trong thư mục kết quả.",
    ) -> None:
        super().__init__(message, hint)


# ─── Stage 1 — Collector ─────────────────────────────────────────────────────


class CollectError(GdmError):
    """The page could not be captured."""


#: Hint for a missing browser: any of the three options is enough.
BROWSER_INSTALL_HINT = (
    "Cài Microsoft Edge hoặc Google Chrome (công cụ tự dùng nếu có), hoặc bấm nút "
    '"Tải Chromium" trong giao diện (gdm ui), hoặc chạy: '
    "python -m playwright install chromium (từ mã nguồn: uv run playwright install chromium)"
)


class BrowserNotInstalledError(CollectError):
    """No usable browser: Playwright's Chromium is missing and, for --browser auto, neither
    Edge nor Chrome was found (hint: Edge/Chrome, the UI button or playwright install)."""

    def __init__(
        self,
        message: str = "Không tìm thấy trình duyệt để mở trang (Chromium, Edge hoặc Chrome).",
        hint: str | None = BROWSER_INSTALL_HINT,
    ) -> None:
        super().__init__(message, hint)


class PageTimeoutError(CollectError):
    """The page did not load within the configured timeout."""


class NavigationError(CollectError):
    """DNS/connection failure or an HTTP error status on the main document."""


class BotBlockedError(CollectError):
    """The site served a bot challenge (Cloudflare, captcha, 403/429...)."""


class LoginRequiredError(CollectError):
    """The URL redirects to or only shows a sign-in wall."""


# ─── Stage 2 / 3 / exports ───────────────────────────────────────────────────


class AnalysisError(GdmError):
    """raw.json cannot be analyzed (no pages, no visible elements...)."""


class SynthesisValidationError(GdmError):
    """The generated DESIGN.md failed its self-check; nothing was written."""

    def __init__(
        self,
        message: str,
        hint: str | None = None,
        issues: Sequence[ValidationIssue] = (),
    ) -> None:
        super().__init__(message, hint)
        self.issues = list(issues)


class ExportError(GdmError):
    """tokens.json or preview.html could not be produced."""
