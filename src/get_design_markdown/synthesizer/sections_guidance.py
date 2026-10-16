"""Components, Do's and Don'ts and Agent Prompt Guide sections."""

from __future__ import annotations

import re

from get_design_markdown.analyzer.css_values import (
    AA_NORMAL,
    GENERIC_FONT_FAMILIES,
    is_generic_family,
    parse_color,
    parse_sides_px,
)
from get_design_markdown.analyzer.naming import (
    COLORS,
    ROUNDED,
    SPACING,
    TYPOGRAPHY,
    state_variant_name,
    token_ref,
)
from get_design_markdown.models.analysis import (
    ComponentKind,
    ComponentSpec,
    TypeLevel,
)
from get_design_markdown.synthesizer.colornames import is_chromatic
from get_design_markdown.synthesizer.context import Context
from get_design_markdown.synthesizer.examples import ExampleSurface
from get_design_markdown.synthesizer.facts import STATUS_ROLES, PairCheck, is_transparent_value
from get_design_markdown.synthesizer.formatting import (
    bullets,
    fmt_num,
    fmt_px,
    fmt_ratio,
    md_code,
    md_quote,
    md_text,
    table,
    unique,
)
from get_design_markdown.synthesizer.sections_layout import radius_usage, width_breakpoints

#: Measured properties shown for a component, in display order ("border" combines two keys).
DISPLAY_KEYS = (
    "backgroundColor",
    "textColor",
    "border",
    "typography",
    "rounded",
    "padding",
    "height",
    "width",
    "gap",
    "shadow",
    "backdropFilter",
    "position",
    "placeholderColor",
    "textTransform",
    "letterSpacing",
    "textDecoration",
)
LABELLED_SAMPLE_KINDS = frozenset({ComponentKind.BUTTON, ComponentKind.LINK, ComponentKind.BADGE})
#: Front-matter keys left out of the nav call to action: button-primary is measured on all
#: its instances (linear.app: the 44px hero "Get started"), not on the compact header button.
NAV_CTA_SIZE_KEYS = frozenset({"typography", "padding", "size", "height", "width"})
#: A secondary text tier holding at least this share of the text is a Quick Reference neutral.
QUICK_TEXT_TIER_SHARE = 0.10
MAX_SAMPLES = 3
#: Named families kept in a CSS font hint before the generic fallback.
MAX_STACK_FAMILIES = 4
#: Samples are cut at 50 characters; a shorter text matches another only when equal.
MIN_TRUNCATED_SAMPLE = 24
_SIDES = ("top", "right", "bottom", "left")

# ─── Components ──────────────────────────────────────────────────────────────


def _label(ctx: Context, key: str) -> str:
    phrase = f"components.prop.{key}"
    return ctx.p(phrase) if ctx.phrases.has(phrase) else key


def _value(ctx: Context, spec: ComponentSpec, key: str, raw: str | None, ref: str | None) -> str:
    if ref is not None:
        described = ctx.facts.ref_from_string(ref)
        if described is not None:
            return described
    if raw is None:
        return "—"
    if key in ("backgroundColor", "textColor", "placeholderColor", "borderColor"):
        if is_transparent_value(raw):
            return ctx.p("common.transparent")
        if key == "backgroundColor":
            return ctx.flattened(raw)
        return raw
    if key == "shadow":
        level = next((s for s in ctx.facts.analysis.shadows if s.css == raw), None)
        if level is not None:
            return ctx.p("components.shadow_level", name=md_text(level.name), css=md_code(raw))
        return md_code(raw)
    if key == "backdropFilter":
        return md_code(raw)
    return md_text(raw)


def _border_text(ctx: Context, spec: ComponentSpec) -> str | None:
    width = spec.properties.get("borderWidth")
    color = spec.properties.get("borderColor")
    if width is None and color is None:
        return None
    sides = parse_sides_px(width.split()) if width else (1.0, 1.0, 1.0, 1.0)
    if not any(sides):
        return None
    if len(set(sides)) == 1:
        width_text = fmt_px(sides[0])
    else:
        width_text = ", ".join(
            ctx.p(f"components.side.{name}", width=fmt_px(value))
            for name, value in zip(_SIDES, sides, strict=True)
            if value
        )
    color_text = _value(ctx, spec, "borderColor", color, spec.token_refs.get("borderColor"))
    return f"{width_text} · {color_text}" if color else width_text


