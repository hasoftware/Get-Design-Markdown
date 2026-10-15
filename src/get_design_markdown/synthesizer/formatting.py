"""Number formatting and Markdown escaping shared by the synthesizer modules."""

from __future__ import annotations

import re
from collections.abc import Iterable

from get_design_markdown.analyzer.css_values import format_px

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|{}#~])")
_WHITESPACE = re.compile(r"\s+")
_BACKTICKS = re.compile(r"`+")


def fmt_num(value: float, *, precision: int = 2) -> str:
    """1024.0 -> "1024", 25.6 -> "25.6", 1.333 -> "1.33", -0.0 -> "0"."""
    rounded = round(value, precision)
    if rounded == 0:
        return "0"
    return f"{rounded:.{precision}f}".rstrip("0").rstrip(".")


def fmt_px(value: float) -> str:
    """16.0 -> "16px" (css_values.format_px)."""
    return format_px(value)


def fmt_ratio(ratio: float) -> str:
    """WCAG ratio as written everywhere in DESIGN.md: 2.564 -> "2.56:1", 3.3 -> "3.30:1"."""
    return f"{ratio:.2f}:1"


def fmt_percent(fraction: float) -> str:
    """0.8 -> "80%", 0.122 -> "12%"."""
    return f"{round(fraction * 100)}%"


def md_text(text: str) -> str:
    """Site-derived text made safe for inline Markdown.

    Collapses whitespace and backslash-escapes Markdown syntax, including braces, so that a
    page text such as "{colors.x}" can never be read as a token reference.
    """
    return _MD_SPECIAL.sub(r"\\\1", _WHITESPACE.sub(" ", text).strip())


def md_code(text: str) -> str:
    """Inline code span that survives backticks inside the text."""
    clean = _WHITESPACE.sub(" ", text).strip()
    longest = max((len(run) for run in _BACKTICKS.findall(clean)), default=0)
    fence = "`" * (longest + 1)
    if longest or clean.startswith(" ") or clean.endswith(" "):
        return f"{fence} {clean} {fence}"
    return f"{fence}{clean}{fence}"


def md_quote(text: str) -> str:
    """Site text shown as a short quotation (“Get started”)."""
    return f"“{md_text(text)}”"


def table(headers: Iterable[str], rows: Iterable[Iterable[str]]) -> str:
    """GitHub-flavoured Markdown table; cells must already be escaped (no raw "|")."""
    head = list(headers)
    lines = [
        "| " + " | ".join(head) + " |",
        "| " + " | ".join("---" for _ in head) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def bullets(items: Iterable[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def unique(items: Iterable[str]) -> list[str]:
    """Items without duplicates, first occurrence kept (deterministic)."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def longest_backtick_run(text: str) -> int:
    return max((len(run) for run in _BACKTICKS.findall(text)), default=0)
