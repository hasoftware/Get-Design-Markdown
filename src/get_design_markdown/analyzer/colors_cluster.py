"""CIEDE2000 clustering of color samples, one usage channel at a time.

Colors are never merged across channels (a border is not a background, even when ΔE < 3), and
alpha is kept: #ffffff and #ffffff14 are different colors.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from get_design_markdown.analyzer.colors_hints import ColorVariable, is_generated_name
from get_design_markdown.analyzer.colors_samples import ColorSample, SampleSet
from get_design_markdown.analyzer.common import short_selector
from get_design_markdown.analyzer.css_values import OKLCH, RGBA, delta_e_2000, to_oklch
from get_design_markdown.models.analysis import ColorChannel, ColorCluster, ColorUsage

CHANNELS: tuple[ColorChannel, ...] = ("background", "text", "border", "gradient")
CHANNEL_PREFIX: dict[ColorChannel, str] = {
    "background": "bg",
    "text": "text",
    "border": "border",
    "gradient": "gradient",
}
#: Colors whose alpha differs more than this never merge.
MAX_ALPHA_DIFF = 0.1
#: A color carrying this share of its channel stays separate when it is visibly distinct...
SIGNIFICANT_SHARE = 0.02
#: ...that is, at least this far (ΔE2000) from the cluster it would join.
DISTINCT_DELTA_E = 1.0
#: A CSS variable names a cluster when its color is this close (ΔE2000).
VARIABLE_DELTA_E = 1.0
MAX_VARIABLES_PER_CLUSTER = 8
#: Unique colors clustered one by one per channel; the long tail is bucketed first.
MAX_UNIQUE_COLORS = 400
MAX_TAIL_BUCKETS = 400
TAIL_QUANTUM = 8
MAX_MEMBERS = 12
MAX_SAMPLES = 3
#: Clusters written to analysis.json per channel (token clusters are always kept).
MAX_CLUSTERS_PER_CHANNEL = 40
#: Tiny decorative shapes: smaller than this share of the page and at least this chromatic.
BLOB_MAX_PAGE_SHARE = 0.002
BLOB_MIN_CHROMA = 0.1


@dataclass(slots=True)
class _Unique:
    color: RGBA
    weight: float
    samples: list[ColorSample]
    members: dict[str, float]


@dataclass(slots=True)
class Cluster:
    """A cluster under construction (internal; converted to ColorCluster at the end)."""

    channel: ColorChannel
    rep: RGBA
    weight: float = 0.0
    members: dict[str, float] = field(default_factory=dict)
    samples: list[ColorSample] = field(default_factory=list)
    id: str = ""
    share: float = 0.0
    oklch: OKLCH = field(default_factory=lambda: OKLCH(0.0, 0.0, None))
    variables: list[ColorVariable] = field(default_factory=list)
    usage: dict[ColorChannel, float] = field(default_factory=dict)
    decorative: bool = False

    def absorb(self, unique: _Unique) -> None:
        self.weight += unique.weight
        self.samples.extend(unique.samples)
        for member, weight in unique.members.items():
            self.members[member] = self.members.get(member, 0.0) + weight

    @property
    def element_keys(self) -> set[tuple[int, int]]:
        return {sample.key for sample in self.samples}

    @property
    def count(self) -> int:
        return len(self.element_keys)

    def heaviest_samples(self) -> list[ColorSample]:
        return sorted(self.samples, key=lambda sample: -sample.weight)

    def to_model(self) -> ColorCluster:
        selectors: list[str] = []
        for sample in self.heaviest_samples():
            selector = short_selector(sample.ref.element)
            if selector not in selectors:
                selectors.append(selector)
            if len(selectors) >= MAX_SAMPLES:
                break
        members = sorted(self.members.items(), key=lambda item: (-item[1], item[0]))
        return ColorCluster(
            id=self.id,
            channel=self.channel,
            hex=self.rep.css_hex,
            alpha=self.rep.alpha,
            weight=round(self.weight, 1),
            share=round(min(max(self.share, 0.0), 1.0), 4),
            count=self.count,
            usage=ColorUsage(
                text=round(self.usage.get("text", 0.0), 1),
                background=round(self.usage.get("background", 0.0), 1),
                border=round(self.usage.get("border", 0.0), 1),
                gradient=round(self.usage.get("gradient", 0.0), 1),
            ),
            decorative=self.decorative,
            lightness=self.oklch.lightness,
            chroma=self.oklch.chroma,
            hue=self.oklch.hue,
            css_variables=[variable.name for variable in self.variables],
            members=[member for member, _ in members[:MAX_MEMBERS]],
            samples=selectors,
        )


def close_alpha(first: RGBA, second: RGBA) -> bool:
    return abs(first.alpha - second.alpha) <= MAX_ALPHA_DIFF + 1e-9


def _nearest(color: RGBA, clusters: Sequence[Cluster]) -> tuple[Cluster | None, float]:
    best: Cluster | None = None
    best_distance = float("inf")
    for cluster in clusters:
        if not close_alpha(color, cluster.rep):
            continue
        distance = delta_e_2000(color, cluster.rep)
        if distance < best_distance:
            best, best_distance = cluster, distance
    return best, best_distance


def _uniques(samples: Iterable[ColorSample]) -> list[_Unique]:
    groups: dict[str, _Unique] = {}
    for sample in samples:
        key = sample.color.css_hex
        unique = groups.get(key)
        if unique is None:
            unique = groups[key] = _Unique(sample.color, 0.0, [], {key: 0.0})
        unique.weight += sample.weight
        unique.members[key] += sample.weight
        unique.samples.append(sample)
    return sorted(groups.values(), key=lambda item: (-item.weight, item.color.css_hex))


def _bucket_tail(tail: Sequence[_Unique]) -> list[_Unique]:
    """Merge the long tail of rare colors into coarse RGB/alpha buckets."""
    buckets: dict[tuple[int, int, int, int], _Unique] = {}
    for unique in tail:
        color = unique.color
        key = (
            color.r // TAIL_QUANTUM,
            color.g // TAIL_QUANTUM,
            color.b // TAIL_QUANTUM,
            round(color.alpha * 10),
        )
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = _Unique(color, unique.weight, list(unique.samples), dict(unique.members))
            continue
        bucket.weight += unique.weight
        bucket.samples.extend(unique.samples)
        for member, weight in unique.members.items():
            bucket.members[member] = bucket.members.get(member, 0.0) + weight
    return sorted(buckets.values(), key=lambda item: (-item.weight, item.color.css_hex))


def cluster_channel(
    samples: Sequence[ColorSample], channel: ColorChannel, *, threshold: float
) -> list[Cluster]:
    """Greedy CIEDE2000 clustering of one channel, heaviest colors first.

    A color joins the nearest cluster (similar alpha) when ΔE2000 < threshold, unless the color
    is significant (>= 2% of the channel weight) and visibly distinct (ΔE >= 1) from it. The
    representative of a cluster is its heaviest member.
    """
    uniques = _uniques(samples)
    total = sum(unique.weight for unique in uniques)
    head = uniques[:MAX_UNIQUE_COLORS]
    buckets = _bucket_tail(uniques[MAX_UNIQUE_COLORS:])
    clusters: list[Cluster] = []
    for position, unique in enumerate([*head, *buckets]):
        best, distance = _nearest(unique.color, clusters)
        significant = total > 0 and unique.weight >= SIGNIFICANT_SHARE * total
        joins = (
            best is not None
            and distance < threshold
            and not (significant and distance >= DISTINCT_DELTA_E)
        )
        if best is not None and joins:
            best.absorb(unique)
        elif position < len(head) + MAX_TAIL_BUCKETS:
            cluster = Cluster(channel, unique.color)
            cluster.absorb(unique)
            clusters.append(cluster)
    clusters.sort(key=lambda cluster: (-cluster.weight, cluster.rep.css_hex))
    for number, cluster in enumerate(clusters, start=1):
        cluster.id = f"{CHANNEL_PREFIX[channel]}-{number}"
        cluster.share = cluster.weight / total if total > 0 else 0.0
        cluster.oklch = to_oklch(cluster.rep)
    return clusters


def _is_decorative_sample(sample: ColorSample, chroma: float, page_areas: Sequence[float]) -> bool:
    element = sample.ref.element
    if sample.channel == "text":
        # Text is decorative only as one layer of a stacked, blended copy of the same text.
        return sample.traits.layered_text
    if sample.synthetic is not None:
        return False
    if element.has_own_text or sample.traits.interactive:
        return False
    if sample.traits.hides_background:
        return True
    page_area = page_areas[sample.ref.page_index] if sample.ref.page_index < len(page_areas) else 0
    return (
        page_area > 0
        and sample.traits.area < BLOB_MAX_PAGE_SHARE * page_area
        and chroma >= BLOB_MIN_CHROMA
        and sample.traits.textless
    )


def build_clusters(
    sample_set: SampleSet,
    variables: Sequence[ColorVariable],
    *,
    threshold: float,
) -> dict[ColorChannel, list[Cluster]]:
    """Cluster every channel, then attach cross-channel usage, CSS variables and the
    decorative flag."""
    result: dict[ColorChannel, list[Cluster]] = {
        channel: cluster_channel(sample_set.samples[channel], channel, threshold=threshold)
        for channel in CHANNELS
    }
    everything = [cluster for channel in CHANNELS for cluster in result[channel]]
    for cluster in everything:
        usage: dict[ColorChannel, float] = {}
        for other in everything:
            if not close_alpha(cluster.rep, other.rep):
                continue
            if other is cluster or delta_e_2000(cluster.rep, other.rep) < threshold:
                usage[other.channel] = usage.get(other.channel, 0.0) + other.weight
        cluster.usage = usage
        matched: list[ColorVariable] = []
        for variable in variables:
            if (
                close_alpha(variable.color, cluster.rep)
                and delta_e_2000(variable.color, cluster.rep) < VARIABLE_DELTA_E
                and all(item.name != variable.name for item in matched)
            ):
                matched.append(variable)
        # Variables holding exactly the representative first, then role-hinting names (what
        # token selection reads), then the rest; machine-generated names last.
        rep_hex = cluster.rep.css_hex
        matched.sort(
            key=lambda variable: (
                0 if variable.color.css_hex == rep_hex else 1,
                0 if variable.hints else 1,
                1 if is_generated_name(variable.name) else 0,
            )
        )
        cluster.variables = matched[:MAX_VARIABLES_PER_CLUSTER]
        if cluster.channel == "gradient":
            cluster.decorative = True
        else:
            cluster.decorative = bool(cluster.samples) and all(
                _is_decorative_sample(sample, cluster.oklch.chroma, sample_set.page_areas)
                for sample in cluster.samples
            )
    return result


def output_clusters(
    clusters: dict[ColorChannel, list[Cluster]], keep_ids: Iterable[str]
) -> list[ColorCluster]:
    """ColorCluster models in channel order, heaviest first, capped per channel."""
    keep = set(keep_ids)
    models: list[ColorCluster] = []
    for channel in CHANNELS:
        for position, cluster in enumerate(clusters[channel]):
            if position < MAX_CLUSTERS_PER_CHANNEL or cluster.id in keep:
                models.append(cluster.to_model())
    return models
