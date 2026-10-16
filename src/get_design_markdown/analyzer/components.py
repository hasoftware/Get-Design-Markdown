"""Components by heuristics: buttons, links, inputs, cards, navigation, badges, footer,
their variants, hover states and token references.

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_components(pages, palette=..., typography=..., spacing=..., radii=..., shadows=...)
        -> list[ComponentSpec]

How it works:
1. components_detect sorts every rendered element into candidate pools (button-like
   filled/outline, ghost-shaped, nav links, inline text links, inputs, cards, badges) and finds
   the page chrome (top nav, sidebar, footer). Overlays, decorative subtrees (illustrations,
   app mock-ups built from DOM) and elements mostly outside the viewport are skipped.
2. Pools are grouped by visual signature (colors merged within ΔE2000 < 3). Single-spec pools
   (links, inputs, badges) keep the group with the most members counted at their page weight,
   so the requested page leads and sub-pages only add evidence. A group's representative is
   its most prominent member (visible area, x2 in the first two screens, x page weight) among
   those with the group's most common background; it gives the raw properties, with uneven
   corners kept as a border-radius shorthand. The "hover" state keeps the changes shared by
   most hovered members.
3. Filled button groups become primary/secondary/tertiary from the palette tokens, else from
   the header CTA, chroma and emphasis; outline and ghost groups keep their own variant.
4. Cards are read from their painted surface (components_surface), which may come from
   layers hugging the card box. Token references are added only when they resolve.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from get_design_markdown.analyzer.common import own_background
from get_design_markdown.analyzer.components_context import (
    Group,
    Member,
    PageContext,
    backdrop,
    build_contexts,
    by_prominence,
    canonical_color,
    canvas_color,
    css_color,
    is_pill,
    padding_px,
    radius_px,
    rounded_value,
    same_color,
    text_color,
    visible_border,
)
from get_design_markdown.analyzer.components_detect import (
    ButtonStyle,
    Detection,
    detect,
    fill_on,
    is_chromatic_fill,
)
from get_design_markdown.analyzer.components_props import (
    SURFACE_ROLES,
    TokenContext,
    background_property,
    box_properties,
    class_hints,
    collect_samples,
    color_refs,
    contrast_for,
    dominant_text,
    has_background_hover,
    heading_text,
    input_sample,
    ordered,
    ordered_refs,
    padding_ref,
    radius_ref,
    states_and_refs,
    text_properties,
    text_source,
    text_style,
    typography_ref,
)
from get_design_markdown.analyzer.components_surface import (
    Surface,
    content_padding_holder,
    resolve_surface,
    visual_padding,
)
from get_design_markdown.analyzer.css_values import (
    RGBA,
    contrast_ratio,
    format_box_shadow,
    format_px,
    format_sides_px,
    gradient_colors,
    parse_px,
)
from get_design_markdown.analyzer.naming import COMPONENT_NAME_ORDER, match_color_token
from get_design_markdown.models.analysis import (
    ColorPalette,
    ComponentKind,
    ComponentSpec,
    ScaleStep,
    ShadowLevel,
    SpacingSystem,
    Typography,
)
from get_design_markdown.models.raw import ElementSample, PageCapture

#: Variants named after palette tokens of the same name.
TOKEN_VARIANTS = ("primary", "secondary", "tertiary")
#: Multi-line fields (<textarea>), listed right after "text-input" in COMPONENT_NAME_ORDER.
TEXT_AREA_NAME = "text-area"
RADIUS_TOLERANCE_PX = 0.5
#: A weaker-shadow card group must be at least this prominent (vs the main one) to become
#: the base "card" under a "card-elevated" main group.
CARD_PAIR_MIN_SHARE = 0.25
_HINT_LIMIT = 3
#: A descendant covering this share of a transparent header/footer lends it its background.
COVERING_AREA_SHARE = 0.8
#: A label wrapper this close to the button box carries the button's padding.
WRAPPER_SLACK_PX = 8.0
#: Wrappers hugging a borderless field (within this many px per edge) may draw its border.
FRAME_SLACK_PX = 4.0
FRAME_MAX_DEPTH = 3
FRAME_MIN_PADDING_PX = 0.5
FRAME_MAX_PADDING_PX = 3.0


def analyze_components(
    pages: Sequence[PageCapture],
    *,
    palette: ColorPalette,
    typography: Typography,
    spacing: SpacingSystem,
    radii: Sequence[ScaleStep],
    shadows: Sequence[ShadowLevel],
) -> list[ComponentSpec]:
    """Component variants in naming.COMPONENT_NAME_ORDER order (pure).

    The other analyses may be empty (that part was skipped): then token_refs stay empty
    for that group, the raw `properties` are still filled.
    """
    del shadows  # shadows have no token group in the DESIGN.md front matter
    if not pages:
        return []
    contexts = build_contexts(pages)
    canvas = canvas_color(palette)
    tokens = TokenContext(palette=palette, typography=typography, spacing=spacing, radii=radii)
    found = detect(contexts, canvas)
    builder = _Builder(tokens=tokens, canvas=canvas)
    specs: list[ComponentSpec] = []
    specs.extend(builder.buttons(found))
    specs.extend(builder.links(found))
    specs.extend(builder.inputs(found))
    specs.extend(builder.cards(found))
    specs.extend(builder.badges(found))
    specs.extend(builder.chrome(found))
    return sort_components(specs)


def sort_components(specs: Sequence[ComponentSpec]) -> list[ComponentSpec]:
    """naming.COMPONENT_NAME_ORDER first, unknown names after (alphabetically)."""
    rank = {name: position for position, name in enumerate(COMPONENT_NAME_ORDER)}
    return sorted(specs, key=lambda spec: (rank.get(spec.name, len(rank)), spec.name))


# ─── Grouping ────────────────────────────────────────────────────────────────

Key = tuple[object, ...]


def group_members(
    members: Sequence[Member],
    key: Callable[[Member], Key],
    same: Callable[[Key, Key], bool],
) -> list[Group]:
    """Greedy grouping in prominence order: a member joins the first group with a same key."""
    groups: list[Group] = []
    for member in by_prominence(members):
        member_key = key(member)
        for group in groups:
            if same(group.key, member_key):
                group.members.append(member)
                break
        else:
            groups.append(Group(key=member_key, members=[member]))
    return groups


def _color_at(key: Key, position: int) -> RGBA | None:
    value = key[position]
    return value if isinstance(value, RGBA) else None


def _float_at(key: Key, position: int) -> float:
    value = key[position]
    return float(value) if isinstance(value, int | float) else 0.0


def _same_colors(first: Key, second: Key, positions: Sequence[int]) -> bool:
    return all(
        same_color(_color_at(first, position), _color_at(second, position))
        for position in positions
    )


def _pick(groups: Sequence[Group]) -> Group | None:
    """Most common signature, members counted at their page weight (the requested page
    leads; a sub-page only adds evidence); ties go to the most prominent group."""
    if not groups:
        return None
    return max(groups, key=lambda group: (round(group.weight, 6), group.prominence))


# ─── Builder ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Builder:
    tokens: TokenContext
    canvas: RGBA

    # Buttons ─────────────────────────────────────────────────────────────

    def _button_key(self, member: Member, style: ButtonStyle) -> Key:
        element = member.element
        source = text_source(member.ctx, element)
        fill = None
        if style is ButtonStyle.FILLED:
            fill = own_background(element)
        border = visible_border(element)
        return (
            style.value,
            fill,
            text_color(source),
            None if border is None else border[0],
            radius_px(element),
        )

    @staticmethod
    def _same_button(first: Key, second: Key) -> bool:
        return (
            first[0] == second[0]
            and _same_colors(first, second, (1, 2, 3))
            and abs(_float_at(first, 4) - _float_at(second, 4)) <= RADIUS_TOLERANCE_PX
        )

    def buttons(self, found: Detection) -> list[ComponentSpec]:
        by_style: dict[ButtonStyle, list[Member]] = {
            ButtonStyle.FILLED: [],
            ButtonStyle.OUTLINE: [],
        }
        styles: dict[tuple[int, int], ButtonStyle] = {}
        for member, style in found.buttons:
            by_style[style].append(member)
            styles[(member.ctx.page_index, member.element.index)] = style

        def key(member: Member) -> Key:
            return self._button_key(member, styles[(member.ctx.page_index, member.element.index)])

        filled = _merge_sizes(group_members(by_style[ButtonStyle.FILLED], key, self._same_button))
        outline = group_members(by_style[ButtonStyle.OUTLINE], key, self._same_button)
        variants = self._filled_variants(filled, found)
        if outline:
            variants["outline"] = outline
        ghosts = [
            group
            for group in group_members(
                found.ghosts,
                lambda member: self._button_key(member, ButtonStyle.OUTLINE),
                self._same_button,
            )
            if has_background_hover(group.members)
        ]
        if ghosts:
            variants["ghost"] = ghosts
        specs: list[ComponentSpec] = []
        # The first variant (the primary) sets the size class the others are shown at, so a
        # documented pair ("Get started" / "Contact sales") is one size, as on the page.
        anchor: Member | None = None
        for variant, groups in variants.items():
            group = _collapse(groups)
            specs.append(
                self.text_component(
                    f"button-{variant}",
                    ComponentKind.BUTTON,
                    variant,
                    group.members,
                    size_of=anchor,
                )
            )
            if anchor is None:
                anchor = _representative(by_prominence(group.members))
        return specs

    def _filled_variants(self, groups: Sequence[Group], found: Detection) -> dict[str, list[Group]]:
        """primary/secondary/tertiary for filled groups.

        Palette tokens decide first; otherwise the primary is the chromatic group with a
        member in the top nav (the header CTA), else the most prominent chromatic group.
        With neutral fills only, the strongest fill (contrast with what is behind it) among
        the header CTAs — or among all groups — wins: black on white beats a white pill.
        """
        ordered_groups = sorted(groups, key=lambda group: -group.prominence)
        variants: dict[str, list[Group]] = {}
        unassigned: list[Group] = []
        for group in ordered_groups:
            variant = self._token_variant(group)
            if variant is None:
                unassigned.append(group)
            else:
                variants.setdefault(variant, []).append(group)
        if "primary" not in variants and unassigned:
            chromatic = [group for group in unassigned if self._chromatic(group)]
            in_nav = [
                group
                for group in unassigned
                if any(found.in_top_nav(member) for member in group.members)
            ]
            chromatic_nav = [group for group in chromatic if group in in_nav]
            if chromatic:
                pick = (chromatic_nav or chromatic)[0]
            else:
                pick = max(
                    in_nav or unassigned,
                    key=lambda group: (round(self._emphasis(group), 1), group.prominence),
                )
            variants["primary"] = [pick]
            unassigned.remove(pick)
        rest = [group for group in unassigned if self._chromatic(group)]
        rest += [group for group in unassigned if not self._chromatic(group)]
        for group in rest:
            variant = "secondary" if "secondary" not in variants else "tertiary"
            variants.setdefault(variant, []).append(group)
        return {name: variants[name] for name in TOKEN_VARIANTS if name in variants}

    def _token_variant(self, group: Group) -> str | None:
        fill = _color_at(group.key, 1)
        if fill is None:
            return None
        token = match_color_token(fill, self.tokens.palette.tokens, prefer_roles=SURFACE_ROLES)
        if token is not None and token.name in TOKEN_VARIANTS:
            return token.name
        return None

    def _emphasis(self, group: Group) -> float:
        """Contrast between the group's fill and what is behind it."""
        member = group.representative
        fill = fill_on(member.ctx, member.element, self.canvas)
        if fill is None:
            return 1.0
        return contrast_ratio(fill, backdrop(member.ctx, member.element, self.canvas))

    def _chromatic(self, group: Group) -> bool:
        member = group.representative
        return is_chromatic_fill(member.ctx, member.element, self.canvas)

    # Links & inputs ──────────────────────────────────────────────────────

    def links(self, found: Detection) -> list[ComponentSpec]:
        specs: list[ComponentSpec] = []
        text_group = _pick(group_members(found.text_links, self._link_key, _same_link))
        if text_group is not None:
            specs.append(
                self.text_component(
                    "text-link", ComponentKind.LINK, "inline", text_group.members, link=True
                )
            )
        pool = [member for member in found.nav_links if found.in_top_nav(member)]
        pool = pool or found.nav_links
        nav_group = _pick(group_members(pool, self._nav_key, _same_nav))
        if nav_group is not None:
            specs.append(
                self.text_component(
                    "nav-link", ComponentKind.LINK, "nav", nav_group.members, link=True
                )
            )
        return specs

    @staticmethod
    def _link_key(member: Member) -> Key:
        source = text_source(member.ctx, member.element)
        return (
            text_color(source),
            _decoration(member.element, source),
            source.styles.font_weight,
        )

    @staticmethod
    def _nav_key(member: Member) -> Key:
        source = text_source(member.ctx, member.element)
        styles = source.styles
        return (
            text_color(source),
            _decoration(member.element, source),
            styles.font_weight,
            styles.font_size,
            styles.font_family,
        )

    def inputs(self, found: Detection) -> list[ComponentSpec]:
        """text-input from single-line fields; text-area from <textarea> (no fixed height:
        it follows the rows/content, and must not size a one-line field)."""
        specs: list[ComponentSpec] = []
        for name, variant, pool in (
            ("text-input", None, found.inputs),
            (TEXT_AREA_NAME, "multiline", found.text_areas),
        ):
            group = _pick(group_members(pool, _input_key, _same_input))
            if group is not None:
                specs.append(
                    self.text_component(
                        name,
                        ComponentKind.INPUT,
                        variant,
                        group.members,
                        field=True,
                        fixed_height=pool is found.inputs,
                    )
                )
        return specs

    # Containers ──────────────────────────────────────────────────────────

    def cards(self, found: Detection) -> list[ComponentSpec]:
        groups = [
            group
            for group in group_members(found.cards, _card_key, _same_card)
            if _card_group_qualifies(group)
        ]
        if not groups:
            return []
        groups.sort(key=lambda group: -group.prominence)
        main = groups[0]
        main_strength = _float_at(main.key, 4)
        others = [
            group for group in groups[1:] if abs(_float_at(group.key, 4) - main_strength) > 0.5
        ]
        base, elevated = main, None
        if others:
            other = others[0]
            if _float_at(other.key, 4) > main_strength:
                elevated = other
            elif other.prominence >= CARD_PAIR_MIN_SHARE * main.prominence:
                base, elevated = other, main
        specs = [self.container("card", ComponentKind.CARD, None, base.members)]
        if elevated is not None:
            specs.append(
                self.container("card-elevated", ComponentKind.CARD, "elevated", elevated.members)
            )
        return specs

    def badges(self, found: Detection) -> list[ComponentSpec]:
        group = _pick(group_members(found.badges, _badge_key, _same_badge))
        if group is None:
            return []
        representative = group.representative.element
        variant = "pill" if is_pill(representative) else None
        return [self.text_component("badge", ComponentKind.BADGE, variant, group.members)]

    def chrome(self, found: Detection) -> list[ComponentSpec]:
        specs: list[ComponentSpec] = []
        if found.top_nav is not None:
            specs.append(self.container("top-nav", ComponentKind.NAVIGATION, None, [found.top_nav]))
        if found.sidebar is not None:
            specs.append(self.container("sidebar", ComponentKind.NAVIGATION, None, [found.sidebar]))
        if found.footer is not None:
            specs.append(self.container("footer", ComponentKind.FOOTER, None, [found.footer]))
        return specs

    # Spec construction ───────────────────────────────────────────────────

    def text_component(
        self,
        name: str,
        kind: ComponentKind,
        variant: str | None,
        members: Sequence[Member],
        *,
        link: bool = False,
        field: bool = False,
        fixed_height: bool = True,
        size_of: Member | None = None,
    ) -> ComponentSpec:
        """Button / link / input / badge: box properties plus text style.

        `size_of`: prefer a representative of the same size class as this member (paired
        buttons are documented at one size)."""
        members = by_prominence(members)
        representative = _representative(members, size_of=size_of)
        ctx, element = representative.ctx, representative.element
        source = text_source(ctx, element)
        style = text_style(source, self.tokens.typography)
        holder = _padding_holder(ctx, element)
        padded = any(side > 0 for side in padding_px(holder))
        properties = box_properties(
            element,
            background=True,
            height=fixed_height and (not link or padded),
            padding_from=holder,
        )
        if link and properties.get("backgroundColor") == "transparent":
            del properties["backgroundColor"]
        properties.update(text_properties(style))
        if link:
            properties["textDecoration"] = _decoration(element, source)
        frame_radius: float | None = None
        if field:
            if element.placeholder_color:
                placeholder = canonical_color(element.placeholder_color)
                if placeholder is not None:
                    properties["placeholderColor"] = placeholder
            if "borderColor" not in properties:
                frame, frame_radius = _field_frame(ctx, element)
                properties.update(frame)
        refs = color_refs(properties, self.tokens)
        refs.update(self._shape_refs(element, properties, holder, frame_radius))
        type_ref = typography_ref(style, self.tokens)
        if type_ref is not None:
            refs["typography"] = type_ref
        states, state_refs = states_and_refs(members, self.tokens, self.canvas)
        if field:
            samples = collect_samples(input_sample(m.element) for m in members)
        else:
            samples = collect_samples(text_source(m.ctx, m.element).text for m in members)
        return ComponentSpec(
            name=name,
            kind=kind,
            variant=variant,
            count=len(members),
            properties=ordered(properties),
            token_refs=ordered_refs(refs),
            states=states,
            state_token_refs=state_refs,
            contrast_ratio=contrast_for(ctx, source, style.color, self.canvas),
            class_hints=class_hints((m.element for m in members), limit=_HINT_LIMIT),
            samples=samples,
        )

    def container(
        self,
        name: str,
        kind: ComponentKind,
        variant: str | None,
        members: Sequence[Member],
    ) -> ComponentSpec:
        """Card / navigation / footer: box properties only (footer adds its text color)."""
        members = by_prominence(members)
        representative = members[0]
        ctx, element = representative.ctx, representative.element
        chrome = kind is not ComponentKind.CARD
        radius: float | None = None
        if chrome:
            padding_from = _padding_source(ctx, element) if name == "top-nav" else None
            properties = box_properties(
                element, background=True, height=name == "top-nav", padding_from=padding_from
            )
        else:
            padding_from = content_padding_holder(ctx, element)
            surface = resolve_surface(ctx, element)
            radius = surface.radius
            properties = _surface_properties(element, surface, padding_from)
            if "padding" not in properties:
                seen = visual_padding(ctx, element)
                if seen is not None:
                    properties["padding"] = format_sides_px(seen)
        if chrome and properties["backgroundColor"] == "transparent":
            covering = _covering_background(ctx, element)
            if covering is not None:
                properties["backgroundColor"] = covering
        if name == "sidebar":
            properties["width"] = format_px(element.box.width)
        if name == "top-nav" and element.styles.position in ("sticky", "fixed"):
            properties["position"] = element.styles.position
        refs = color_refs(properties, self.tokens)
        refs.update(self._shape_refs(element, properties, padding_from, radius))
        contrast: float | None = None
        if kind is ComponentKind.FOOTER:
            text = dominant_text(ctx, element)
            if text is not None:
                style = text_style(text, self.tokens.typography)
                properties["textColor"] = css_color(style.color)
                text_ref = color_refs({"textColor": properties["textColor"]}, self.tokens)
                refs.update(text_ref)
                type_ref = typography_ref(style, self.tokens)
                if type_ref is not None:
                    refs["typography"] = type_ref
                contrast = contrast_for(ctx, text, style.color, self.canvas)
        if kind is ComponentKind.CARD:
            hints = class_hints((m.element for m in members), limit=_HINT_LIMIT)
            samples = collect_samples(heading_text(m.ctx, m.element) for m in members)
        else:
            hints = _chrome_hints(ctx, element)
            samples = collect_samples([element.text])
        return ComponentSpec(
            name=name,
            kind=kind,
            variant=variant,
            count=len(members),
            properties=ordered(properties),
            token_refs=ordered_refs(refs),
            contrast_ratio=contrast,
            class_hints=hints,
            samples=samples,
        )

    def _shape_refs(
        self,
        element: ElementSample,
        properties: dict[str, str],
        padding_from: ElementSample | None = None,
        radius: float | None = None,
    ) -> dict[str, str]:
        refs: dict[str, str] = {}
        # A radius step stands for four equal corners: a tab ("4px 4px 0px 0px") has none.
        if "rounded" in properties and " " not in properties["rounded"].strip():
            ref = radius_ref(element, self.tokens, radius)
            if ref is not None:
                refs["rounded"] = ref
        holder = padding_from or element
        if "padding" in properties and properties["padding"] == _own_padding(holder):
            ref = padding_ref(holder, self.tokens)
            if ref is not None:
                refs["padding"] = ref
        return refs


