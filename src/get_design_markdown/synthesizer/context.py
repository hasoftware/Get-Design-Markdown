"""Shared state passed to every section builder of the narrative."""

from __future__ import annotations

from dataclasses import dataclass

from get_design_markdown.analyzer.naming import COLORS, TYPOGRAPHY
from get_design_markdown.models.analysis import ColorRole, ColorToken, ResponsiveChangeKind
from get_design_markdown.synthesizer.catalog import Phrases
from get_design_markdown.synthesizer.colornames import color_name
from get_design_markdown.synthesizer.examples import ExampleSurface
from get_design_markdown.synthesizer.facts import ContrastIssue, DesignFacts
from get_design_markdown.synthesizer.formatting import fmt_num, fmt_ratio, md_text
from get_design_markdown.synthesizer.traits import Traits


@dataclass(frozen=True)
class Context:
    facts: DesignFacts
    phrases: Phrases
    traits: Traits
    #: Front-matter components (including hover variants and enabled ex-* surfaces).
    components: dict[str, dict[str, str]]
    examples: tuple[ExampleSurface, ...] = ()

    def p(self, key: str, **values: object) -> str:
        return self.phrases.get(key, **values)

    def color_ref(self, name: str) -> str:
        return self.facts.ref(COLORS, name)

    def hamburger(self, key: str) -> str | None:
        """The measured nav collapse in the phrase family `key` (None without one).

        `key` takes {below} for a min-width breakpoint ("below 940px"); `key`_max takes {max}
        for a max-width one ("at 768px and below"); `key`_between takes {from_width} and
        {to_width} when no breakpoint matches both captures, so no threshold is invented.
        """
        for change in self.facts.analysis.layout.changes:
            if change.kind != ResponsiveChangeKind.NAV_COLLAPSES_TO_HAMBURGER:
                continue
            params = change.params
            if params.get("max_px") is not None:
                return self.p(f"{key}_max", max=fmt_num(params["max_px"]))
            if params.get("below_px") is not None:
                return self.p(key, below=fmt_num(params["below_px"]))
            return self.p(f"{key}_between", from_width=change.from_width, to_width=change.to_width)
        return None

    def color_name(self, value: str) -> str:
        return color_name(value, self.phrases)

    def ink(self) -> ColorToken | None:
        """Main text token ("ink", else the first text-role token that is not on-*)."""
        if "ink" in self.facts.colors:
            return self.facts.colors["ink"]
        for name, token in self.facts.colors.items():
            if token.role == ColorRole.TEXT and not name.startswith("on-"):
                return token
        return None

    def canvas_display(self) -> str:
        if "canvas" in self.facts.colors:
            return self.color_ref("canvas")
        return self.facts.canvas.css_hex

    def level_ref(self, name: str) -> str:
        return self.facts.ref(TYPOGRAPHY, name)

    def ref_or_value(self, value: str) -> str:
        """A front-matter value written for prose: references get their value appended."""
        described = self.facts.ref_from_string(value)
        if described is not None:
            return described
        if value == "transparent":
            return self.p("common.transparent")
        return value

    def flattened(self, value: str, display: str | None = None) -> str:
        """A translucent fill written with the opaque color it shows over the canvas; other
        values unchanged."""
        shown = value if display is None else display
        opaque = self.facts.flat_hex(value)
        return shown if opaque is None else self.p("common.flattened", value=shown, opaque=opaque)

    def issue_side(self, value: str | None, token: str | None, dark: bool) -> str:
        """One side of a failing contrast pair, with its token when it is a light token."""
        if value is None:
            return self.p("common.surface_behind")
        if dark:
            return f"`{token}` {value}" if token else value
        return self.facts.color_display(value, token)

    def issue_text(self, issue: ContrastIssue) -> dict[str, str]:
        """Values shared by every phrase that reports a failing pair."""
        return {
            "fg": self.issue_side(issue.foreground, issue.foreground_token, issue.dark),
            "bg": self.issue_side(issue.background, issue.background_token, issue.dark),
            "ratio": fmt_ratio(issue.ratio),
            "context": md_text(issue.context),
        }
