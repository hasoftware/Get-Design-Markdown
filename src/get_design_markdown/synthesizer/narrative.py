"""Rule-based prose for each DESIGN.md section (stream: synthesizer, phase GĐ6).

Every sentence comes from synthesizer/phrases/<lang>.yaml through catalog.Phrases; every number,
hex value and token comes from the DesignAnalysis (through facts.DesignFacts). A section whose
data is missing is omitted, so partial analyses still give a valid document.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from get_design_markdown.models.analysis import DesignAnalysis
from get_design_markdown.synthesizer import sections_guidance, sections_layout, sections_style
from get_design_markdown.synthesizer.catalog import Phrases
from get_design_markdown.synthesizer.context import Context
from get_design_markdown.synthesizer.examples import build_examples
from get_design_markdown.synthesizer.facts import DesignFacts
from get_design_markdown.synthesizer.frontmatter import build_front_matter_from_facts
from get_design_markdown.synthesizer.traits import compute_traits


@dataclass(frozen=True)
class Section:
    #: One of render.DESIGN_SECTIONS.
    title: str
    #: Markdown body without the "## " heading line.
    body: str


SectionBuilder = Callable[[Context], str | None]

#: Builders in render.DESIGN_SECTIONS order.
BUILDERS: tuple[tuple[str, SectionBuilder], ...] = (
    ("Overview", sections_style.overview),
    ("Colors", sections_style.colors),
    ("Typography", sections_style.typography),
    ("Layout", sections_layout.layout),
    ("Elevation & Depth", sections_layout.elevation),
    ("Shapes", sections_layout.shapes),
    ("Components", sections_guidance.components),
    ("Do's and Don'ts", sections_guidance.dos_and_donts),
    ("Agent Prompt Guide", sections_guidance.agent_prompt_guide),
)


def build_context(
    facts: DesignFacts,
    phrases: Phrases,
    components: dict[str, dict[str, str]],
    *,
    examples: bool = False,
) -> Context:
    return Context(
        facts=facts,
        phrases=phrases,
        traits=compute_traits(facts),
        components=components,
        examples=tuple(build_examples(facts)) if examples else (),
    )


def sections_from_context(context: Context) -> list[Section]:
    sections: list[Section] = []
    for title, builder in BUILDERS:
        body = builder(context)
        if body and body.strip():
            sections.append(Section(title=title, body=body.strip()))
    return sections


def build_sections(
    analysis: DesignAnalysis, phrases: Phrases, *, examples: bool = False
) -> list[Section]:
    """Sections in render.DESIGN_SECTIONS order; sections without data are omitted."""
    facts = DesignFacts(analysis)
    front = build_front_matter_from_facts(facts, phrases, examples=examples)
    components = front.get("components", {})
    return sections_from_context(build_context(facts, phrases, components, examples=examples))