# ─── Keys & helpers ──────────────────────────────────────────────────────────


def _decoration(element: ElementSample, source: ElementSample) -> str:
    for node in (element, source):
        value = " ".join(node.styles.text_decoration_line.split()).lower()
        if value and value != "none":
            return value
    return "none"


def _same_link(first: Key, second: Key) -> bool:
    return _same_colors(first, second, (0,)) and first[1:] == second[1:]


def _same_nav(first: Key, second: Key) -> bool:
    return _same_colors(first, second, (0,)) and first[1:] == second[1:]


def _input_key(member: Member) -> Key:
    element = member.element
    border = visible_border(element)
    return (
        own_background(element),
        None if border is None else border[0],
        text_color(element),
        radius_px(element),
    )


def _same_input(first: Key, second: Key) -> bool:
    return (
        _same_colors(first, second, (0, 1, 2))
        and abs(_float_at(first, 3) - _float_at(second, 3)) <= RADIUS_TOLERANCE_PX
    )


def _card_key(member: Member) -> Key:
    surface = resolve_surface(member.ctx, member.element)
    return (
        surface.background,
        None if surface.border is None else surface.border[0],
        surface.radius,
        format_box_shadow(surface.shadow),
        surface.strength,
    )


def _surface_properties(
    element: ElementSample, surface: Surface, padding_from: ElementSample
) -> dict[str, str]:
    """Card properties from its painted surface (see components_surface)."""
    properties = {"backgroundColor": css_color(surface.background)}
    if surface.border is not None:
        color, widths = surface.border
        properties["borderColor"] = css_color(color)
        properties["borderWidth"] = format_sides_px(widths)
    rounded = rounded_value(surface.corners)
    if rounded is not None:
        properties["rounded"] = rounded
    padding = padding_px(padding_from)
    if any(side > 0 for side in padding):
        properties["padding"] = format_sides_px(padding)
    if surface.shadow:
        properties["shadow"] = format_box_shadow(surface.shadow)
    backdrop_filter = element.styles.backdrop_filter.strip()
    if backdrop_filter and backdrop_filter.lower() != "none":
        properties["backdropFilter"] = backdrop_filter
    return properties


