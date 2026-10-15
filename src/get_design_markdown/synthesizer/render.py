"""Assemble the full DESIGN.md text.

PUBLIC ENTRY POINT (frozen signature, called by synthesizer/synthesize.py):
    render_design_md(analysis, lang=..., examples=...) -> str
"""

from __future__ import annotations

import re
from datetime import UTC
from typing import Final

from get_design_markdown.models.analysis import DesignAnalysis
from get_design_markdown.resources import render_template
from get_design_markdown.synthesizer.catalog import Phrases, load_phrases
from get_design_markdown.synthesizer.facts import DesignFacts
from get_design_markdown.synthesizer.formatting import md_text
from get_design_markdown.synthesizer.frontmatter import (
    build_front_matter_from_facts,
    dump_front_matter,
)
from get_design_markdown.synthesizer.narrative import build_context, sections_from_context

#: "## " sections in the mandatory order (Google Labs DESIGN.md spec + Agent Prompt Guide last).
DESIGN_SECTIONS: Final = (
    "Overview",
    "Colors",
    "Typography",
    "Layout",
    "Elevation & Depth",
    "Shapes",
    "Components",
    "Do's and Don'ts",
    "Agent Prompt Guide",
)
SUPPORTED_LANGS: Final = ("en", "vi")
TEMPLATE: Final = "design.md.j2"

_BLANK_RUNS = re.compile(r"\n{3,}")


def site_label(analysis: DesignAnalysis) -> str:
    return analysis.site.name.strip() or analysis.site.url


def extraction_date(analysis: DesignAnalysis, phrases: Phrases) -> str:
    collected = analysis.site.collected_at
    if collected is None:
        return phrases.get("note.unknown_date")
    if collected.tzinfo is not None:
        collected = collected.astimezone(UTC)
    return collected.date().isoformat()


def reference_note(analysis: DesignAnalysis, phrases: Phrases) -> str:
    """Blockquote under the title: source, date, pages, generator and disclaimer."""
    site = md_text(site_label(analysis))
    pages = [md_text(page) for page in analysis.site.pages_analyzed] or ["—"]
    paragraphs = [
        phrases.get(
            "note.source",
            url=f"<{analysis.site.url}>",
            date=extraction_date(analysis, phrases),
            pages=phrases.join(pages),
        ),
        phrases.get("note.generated", version=analysis.tool_version),
        phrases.get("note.disclaimer", site=site),
    ]
    return "\n>\n".join(f"> {paragraph}" for paragraph in paragraphs)


def normalize_markdown(text: str) -> str:
    """LF newlines, no trailing spaces, at most one blank line in a row, one final newline."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return _BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip("\n") + "\n"


def render_design_md(analysis: DesignAnalysis, *, lang: str = "en", examples: bool = False) -> str:
    """Front matter (YAML between "---" lines) + "# <Site> — Design Reference" + reference note
    (source URL, extraction date = analysis.site.collected_at, pages analyzed, "reference only,
    not an official design system") + the sections of DESIGN_SECTIONS in order.

    Deterministic: the same analysis gives byte-identical output (no clock, no randomness).
    Section titles and token names stay English; prose follows `lang`.
    """
    phrases = load_phrases(lang)
    facts = DesignFacts(analysis)
    front = build_front_matter_from_facts(facts, phrases, examples=examples)
    context = build_context(facts, phrases, front.get("components", {}), examples=examples)
    sections = sections_from_context(context)
    text = render_template(
        TEMPLATE,
        front_matter=dump_front_matter(front),
        title=f"{md_text(site_label(analysis))} — Design Reference",
        note=reference_note(analysis, phrases),
        sections=sections,
    )
    return normalize_markdown(text)
