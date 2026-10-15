"""--prompt-only: prompt.md + resized desktop/mobile screenshots for manual use in Claude Code /
claude.ai (no API call) (stream: synthesizer, phase GĐ6).

PUBLIC ENTRY POINTS (frozen signatures, called by synthesizer/synthesize.py):
    prepare_prompt_images(folder, screenshots, max_side=...) -> list[str]
    render_prompt_md(analysis, design_md, image_paths, lang=...) -> str
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

from PIL import Image

from get_design_markdown.analyzer.naming import COLORS, ROUNDED, SPACING, TYPOGRAPHY
from get_design_markdown.models.analysis import DesignAnalysis
from get_design_markdown.models.raw import Screenshot
from get_design_markdown.resources import render_template
from get_design_markdown.storage import PROMPT_IMAGES_DIR
from get_design_markdown.synthesizer.catalog import Phrases, load_phrases
from get_design_markdown.synthesizer.facts import DesignFacts
from get_design_markdown.synthesizer.formatting import fmt_num, fmt_ratio, longest_backtick_run
from get_design_markdown.synthesizer.render import normalize_markdown, site_label

#: Longest side of images referenced by prompt.md.
MAX_IMAGE_SIDE = 1568
#: Screenshots are cropped to at most this many viewport heights from the top.
MAX_SCREENS: Final = 3
DESKTOP_WIDTH: Final = 1440
MOBILE_WIDTH: Final = 390
MIN_DESKTOP_WIDTH: Final = 1024
MAX_MOBILE_WIDTH: Final = 480
TEMPLATE: Final = "prompt.md.j2"


def _light(screenshots: Sequence[Screenshot]) -> list[Screenshot]:
    return [shot for shot in screenshots if shot.color_scheme == "light"]


def pick_desktop(screenshots: Sequence[Screenshot]) -> Screenshot | None:
    """The light 1440px screenshot, else the widest light one of at least 1024px."""
    light = _light(screenshots)
    exact = [shot for shot in light if shot.viewport_width == DESKTOP_WIDTH]
    if exact:
        return exact[0]
    wide = [shot for shot in light if shot.viewport_width >= MIN_DESKTOP_WIDTH]
    return max(wide, key=lambda shot: shot.viewport_width, default=None)


def pick_mobile(screenshots: Sequence[Screenshot]) -> Screenshot | None:
    """The light 390px screenshot, else the narrowest light one of at most 480px."""
    light = _light(screenshots)
    exact = [shot for shot in light if shot.viewport_width == MOBILE_WIDTH]
    if exact:
        return exact[0]
    narrow = [shot for shot in light if shot.viewport_width <= MAX_MOBILE_WIDTH]
    return min(narrow, key=lambda shot: shot.viewport_width, default=None)


def fit_image(image: Image.Image, shot: Screenshot, max_side: int) -> Image.Image:
    """Crop to the top MAX_SCREENS viewport heights, then shrink so the longest side fits."""
    scale = image.width / shot.viewport_width if shot.viewport_width > 0 else 1.0
    if shot.viewport_height > 0:
        max_height = max(1, round(MAX_SCREENS * shot.viewport_height * scale))
        if image.height > max_height:
            image = image.crop((0, 0, image.width, max_height))
    longest = max(image.size)
    if longest > max_side:
        factor = max_side / longest
        size = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
        image = image.resize(size, Image.Resampling.LANCZOS)
    if image.mode not in ("RGB", "RGBA", "L", "LA"):
        image = image.convert("RGBA")
    return image


def prepare_prompt_images(
    folder: Path, screenshots: Sequence[Screenshot], *, max_side: int = MAX_IMAGE_SIDE
) -> list[str]:
    """Copy the light desktop (1440) and mobile (390) screenshots into
    folder/prompt-images/, cropped to at most 3 screens tall and resized so the longest side
    is <= max_side (Pillow). Returns paths relative to `folder`; missing files are skipped."""
    written: list[str] = []
    for label, shot in (
        ("desktop", pick_desktop(screenshots)),
        ("mobile", pick_mobile(screenshots)),
    ):
        if shot is None:
            continue
        source = folder / shot.path
        if not source.is_file():
            continue
        try:
            with Image.open(source) as opened:
                opened.load()
                image = fit_image(opened, shot, max_side)
        except (OSError, ValueError, Image.DecompressionBombError):
            continue
        target_dir = folder / PROMPT_IMAGES_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        image.save(target_dir / f"{label}.png", format="PNG", optimize=True)
        written.append(f"{PROMPT_IMAGES_DIR}/{label}.png")
    return written


def _summary(facts: DesignFacts, phrases: Phrases) -> list[tuple[str, str]]:
    """(label, value) rows of the compact data summary."""
    none = phrases.get("prompt.summary_none")
    colors = ", ".join(f"{name} {token.value}" for name, token in facts.colors.items())
    levels = ", ".join(
        f"{name} {fmt_num(level.size_px)}px/{level.weight}" for name, level in facts.levels.items()
    )
    radii = ", ".join(f"{name} {facts.value(ROUNDED, name)}" for name in facts.radii)
    spacing = ", ".join(f"{name} {facts.value(SPACING, name)}" for name in facts.spacing)
    failing = "; ".join(
        f"{issue.foreground} / {issue.background or '—'} {fmt_ratio(issue.ratio)}"
        + (f" ({phrases.get('prompt.summary_dark')})" if issue.dark else "")
        + (f" — {issue.context}" if issue.context else "")
        for issue in facts.contrast_issues()
    )
    breakpoints = ", ".join(
        fmt_num(bp.min_width_px if bp.min_width_px is not None else bp.max_width_px or 0) + "px"
        for bp in facts.analysis.breakpoints
        if bp.min_width_px is not None or bp.max_width_px is not None
    )
    return [
        (phrases.get("prompt.summary_colors") + f" ({COLORS})", colors or none),
        (phrases.get("prompt.summary_type") + f" ({TYPOGRAPHY})", levels or none),
        (phrases.get("prompt.summary_rounded") + f" ({ROUNDED})", radii or none),
        (phrases.get("prompt.summary_spacing") + f" ({SPACING})", spacing or none),
        (phrases.get("prompt.summary_failing"), failing or none),
        (phrases.get("prompt.summary_breakpoints"), breakpoints or none),
    ]


def render_prompt_md(
    analysis: DesignAnalysis,
    design_md: str,
    image_paths: Sequence[str],
    *,
    lang: str = "en",
) -> str:
    """prompt.md: instructions ("rewrite the prose only, never change numbers, token names or
    references"), the rule-built DESIGN.md, a compact data summary and the image list."""
    phrases = load_phrases(lang)
    facts = DesignFacts(analysis)
    images = [
        (
            path,
            phrases.get(
                "prompt.image_mobile" if Path(path).stem == "mobile" else "prompt.image_desktop"
            ),
        )
        for path in image_paths
    ]
    rules = [
        phrases.get("prompt.rule_prose", language=phrases.get("prompt.language")),
        phrases.get("prompt.rule_values"),
        phrases.get("prompt.rule_screens"),
        phrases.get("prompt.rule_contrast"),
        phrases.get("prompt.rule_output"),
    ]
    text = render_template(
        TEMPLATE,
        title=phrases.get("prompt.title", site=site_label(analysis)),
        how_to=phrases.get("prompt.how_to"),
        ai_heading=phrases.get("prompt.ai_heading"),
        ai_intro=phrases.get("prompt.ai_intro", url=analysis.site.url),
        rules=rules,
        images_heading=phrases.get("prompt.images_heading"),
        images=images,
        no_images=phrases.get("prompt.no_images"),
        summary_heading=phrases.get("prompt.summary_heading"),
        summary=_summary(facts, phrases),
        draft_heading=phrases.get("prompt.draft_heading"),
        fence="`" * max(4, longest_backtick_run(design_md) + 1),
        design_md=design_md.rstrip("\n"),
    )
    # The embedded DESIGN.md is already normalized, so this only tidies the wrapper text.
    return normalize_markdown(text)
