"""Overview, Colors and Typography sections (rule-based prose)."""

from __future__ import annotations

from get_design_markdown.analyzer.css_values import parse_color
from get_design_markdown.analyzer.naming import COLORS, TYPOGRAPHY, parse_token_ref, token_ref
from get_design_markdown.models.analysis import (
    ColorRole,
    ColorToken,
    ContrastPair,
    FontFamilyInfo,
)
from get_design_markdown.synthesizer.colornames import is_chromatic
from get_design_markdown.synthesizer.context import Context
from get_design_markdown.synthesizer.facts import (
    STATUS_ROLES,
    SURFACE_ROLES,
    TEXT_ROLES,
    ActionColor,
)
from get_design_markdown.synthesizer.formatting import (
    bullets,
    fmt_num,
    fmt_percent,
    fmt_px,
    fmt_ratio,
    md_code,
    md_text,
    table,
)
from get_design_markdown.synthesizer.traits import (
    action_words,
    canvas_words,
    shape_words,
    type_words,
)

MAX_KEY_CHARACTERISTICS = 8
MAX_CONTRAST_ROWS = 16
MAX_DECORATIVE = 6
BORDER_SUBTLE_RATIO = 1.2

# ─── Overview ────────────────────────────────────────────────────────────────


def _theme_paragraph(ctx: Context) -> str:
    traits = ctx.traits
    site = md_text(ctx.facts.analysis.site.name or ctx.facts.analysis.site.url)
    canvas = canvas_words(traits, ctx.phrases)
    if canvas is None or traits.theme is None:
        return ctx.p("overview.no_colors", site=site)
    sentences = [
        ctx.p(
            "overview.theme",
            site=site,
            theme=ctx.p(f"trait.theme.{traits.theme}"),
            canvas=ctx.phrases.indefinite(canvas),
            canvas_ref=ctx.canvas_display(),
        )
    ]
    ink = ctx.ink()
    if ink is not None:
        sentences.append(
            ctx.p("overview.ink", name=ctx.color_name(ink.value), ref=ctx.color_ref(ink.name))
        )
    return " ".join(sentences)


def _accent_sentence(ctx: Context) -> str | None:
    traits = ctx.traits
    facts = ctx.facts
    if traits.accent is None:
        if "primary" in facts.colors:
            primary = facts.colors["primary"]
            return ctx.p(
                "overview.accent.primary_only",
                name=ctx.color_name(primary.value),
                ref=ctx.color_ref("primary"),
            )
        return None
    described = [_action_text(ctx, action) for action in traits.actions]
    sentence = ctx.p(
        f"overview.accent.{traits.accent}",
        colors=ctx.phrases.join(described),
        count=len(described),
    )
    if traits.minor_actions:
        minor = [_action_text(ctx, action) for action in traits.minor_actions]
        sentence += " " + ctx.p("overview.accent.minor", colors=ctx.phrases.join(minor))
    return sentence


def _action_value(ctx: Context, action: ActionColor) -> str:
    value = ctx.facts.color_display(action.hex, action.token)
    if action.variants:
        value += " / " + " / ".join(action.variants)
    return value


def _action_text(ctx: Context, action: ActionColor) -> str:
    """ "indigo `{colors.primary}` — #533afd on button-primary", followed by the variant
    shades of the same hue that carry their own token."""
    text = ctx.p(
        "overview.accent.color",
        name=ctx.color_name(action.hex),
        value=_action_value(ctx, action),
        components=ctx.phrases.join(list(action.components)),
    )
    if not action.shades:
        return text
    shades = [
        ctx.p(
            "overview.accent.shade_color",
            value=_action_value(ctx, shade),
            components=ctx.phrases.join(list(shade.components)),
        )
        for shade in action.shades
    ]
    key = "overview.accent.shades.one" if len(shades) == 1 else "overview.accent.shades.many"
    return ctx.p(key, lead=text, shades=ctx.phrases.join(shades))