def _typography_text(ctx: Context, spec: ComponentSpec) -> str | None:
    ref = ctx.facts.typography_ref(spec)
    if ref is not None and ctx.facts.has_ref(ref):
        return ctx.facts.ref_from_string(ref)
    props = spec.properties
    if "fontFamily" not in props and "fontSize" not in props:
        return None
    parts = [
        " ".join(md_text(props[key]) for key in ("fontFamily", "fontSize") if key in props),
        md_text(props.get("fontWeight", "")),
        md_text(props.get("lineHeight", "")),
    ]
    return " / ".join(part for part in parts if part)


def _property_lines(ctx: Context, spec: ComponentSpec) -> list[str]:
    lines: list[str] = []
    props = spec.properties
    for key in DISPLAY_KEYS:
        if key == "border":
            text = _border_text(ctx, spec)
        elif key == "typography":
            text = _typography_text(ctx, spec)
        elif key in props:
            text = _value(ctx, spec, key, props[key], spec.token_refs.get(key))
            if key == "placeholderColor":
                text += _placeholder_note(ctx, spec)
        else:
            text = None
        if text:
            lines.append(f"{_label(ctx, key)}: {text}")
    return lines


def _placeholder_note(ctx: Context, spec: ComponentSpec) -> str:
    placeholder = spec.properties.get("placeholderColor")
    background = ctx.facts.component_color(spec, "backgroundColor") or ctx.facts.canvas.css_hex
    if placeholder is None or is_transparent_value(placeholder):
        return ""
    ratio = ctx.facts.contrast_on(placeholder, background)
    if ratio is None or ratio >= AA_NORMAL:
        return ""
    return ctx.p("components.placeholder_low", ratio=fmt_ratio(ratio))


def _state_lines(ctx: Context, spec: ComponentSpec) -> list[str]:
    lines: list[str] = []
    for state in ctx.facts.states(spec):
        changed = spec.states.get(state, {})
        refs = spec.state_token_refs.get(state, {})
        keys = list(changed) + [key for key in refs if key not in changed]
        if not keys:
            continue
        changes = [
            ctx.p(
                "components.change",
                label=_label(ctx, key).lower(),
                value=_value(ctx, spec, key, changed.get(key), refs.get(key)),
            )
            for key in keys
        ]
        state_key = f"components.state_name.{state}"
        state_label = ctx.p(state_key) if ctx.phrases.has(state_key) else md_text(state)
        line = ctx.p("components.state", state=state_label, changes="; ".join(changes))
        foreground = ctx.facts.resolve(refs.get("textColor")) or changed.get("textColor")
        background = ctx.facts.resolve(refs.get("backgroundColor")) or changed.get(
            "backgroundColor"
        )
        foreground = foreground or ctx.facts.component_color(spec, "textColor")
        background = background or ctx.facts.component_color(spec, "backgroundColor")
        if (
            foreground
            and background
            and ("textColor" in keys or "backgroundColor" in keys)
            and not is_transparent_value(background)
        ):
            ratio = ctx.facts.contrast_on(foreground, background)
            if ratio is not None and ratio < AA_NORMAL:
                line += " " + ctx.p("components.state_contrast_low", ratio=fmt_ratio(ratio))
        lines.append(line)
    return lines


def _purpose(ctx: Context, spec: ComponentSpec) -> str:
    kind = spec.kind.value
    keys: tuple[str, ...] = (f"components.purpose.{kind}.default", "components.purpose.default")
    if spec.variant:
        keys = (f"components.purpose.{kind}.{spec.variant}", *keys)
    return ctx.phrases.first(keys, kind=kind)