def _same_card(first: Key, second: Key) -> bool:
    return (
        _same_colors(first, second, (0, 1))
        and abs(_float_at(first, 2) - _float_at(second, 2)) <= 1.0
        and first[3] == second[3]
    )


def _card_group_qualifies(group: Group) -> bool:
    """Repeated cards (siblings, or cousins through a wrapper) — or a single shadowed one."""
    if _float_at(group.key, 4) > 0:
        return True
    parents: dict[tuple[int, int | None], int] = {}
    grandparents: dict[tuple[int, int | None], int] = {}
    for member in group.members:
        page = member.ctx.page_index
        parent = member.ctx.parent(member.element)
        parent_key = (page, member.element.parent_index)
        parents[parent_key] = parents.get(parent_key, 0) + 1
        grand_key = (page, None if parent is None else parent.parent_index)
        grandparents[grand_key] = grandparents.get(grand_key, 0) + 1
    return max(parents.values()) >= 2 or max(grandparents.values()) >= 2


def _badge_key(member: Member) -> Key:
    element = member.element
    source = text_source(member.ctx, element)
    border = visible_border(element)
    return (
        own_background(element),
        text_color(source),
        None if border is None else border[0],
        is_pill(element),
    )


def _same_badge(first: Key, second: Key) -> bool:
    return _same_colors(first, second, (0, 1, 2)) and first[3] == second[3]


