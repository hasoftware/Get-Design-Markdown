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
    generated_at: datetime
    issues: list[ValidationIssue] = Field(default_factory=list)
    design_md_path: str | None = None
    prompt_path: str | None = None
