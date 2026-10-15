"""Colors: normalization, CIEDE2000 clustering per usage channel, roles, semantic tokens,
WCAG contrast pairs.

PUBLIC ENTRY POINT (frozen signature, called by analyzer/analyze.py):
    analyze_colors(pages, stylesheets, threshold=...) -> ColorPalette

Pipeline (helpers live in the colors_* modules):
1. colors_samples — weighted samples per channel (background area, text length x size²,
   border length x width, gradient stops), overlays and invisible elements skipped.
2. colors_cluster — greedy CIEDE2000 clusters inside each channel; significant, visibly
   distinct colors (>= 2% of the channel, ΔE >= 1) never merge; alpha is kept.
3. colors_roles — canvas, surfaces, ink/muted/faint, hairlines, brand and status tokens
   (8–14 semantic names from naming.COLOR_TOKEN_ROLES, values are real used colors).
4. colors_contrast — WCAG pairs of every text color over its effective background.
"""

from __future__ import annotations

from collections.abc import Sequence

from get_design_markdown.analyzer.colors_cluster import Cluster, build_clusters, output_clusters
from get_design_markdown.analyzer.colors_contrast import contrast_pairs
from get_design_markdown.analyzer.colors_hints import collect_color_variables
from get_design_markdown.analyzer.colors_roles import assign_roles, choose_canvas
from get_design_markdown.analyzer.colors_samples import SampleSet, collect_samples
from get_design_markdown.analyzer.css_values import WHITE, to_oklch
from get_design_markdown.models.analysis import ColorChannel, ColorPalette
from get_design_markdown.models.raw import PageCapture, StylesheetInfo

#: Default CIEDE2000 merge threshold (spec: 3).
DEFAULT_DELTA_E = 3.0
#: Canvas OKLCH lightness under which the palette is a dark scheme.
DARK_SCHEME_LIGHTNESS = 0.5


def _cluster(
    pages: Sequence[PageCapture],
    stylesheets: Sequence[StylesheetInfo],
    threshold: float,
) -> tuple[SampleSet, dict[ColorChannel, list[Cluster]], Cluster | None]:
    variables = collect_color_variables(
        [variable for page in pages for variable in page.css_variables], stylesheets
    )
    samples = collect_samples(pages)
    clusters = build_clusters(samples, variables, threshold=threshold)
    canvas = choose_canvas(clusters["background"])
    if samples.rootless_pages and canvas is not None and canvas.rep != WHITE:
        # Pages without a sampled html/body: what shows behind transparent content is the
        # canvas, not the browser white assumed by the first pass.
        samples = collect_samples(pages, canvas_guess=canvas.rep)
        clusters = build_clusters(samples, variables, threshold=threshold)
        canvas = choose_canvas(clusters["background"])
    return samples, clusters, canvas


def analyze_colors(
    pages: Sequence[PageCapture],
    stylesheets: Sequence[StylesheetInfo] = (),
    *,
    threshold: float = DEFAULT_DELTA_E,
) -> ColorPalette:
    """Build the palette of one color scheme from the captured pages (pure function).

    Called once with RawCapture.pages (light, pages[0] = main page) and once with
    [RawCapture.dark] for the dark theme. ColorPalette.scheme must reflect the measured canvas
    lightness ("dark" when the canvas OKLCH lightness < 0.5), not the emulated media.
    """
    if not pages:
        return ColorPalette()
    threshold = max(float(threshold), 0.0)
    samples, clusters, canvas_cluster = _cluster(pages, stylesheets, threshold)
    if not any(samples.samples.values()):
        return ColorPalette()
    context, tokens = assign_roles(
        clusters,
        canvas_cluster=canvas_cluster,
        threshold=threshold,
        text_total=samples.text_weight_total,
    )
    canvas = context.canvas
    scheme = "dark" if to_oklch(canvas).lightness < DARK_SCHEME_LIGHTNESS else "light"
    pairs = contrast_pairs(samples.samples["text"], tokens)
    keep = [token.cluster_id for token in tokens if token.cluster_id]
    return ColorPalette(
        scheme=scheme,
        clusters=output_clusters(clusters, keep),
        tokens=tokens,
        contrast_pairs=pairs,
    )