def _type_sentence(ctx: Context) -> str | None:
    traits = ctx.traits
    words = type_words(traits, ctx.phrases)
    if words is None:
        return None
    sentences = [ctx.p("overview.type", type=words)]
    weights = ctx.facts.weights()
    if traits.weight == "heavy_only":
        sentences.append(ctx.p("overview.weights.heavy_only", weights=_weights(ctx, weights)))
    elif traits.weight == "no_bold":
        sentences.append(ctx.p("overview.weights.no_bold", weights=_weights(ctx, weights)))
    elif traits.weight == "range":
        sentences.append(ctx.p("overview.weights.range", low=min(weights), high=max(weights)))
    return " ".join(sentences)


def _weights(ctx: Context, weights: list[int]) -> str:
    return ctx.phrases.join([str(weight) for weight in weights])


def _shape_depth_sentence(ctx: Context) -> str | None:
    sentences: list[str] = []
    shape = shape_words(ctx.traits, ctx.facts, ctx.phrases)
    if shape is not None:
        sentences.append(ctx.p("overview.shape", shape=shape))
    if ctx.traits.depth is not None and (ctx.facts.colors or ctx.facts.analysis.shadows):
        sentences.append(
            ctx.p(f"overview.depth.{ctx.traits.depth}", count=len(ctx.facts.analysis.shadows))
        )
    return " ".join(sentences) or None


def _key_characteristics(ctx: Context) -> list[str]:
    facts = ctx.facts
    traits = ctx.traits
    items: list[str] = []
    canvas = canvas_words(traits, ctx.phrases)
    ink = ctx.ink()
    if canvas is not None:
        if ink is not None:
            items.append(
                ctx.p(
                    "kc.canvas_ink",
                    canvas=canvas,
                    canvas_ref=ctx.canvas_display(),
                    ink_ref=ctx.color_ref(ink.name),
                )
            )
        else:
            items.append(ctx.p("kc.canvas", canvas=canvas, canvas_ref=ctx.canvas_display()))
    actions = action_words(traits, facts, ctx.phrases)
    if actions is not None:
        items.append(ctx.p("kc.actions", actions=actions))
    type_text = type_words(traits, ctx.phrases)
    if type_text is not None:
        mono = traits.mono_family
        item = (
            ctx.p("kc.type_mono", type=type_text, mono=mono.name)
            if mono is not None
            else ctx.p("kc.type", type=type_text)
        )
        weights = facts.weights()
        if traits.weight is not None and weights:
            item += "; " + ctx.p(f"kc.weights.{traits.weight}", weights=_weights(ctx, weights))
        items.append(item)
    shape = shape_words(traits, facts, ctx.phrases)
    if shape is not None:
        items.append(ctx.p("kc.shape", shape=shape))
    if traits.depth is not None and (facts.colors or facts.analysis.shadows):
        items.append(ctx.p(f"kc.depth.{traits.depth}", count=len(facts.analysis.shadows)))
    base = facts.analysis.spacing.base_unit_px
    container = (
        facts.analysis.layout.container_max_width_px
        or facts.analysis.spacing.container_max_width_px
    )
    if base and container:
        items.append(ctx.p("kc.grid_container", base=base, container=fmt_num(container)))
    elif base:
        items.append(ctx.p("kc.grid", base=base))
    elif container:
        items.append(ctx.p("kc.container", container=fmt_num(container)))
    hamburger = ctx.hamburger("kc.hamburger")
    if hamburger is not None:
        items.append(hamburger)
    if facts.analysis.dark is not None and facts.has_dark_theme():
        dark_canvas = next(
            (t.value for t in facts.analysis.dark.colors.tokens if t.name == "canvas"), None
        )
        items.append(
            ctx.p("kc.dark_canvas", canvas=dark_canvas) if dark_canvas else ctx.p("kc.dark")
        )
    typography = facts.analysis.typography
    if typography.scale_ratio is not None:
        items.append(ctx.p("kc.scale", ratio=fmt_num(typography.scale_ratio)))
    return items[:MAX_KEY_CHARACTERISTICS]