def _seen(ctx: Context, spec: ComponentSpec) -> str | None:
    if spec.count <= 0:
        return None
    samples = [s for s in spec.samples if s.strip()][:MAX_SAMPLES]
    if samples and spec.kind in LABELLED_SAMPLE_KINDS:
        return ctx.p(
            "components.seen_samples",
            count=spec.count,
            samples=ctx.phrases.join([md_quote(sample) for sample in samples]),
        )
    return ctx.p("components.seen", count=spec.count)


def _verdict(ctx: Context, check: PairCheck) -> str:
    if check.passes:
        return ctx.p("components.verdict.pass")
    return ctx.p("components.verdict.large" if check.passes_large else "components.verdict.fail")


def check_text(ctx: Context, check: PairCheck) -> str:
    """ "`{colors.primary}` — #635bff on `{colors.canvas}` — #f6f9fc: 4.45:1 (…)": both
    sides written with the values the ratio was computed from."""
    return ctx.p(
        "components.contrast_item",
        fg=ctx.facts.color_display(check.foreground, check.foreground_token),
        bg=ctx.flattened(
            check.background, ctx.facts.color_display(check.background, check.background_token)
        ),
        ratio=fmt_ratio(check.ratio),
        verdict=_verdict(ctx, check),
    )


def _contrast_line(ctx: Context, spec: ComponentSpec) -> str | None:
    """Text contrast recomputed from the colors printed for the component; for a component
    without a fill, on each surface its text color was measured on (worst first)."""
    checks = ctx.facts.component_contrasts(spec)
    if not checks:
        ratio = spec.contrast_ratio
        if ratio is None:
            return None
        key = "components.contrast_measured_" + ("fail" if ratio < AA_NORMAL else "pass")
        return ctx.p(key, ratio=fmt_ratio(ratio))
    pairs = "; ".join(check_text(ctx, check) for check in checks)
    if all(check.passes for check in checks):
        return ctx.p("components.contrast_pass", pairs=pairs)
    return ctx.p("components.contrast_fail", pairs=pairs)


def _component_block(ctx: Context, spec: ComponentSpec) -> str:
    intro = [_purpose(ctx, spec)]
    seen = _seen(ctx, spec)
    if seen:
        intro.append(seen)
    lines = _property_lines(ctx, spec) + _state_lines(ctx, spec)
    contrast = _contrast_line(ctx, spec)
    if contrast:
        lines.append(contrast)
    lines.extend(
        ctx.p(
            "components.alpha_note",
            entry=pair.entry,
            fill=pair.fill,
            ratio=fmt_ratio(pair.ratio),
            blind=fmt_ratio(pair.alpha_blind_ratio),
        )
        for pair in ctx.facts.alpha_blind_pairs()
        if pair.component == spec.name
    )
    names = [spec.name] if spec.name in ctx.components else []
    names += [
        state_variant_name(spec.name, state)
        for state in ctx.facts.states(spec)
        if state_variant_name(spec.name, state) in ctx.components
    ]
    if names:
        lines.append(
            ctx.p("components.front_matter", names=ctx.phrases.join([f"`{n}`" for n in names]))
        )
    else:
        lines.append(ctx.p("components.not_in_front_matter"))
    block = [f"### {spec.name}", " ".join(intro)]
    if lines:
        block.append(bullets(lines))
    return "\n\n".join(block)


def _example_line(ctx: Context, example: ExampleSurface) -> str:
    details = [
        ctx.p(
            "components.prop_value",
            label=_label(ctx, key).lower(),
            value=ctx.ref_or_value(value),
        )
        for key, value in example.props.items()
    ]
    return ctx.p(
        "components.example",
        name=f"`{example.name}`",
        purpose=ctx.p(f"components.example_purpose.{example.name.removeprefix('ex-')}"),
        details="; ".join(details),
        ratio=fmt_ratio(example.ratio),
    )


