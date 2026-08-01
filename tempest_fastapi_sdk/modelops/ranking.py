"""Ranking measured models: composite cost score and Pareto frontier.

Two ways to answer "which model should we ship", kept side by side on
purpose.

The **composite score** collapses several cost axes into one number. That is
convenient and it is also an opinion: the weights encode a deployment
scenario. Publish them.

The **Pareto frontier** takes no opinion. A model is on it when nothing else
is at least as cheap on every axis *and* at least as good. What survives is
the set of defensible choices; picking among them is a product decision, not
a measurement.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from tempest_fastapi_sdk.modelops.schemas import (
    BenchmarkProfile,
    BenchmarkReport,
    ParetoPoint,
    StaticModelMetrics,
)

DEFAULT_COST_WEIGHTS: dict[str, float] = {
    "latency_ms_median": 0.40,
    "energy_per_inference_j": 0.25,
    "rss_peak_mb": 0.20,
    "disk_size_mb": 0.15,
}
"""Default composite-score weights, tuned for an edge/mobile deployment.

Latency dominates because it is what a user feels; energy and memory follow
because they are what a battery and a small device run out of; artifact size
matters because it gates the app download. A server deployment with a
throughput SLO should re-weight — that is the point of the parameter.
"""

_PARETO_COST_AXES: tuple[str, ...] = (
    "latency_ms",
    "energy_j",
    "gflops",
    "memory_mb",
    "disk_size_mb",
)
"""Axes on which lower is better when checking domination."""


def _extract(profile: BenchmarkProfile, dimension: str) -> float | None:
    """Read one named dimension off a profile.

    Runtime fields win over static ones, so ``disk_size_mb`` resolves on the
    static block while ``latency_ms_median`` resolves on the runtime block
    without the caller needing to know where each lives.

    Args:
        profile (BenchmarkProfile): Profile to read.
        dimension (str): Field name on the runtime or static block.

    Returns:
        float | None: The value, or ``None`` when it was never measured.

    Raises:
        ValueError: When no block declares that field, or when the field is
            not numeric — a typo in a weight key would otherwise silently
            drop a whole dimension.
    """
    if hasattr(profile.runtime, dimension):
        return _as_float(getattr(profile.runtime, dimension), dimension)
    if profile.static is not None and hasattr(profile.static, dimension):
        return _as_float(getattr(profile.static, dimension), dimension)
    if dimension in StaticModelMetrics.model_fields:
        return None
    raise ValueError(
        f"unknown ranking dimension {dimension!r}: not a field of "
        "RuntimeAggregate or StaticModelMetrics"
    )


def _as_float(value: object, dimension: str) -> float | None:
    """Coerce a measured field to a float for scoring.

    Args:
        value (object): The raw field value.
        dimension (str): Field name, for the error message.

    Returns:
        float | None: The numeric value, or ``None`` when unmeasured.

    Raises:
        ValueError: When the field holds something that cannot be ranked.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"ranking dimension {dimension!r} is not numeric: {value!r}")
    return float(value)


def _min_max(values: Sequence[float | None]) -> list[float | None]:
    """Scale the non-``None`` values into ``[0, 1]``.

    Args:
        values (Sequence[float | None]): Raw values, one per profile.

    Returns:
        list[float | None]: Scaled values, ``None`` preserved. When every
        present value is identical the dimension carries no information and
        every entry becomes ``0.0``.
    """
    present = [value for value in values if value is not None]
    if not present:
        return [None for _ in values]
    low, high = min(present), max(present)
    if high == low:
        return [None if value is None else 0.0 for value in values]
    span = high - low
    return [None if value is None else (value - low) / span for value in values]


def composite_scores(
    profiles: Sequence[BenchmarkProfile],
    *,
    weights: Mapping[str, float] | None = None,
) -> tuple[list[float | None], dict[str, float]]:
    """Score each profile on weighted, min-max-normalized cost. Lower wins.

    Two renormalizations keep a missing measurement from distorting the
    ranking:

    * A dimension that **no** profile measured is dropped entirely, and the
      remaining weights are rescaled to sum to 1. Benchmarking on a laptop
      with no GPU counter therefore compares latency, memory and size on
      their own terms instead of handing every model the same free 25%.
    * A dimension that **some** profile is missing is skipped for that
      profile only, and its score is divided by the weight it could
      actually be scored on. A model with no energy figure is neither
      rewarded nor punished for it.

    Args:
        profiles (Sequence[BenchmarkProfile]): Profiles to score. Scores are
            relative to this set — adding a model changes everyone's score.
        weights (Mapping[str, float] | None): Dimension to weight. Defaults
            to :data:`DEFAULT_COST_WEIGHTS`.

    Returns:
        tuple[list[float | None], dict[str, float]]: Scores in input order
        (``None`` when a profile had no measured dimension at all), and the
        effective weights after dropping empty dimensions.

    Raises:
        ValueError: When ``profiles`` is empty or a weight names a field
            that does not exist.

    Example:

        >>> from tempest_fastapi_sdk.modelops import composite_scores
        >>> scores, effective = composite_scores(profiles)
        >>> effective
    """
    if not profiles:
        raise ValueError("cannot score an empty profile list")
    requested = dict(weights or DEFAULT_COST_WEIGHTS)

    raw: dict[str, list[float | None]] = {
        dimension: [_extract(profile, dimension) for profile in profiles]
        for dimension in requested
    }
    usable = {
        dimension: weight
        for dimension, weight in requested.items()
        if any(value is not None for value in raw[dimension])
    }
    total = sum(usable.values())
    if not usable or total <= 0:
        return [None for _ in profiles], {}
    effective = {dimension: weight / total for dimension, weight in usable.items()}

    normalized = {dimension: _min_max(raw[dimension]) for dimension in effective}
    scores: list[float | None] = []
    for index in range(len(profiles)):
        weighted = 0.0
        covered = 0.0
        for dimension, weight in effective.items():
            value = normalized[dimension][index]
            if value is None:
                continue
            weighted += weight * value
            covered += weight
        scores.append(weighted / covered if covered > 0 else None)
    return scores, effective


