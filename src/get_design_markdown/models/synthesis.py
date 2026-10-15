"""Stage 3 (Synthesizer) output: synthesis.json."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from get_design_markdown.models.base import GdmModel

SYNTHESIS_SCHEMA_VERSION = 1


class ValidationIssue(GdmModel):
    #: Rule id, aligned with the official linter where possible ("broken-ref", "contrast-ratio"...).
    rule: str
    severity: Literal["error", "warning", "info"]
    message: str


class SynthesisResult(GdmModel):
    schema_version: int = SYNTHESIS_SCHEMA_VERSION
    tool_version: str
    mode: Literal["rules", "prompt-only"]
    lang: str = "en"
    examples: bool = False
    generated_at: datetime
    issues: list[ValidationIssue] = Field(default_factory=list)
    #: "## " section titles in the order written.
    sections: list[str] = Field(default_factory=list)
    #: Front-matter group -> number of tokens ("colors": 10, "components": 14...).
    token_counts: dict[str, int] = Field(default_factory=dict)
    design_md_path: str | None = None
    prompt_path: str | None = None
    #: Resized screenshots referenced by prompt.md (relative to the site folder).
    image_paths: list[str] = Field(default_factory=list)
