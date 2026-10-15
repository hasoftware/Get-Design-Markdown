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


# ─── Stage 1 — Collector ─────────────────────────────────────────────────────


class CollectError(GdmError):
    """The page could not be captured."""


class BrowserNotInstalledError(CollectError):
    """Playwright's Chromium is missing (hint: playwright install chromium)."""


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