def _to_point(profile: BenchmarkProfile) -> ParetoPoint:
    """Reduce a profile to the axes the Pareto comparison uses.

    Args:
        profile (BenchmarkProfile): Profile to project.

    Returns:
        ParetoPoint: Cost axes plus the caller-supplied quality.
    """
    runtime = profile.runtime
    memory = runtime.gpu_memory_peak_mb
    if memory is None:
        memory = runtime.rss_peak_mb
    return ParetoPoint(
        name=profile.name,
        latency_ms=runtime.latency_ms_median,
        energy_j=runtime.energy_per_inference_j,
        gflops=profile.static.gflops if profile.static else None,
        memory_mb=memory,
        disk_size_mb=profile.static.disk_size_mb if profile.static else None,
        quality=profile.quality,
    )


def _dominates(left: ParetoPoint, right: ParetoPoint) -> bool:
    """Return whether ``left`` dominates ``right``.

    Domination means: no worse on every comparable cost axis, no worse on
    quality, and strictly better on at least one. An axis where either side
    has no measurement is skipped rather than assumed — a model that was
    never energy-profiled is not thereby declared efficient.

    Args:
        left (ParetoPoint): Candidate dominator.
        right (ParetoPoint): Point being tested.

    Returns:
        bool: ``True`` when ``left`` dominates ``right``.
    """
    strictly_better = False
    comparable = False
    for axis in _PARETO_COST_AXES:
        a = getattr(left, axis)
        b = getattr(right, axis)
        if a is None or b is None:
            continue
        comparable = True
        if a > b:
            return False
        if a < b:
            strictly_better = True
    if left.quality is not None and right.quality is not None:
        comparable = True
        if left.quality < right.quality:
            return False
        if left.quality > right.quality:
            strictly_better = True
    return comparable and strictly_better


def pareto_points(profiles: Sequence[BenchmarkProfile]) -> list[ParetoPoint]:
    """Annotate every profile with Pareto-frontier membership.

    With no ``quality`` set on any profile this degrades to the pure-cost
    frontier, which is still useful — it tells you which models are simply
    never worth running — but it cannot tell you which one is *best*, since
    the cheapest model is by definition on it.

    Args:
        profiles (Sequence[BenchmarkProfile]): Profiles to compare.

    Returns:
        list[ParetoPoint]: One point per profile, in input order, with
        ``is_pareto`` set.

    Example:

        >>> from tempest_fastapi_sdk.modelops import pareto_points
        >>> [p.name for p in pareto_points(profiles) if p.is_pareto]
    """
    points = [_to_point(profile) for profile in profiles]
    return [
        point.model_copy(
            update={
                "is_pareto": not any(
                    _dominates(other, point)
                    for index, other in enumerate(points)
                    if index != position
                )
            }
        )
        for position, point in enumerate(points)
    ]


def rank(
    profiles: Sequence[BenchmarkProfile],
    *,
    weights: Mapping[str, float] | None = None,
    quality: Mapping[str, float] | None = None,
) -> BenchmarkReport:
    """Score, annotate and sort a set of measured profiles.

    Args:
        profiles (Sequence[BenchmarkProfile]): Profiles measured under the
            same conditions. Comparing runs from different machines or
            different repetition counts produces a ranking of the machines,
            not of the models.
        weights (Mapping[str, float] | None): Composite-score weights.
            Defaults to :data:`DEFAULT_COST_WEIGHTS`.
        quality (Mapping[str, float] | None): Model name to task quality,
            higher is better. Without it the Pareto frontier is cost-only.

    Returns:
        BenchmarkReport: Profiles sorted by composite score ascending
        (unscored last), Pareto annotation, effective weights and the host
        description.

    Raises:
        ValueError: When ``profiles`` is empty or a weight key is unknown.

    Example:

        >>> from tempest_fastapi_sdk.modelops import rank
        >>> report = rank(profiles, quality={"yolov8n": 0.80})
        >>> report.profiles[0].name
    """
    from tempest_fastapi_sdk.genai.hardware import probe_hardware

    scored = [
        profile.model_copy(
            update={"quality": (quality or {}).get(profile.name, profile.quality)}
        )
        for profile in profiles
    ]
    scores, effective = composite_scores(scored, weights=weights)
    points = pareto_points(scored)
    annotated = [
        profile.model_copy(
            update={
                "composite_score": scores[index],
                "is_pareto": points[index].is_pareto,
            }
        )
        for index, profile in enumerate(scored)
    ]
    annotated.sort(
        key=lambda profile: (
            profile.composite_score is None,
            profile.composite_score or 0.0,
        )
    )
    return BenchmarkReport(
        profiles=annotated,
        pareto=points,
        weights=effective,
        hardware=probe_hardware(),
    )


__all__: list[str] = [
    "DEFAULT_COST_WEIGHTS",
    "composite_scores",
    "pareto_points",
    "rank",
]