def overview(ctx: Context) -> str:
    parts = [_theme_paragraph(ctx)]
    style = [
        sentence
        for sentence in (_accent_sentence(ctx), _type_sentence(ctx), _shape_depth_sentence(ctx))
        if sentence
    ]
    if style:
        parts.append(" ".join(style))
    missing = ctx.facts.missing_groups()
    if missing:
        parts.append(ctx.p("overview.coverage", groups=ctx.phrases.join(missing)))
    characteristics = _key_characteristics(ctx)
    if characteristics:
        parts.append(f"### {ctx.p('heading.key_characteristics')}\n\n{bullets(characteristics)}")
    return "\n\n".join(parts)


# ─── Colors ──────────────────────────────────────────────────────────────────

_COLOR_GROUPS = ("brand", "surfaces", "text", "borders", "status")
_ON_BRAND = ("on-primary", "on-secondary", "on-tertiary")


def color_group(name: str, token: ColorToken) -> str:
    if name in _ON_BRAND or token.role in (ColorRole.BRAND, ColorRole.ACCENT):
        return "brand"
    if token.role in SURFACE_ROLES:
        return "surfaces"
    if token.role in TEXT_ROLES:
        return "text"
    if token.role == ColorRole.BORDER:
        return "borders"
    if token.role in STATUS_ROLES:
        return "status"
    return "brand"


def _color_entry(ctx: Context, name: str, token: ColorToken, usage: list[str]) -> str:
    details: list[str] = []
    color = parse_color(token.value)
    descriptive = ctx.color_name(token.value)
    if name.startswith("on-") and name[3:]:
        role = ctx.p("colors.on_role", base=name[3:])
    else:
        role = ctx.p(f"colors.role.{token.role.value}")
    details.append(f"{descriptive} — {role}" if descriptive else role)
    if color is not None and not color.is_opaque:
        details.append(ctx.p("colors.translucent", alpha=fmt_percent(color.alpha)))
    if token.role == ColorRole.BORDER and color is not None:
        ratio = ctx.facts.contrast_on(token.value, ctx.facts.canvas.css_hex)
        if ratio is not None:
            key = "colors.border_subtle" if ratio < BORDER_SUBTLE_RATIO else "colors.border_ratio"
            details.append(ctx.p(key, ratio=fmt_ratio(ratio)))
    if token.evidence.strip():
        details.append(ctx.p("colors.evidence", evidence=md_text(token.evidence)))
    if token.css_variable and token.css_variable not in token.evidence:
        details.append(ctx.p("colors.css_variable", variable=md_code(token.css_variable)))
    if usage:
        details.append(ctx.p("colors.used_by", components=ctx.phrases.join(usage)))
    return f"**{name}** {ctx.color_ref(name)}: " + "; ".join(details) + "."


def _contrast_cell(ctx: Context, pair: ContrastPair) -> str:
    if pair.passes_aa:
        return ctx.p("colors.contrast.aaa" if pair.passes_aaa else "colors.contrast.aa")
    if pair.passes_aa_large:
        return ctx.p("colors.contrast.large_only")
    return ctx.p("colors.contrast.fail")


