"""CSS generation for preview.html: theme variables, font stacks and rebuilt components.

Every declaration is assembled from values rebuilt by safe_css (never a raw string from the
analyzed site), so the generated stylesheet can be placed verbatim in a <style> element.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import quote

from get_design_markdown.analyzer import fonts
from get_design_markdown.analyzer.css_values import (
    format_px,
    is_monospace_family,
    parse_color,
    parse_font_weight,
    parse_px,
)
from get_design_markdown.analyzer.naming import FULL_RADIUS_PX, PILL_RADIUS_PX, parse_token_ref
from get_design_markdown.exporters.safe_css import (
    clean_font_name,
    css_backdrop_filter,
    css_color,
    css_font_stack,
    css_font_weight,
    css_length,
    css_line_height,
    css_opacity,
    css_shadow,
    css_sides,
    css_text_decoration,
    css_text_transform,
    ident,
    unique,
)
from get_design_markdown.models.analysis import (
    ColorPalette,
    ComponentSpec,
    FontCategory,
    FontFamilyInfo,
    ScaleStep,
    ShadowLevel,
    TypeLevel,
    Typography,
)

GOOGLE_FONTS_CSS = "https://fonts.googleapis.com/css2"
LIGHT_FALLBACK = ("#ffffff", "#111827")
DARK_FALLBACK = ("#0b0b0f", "#f3f4f6")
MONO_STACK = 'ui-monospace, "SFMono-Regular", "JetBrains Mono", Menlo, Consolas, monospace'
SANS_STACK = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'


def rule(selector: str, declarations: Iterable[tuple[str, str | None]]) -> str:
    """ "sel{prop:value;...}" with None values skipped ("" when nothing is left)."""
    body = ";".join(f"{name}:{value}" for name, value in declarations if value)
    return f"{selector}{{{body}}}" if body else ""


def var(name: str, fallback: str | None = None) -> str:
    return f"var({name}, {fallback})" if fallback else f"var({name})"


def radius_css(px: float) -> str:
    return format_px(FULL_RADIUS_PX if px >= PILL_RADIUS_PX else max(px, 0.0))


# ─── Fonts ───────────────────────────────────────────────────────────────────


def _google_substitute(family: FontFamilyInfo) -> str | None:
    """First substitute of a non-Google font that the font table knows is a Google Font."""
    if family.is_google_font or not family.substitutes:
        return None
    for candidate in family.substitutes:
        if fonts.lookup_font(candidate).is_google_font:
            return candidate
    return None


@dataclass
class FontResolver:
    """Font stacks of the analyzed families, their CSS variables and the Google Fonts link."""

    typography: Typography
    #: family name (lowercase) -> role token ("primary"...).
    roles: dict[str, str] = field(default_factory=dict)
    stacks: dict[str, str] = field(default_factory=dict)
    #: Non-Google family name -> Google substitute used to render it.
    rendered_with: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for family in self.typography.families:
            key = family.name.strip().lower()
            if not key or key in self.roles:
                continue
            role = base = ident(family.role.value, fallback="font")
            counter = 2
            while role in self.stacks:
                role = f"{base}-{counter}"
                counter += 1
            substitute = _google_substitute(family)
            names = [family.name, *family.stack]
            if substitute is not None:
                self.rendered_with[family.name] = substitute
                names.insert(1, substitute)
            self.roles.setdefault(key, role)
            self.stacks[role] = css_font_stack(unique(names), category=family.category)

    def variables(self) -> list[tuple[str, str]]:
        return [(f"--font-{role}", stack) for role, stack in self.stacks.items()]

    def stack(self, name: str | None) -> str:
        """var(--font-role) for a known family, else a cleaned stack with a generic fallback."""
        if not name or not name.strip():
            return var("--font-primary", SANS_STACK) if "primary" in self.stacks else SANS_STACK
        role = self.roles.get(name.strip().lower())
        if role is not None:
            return var(f"--font-{role}", self.stacks[role])
        category: FontCategory = "monospace" if is_monospace_family(name) else "unknown"
        return css_font_stack(name, category=category)

    def role_stack(self, role: str, fallback: str) -> str:
        return var(f"--font-{role}", self.stacks[role]) if role in self.stacks else fallback

    def google_link(self, extra_weights: Mapping[str, Iterable[int]]) -> str | None:
        """One css2 URL for every Google family (and Google substitute) with its weights.

        A single URL fails as a whole when Google lacks one requested weight; the page then
        falls back to the font stacks, exactly as it does offline."""
        specs: list[str] = []
        seen: set[str] = set()
        for family in self.typography.families:
            name = family.name if family.is_google_font else self.rendered_with.get(family.name)
            if name is None:
                continue
            cleaned = " ".join(
                "".join(char for char in name if char.isalnum() or char == " ").split()
            )
            if not cleaned or cleaned.lower() in seen:
                continue
            seen.add(cleaned.lower())
            weights = {*family.weights, *extra_weights.get(family.name.strip().lower(), ())}
            # css2 wants standard weights: 425 is requested as 400 (the browser picks the
            # nearest face anyway).
            valid = sorted({min(900, max(100, round(w / 100) * 100)) for w in weights if w > 0})
            spec = "family=" + quote(cleaned).replace("%20", "+")
            if valid:
                spec += ":wght@" + ";".join(str(weight) for weight in valid)
            specs.append(spec)
        if not specs:
            return None
        return f"{GOOGLE_FONTS_CSS}?{'&'.join(specs)}&display=swap"


# ─── Theme variables ─────────────────────────────────────────────────────────


def palette_fallbacks(palette: ColorPalette) -> tuple[str, str]:
    """(background, text) used by the preview chrome when canvas/ink tokens are missing."""
    default = DARK_FALLBACK if palette.scheme == "dark" else LIGHT_FALLBACK

    def pick(token: str, channel: str, fallback: str) -> str:
        for item in palette.tokens:
            if item.name == token and (color := css_color(item.value)) is not None:
                return color
        for cluster in palette.clusters:
            parsed = parse_color(cluster.hex)
            if (
                cluster.channel == channel
                and not cluster.decorative
                and parsed is not None
                and parsed.is_opaque
            ):
                return parsed.css_hex
        return fallback

    return pick("canvas", "background", default[0]), pick("ink", "text", default[1])


def theme_block(theme: str, palette: ColorPalette) -> str:
    """:root[data-theme="<theme>"] { color-scheme; chrome fallbacks; --color-<token>... }."""
    return palette_block(f':root[data-theme="{theme}"]', palette)


def palette_block(selector: str, palette: ColorPalette) -> str:
    background, text = palette_fallbacks(palette)
    declarations: list[tuple[str, str | None]] = [
        ("color-scheme", "dark" if palette.scheme == "dark" else "light"),
        ("--pv-fallback-bg", background),
        ("--pv-fallback-ink", text),
    ]
    seen: set[str] = set()
    for token in palette.tokens:
        name = ident(token.name)
        if name in seen:
            continue
        seen.add(name)
        declarations.append((f"--color-{name}", css_color(token.value)))
    return rule(selector, declarations)


def scale_variables(
    spacing: Sequence[ScaleStep], radii: Sequence[ScaleStep], shadows: Sequence[ShadowLevel]
) -> list[tuple[str, str | None]]:
    declarations: list[tuple[str, str | None]] = []
    declarations += [
        (f"--space-{ident(step.name)}", format_px(step.value_px))
        for step in spacing
        if step.value_px >= 0
    ]
    declarations += [(f"--radius-{ident(step.name)}", radius_css(step.value_px)) for step in radii]
    declarations += [(f"--shadow-{ident(level.name)}", css_shadow(level.css)) for level in shadows]
    return declarations


# ─── Components ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ComponentContext:
    fonts: FontResolver
    levels: Mapping[str, TypeLevel]
    color_names: frozenset[str]
    spacing_names: frozenset[str]
    radius_names: frozenset[str]
    #: Normalized box-shadow CSS -> shadow level name (for var(--shadow-<name>)).
    shadow_names: Mapping[str, str] = field(default_factory=dict)


def _color_decl(value: str | None, reference: str | None, context: ComponentContext) -> str | None:
    raw = css_color(value)
    parsed = parse_token_ref(reference or "")
    if parsed is not None and parsed[0] == "colors" and ident(parsed[1]) in context.color_names:
        return var(f"--color-{ident(parsed[1])}", raw)
    return raw


def _radius_decl(value: str | None, reference: str | None, context: ComponentContext) -> str | None:
    raw = css_length(value, allow_negative=False)
    if raw is not None and not raw.endswith("%"):
        px = parse_px(raw)
        raw = radius_css(px) if px is not None else raw
    parsed = parse_token_ref(reference or "")
    if parsed is not None and parsed[0] == "rounded" and ident(parsed[1]) in context.radius_names:
        return var(f"--radius-{ident(parsed[1])}", raw)
    return raw


def _padding_decl(
    value: str | None, reference: str | None, context: ComponentContext
) -> str | None:
    raw = css_sides(value)
    parsed = parse_token_ref(reference or "")
    if (
        raw is not None
        and " " not in raw
        and parsed is not None
        and parsed[0] == "spacing"
        and ident(parsed[1]) in context.spacing_names
    ):
        return var(f"--space-{ident(parsed[1])}", raw)
    return raw


def _shadow_decl(value: str | None, context: ComponentContext) -> str | None:
    if value is None:
        return None
    css = css_shadow(value)
    if css is None:
        return "none"
    name = context.shadow_names.get(css)
    return var(f"--shadow-{name}", css) if name is not None else css


def shadow_names(shadows: Sequence[ShadowLevel]) -> dict[str, str]:
    names: dict[str, str] = {}
    for level in shadows:
        css = css_shadow(level.css)
        if css is not None:
            names.setdefault(css, ident(level.name))
    return names


def style_declarations(
    properties: Mapping[str, str],
    refs: Mapping[str, str],
    context: ComponentContext,
    *,
    base: bool,
) -> list[tuple[str, str | None]]:
    """CSS declarations of a component property set (base styles or a state's changes)."""
    level: TypeLevel | None = None
    parsed = parse_token_ref(refs.get("typography", ""))
    if parsed is not None and parsed[0] == "typography":
        level = context.levels.get(parsed[1])

    def prop(key: str) -> str | None:
        return properties.get(key)

    declarations: list[tuple[str, str | None]] = [
        (
            "background-color",
            _color_decl(prop("backgroundColor"), refs.get("backgroundColor"), context),
        ),
        ("color", _color_decl(prop("textColor"), refs.get("textColor"), context)),
    ]
    border_color = _color_decl(prop("borderColor"), refs.get("borderColor"), context)
    border_width = css_sides(prop("borderWidth"))
    if border_color or border_width:
        declarations += [
            ("border-style", "solid"),
            ("border-width", border_width or ("1px" if base else None)),
            ("border-color", border_color or ("currentColor" if base else None)),
        ]
    declarations += [
        ("border-radius", _radius_decl(prop("rounded"), refs.get("rounded"), context)),
        ("padding", _padding_decl(prop("padding"), refs.get("padding"), context)),
        ("min-height", css_length(prop("height"), allow_negative=False)),
        ("gap", css_length(prop("gap"), allow_negative=False)),
    ]
    family = prop("fontFamily") or (level.font_family if level is not None and base else None)
    size = css_length(prop("fontSize"), allow_negative=False)
    weight = css_font_weight(prop("fontWeight"))
    line_height = css_line_height(prop("lineHeight"))
    letter_spacing = css_length(prop("letterSpacing"))
    if level is not None and base:
        size = size or format_px(level.size_px)
        weight = weight or str(level.weight)
        if line_height is None and level.line_height is not None:
            line_height = css_line_height(str(level.line_height))
        if letter_spacing is None and level.letter_spacing_px:
            letter_spacing = format_px(level.letter_spacing_px)
    shadow = prop("shadow")
    declarations += [
        ("font-family", context.fonts.stack(family) if family else None),
        ("font-size", size),
        ("font-weight", weight),
        ("line-height", line_height),
        ("letter-spacing", letter_spacing),
        ("text-transform", css_text_transform(prop("textTransform"))),
        ("text-decoration-line", css_text_decoration(prop("textDecoration"))),
        ("box-shadow", _shadow_decl(shadow, context)),
        ("opacity", css_opacity(prop("opacity"))),
    ]
    backdrop = css_backdrop_filter(prop("backdropFilter"))
    declarations += [("-webkit-backdrop-filter", backdrop), ("backdrop-filter", backdrop)]
    return declarations


def component_rules(spec: ComponentSpec, selector: str, context: ComponentContext) -> list[str]:
    """Base rule, ::placeholder rule and one rule per state (":hover" + ".is-hover" twin)."""
    rules = [
        rule(selector, style_declarations(spec.properties, spec.token_refs, context, base=True))
    ]
    placeholder = _color_decl(
        spec.properties.get("placeholderColor"), spec.token_refs.get("placeholderColor"), context
    )
    if placeholder:
        rules.append(rule(f"{selector}::placeholder", [("color", placeholder), ("opacity", "1")]))
    for state, changes in spec.states.items():
        name = ident(state)
        pseudo = {"hover": ":hover", "focus": ":focus-visible", "active": ":active"}.get(name)
        selectors = [f"{selector}.is-{name}"]
        if pseudo is not None:
            selectors.insert(0, f"{selector}{pseudo}")
        declarations = style_declarations(
            changes, spec.state_token_refs.get(state, {}), context, base=False
        )
        rules.append(rule(", ".join(selectors), declarations))
    return [text for text in rules if text]


def weights_by_family(
    typography: Typography, components: Sequence[ComponentSpec]
) -> dict[str, set[int]]:
    """Weights each family is rendered with (levels and component text), keyed lowercase."""
    result: dict[str, set[int]] = {}
    for level in typography.levels:
        result.setdefault(level.font_family.strip().lower(), set()).add(level.weight)
    for component in components:
        family = component.properties.get("fontFamily")
        weight = component.properties.get("fontWeight")
        if family and weight:
            result.setdefault(family.strip().lower(), set()).add(parse_font_weight(weight))
    return result


def font_label(name: str) -> str:
    return clean_font_name(name) or "—"