def _merge_sizes(groups: Sequence[Group]) -> list[Group]:
    """Merge groups whose colors match (they differ only in radius, i.e. size variants)."""
    merged: list[Group] = []
    for group in sorted(groups, key=lambda item: -item.prominence):
        for target in merged:
            if _same_colors(target.key, group.key, (1, 2, 3)):
                target.merge(group)
                break
        else:
            merged.append(Group(key=group.key, members=list(group.members)))
    return merged


def _collapse(groups: Sequence[Group]) -> Group:
    """One group per variant: the most prominent, absorbing groups that differ only in size."""
    ordered_groups = sorted(groups, key=lambda group: -group.prominence)
    main = Group(key=ordered_groups[0].key, members=list(ordered_groups[0].members))
    for other in ordered_groups[1:]:
        if _same_colors(main.key, other.key, (1, 2, 3)):
            main.merge(other)
    return main


def _representative(members: Sequence[Member], *, size_of: Member | None = None) -> Member:
    """Most prominent member (members come most prominent first) among those with the
    group's most seen background — votes weighted by prominence, so the hero instance of an
    outline button outweighs two plain ones far down the page. With `size_of`, a member of
    the same size class is preferred when the group has one."""
    seen: dict[str, float] = {}
    for member in members:
        key = background_property(member.element)
        seen[key] = seen.get(key, 0.0) + member.prominence
    best = max(seen.values(), default=0.0)
    pool = [member for member in members if seen[background_property(member.element)] == best]
    pool = pool or list(members)
    if size_of is not None:
        target = _size_class(size_of)
        same = [member for member in pool if _size_class(member) == target]
        if same:
            return same[0]
    return pool[0]


