"""Expected errors carrying a Vietnamese, user-facing message."""

from __future__ import annotations


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
