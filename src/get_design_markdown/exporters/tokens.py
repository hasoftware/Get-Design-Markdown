"""tokens.json in the W3C Design Tokens Community Group format.

PUBLIC ENTRY POINT (frozen signature, called by exporters/export.py):
    build_tokens(analysis) -> dict[str, Any]

Layout of the document (Format Module 2025.10, Color Module 2025.10):

    $description, $extensions["dev.get-design-markdown"]   source URL, dates, tool version
    color          $type color       one token per semantic ColorToken
    font-family    $type fontFamily  primary / heading / display / mono -> font stack
    font-weight    $type fontWeight  thin ... black, for every weight in use
    typography     $type typography  one composite per type level (aliases into the two above)
    spacing        $type dimension   spacing scale
    rounded        $type dimension   radius scale (pills: full = 9999px)
    layout         $type dimension   container max width, gutter, section gap
    shadow         $type shadow      elevation levels, one object per box-shadow layer
    breakpoint     $type dimension   @media widths
    component      one group per ComponentSpec; values alias the groups above whenever the
                   analyzer linked them to a token, with a nested "hover" group for states
    dark           { color } when a dark-mode capture exists

Token references of the DESIGN.md front matter are translated: "{colors.x}" -> "{color.x}",
"{rounded.x}", "{spacing.x}", "{typography.x}" stay in groups of the same name. An alias is
only written when its target exists, so every alias resolves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from get_design_markdown.analyzer.css_values import (
    BLACK,
    NORMAL_LINE_HEIGHT,
    ShadowLayer,
    format_box_shadow,
    parse_box_shadow,
    parse_color,
    parse_font_family,
    parse_font_weight,
    parse_line_height,
    parse_px,
)
from get_design_markdown.analyzer.naming import parse_token_ref
from get_design_markdown.exporters.dtcg import (
    EXTENSION_KEY,
    FORMAT_VERSION,
    alias,
    color_value,
    dimension,
    number,
    token_name,
    weight_token_name,
)
from get_design_markdown.exporters.safe_css import length_px, opacity_value, sides_px
from get_design_markdown.models.analysis import (
    Breakpoint,
    ColorToken,
    ComponentSpec,
    DesignAnalysis,
    FontFamilyInfo,
    ScaleStep,
    ShadowLevel,
    TypeLevel,
)

Token = dict[str, Any]
Group = dict[str, Any]

#: DESIGN.md front-matter group -> tokens.json group.
REF_GROUPS: dict[str, str] = {
    "colors": "color",
    "typography": "typography",
    "rounded": "rounded",
    "spacing": "spacing",
}
#: ComponentSpec color properties -> component token name.
COMPONENT_COLORS: dict[str, str] = {
    "backgroundColor": "background-color",
    "textColor": "text-color",
    "borderColor": "border-color",
    "placeholderColor": "placeholder-color",
}
#: ComponentSpec single-length properties -> component token name.
COMPONENT_LENGTHS: dict[str, str] = {
    "rounded": "border-radius",
    "height": "height",
    "width": "width",
    "gap": "gap",
}
#: Properties without a DTCG type, kept as metadata in $extensions.
COMPONENT_EXTRAS: dict[str, str] = {
    "textTransform": "textTransform",
    "textDecoration": "textDecoration",
    "position": "position",
    "backdropFilter": "backdropFilter",
}
_SIDES = ("top", "right", "bottom", "left")


def _ext(**fields: Any) -> dict[str, Any]:
    """$extensions object with the tool key; None / empty fields are dropped."""
    data = {key: value for key, value in fields.items() if value not in (None, [], {}, "")}
    return {EXTENSION_KEY: data}


def _token(
    value: Any, *, kind: str | None = None, description: str = "", **extensions: Any
) -> Token:
    token: Token = {}
    if kind is not None:
        token["$type"] = kind
    token["$value"] = value
    if description:
        token["$description"] = description
    extension = _ext(**extensions)
    if extension[EXTENSION_KEY]:
        token["$extensions"] = extension
    return token


def _unique_name(name: str, used: set[str]) -> str:
    candidate = name
    counter = 2
    while candidate in used:
        candidate = f"{name}-{counter}"
        counter += 1
    used.add(candidate)
    return candidate


class _Builder:
    """Accumulates groups and remembers every token path so aliases are never dangling."""

    def __init__(self, analysis: DesignAnalysis) -> None:
        self.analysis = analysis
        self.document: dict[str, Any] = {}
        self.paths: set[tuple[str, ...]] = set()
        self.family_tokens: dict[str, str] = {}
        self.weight_tokens: dict[int, str] = {}
        self.shadow_tokens: dict[str, str] = {}

    # Helpers -----------------------------------------------------------------

    def add_group(self, name: str, group: Group, prefix: tuple[str, ...] = ()) -> None:
        tokens = [key for key in group if not key.startswith("$")]
        if not tokens:
            return
        self.document[name] = group
        self._register(group, (*prefix, name))

    def _register(self, group: Mapping[str, Any], path: tuple[str, ...]) -> None:
        for key, child in group.items():
            if key.startswith("$") or not isinstance(child, Mapping):
                continue
            if "$value" in child:
                self.paths.add((*path, key))
            else:
                self._register(child, (*path, key))

    def ref(self, reference: str | None) -> str | None:
        """DESIGN.md reference -> resolvable tokens.json alias, or None."""
        if not reference:
            return None
        parsed = parse_token_ref(reference)
        if parsed is None:
            return None
        group = REF_GROUPS.get(parsed[0])
        if group is None or (group, parsed[1]) not in self.paths:
            return None
        return alias(group, parsed[1])

    def target_px(self, reference: str | None) -> float | None:
        """Value of a referenced spacing/rounded step (to check a ref matches a side)."""
        parsed = parse_token_ref(reference or "")
        if parsed is None:
            return None
        steps: Sequence[ScaleStep]
        if parsed[0] == "spacing":
            steps = self.analysis.spacing.steps
        elif parsed[0] == "rounded":
            steps = self.analysis.radii
        else:
            return None
        return next((step.value_px for step in steps if step.name == parsed[1]), None)

    # Root ----------------------------------------------------------------------

    def root(self) -> None:
        site = self.analysis.site
        self.document["$description"] = (
            f"Design tokens extracted from {site.url} — reference only, "
            "not an official design system"
        )
        self.document["$extensions"] = _ext(
            source=site.url,
            siteName=site.name,
            collectedAt=site.collected_at.isoformat() if site.collected_at else None,
            analyzedAt=self.analysis.analyzed_at.isoformat(),
            toolVersion=self.analysis.tool_version,
            pagesAnalyzed=list(site.pages_analyzed),
            colorScheme=self.analysis.colors.scheme,
            format=f"W3C Design Tokens Format Module {FORMAT_VERSION}",
        )

    # Colors ------------------------------------------------------------------

    @staticmethod
    def color_group(tokens: Sequence[ColorToken], description: str) -> Group:
        group: Group = {"$type": "color", "$description": description}
        used: set[str] = set()
        for token in tokens:
            color = parse_color(token.value)
            if color is None:
                continue
            name = token_name(token.name, fallback="color")
            if name in used:
                continue
            used.add(name)
            text = token.role.value.replace("_", " ")
            if token.evidence:
                text = f"{text} — {token.evidence}"
            group[name] = _token(
                color_value(color),
                description=text,
                role=token.role.value,
                cssVariable=token.css_variable,
            )
        return group

    def colors(self) -> None:
        palette = self.analysis.colors
        self.add_group(
            "color",
            self.color_group(palette.tokens, f"Semantic palette ({palette.scheme} scheme)"),
        )

    # Typography --------------------------------------------------------------

    def font_families(self) -> None:
        group: Group = {"$type": "fontFamily", "$description": "Font stacks by role"}
        used: set[str] = set()
        for family in self.analysis.typography.families:
            stack = [name for name in (item.strip() for item in family.stack) if name]
            if family.name.strip() and family.name.strip() not in stack:
                stack.insert(0, family.name.strip())
            if not stack:
                continue
            name = _unique_name(token_name(family.role.value, fallback="font"), used)
            self.family_tokens.setdefault(family.name, name)
            group[name] = _token(
                stack,
                description=_family_description(family),
                category=family.category,
                isGoogleFont=family.is_google_font,
                substitutes=list(family.substitutes),
                share=family.share,
            )
        self.add_group("font-family", group)

    def font_weights(self) -> None:
        typography = self.analysis.typography
        weights = set(typography.weights_used)
        weights.update(level.weight for level in typography.levels)
        for family in typography.families:
            weights.update(family.weights)
        group: Group = {"$type": "fontWeight", "$description": "Font weights in use"}
        for weight in sorted(weight for weight in weights if 1 <= weight <= 1000):
            name = weight_token_name(weight)
            self.weight_tokens[weight] = name
            group[name] = _token(weight)
        self.add_group("font-weight", group)

    def family_value(self, family: str) -> Any:
        token = self.family_tokens.get(family)
        if token is not None and ("font-family", token) in self.paths:
            return alias("font-family", token)
        stack = parse_font_family(family) or [family.strip() or "sans-serif"]
        return stack

    def weight_value(self, weight: int) -> Any:
        name = self.weight_tokens.get(weight)
        if name is not None and ("font-weight", name) in self.paths:
            return alias("font-weight", name)
        return max(1, min(1000, weight))

    def type_levels(self) -> None:
        group: Group = {"$type": "typography", "$description": "Type scale, largest first"}
        used: set[str] = set()
        for level in self.analysis.typography.levels:
            if level.size_px <= 0:
                continue
            name = _unique_name(token_name(level.name, fallback="text"), used)
            line_height, normal = _line_height(level)
            group[name] = _token(
                {
                    "fontFamily": self.family_value(level.font_family),
                    "fontSize": dimension(level.size_px),
                    "fontWeight": self.weight_value(level.weight),
                    "letterSpacing": dimension(level.letter_spacing_px),
                    "lineHeight": line_height,
                },
                description=_level_description(level),
                tags=list(level.tags),
                textTransform=None if level.text_transform == "none" else level.text_transform,
                lineHeightPx=None if level.line_height_px is None else number(level.line_height_px),
                lineHeightNormal=True if normal else None,
                count=level.count,
            )
        self.add_group("typography", group)

    # Scales ------------------------------------------------------------------

    @staticmethod
    def scale_group(steps: Sequence[ScaleStep], description: str) -> Group:
        group: Group = {"$type": "dimension", "$description": description}
        used: set[str] = set()
        for step in steps:
            if step.value_px < 0:
                continue
            name = _unique_name(token_name(step.name, fallback="step"), used)
            group[name] = _token(
                dimension(step.value_px),
                description=_used_on(step.used_on),
                count=step.count,
            )
        return group

    def scales(self) -> None:
        spacing = self.analysis.spacing
        base = f" (base unit {spacing.base_unit_px}px)" if spacing.base_unit_px else ""
        self.add_group("spacing", self.scale_group(spacing.steps, f"Spacing scale{base}"))
        self.add_group(
            "rounded",
            self.scale_group(self.analysis.radii, "Corner radius scale (full = pill)"),
        )
        layout = self.analysis.layout
        group: Group = {"$type": "dimension", "$description": "Page layout measurements"}
        container = spacing.container_max_width_px or layout.container_max_width_px
        for name, value, text in (
            ("container-max-width", container, "Max width of the main content container"),
            ("gutter", layout.gutter_px, "Horizontal padding of the container at desktop width"),
            ("section-gap", spacing.section_gap_px, "Typical vertical gap between sections"),
        ):
            if value is not None and value >= 0:
                group[name] = _token(dimension(value), description=text)
        self.add_group("layout", group)

    def shadows(self) -> None:
        group: Group = {"$type": "shadow", "$description": "Elevation levels, subtlest first"}
        used: set[str] = set()
        for level in self.analysis.shadows:
            layers = parse_box_shadow(level.css)
            if not layers:
                continue
            name = _unique_name(token_name(level.name, fallback="shadow"), used)
            self.shadow_tokens.setdefault(format_box_shadow(layers), name)
            group[name] = _token(
                [_shadow_layer(layer) for layer in layers],
                description=_shadow_description(level),
                css=format_box_shadow(layers),
                count=level.count,
            )
        self.add_group("shadow", group)

    def breakpoints(self) -> None:
        group: Group = {"$type": "dimension", "$description": "Breakpoints from @media rules"}
        used: set[str] = set()
        for breakpoint in self.analysis.breakpoints:
            value = (
                breakpoint.min_width_px
                if breakpoint.min_width_px is not None
                else breakpoint.max_width_px
            )
            if value is None or value < 0:
                continue
            name = _unique_name(token_name(breakpoint.name, fallback="bp"), used)
            group[name] = _token(
                dimension(value),
                description=_breakpoint_description(breakpoint),
                minWidth=None
                if breakpoint.min_width_px is None
                else number(breakpoint.min_width_px),
                maxWidth=None
                if breakpoint.max_width_px is None
                else number(breakpoint.max_width_px),
                occurrences=breakpoint.occurrences,
            )
        self.add_group("breakpoint", group)

    # Components ----------------------------------------------------------------

    def component_tokens(
        self, properties: Mapping[str, str], refs: Mapping[str, str], group: Group
    ) -> dict[str, str]:
        """Fill `group` with the DTCG tokens of one property set; returns the leftovers that
        have no DTCG type (kept as $extensions metadata)."""
        for key, name in COMPONENT_COLORS.items():
            if key not in properties and key not in refs:
                continue
            reference = self.ref(refs.get(key))
            if reference is not None:
                group[name] = _token(reference, kind="color")
                continue
            color = parse_color(properties.get(key))
            if color is not None:
                group[name] = _token(color_value(color), kind="color")
        for key, name in COMPONENT_LENGTHS.items():
            reference = self.ref(refs.get(key))
            if reference is not None:
                group[name] = _token(reference, kind="dimension")
                continue
            px = length_px(properties.get(key))
            if px is not None and px >= 0:
                group[name] = _token(dimension(px), kind="dimension")
        opacity = opacity_value(properties.get("opacity"))
        if opacity is not None:
            group["opacity"] = _token(number(opacity, digits=3), kind="number")
        self.border_width(properties.get("borderWidth"), group)
        self.padding(properties.get("padding"), refs.get("padding"), group)
        self.component_typography(properties, refs.get("typography"), group)
        shadow = properties.get("shadow")
        if shadow:
            layers = parse_box_shadow(shadow)
            if layers:
                level = self.shadow_tokens.get(format_box_shadow(layers))
                value: Any = (
                    alias("shadow", level)
                    if level is not None and ("shadow", level) in self.paths
                    else [_shadow_layer(layer) for layer in layers]
                )
                group["shadow"] = _token(value, kind="shadow")
        return {
            extra: properties[key] for key, extra in COMPONENT_EXTRAS.items() if properties.get(key)
        }

    @staticmethod
    def border_width(value: str | None, group: Group) -> None:
        sides = sides_px(value)
        if sides is None:
            return
        if len(set(sides)) == 1:
            group["border-width"] = _token(dimension(max(sides[0], 0.0)), kind="dimension")
            return
        for side, px in zip(_SIDES, sides, strict=True):
            if px > 0:
                group[f"border-{side}-width"] = _token(dimension(px), kind="dimension")

    def padding(self, value: str | None, reference: str | None, group: Group) -> None:
        sides = sides_px(value)
        if sides is None:
            return
        top, right, bottom, left = sides
        target = self.target_px(reference)
        reference_alias = self.ref(reference)

        def side_value(px: float) -> Token:
            if reference_alias is not None and target is not None and abs(target - px) <= 0.5:
                return _token(reference_alias, kind="dimension")
            return _token(dimension(max(px, 0.0)), kind="dimension")

        if top == bottom and left == right:
            group["padding-block"] = side_value(top)
            group["padding-inline"] = side_value(left)
            return
        for side, px in zip(_SIDES, sides, strict=True):
            group[f"padding-{side}"] = side_value(px)

    def component_typography(
        self, properties: Mapping[str, str], reference: str | None, group: Group
    ) -> None:
        resolved = self.ref(reference)
        if resolved is not None:
            group["typography"] = _token(resolved, kind="typography")
            return
        size = length_px(properties.get("fontSize"))
        family = properties.get("fontFamily")
        if size is None or size <= 0 or not family:
            return
        weight = parse_font_weight(properties.get("fontWeight"))
        line_px = parse_line_height(properties.get("lineHeight"), size)
        group["typography"] = _token(
            {
                "fontFamily": self.family_value(family),
                "fontSize": dimension(size),
                "fontWeight": self.weight_value(weight),
                "letterSpacing": dimension(
                    parse_px(properties.get("letterSpacing"), font_size_px=size) or 0.0
                ),
                "lineHeight": number(
                    line_px / size if line_px is not None else NORMAL_LINE_HEIGHT, digits=3
                ),
            },
            kind="typography",
        )

    def components(self) -> None:
        group: Group = {
            "$description": "Components rebuilt from measured styles; values alias the "
            "palette and scales whenever they match a token"
        }
        used: set[str] = set()
        for component in self.analysis.components:
            name = _unique_name(token_name(component.name, fallback="component"), used)
            group[name] = self.component_group(component)
        self.add_group("component", group)

    def component_group(self, component: ComponentSpec) -> Group:
        tokens: Group = {}
        extras = self.component_tokens(component.properties, component.token_refs, tokens)
        group: Group = {
            "$description": _component_description(component),
            "$extensions": _ext(
                kind=component.kind.value,
                variant=component.variant,
                count=component.count,
                contrastRatio=component.contrast_ratio,
                css=extras,
            ),
            **tokens,
        }
        used = set(tokens)
        for state, changes in component.states.items():
            state_tokens: Group = {}
            state_extras = self.component_tokens(
                changes, component.state_token_refs.get(state, {}), state_tokens
            )
            if not state_tokens and not state_extras:
                continue
            state_group: Group = {"$description": f"{component.name} on {state}"}
            if state_extras:
                state_group["$extensions"] = _ext(css=state_extras)
            state_group.update(state_tokens)
            group[_unique_name(token_name(state, fallback="state"), used)] = state_group
        return group

    # Dark theme ----------------------------------------------------------------

    def dark(self) -> None:
        dark = self.analysis.dark
        if dark is None:
            return
        colors = self.color_group(
            dark.colors.tokens,
            f"Palette of the prefers-color-scheme: dark capture ({dark.colors.scheme} scheme)",
        )
        if not any(not key.startswith("$") for key in colors):
            return
        self.add_group(
            "dark",
            {"$description": "Dark color scheme (prefers-color-scheme: dark)", "color": colors},
        )

    def build(self) -> dict[str, Any]:
        self.root()
        self.colors()
        self.font_families()
        self.font_weights()
        self.type_levels()
        self.scales()
        self.shadows()
        self.breakpoints()
        self.components()
        self.dark()
        return self.document


# ─── Descriptions & values ───────────────────────────────────────────────────


def _shadow_layer(layer: ShadowLayer) -> dict[str, Any]:
    return {
        "color": color_value(layer.color if layer.color is not None else BLACK),
        "offsetX": dimension(layer.offset_x),
        "offsetY": dimension(layer.offset_y),
        "blur": dimension(max(layer.blur, 0.0)),
        "spread": dimension(layer.spread),
        "inset": layer.inset,
    }


def _line_height(level: TypeLevel) -> tuple[int | float, bool]:
    """Unitless line height of a level and whether it is the `normal` approximation."""
    if level.line_height is not None and level.line_height > 0:
        return number(level.line_height, digits=3), False
    if level.line_height_px is not None and level.size_px > 0:
        return number(level.line_height_px / level.size_px, digits=3), False
    return number(NORMAL_LINE_HEIGHT, digits=3), True


def _used_on(used_on: Sequence[str]) -> str:
    return f"Used on {', '.join(used_on)}" if used_on else ""


def _family_description(family: FontFamilyInfo) -> str:
    weights = ", ".join(str(weight) for weight in family.weights)
    text = f"{family.name} ({family.category})"
    if weights:
        text += f" — weights {weights}"
    if family.substitutes:
        text += f"; free substitutes: {', '.join(family.substitutes)}"
    return text


def _level_description(level: TypeLevel) -> str:
    text = f"{level.font_family} {number(level.size_px)}px / {level.weight}"
    if level.tags:
        text += f" — {', '.join(level.tags)}"
    return text


def _shadow_description(level: ShadowLevel) -> str:
    layers = "layer" if level.layers == 1 else "layers"
    text = f"{level.layers} {layers}"
    used = _used_on(level.used_on)
    return f"{text}; {used[0].lower()}{used[1:]}" if used else text


def _breakpoint_description(breakpoint: Breakpoint) -> str:
    parts = []
    if breakpoint.min_width_px is not None:
        parts.append(f"min-width: {number(breakpoint.min_width_px)}px")
    if breakpoint.max_width_px is not None:
        parts.append(f"max-width: {number(breakpoint.max_width_px)}px")
    text = " and ".join(parts)
    if breakpoint.occurrences:
        text += f" ({breakpoint.occurrences} rules)"
    return text


def _component_description(component: ComponentSpec) -> str:
    kind = component.kind.value
    if component.variant:
        kind += f" ({component.variant})"
    count = f", {component.count} on the page" if component.count else ""
    return f"{kind}{count}"


def build_tokens(analysis: DesignAnalysis) -> dict[str, Any]:
    """DTCG document: groups color / typography / dimension (spacing, rounded) / shadow /
    breakpoint / component aliases, every token with "$type" and "$value", aliases written
    "{color.primary}"; dark theme under a "dark" group when present (pure, JSON-serializable)."""
    return _Builder(analysis).build()