def _size_class(member: Member) -> tuple[int, float]:
    """(height, label font size) rounded: "small" / "medium" / "large" button classes."""
    source = text_source(member.ctx, member.element)
    size = parse_px(source.styles.font_size) or 0.0
    return round(member.element.box.height), round(size * 2) / 2


def _padding_holder(ctx: PageContext, element: ElementSample) -> ElementSample:
    """The element, or — when it has no padding — the wrapper of its label filling its box."""
    if any(side > 0 for side in padding_px(element)):
        return element
    box = element.box
    for node in ctx.descendants(element, max_depth=3):
        if (
            abs(node.box.width - box.width) <= WRAPPER_SLACK_PX
            and abs(node.box.height - box.height) <= WRAPPER_SLACK_PX
            and any(side > 0 for side in padding_px(node))
        ):
            return node
    return element


def _hugs(outer: ElementSample, inner: ElementSample) -> bool:
    a, b = outer.box, inner.box
    return (
        abs(a.x - b.x) <= FRAME_SLACK_PX
        and abs(a.y - b.y) <= FRAME_SLACK_PX
        and abs(a.width - b.width) <= 2 * FRAME_SLACK_PX
        and abs(a.height - b.height) <= 2 * FRAME_SLACK_PX
    )


def _field_frame(ctx: PageContext, element: ElementSample) -> tuple[dict[str, str], float | None]:
    """Border and radius of a borderless field drawn by the wrappers hugging it: a real
    border, or a 1-3px padding showing the wrapper's background (the "gradient border" trick).
    Returns (properties, radius in px or None)."""
    radius = radius_px(element)
    found: dict[str, str] = {}
    for node in list(ctx.lineage(element))[1 : FRAME_MAX_DEPTH + 1]:
        if not _hugs(node, element):
            break
        radius = max(radius, radius_px(node))
        border = visible_border(node)
        if border is not None:
            found = {"borderColor": css_color(border[0]), "borderWidth": format_sides_px(border[1])}
            break
        sides = padding_px(node)
        fill = own_background(node)
        if (
            fill is not None
            and max(sides) - min(sides) < 0.01
            and FRAME_MIN_PADDING_PX <= sides[0] <= FRAME_MAX_PADDING_PX
        ):
            found = {"borderColor": css_color(fill), "borderWidth": format_px(sides[0])}
            break
    if not found:
        return {}, None
    if radius > 0:
        found["rounded"] = format_px(radius)
    return found, radius if radius > 0 else None


