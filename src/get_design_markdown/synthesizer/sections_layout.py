"""Layout (with Responsive Strategy), Elevation & Depth and Shapes sections."""

from __future__ import annotations

from get_design_markdown.analyzer.css_values import parse_color
from get_design_markdown.analyzer.naming import ROUNDED, SPACING, parse_token_ref, token_ref
from get_design_markdown.models.analysis import Breakpoint, ColorRole, ResponsiveChange
from get_design_markdown.synthesizer.catalog import placeholders
from get_design_markdown.synthesizer.context import Context
from get_design_markdown.synthesizer.facts import is_transparent_value
from get_design_markdown.synthesizer.formatting import (
    bullets,
    fmt_num,
    fmt_percent,
    fmt_px,
    md_code,
    md_text,
    table,
    unique,
)

MAX_USED_ON = 4

# ─── Layout ──────────────────────────────────────────────────────────────────


def _breakpoint_range(ctx: Context, breakpoint: Breakpoint) -> str:
    low = breakpoint.min_width_px
    high = breakpoint.max_width_px
    if low is not None and high is not None:
        return ctx.p("layout.range.between", low=fmt_num(low), high=fmt_num(high))
    if low is not None:
        return ctx.p("layout.range.min", low=fmt_num(low))
    return ctx.p("layout.range.max", high=fmt_num(high or 0))


def change_text(ctx: Context, change: ResponsiveChange) -> str:
    """One responsive change, phrase key = change.kind (fallback without params)."""
    values: dict[str, object] = {
        "from_width": change.from_width,
        "to_width": change.to_width,
    }
    values.update({key: fmt_num(value) for key, value in change.params.items()})
    key = f"responsive.{change.kind.value}"
    if placeholders(ctx.phrases.template(key)) <= values.keys():
        return ctx.p(key, **values)
    return ctx.p(f"responsive.fallback.{change.kind.value}", **values)


def _snapshot_table(ctx: Context) -> str | None:
    snapshots = sorted(
        ctx.facts.analysis.layout.snapshots, key=lambda snapshot: -snapshot.viewport_width
    )
    if not snapshots:
        return None
    rows = []
    for snapshot in snapshots:
        if snapshot.hamburger_visible:
            nav = ctx.p("layout.snap.hamburger")
        elif snapshot.nav_visible:
            nav = ctx.p("layout.snap.links", count=snapshot.visible_nav_links)
        else:
            nav = ctx.p("layout.snap.hidden")
        if snapshot.sidebar_visible:
            nav += ctx.p("layout.snap.sidebar")
        rows.append(
            [
                fmt_px(snapshot.viewport_width),
                nav,
                str(snapshot.main_columns),
                fmt_px(snapshot.container_width_px) if snapshot.container_width_px else "—",
                fmt_px(snapshot.gutter_px) if snapshot.gutter_px is not None else "—",
                fmt_px(snapshot.max_heading_px) if snapshot.max_heading_px else "—",
                fmt_px(snapshot.min_tap_target_px) if snapshot.min_tap_target_px else "—",
            ]
        )
    headers = [
        ctx.p("layout.snap.col.viewport"),
        ctx.p("layout.snap.col.navigation"),
        ctx.p("layout.snap.col.columns"),
        ctx.p("layout.snap.col.container"),
        ctx.p("layout.snap.col.gutter"),
        ctx.p("layout.snap.col.heading"),
        ctx.p("layout.snap.col.tap"),
    ]
    return "\n\n".join([ctx.p("layout.snap.intro"), table(headers, rows)])


def width_breakpoints(ctx: Context) -> list[Breakpoint]:
    return [
        breakpoint
        for breakpoint in ctx.facts.analysis.breakpoints
        if breakpoint.min_width_px is not None or breakpoint.max_width_px is not None
    ]