def components(ctx: Context) -> str | None:
    specs = ctx.facts.components
    if not specs and not ctx.examples:
        return None
    parts: list[str] = []
    if specs:
        parts.append(ctx.p("components.intro", count=len(specs)))
        parts.extend(_component_block(ctx, spec) for spec in specs)
    if ctx.examples:
        parts.append(
            "\n\n".join(
                [
                    f"### {ctx.p('heading.examples')}",
                    ctx.p("components.examples_intro"),
                    bullets(_example_line(ctx, example) for example in ctx.examples),
                ]
            )
        )
    return "\n\n".join(parts)


# ─── Do's and Don'ts ─────────────────────────────────────────────────────────


def _component_brief(ctx: Context, name: str, exclude: frozenset[str] = frozenset()) -> str | None:
    """ "background `{colors.primary}` — #4f46e5, text …" from the front-matter entry
    (without the `exclude` keys)."""
    props = {
        key: value for key, value in ctx.components.get(name, {}).items() if key not in exclude
    }
    if not props:
        return None
    return ", ".join(
        ctx.p(
            "components.prop_value", label=_label(ctx, key).lower(), value=ctx.ref_or_value(value)
        )
        for key, value in props.items()
    )


def name_list(ctx: Context, names: list[str], limit: int = 4) -> str:
    """ "a, b, c and d" or "a, b, c, d and 3 more" (localized)."""
    if len(names) <= limit:
        return ctx.phrases.join(names)
    rest = ctx.p("list.more", count=len(names) - limit)
    return ctx.phrases.join([*names[:limit], rest])


def _dos(ctx: Context) -> list[str]:
    facts = ctx.facts
    dos: list[str] = []
    usage = facts.color_usage()
    if "primary" in facts.colors:
        users = usage.get("primary", [])
        primary_button = facts.component_by_name("button-primary")
        line: str | None = None
        if primary_button is not None and primary_button.token_refs.get(
            "backgroundColor"
        ) == token_ref(COLORS, "primary"):
            primary = parse_color(facts.colors["primary"].value)
            neutral = primary is not None and not is_chromatic(primary)
            line = ctx.p(
                "dos.primary_action_neutral" if neutral else "dos.primary_action",
                ref=ctx.color_ref("primary"),
                components=name_list(ctx, users),
            )
        elif users:
            line = ctx.p(
                "dos.primary_color",
                ref=ctx.color_ref("primary"),
                components=name_list(ctx, users),
            )
        failures = facts.text_failures("primary")
        if line is not None and failures:
            line += " " + ctx.p("caveat.primary_text", pair=check_text(ctx, failures[0]))
        if line is not None:
            dos.append(line)
    pair = facts.best_text_pair()
    if pair is not None and pair.foreground_token and pair.background_token:
        dos.append(
            ctx.p(
                "dos.text_pair",
                fg=ctx.color_ref(pair.foreground_token),
                bg=ctx.color_ref(pair.background_token),
                ratio=fmt_ratio(pair.ratio),
            )
        )
    heading = facts.headline_level()
    body = facts.body_level()
    if heading is not None and body is not None and heading.name != body.name:
        dos.append(
            ctx.p(
                "dos.type_pair", heading=ctx.level_ref(heading.name), body=ctx.level_ref(body.name)
            )
        )
    elif body is not None:
        dos.append(ctx.p("dos.body_type", body=ctx.level_ref(body.name)))
    base = facts.analysis.spacing.base_unit_px
    if base and facts.spacing:
        common = sorted(facts.spacing.items(), key=lambda item: (-item[1].weight, -item[1].count))[
            :4
        ]
        refs = [facts.ref(SPACING, name) for name, _ in sorted(common, key=lambda i: i[1].value_px)]
        dos.append(ctx.p("dos.spacing", base=base, steps=ctx.phrases.join(refs)))
    radii = radius_usage(ctx)
    if radii:
        items = [
            ctx.p(
                "dos.radius_item",
                ref=facts.ref(ROUNDED, name),
                components=name_list(ctx, names),
            )
            for name, names in list(radii.items())[:3]
        ]
        dos.append(ctx.p("dos.radius", items="; ".join(items)))
    hovers = [
        (spec.name, ctx.components[state_variant_name(spec.name)])
        for spec in facts.components_of_kind(ComponentKind.BUTTON)
        if state_variant_name(spec.name) in ctx.components
    ]
    if hovers:
        examples = [
            ctx.p(
                "dos.hover_item",
                component=name,
                changes=", ".join(
                    ctx.p(
                        "components.change",
                        label=_label(ctx, key).lower(),
                        value=ctx.ref_or_value(value),
                    )
                    for key, value in props.items()
                ),
            )
            for name, props in hovers[:2]
        ]
        dos.append(ctx.p("dos.hover", examples="; ".join(examples)))
    shadows = facts.analysis.shadows
    if shadows:
        levels = [
            ctx.p(
                "dos.shadow_item",
                name=md_text(level.name),
                used_on=ctx.phrases.join([md_text(u) for u in level.used_on[:2]]) or "—",
            )
            for level in shadows
        ]
        dos.append(ctx.p("dos.shadows", levels="; ".join(levels)))
    hamburger = ctx.hamburger("dos.hamburger")
    if hamburger:
        dos.append(hamburger)
    return dos