def _own_padding(element: ElementSample) -> str:
    return format_sides_px(padding_px(element))


def _padding_source(ctx: PageContext, element: ElementSample) -> ElementSample:
    """The header's own padding, else the first descendant container with a padding."""
    if any(side > 0 for side in padding_px(element)):
        return element
    for node in ctx.descendants(element, max_depth=3):
        if node.child_count > 0 and any(side > 0 for side in padding_px(node)):
            return node
    return element


def _covering_background(ctx: PageContext, element: ElementSample) -> str | None:
    """Fill seen on a transparent container: the background color of a descendant covering
    most of it, else the tint of a gradient painted by the container or by such a
    descendant (a translucent dark gradient behind a blurred header), alpha kept."""
    area = element.box.area
    if area <= 0:
        return None
    covering = [
        node
        for node in ctx.descendants(element, max_depth=3)
        if node.box.area >= COVERING_AREA_SHARE * area
    ]
    for node in covering:
        color = own_background(node)
        if color is not None:
            return css_color(color)
    for node in (element, *covering):
        tint = _gradient_tint(node)
        if tint is not None:
            return css_color(tint)
    return None


def _gradient_tint(element: ElementSample) -> RGBA | None:
    """First visible color stop of the element's background gradient (None without one)."""
    stops = gradient_colors(element.styles.background_image)
    return next((stop for stop in stops if not stop.is_transparent), None)


def _chrome_hints(ctx: PageContext, element: ElementSample) -> list[str]:
    hints = class_hints([element], limit=_HINT_LIMIT)
    descendants = [node for node in ctx.descendants(element) if node.child_count > 0]
    for name in class_hints(descendants, limit=_HINT_LIMIT * 2):
        if name not in hints:
            hints.append(name)
    return hints[:_HINT_LIMIT]