def _responsive(ctx: Context) -> str | None:
    analysis = ctx.facts.analysis
    breakpoints = width_breakpoints(ctx)
    if not (breakpoints or analysis.layout.changes or analysis.layout.snapshots):
        return None
    parts = [f"### {ctx.p('heading.responsive')}"]
    if breakpoints:
        rows = [
            [md_text(bp.name) or "—", _breakpoint_range(ctx, bp), str(bp.occurrences)]
            for bp in breakpoints
        ]
        parts.append(ctx.p("layout.breakpoints.intro", count=len(breakpoints)))
        parts.append(
            table(
                [
                    ctx.p("layout.breakpoints.col.name"),
                    ctx.p("layout.breakpoints.col.range"),
                    ctx.p("layout.breakpoints.col.occurrences"),
                ],
                rows,
            )
        )
    elif "breakpoints" not in ctx.facts.skipped_parts():
        parts.append(ctx.p("layout.breakpoints.none"))
    if analysis.layout.changes:
        parts.append(ctx.p("layout.changes.intro"))
        parts.append(bullets(change_text(ctx, change) for change in analysis.layout.changes))
    snapshots = _snapshot_table(ctx)
    if snapshots:
        parts.append(snapshots)
    return "\n\n".join(parts)


def layout(ctx: Context) -> str | None:
    facts = ctx.facts
    analysis = facts.analysis
    spacing = analysis.spacing
    container = analysis.layout.container_max_width_px or spacing.container_max_width_px
    gutter = analysis.layout.gutter_px
    responsive = _responsive(ctx)
    if not (
        container
        or gutter
        or spacing.base_unit_px
        or facts.spacing
        or spacing.section_gap_px
        or spacing.common_paddings
        or analysis.layout.shell != "unknown"
        or responsive
    ):
        return None
    parts: list[str] = []
    sentences: list[str] = []
    if container and gutter is not None:
        sentences.append(
            ctx.p("layout.container_gutter", container=fmt_num(container), gutter=fmt_num(gutter))
        )
    elif container:
        sentences.append(ctx.p("layout.container", container=fmt_num(container)))
    elif gutter is not None:
        sentences.append(ctx.p("layout.gutter", gutter=fmt_num(gutter)))
    if analysis.layout.shell != "unknown":
        shell = analysis.layout.shell.replace("+", "_").replace("-", "_")
        sentences.append(ctx.p(f"layout.shell.{shell}"))
    if sentences:
        parts.append(" ".join(sentences))
    if spacing.base_unit_px:
        parts.append(ctx.p("layout.base_unit", base=spacing.base_unit_px))
    elif facts.spacing:
        parts.append(ctx.p("layout.no_base_unit"))
    if facts.spacing:
        rows = [
            [
                f"`{token_ref(SPACING, name)}`",
                fmt_px(step.value_px),
                ", ".join(md_text(item) for item in step.used_on[:MAX_USED_ON]) or "—",
            ]
            for name, step in facts.spacing.items()
        ]
        parts.append(
            table(
                [
                    ctx.p("layout.spacing.col.token"),
                    ctx.p("layout.spacing.col.value"),
                    ctx.p("layout.spacing.col.used_on"),
                ],
                rows,
            )
        )
    rhythm: list[str] = []
    if spacing.section_gap_px:
        rhythm.append(ctx.p("layout.section_gap", gap=fmt_num(spacing.section_gap_px)))
    if spacing.common_paddings:
        rhythm.append(
            ctx.p(
                "layout.paddings",
                paddings=ctx.phrases.join([md_code(p) for p in spacing.common_paddings[:6]]),
            )
        )
    if rhythm:
        parts.append(bullets(rhythm))
    if responsive:
        parts.append(responsive)
    return "\n\n".join(parts)


# ─── Elevation & Depth ───────────────────────────────────────────────────────