def _remedy(ctx: Context, issue_background: str | None, token: str | None, status: bool) -> str:
    if status:
        return ctx.p("donts.remedy.status")
    if issue_background is not None:
        alternative = ctx.facts.text_alternative(issue_background, exclude=token)
        if alternative is not None:
            name, ratio = alternative
            return ctx.p(
                "donts.remedy.alternative", ref=ctx.color_ref(name), ratio=fmt_ratio(ratio)
            )
    return ctx.p("donts.remedy.darken")


def _donts(ctx: Context) -> list[str]:
    facts = ctx.facts
    donts: list[str] = []
    one_off: list[str] = []
    for issue in facts.contrast_issues():
        values = ctx.issue_text(issue)
        if issue.dark:
            remedy = ctx.p("donts.remedy.large") if issue.passes_large else ""
            donts.append(ctx.p("donts.contrast_dark", remedy=remedy, **values).rstrip())
            continue
        if issue.foreground_token not in facts.colors:
            # Not a palette color: the agent would not pick it, so one grouped Don't is enough.
            one_off.append(ctx.p("donts.contrast_item", **values))
            continue
        status = issue.foreground_role in STATUS_ROLES
        remedy = _remedy(ctx, issue.background, issue.foreground_token, status)
        if issue.passes_large and not status:
            remedy = ctx.p("donts.remedy.large") + " " + remedy
        donts.append(ctx.p("donts.contrast", remedy=remedy, **values))
    if one_off:
        donts.append(ctx.p("donts.contrast_group", items="; ".join(one_off)))
    if facts.colors or facts.levels or facts.radii:
        donts.append(ctx.p("donts.invent"))
    weights = facts.weights()
    if ctx.traits.weight == "heavy_only":
        donts.append(
            ctx.p("donts.regular_weight", weights=ctx.phrases.join([str(w) for w in weights]))
        )
    elif weights:
        donts.append(ctx.p("donts.weights", weights=ctx.phrases.join([str(w) for w in weights])))
    if facts.analysis.shadows:
        strongest = facts.analysis.shadows[-1]
        donts.append(ctx.p("donts.shadows_stronger", name=md_text(strongest.name)))
    elif facts.colors and ctx.traits.depth == "none":
        donts.append(ctx.p("donts.shadows_none"))
    if ctx.traits.accent == "single" and not ctx.traits.minor_actions:
        donts.append(ctx.p("donts.second_accent"))
    decorative = [cluster.hex for cluster in facts.decorative_clusters()]
    if decorative:
        donts.append(ctx.p("donts.decorative", colors=ctx.phrases.join(decorative[:3])))
    return donts


def dos_and_donts(ctx: Context) -> str | None:
    items = _dos(ctx) + _donts(ctx)
    return bullets(items) if items else None


# ─── Agent Prompt Guide ──────────────────────────────────────────────────────


