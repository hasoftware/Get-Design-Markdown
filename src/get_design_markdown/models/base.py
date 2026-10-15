"""Shared base model and constrained field types."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

#: Lowercase #rrggbb or #rrggbbaa.
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-f]{6}([0-9a-f]{2})?$")]

#: Token / component name as used in DESIGN.md front matter, e.g. "on-primary", "button-primary".
TokenName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]


class GdmModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