def elevation(ctx: Context) -> str | None:
    facts = ctx.facts
    shadows = facts.analysis.shadows
    surfaces = facts.surfaces_by_lightness()
    blurred = [spec for spec in facts.components if spec.properties.get("backdropFilter")]
    if not (shadows or facts.colors or blurred):
        return None
    parts: list[str] = []
    if shadows:
        profile = facts.shadow_profile()
        parts.append(
            ctx.p(
                f"elevation.shadows.{ctx.traits.depth}",
                count=len(shadows),
                alpha=fmt_num(profile.drop_alpha),
            )
        )
        lines = []
        for level in shadows:
            details = [md_code(level.css), ctx.p("elevation.layers", count=level.layers)]
            if level.used_on:
                details.append(
                    ctx.p(
                        "elevation.used_on",
                        items=ctx.phrases.join([md_text(u) for u in level.used_on[:MAX_USED_ON]]),
                    )
                )
            lines.append(f"**{md_text(level.name)}** — " + "; ".join(details))
        parts.append(bullets(lines))
        if profile.has_rings and profile.has_drop:
            parts.append(ctx.p("elevation.rings"))
        parts.append(ctx.p("elevation.no_shadow_token"))
    elif ctx.traits.depth is None:
        parts.append(ctx.p("elevation.not_analyzed"))
    else:
        parts.append(ctx.p("elevation.none" if len(surfaces) >= 2 else "elevation.flat"))
    if len(surfaces) >= 2:
        ladder = " → ".join(ctx.color_ref(token.name) for token in surfaces)
        parts.append(ctx.p("elevation.ladder", ladder=ladder))
    borders = [name for name, token in facts.colors.items() if token.role == ColorRole.BORDER]
    if borders:
        parts.append(ctx.p("elevation.borders", ref=ctx.color_ref(borders[0])))
    for spec in blurred:
        background = facts.component_props(spec).get("backgroundColor")
        raw = spec.properties.get("backgroundColor")
        if background:
            shown = ctx.ref_or_value(background)
        elif raw is not None and is_transparent_value(raw):
            shown = ctx.p("common.transparent")
        else:
            shown = "—"
        color = parse_color(facts.resolve(background))
        if color is not None and not color.is_opaque:
            shown += " — " + ctx.p("colors.translucent", alpha=fmt_percent(color.alpha))
        values = {
            "component": f"`{spec.name}`",
            "filter": md_code(spec.properties.get("backdropFilter", "")),
            "background": shown,
        }
        position = spec.properties.get("position")
        if position in ("sticky", "fixed"):
            parts.append(
                ctx.p(
                    "elevation.backdrop_position",
                    position=ctx.p(f"elevation.position.{position}"),
                    **values,
                )
            )
        else:
            parts.append(ctx.p("elevation.backdrop", **values))
    return "\n\n".join(parts)


# ─── Shapes ──────────────────────────────────────────────────────────────────


def radius_usage(ctx: Context) -> dict[str, list[str]]:
    """Radius token -> components whose `rounded` references it."""
    usage: dict[str, list[str]] = {}
    for spec in ctx.facts.components:
        ref = spec.token_refs.get("rounded")
        parsed = parse_token_ref(ref) if ref is not None else None
        if parsed is not None and parsed[0] == ROUNDED and parsed[1] in ctx.facts.radii:
            usage.setdefault(parsed[1], []).append(spec.name)
    return usage


def shapes(ctx: Context) -> str | None:
    facts = ctx.facts
    if not facts.radii:
        return None
    parts: list[str] = []
    common = ctx.traits.common_radius
    if ctx.traits.softness is not None and common is not None:
        parts.append(
            ctx.p(
                f"shapes.softness.{ctx.traits.softness}",
                ref=facts.ref(ROUNDED, common.name),
            )
        )
    usage = radius_usage(ctx)
    rows = []
    for name, step in facts.radii.items():
        used = unique(usage.get(name, []) + [md_text(item) for item in step.used_on])
        rows.append(
            [
                f"`{token_ref(ROUNDED, name)}`",
                facts.radius_value(step),
                ", ".join(used[: MAX_USED_ON + 2]) or "—",
            ]
        )
    parts.append(
        table(
            [
                ctx.p("shapes.col.token"),
                ctx.p("shapes.col.value"),
                ctx.p("shapes.col.used_on"),
            ],
            rows,
        )
    )
    if len(usage) >= 3:
        parts.append(ctx.p("shapes.nested", count=len(usage)))
    return "\n\n".join(parts)