def _contrast_table(ctx: Context) -> str | None:
    facts = ctx.facts
    pairs = list(facts.analysis.colors.contrast_pairs)
    failing = [pair for pair in pairs if not pair.passes_aa]
    passing = sorted((pair for pair in pairs if pair.passes_aa), key=lambda pair: -pair.occurrences)
    keep = {id(pair) for pair in failing}
    keep.update(id(pair) for pair in passing[: max(MAX_CONTRAST_ROWS - len(failing), 0)])
    rows = [
        [
            facts.color_display(pair.foreground, pair.foreground_token),
            facts.color_display(pair.background, pair.background_token),
            fmt_ratio(pair.ratio),
            _contrast_cell(ctx, pair),
            md_text(pair.context) or "—",
        ]
        for pair in pairs
        if id(pair) in keep
    ]
    component_issues = [
        issue
        for issue in facts.contrast_issues()
        if not issue.dark and issue.context not in {pair.context for pair in pairs}
    ]
    for issue in component_issues:
        values = ctx.issue_text(issue)
        rows.append(
            [
                values["fg"],
                values["bg"],
                values["ratio"],
                ctx.p(
                    "colors.contrast.large_only" if issue.passes_large else "colors.contrast.fail"
                ),
                values["context"],
            ]
        )
    if not rows:
        return None
    headers = [
        ctx.p("colors.contrast.col.foreground"),
        ctx.p("colors.contrast.col.background"),
        ctx.p("colors.contrast.col.ratio"),
        ctx.p("colors.contrast.col.wcag"),
        ctx.p("colors.contrast.col.context"),
    ]
    count = len(failing) + len(component_issues)
    summary = (
        ctx.p("colors.contrast.failing", count=count)
        if count
        else ctx.p("colors.contrast.all_pass")
    )
    return "\n\n".join(
        [
            f"### {ctx.p('heading.contrast')}",
            ctx.p("colors.contrast.intro"),
            table(headers, rows),
            summary,
        ]
    )


def _decorative(ctx: Context) -> str | None:
    clusters = ctx.facts.decorative_clusters()
    if not clusters:
        return None
    lines = []
    for cluster in clusters[:MAX_DECORATIVE]:
        details = [ctx.color_name(cluster.hex)]
        if cluster.alpha < 1:
            details.append(ctx.p("colors.translucent", alpha=fmt_percent(cluster.alpha)))
        details.append(ctx.p(f"colors.channel.{cluster.channel}"))
        if cluster.samples:
            details.append(
                ctx.p(
                    "colors.decorative.seen_on",
                    samples=ctx.phrases.join([md_code(s) for s in cluster.samples[:3]]),
                )
            )
        lines.append(f"{cluster.hex} — " + "; ".join(d for d in details if d) + ".")
    return "\n\n".join(
        [f"### {ctx.p('heading.decorative')}", ctx.p("colors.decorative.intro"), bullets(lines)]
    )


def _unreferenced(ctx: Context) -> str | None:
    """Palette tokens no front-matter component references, with the reason, so the
    orphaned-token warnings of DESIGN.md linters are documented: a token a component uses
    only as a border or placeholder color (no spec key) vs. one measured outside components."""
    if not ctx.components:
        return None
    used: set[str] = set()
    for props in ctx.components.values():
        for value in props.values():
            parsed = parse_token_ref(value)
            if parsed is not None and parsed[0] == COLORS:
                used.add(parsed[1])
    orphans = [name for name in ctx.facts.colors if name not in used]
    if not orphans:
        return None
    usage = ctx.facts.color_usage()
    indirect = [name for name in orphans if usage.get(name)]
    outside = [name for name in orphans if not usage.get(name)]
    items = [
        ctx.p(key, tokens=ctx.phrases.join([f"`{name}`" for name in names]))
        for key, names in (
            ("colors.unreferenced.indirect", indirect),
            ("colors.unreferenced.outside", outside),
        )
        if names
    ]
    return "\n\n".join(
        [
            f"### {ctx.p('heading.colors.unreferenced')}",
            ctx.p("colors.unreferenced.intro"),
            bullets(items),
        ]
    )