def _quick_reference(ctx: Context) -> list[str]:
    facts = ctx.facts
    items: list[str] = []
    if "primary" in facts.colors and "on-primary" in facts.colors:
        items.append(
            ctx.p(
                "guide.quick.action",
                primary=ctx.color_ref("primary"),
                on_primary=ctx.color_ref("on-primary"),
            )
        )
    elif "primary" in facts.colors:
        items.append(ctx.p("guide.quick.primary", primary=ctx.color_ref("primary")))
    neutrals = [
        ctx.color_ref(name)
        for name in ("canvas", "surface", "ink", "muted", "faint", "hairline")
        if name in facts.colors
        and (name != "faint" or (facts.token_share(name) or 0.0) >= QUICK_TEXT_TIER_SHARE)
    ]
    if neutrals:
        items.append(ctx.p("guide.quick.neutrals", items=ctx.phrases.join(neutrals)))
    heading = facts.headline_level()
    body = facts.body_level()
    if heading is not None and body is not None and heading.name != body.name:
        items.append(
            ctx.p(
                "guide.quick.type",
                heading=ctx.level_ref(heading.name),
                body=ctx.level_ref(body.name),
            )
        )
    elif body is not None or heading is not None:
        level = body or heading
        assert level is not None
        items.append(ctx.p("guide.quick.type_single", level=ctx.level_ref(level.name)))
    radii = radius_usage(ctx)
    if radii:
        items.append(
            ctx.p(
                "guide.quick.radius",
                items="; ".join(
                    ctx.p(
                        "dos.radius_item",
                        ref=facts.ref(ROUNDED, name),
                        components=name_list(ctx, names),
                    )
                    for name, names in list(radii.items())[:3]
                ),
            )
        )
    base = facts.analysis.spacing.base_unit_px
    if base:
        items.append(ctx.p("guide.quick.spacing", base=base))
    container = (
        facts.analysis.layout.container_max_width_px
        or facts.analysis.spacing.container_max_width_px
    )
    if container:
        items.append(ctx.p("guide.quick.container", container=fmt_num(container)))
    breakpoints = [
        fmt_num(bp.min_width_px) for bp in width_breakpoints(ctx) if bp.min_width_px is not None
    ]
    if breakpoints:
        items.append(ctx.p("guide.quick.breakpoints", items=", ".join(unique(breakpoints))))
    # Desktop-first boundaries (max-width only) are listed on their own line.
    max_breakpoints = [
        fmt_num(bp.max_width_px)
        for bp in width_breakpoints(ctx)
        if bp.min_width_px is None and bp.max_width_px is not None
    ]
    if max_breakpoints:
        items.append(ctx.p("guide.quick.breakpoints_max", items=", ".join(unique(max_breakpoints))))
    return items


def _hero_prompt(ctx: Context) -> str | None:
    facts = ctx.facts
    clauses: list[str] = []
    if "canvas" in facts.colors:
        clauses.append(ctx.p("guide.prompt.clause.canvas", ref=ctx.color_ref("canvas")))
    heading = facts.headline_level()
    ink = ctx.ink()
    if heading is not None:
        key = "guide.prompt.clause.headline_color" if ink else "guide.prompt.clause.headline"
        clauses.append(
            ctx.p(
                key, ref=ctx.level_ref(heading.name), color=ctx.color_ref(ink.name) if ink else ""
            )
        )
    lead = facts.lead_level()
    if lead is not None and (heading is None or lead.name != heading.name):
        color = facts.lead_color(lead)
        key = "guide.prompt.clause.lead_color" if color else "guide.prompt.clause.lead"
        clauses.append(
            ctx.p(key, ref=ctx.level_ref(lead.name), color=ctx.color_ref(color) if color else "")
        )
    brief = _component_brief(ctx, "button-primary")
    if brief:
        clauses.append(ctx.p("guide.prompt.clause.cta", component="button-primary", details=brief))
    elif "primary" in facts.colors:
        clauses.append(ctx.p("guide.prompt.clause.cta_color", ref=ctx.color_ref("primary")))
    if len(clauses) < 2:
        return None
    return ctx.p("guide.prompt.hero", details="; ".join(clauses))