def _dark_theme(ctx: Context) -> str | None:
    dark = ctx.facts.analysis.dark
    if dark is None or not dark.colors.tokens:
        return None
    scheme = ctx.p(f"trait.theme.{ctx.facts.analysis.colors.scheme}")
    if not ctx.facts.has_dark_theme():
        heading = f"### {ctx.p('heading.dark_theme')}"
        return "\n\n".join([heading, ctx.p("colors.dark.same", scheme=scheme)])
    seen: set[str] = set()
    rows = []
    for token in dark.colors.tokens:
        if token.name in seen:
            continue
        seen.add(token.name)
        light = ctx.color_ref(token.name) if token.name in ctx.facts.colors else "—"
        rows.append([f"`{token.name}`", token.value, light])
    parts = [
        f"### {ctx.p('heading.dark_theme')}",
        ctx.p("colors.dark.intro", count=len(rows), scheme=scheme),
        table(
            [
                ctx.p("colors.dark.col.token"),
                ctx.p("colors.dark.col.dark"),
                ctx.p("colors.dark.col.light"),
            ],
            rows,
        ),
    ]
    issues = [issue for issue in ctx.facts.contrast_issues() if issue.dark]
    if issues:
        parts.append(
            bullets(ctx.p("colors.dark.failing", **ctx.issue_text(issue)) for issue in issues)
        )
    return "\n\n".join(parts)


def colors(ctx: Context) -> str | None:
    facts = ctx.facts
    if not facts.colors:
        return None
    chromatic = 0
    for token in facts.colors.values():
        color = parse_color(token.value)
        if color is not None and is_chromatic(color):
            chromatic += 1
    parts = [
        ctx.p(
            "colors.intro",
            count=len(facts.colors),
            chromatic=chromatic,
            neutral=len(facts.colors) - chromatic,
            scheme=ctx.p(f"trait.theme.{facts.analysis.colors.scheme}"),
            clusters=len(facts.analysis.colors.clusters),
        )
    ]
    usage = facts.color_usage()
    grouped: dict[str, list[str]] = {group: [] for group in _COLOR_GROUPS}
    for name, token in facts.colors.items():
        grouped[color_group(name, token)].append(
            _color_entry(ctx, name, token, usage.get(name, []))
        )
    for group in _COLOR_GROUPS:
        if grouped[group]:
            parts.append(f"### {ctx.p(f'heading.colors.{group}')}\n\n{bullets(grouped[group])}")
    unreferenced = _unreferenced(ctx)
    if unreferenced:
        parts.append(unreferenced)
    for extra in (_contrast_table(ctx), _decorative(ctx), _dark_theme(ctx)):
        if extra:
            parts.append(extra)
    return "\n\n".join(parts)


# ─── Typography ──────────────────────────────────────────────────────────────


def _family_line(ctx: Context, family: FontFamilyInfo) -> str:
    details = [ctx.p(f"typography.role.{family.role.value}")]
    details.append(ctx.p(f"typography.category.{family.category.replace('-', '_')}"))
    if family.stack:
        details.append(ctx.p("typography.stack", stack=md_code(", ".join(family.stack))))
    weights = ctx.facts.family_weights(family)
    if weights:
        details.append(ctx.p("typography.family_weights", weights=_weights(ctx, weights)))
    details.append(ctx.p("typography.google" if family.is_google_font else "typography.not_google"))
    return f"**{md_text(family.name)}** — " + "; ".join(details) + "."


def _type_table(ctx: Context) -> str:
    facts = ctx.facts
    rows = []
    for name, level in facts.levels.items():
        ratio = facts.line_height_ratio(level)
        line_height = "—"
        if ratio is not None:
            line_height = fmt_num(ratio)
            if level.line_height_px is not None:
                line_height += f" ({fmt_px(level.line_height_px)})"
        spacing = fmt_px(level.letter_spacing_px) if round(level.letter_spacing_px, 2) else "0"
        rows.append(
            [
                f"`{token_ref(TYPOGRAPHY, name)}`",
                md_text(level.font_family),
                fmt_px(level.size_px),
                str(level.weight),
                line_height,
                spacing,
                ", ".join(md_text(tag) for tag in level.tags) or "—",
            ]
        )
    headers = [
        ctx.p("typography.col.token"),
        ctx.p("typography.col.family"),
        ctx.p("typography.col.size"),
        ctx.p("typography.col.weight"),
        ctx.p("typography.col.line_height"),
        ctx.p("typography.col.letter_spacing"),
        ctx.p("typography.col.tags"),
    ]
    return table(headers, rows)