def _sample_key(text: str) -> str:
    return " ".join(text.split()).casefold()


def _same_sample(first: str, second: str) -> bool:
    """Equal sample texts; a prefix counts only when long enough to be a truncated sample."""
    if not first or not second:
        return False
    shorter, longer = sorted((first, second), key=len)
    return shorter == longer or (
        len(shorter) >= MIN_TRUNCATED_SAMPLE and longer.startswith(shorter)
    )


def _card_title_level(ctx: Context, card: ComponentSpec) -> TypeLevel | None:
    """The type level whose measured samples include a title of the card (component samples
    are the card's first text), so the prompt never guesses a title size by level name."""
    titles = [_sample_key(sample) for sample in card.samples if sample.strip()]
    for level in ctx.facts.levels.values():
        for sample in level.samples:
            key = _sample_key(sample)
            if any(_same_sample(key, title) for title in titles):
                return level
    return None


def _cards_prompt(ctx: Context) -> str | None:
    """A feature grid built only from a measured card component (None without one)."""
    facts = ctx.facts
    cards = facts.components_of_kind(ComponentKind.CARD)
    card = next((spec for spec in cards if spec.name in ctx.components), None)
    if card is None:
        return None
    clauses = [
        ctx.p(
            "guide.prompt.clause.card",
            component=card.name,
            details=_component_brief(ctx, card.name) or "",
        )
    ]
    title = _card_title_level(ctx, card)
    if title is not None:
        clauses.append(ctx.p("guide.prompt.clause.card_title", ref=ctx.level_ref(title.name)))
    body = facts.body_level()
    if body is not None and (title is None or body.name != title.name):
        clauses.append(ctx.p("guide.prompt.clause.card_body", ref=ctx.level_ref(body.name)))
    if len(clauses) < 2:
        return None
    columns = max((s.main_columns for s in facts.analysis.layout.snapshots), default=3)
    return ctx.p("guide.prompt.cards", columns=max(columns, 2), details="; ".join(clauses))


#: Samples the components analyzer writes for an email input.
EMAIL_FIELD_SAMPLE = "input[type=email]"


def _form_prompt(ctx: Context) -> str | None:
    """A form built from the measured field (text-input, else text-area) + a button; None
    without a measured field. The field is called an email field only when an email input was
    measured; a text-area becomes a message field (its height follows its rows)."""
    name = next((name for name in ("text-input", "text-area") if name in ctx.components), None)
    field = _component_brief(ctx, name) if name else None
    button_name = next(
        (name for name in ("button-primary", "button-secondary") if name in ctx.components), None
    )
    if name is None or not field or button_name is None:
        return None
    spec = next((spec for spec in ctx.facts.components if spec.name == name), None)
    samples = set(spec.samples) if spec is not None else set()
    if name == "text-area":
        field_key, form_key = "guide.prompt.clause.message", "guide.prompt.form_message"
    elif EMAIL_FIELD_SAMPLE in samples:
        field_key, form_key = "guide.prompt.clause.input", "guide.prompt.form"
    else:
        field_key, form_key = "guide.prompt.clause.input_field", "guide.prompt.form_field"
    clauses = [
        ctx.p(field_key, component=name, details=field),
        ctx.p(
            "guide.prompt.clause.submit",
            component=button_name,
            details=_component_brief(ctx, button_name) or "",
        ),
    ]
    return ctx.p(form_key, details="; ".join(clauses))


def _nav_prompt(ctx: Context) -> str | None:
    """The top navigation built from top-nav / nav-link / button-primary and the hamburger.
    The call to action keeps button-primary's colors and radius but not its size, which was
    measured on every instance, not on the header one."""
    clauses: list[str] = []
    for name, key, exclude in (
        ("top-nav", "guide.prompt.clause.nav", frozenset[str]()),
        ("nav-link", "guide.prompt.clause.nav_links", frozenset[str]()),
        ("button-primary", "guide.prompt.clause.nav_cta", NAV_CTA_SIZE_KEYS),
    ):
        brief = _component_brief(ctx, name, exclude)
        if brief:
            clauses.append(ctx.p(key, component=name, details=brief))
    hamburger = ctx.hamburger("guide.prompt.clause.hamburger")
    if hamburger:
        clauses.append(hamburger)
    if len(clauses) < 2:
        return None
    return ctx.p("guide.prompt.nav", details="; ".join(clauses))


def _css_family(name: str) -> str:
    """A font-family entry: generic keywords bare, other names quoted unless one plain word."""
    if is_generic_family(name) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
        return name
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def font_stack(ctx: Context, family_name: str) -> list[str]:
    """The font-family list a CSS hint should use: the measured stack (the display name, e.g.
    "Söhne", is often not a loadable family — the site loads "sohne-var"), led by the display
    name only for Google Fonts (loaded under that name), cut to MAX_STACK_FAMILIES named
    families and ending with a generic family."""
    family = ctx.facts.families.get(family_name)
    generic = "sans-serif"
    if family is not None and family.category in ("serif", "monospace"):
        generic = family.category
    names = list(family.stack) if family is not None and family.stack else [family_name]
    if family is not None and family.is_google_font:
        names.insert(0, family_name)
    named = unique(name for name in names if name.lower() not in GENERIC_FONT_FAMILIES)
    generics = [name for name in names if name.lower() in GENERIC_FONT_FAMILIES]
    return [*named[:MAX_STACK_FAMILIES], generics[-1] if generics else generic]


def _font_css(ctx: Context, name: str) -> str:
    level = ctx.facts.levels[name]
    ratio = ctx.facts.line_height_ratio(level)
    size = fmt_px(level.size_px) + (f"/{fmt_num(ratio)}" if ratio is not None else "")
    stack = ", ".join(_css_family(family) for family in font_stack(ctx, level.font_family))
    return f"{level.weight} {size} {stack}"


def _mapping_table(ctx: Context) -> str | None:
    facts = ctx.facts
    rows: list[list[str]] = []
    for name in facts.colors:
        rows.append(
            [f"`{token_ref(COLORS, name)}`", f"`--color-{name}`", facts.value(COLORS, name)]
        )
    for name in facts.levels:
        rows.append(
            [f"`{token_ref(TYPOGRAPHY, name)}`", f"`--font-{name}`", md_code(_font_css(ctx, name))]
        )
    for name in facts.radii:
        rows.append(
            [f"`{token_ref(ROUNDED, name)}`", f"`--radius-{name}`", facts.value(ROUNDED, name)]
        )
    for name in facts.spacing:
        rows.append(
            [f"`{token_ref(SPACING, name)}`", f"`--space-{name}`", facts.value(SPACING, name)]
        )
    if not rows:
        return None
    return table(
        [
            ctx.p("guide.map.col.token"),
            ctx.p("guide.map.col.property"),
            ctx.p("guide.map.col.value"),
        ],
        rows,
    )


def agent_prompt_guide(ctx: Context) -> str | None:
    facts = ctx.facts
    if not (facts.colors or facts.levels or facts.radii or facts.spacing):
        return None
    parts = [ctx.p("guide.intro")]
    quick = _quick_reference(ctx)
    if quick:
        parts.append(f"### {ctx.p('heading.quick_reference')}\n\n{bullets(quick)}")
    prompts = [
        prompt
        for prompt in (_hero_prompt(ctx), _cards_prompt(ctx), _form_prompt(ctx) or _nav_prompt(ctx))
        if prompt is not None
    ]
    if prompts:
        numbered = "\n".join(f"{index}. {prompt}" for index, prompt in enumerate(prompts, 1))
        parts.append(f"### {ctx.p('heading.example_prompts')}\n\n{numbered}")
    mapping = _mapping_table(ctx)
    if mapping:
        parts.append(
            "\n\n".join(
                [f"### {ctx.p('heading.css_properties')}", ctx.p("guide.map.intro"), mapping]
            )
        )
    return "\n\n".join(parts)