def _type_notes(ctx: Context) -> list[str]:
    facts = ctx.facts
    typography = facts.analysis.typography
    notes: list[str] = []
    if typography.scale_ratio is not None:
        base = typography.base_size_px
        if typography.scale_ratio_name:
            notes.append(
                ctx.p(
                    "typography.scale_named",
                    ratio=fmt_num(typography.scale_ratio),
                    name=md_text(typography.scale_ratio_name),
                    base=fmt_px(base) if base else "—",
                )
            )
        else:
            notes.append(
                ctx.p(
                    "typography.scale",
                    ratio=fmt_num(typography.scale_ratio),
                    base=fmt_px(base) if base else "—",
                )
            )
    weights = facts.weights()
    if weights:
        key = {
            "heavy_only": "typography.weights.heavy_only",
            "no_bold": "typography.weights.no_bold",
        }.get(ctx.traits.weight or "", "typography.weights.range")
        notes.append(ctx.p(key, weights=_weights(ctx, weights)))
    tight = [
        (name, level)
        for name, level in facts.levels.items()
        if level.letter_spacing_px < 0 and level.size_px >= 24
    ]
    if tight:
        notes.append(
            ctx.p(
                "typography.tight_tracking",
                levels=ctx.phrases.join(
                    [
                        f"`{token_ref(TYPOGRAPHY, name)}` ({fmt_px(level.letter_spacing_px)})"
                        for name, level in tight
                    ]
                ),
            )
        )
    upper = [name for name, level in facts.levels.items() if level.text_transform == "uppercase"]
    if upper:
        notes.append(
            ctx.p(
                "typography.uppercase",
                levels=ctx.phrases.join([f"`{token_ref(TYPOGRAPHY, n)}`" for n in upper]),
            )
        )
    body = facts.body_level()
    if body is not None:
        ratio = facts.line_height_ratio(body)
        if ratio is not None and ratio >= 1.5:
            notes.append(
                ctx.p(
                    "typography.body_leading",
                    ref=ctx.level_ref(body.name),
                    ratio=fmt_num(ratio),
                )
            )
    mono = ctx.traits.mono_family
    if mono is not None:
        code = next(
            (name for name, level in facts.levels.items() if level.font_family == mono.name), None
        )
        if code is not None:
            notes.append(ctx.p("typography.mono_level", family=mono.name, ref=ctx.level_ref(code)))
    return notes


def typography(ctx: Context) -> str | None:
    facts = ctx.facts
    if not facts.families and not facts.levels:
        return None
    parts: list[str] = []
    type_text = type_words(ctx.traits, ctx.phrases)
    if type_text is not None:
        parts.append(ctx.p("typography.intro", type=type_text, count=len(facts.families)))
    if facts.families:
        parts.append(bullets(_family_line(ctx, family) for family in facts.families.values()))
    substitutes = [family for family in facts.families.values() if family.substitutes]
    if substitutes:
        parts.append(
            "\n\n".join(
                [
                    f"### {ctx.p('heading.font_substitutes')}",
                    ctx.p("typography.substitutes_intro"),
                    bullets(
                        ctx.p(
                            "typography.substitute",
                            family=md_text(family.name),
                            substitutes=ctx.phrases.join(
                                [md_text(name) for name in family.substitutes]
                            ),
                        )
                        for family in substitutes
                    ),
                ]
            )
        )
    if facts.levels:
        parts.append(
            "\n\n".join(
                [
                    f"### {ctx.p('heading.type_scale')}",
                    ctx.p("typography.scale_intro", count=len(facts.levels)),
                    _type_table(ctx),
                ]
            )
        )
    notes = _type_notes(ctx)
    if notes:
        parts.append(bullets(notes))
    return "\n\n".join(parts)
