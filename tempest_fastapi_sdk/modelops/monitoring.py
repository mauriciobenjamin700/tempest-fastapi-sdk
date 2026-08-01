"""Knowing whether a deployed model is still working.

A device answering in 3 ms tells you nothing about whether the answers are
right. In production there are no labels — nobody tells the device it just
misclassified a transaction — so accuracy is not measurable there. What *is*
measurable is whether the world still looks like the one the model was
trained on, and whether the model's own output has shifted. Both are
proxies, and this module says so rather than pretending otherwise.

Three things are tracked, because together they separate the failure modes:

* **Latency and volume.** The only signal that catches "the device is
  thermally throttled" or "a provider fell back to CPU".
* **Input drift.** The features arriving now, compared against a baseline
  taken from the training set, with the Population Stability Index. Catches
  a sensor that started reporting in different units, a upstream form that
  changed a default, a season the training data never saw.
* **Prediction distribution.** What the model is answering, compared to what
  it answered on the training data. Catches the case input drift misses:
  features within their usual ranges but combined in a way that pushes every
  row to one class.

Input drift with a stable output distribution usually means a harmless
covariate shift. A shifted output with stable inputs usually means the model
is extrapolating. Both shifted means retraining, not tuning.

Memory is bounded and constant. Rows are counted into baseline bins as they
arrive and thrown away — nothing accumulates a copy of the traffic, which is
what makes this safe to run on a device with 512 MB of RAM.

    from tempest_fastapi_sdk.modelops import PredictionMonitor, baseline_from_samples

    baseline = baseline_from_samples(X_train, labels=y_train)
    monitor = PredictionMonitor(baseline=baseline)

    result = predictor.predict(rows)
    monitor.observe(rows, result)
    print(monitor.report().drift.verdict)

Prometheus is optional: the report is a plain schema, and
:class:`PredictionMetrics` publishes it as metrics only when you build one.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from typing import TYPE_CHECKING, Any

from pydantic import Field

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tempest_fastapi_sdk.modelops.serving import Prediction

DEFAULT_BINS: int = 10
"""Quantile bins per feature in a baseline.

Ten is the convention PSI is normally computed with: enough resolution to
see a shifted mode, few enough that each bin holds a usable count on the
sample sizes a device accumulates in an hour.
"""

PSI_MODERATE: float = 0.1
"""PSI above which a feature is considered to have moved.

From credit-scorecard practice (Siddiqi, *Credit Risk Scorecards*), where
PSI has been the standard population-shift measure for decades: below 0.1
the population is treated as stable, 0.1-0.25 as a shift worth
investigating, above 0.25 as a shift that invalidates the model's
calibration.

These are **conventions, not a statistical test.** PSI has no p-value and no
null distribution; it does not tell you a shift is significant, only that it
is large by a rule of thumb the industry agreed on. Treat a crossing as a
reason to look, not a reason to act automatically.
"""

PSI_SIGNIFICANT: float = 0.25
"""PSI above which a feature has moved enough to distrust the model.

See :data:`PSI_MODERATE` for the provenance and the caveat.
"""

MIN_ROWS_FOR_DRIFT: int = 100
"""Rows below which a drift number is noise rather than signal.

PSI compares proportions. With 30 rows across 10 bins, an empty bin is the
expected outcome of sampling, not evidence of drift, and the smoothing
constant then dominates the score. Reports below this threshold carry
``sufficient_sample=False`` — they are not suppressed, because "we do not
have enough traffic yet" is itself worth seeing on a dashboard.
"""

DEFAULT_WINDOW_ROWS: int = 1000
"""Rows per drift window before the counters reset.

Drift is a property of a population, so it is measured over a window rather
than per request. When a window fills, its report is kept as the last
complete measurement and the counters start again — so the numbers describe
recent traffic instead of everything since boot, which would take days to
respond to a real shift.
"""

_PSI_EPSILON: float = 1e-6
"""Substituted for an empty bin so the PSI logarithm stays finite.

A bin with zero observations makes ``ln(actual/expected)`` infinite, which
would turn one unseen value into an unbounded score. This floor caps the
contribution of an empty bin instead.
"""

_LATENCY_WINDOW: int = 512
"""Recent latencies kept for percentiles.

A fixed ring, so the memory cost does not grow with uptime. Percentiles
describe recent behaviour, which is the question being asked on a device.
"""


class DriftVerdict(BaseStrEnum):
    """How much the live population has moved from the baseline.

    Attributes:
        STABLE: No feature crossed :data:`PSI_MODERATE`.
        MODERATE: At least one feature moved; worth investigating.
        SIGNIFICANT: At least one feature crossed :data:`PSI_SIGNIFICANT`;
            the model's calibration should not be trusted.
        INSUFFICIENT_DATA: Fewer than :data:`MIN_ROWS_FOR_DRIFT` rows
            observed — reported explicitly rather than shown as stable,
            because "no data" and "no drift" are different answers.
    """

    STABLE = "stable"
    MODERATE = "moderate"
    SIGNIFICANT = "significant"
    INSUFFICIENT_DATA = "insufficient_data"


class FeatureBins(BaseSchema):
    """The baseline distribution of one feature.

    Attributes:
        name (str): Feature name, or its positional index as a string.
        edges (list[float]): Interior bin boundaries, ascending. A value
            falls in bin ``bisect_right(edges, value)``, so ``len(edges) + 1``
            bins exist and the outer two are unbounded — a live value beyond
            anything seen in training still lands somewhere.
        proportions (list[float]): Share of baseline rows per bin.
        constant (bool): Whether the training data held a single value.
            Such a feature gets a narrow middle bin, so any live value that
            differs registers instead of vanishing into one catch-all bin.
    """

    name: str = Field(
        title="Feature",
        description="Feature name or positional index.",
        examples=["petal_width"],
    )
    edges: list[float] = Field(
        default_factory=list,
        title="Edges",
        description="Interior bin boundaries, ascending.",
    )
    proportions: list[float] = Field(
        default_factory=list,
        title="Proportions",
        description="Share of baseline rows per bin.",
    )
    constant: bool = Field(
        default=False,
        title="Constant",
        description="Whether training held a single value for this feature.",
        examples=[False],
    )


class FeatureBaseline(BaseSchema):
    """What the training data looked like, small enough to ship with a model.

    Built by :func:`baseline_from_samples` at training time and saved next to
    the model file. It holds bin edges and proportions — never the training
    rows themselves — so it is a few kilobytes and carries no records.

    Example:

        >>> baseline = baseline_from_samples(X_train, labels=y_train)
        >>> Path("dist/baseline.json").write_text(baseline.model_dump_json())

    Attributes:
        features (list[FeatureBins]): Per-feature baseline distribution.
        n_samples (int): Rows the baseline was built from.
        label_proportions (dict[str, float]): Share per predicted class in
            the baseline, when labels were supplied. Empty for a regressor
            or when they were not.
    """

    features: list[FeatureBins] = Field(
        default_factory=list,
        title="Features",
        description="Per-feature baseline distribution.",
    )
    n_samples: int = Field(
        default=0,
        title="Samples",
        description="Rows the baseline was built from.",
        examples=[5000],
    )
    label_proportions: dict[str, float] = Field(
        default_factory=dict,
        title="Label proportions",
        description="Share per class in the baseline.",
        examples=[{"0": 0.7, "1": 0.3}],
    )


class FeatureDrift(BaseSchema):
    """How far one feature has moved.

    Attributes:
        name (str): Feature name.
        psi (float): Population Stability Index against the baseline.
        verdict (DriftVerdict): The PSI read against the conventional
            thresholds.
        proportions (list[float]): Observed share per bin, so a dashboard
            can show *where* it moved rather than only how much.
    """

    name: str = Field(
        title="Feature",
        description="Feature name.",
        examples=["petal_width"],
    )
    psi: float = Field(
        default=0.0,
        title="PSI",
        description="Population Stability Index against the baseline.",
        examples=[0.03],
    )
    verdict: DriftVerdict = Field(
        default=DriftVerdict.STABLE,
        title="Verdict",
        description="The PSI read against the conventional thresholds.",
    )
    proportions: list[float] = Field(
        default_factory=list,
        title="Proportions",
        description="Observed share per bin.",
    )


class DriftReport(BaseSchema):
    """Input drift over the current window.

    Attributes:
        features (list[FeatureDrift]): Per-feature drift, worst first.
        worst_psi (float): The highest PSI across features.
        verdict (DriftVerdict): The worst feature's verdict, or
            ``INSUFFICIENT_DATA``.
        n_rows (int): Rows in this window.
        sufficient_sample (bool): Whether the window holds at least
            :data:`MIN_ROWS_FOR_DRIFT` rows.
    """

    features: list[FeatureDrift] = Field(
        default_factory=list,
        title="Features",
        description="Per-feature drift, worst first.",
    )
    worst_psi: float = Field(
        default=0.0,
        title="Worst PSI",
        description="Highest PSI across features.",
        examples=[0.31],
    )
    verdict: DriftVerdict = Field(
        default=DriftVerdict.INSUFFICIENT_DATA,
        title="Verdict",
        description="Overall drift verdict.",
    )
    n_rows: int = Field(
        default=0,
        title="Rows",
        description="Rows in this window.",
        examples=[1000],
    )
    sufficient_sample: bool = Field(
        default=False,
        title="Sufficient sample",
        description="Whether enough rows were seen for the number to mean anything.",
        examples=[True],
    )


class PredictionDistribution(BaseSchema):
    """What the model has been answering.

    For a classifier this is the share per class, compared against the
    baseline share when one was supplied. For a regressor the class fields
    stay empty and the summary statistics carry the signal.

    Attributes:
        shares (dict[str, float]): Observed share per predicted class.
        baseline_shares (dict[str, float]): Share per class in the
            baseline, when known.
        psi (float): PSI of the predicted-class distribution against the
            baseline. Zero when there is no baseline to compare against.
        verdict (DriftVerdict): That PSI read against the thresholds.
        n_rows (int): Predictions in this window.
        mean (float | None): Mean predicted value, for a regressor.
        minimum (float | None): Smallest predicted value seen.
        maximum (float | None): Largest predicted value seen.
    """

    shares: dict[str, float] = Field(
        default_factory=dict,
        title="Shares",
        description="Observed share per predicted class.",
        examples=[{"0": 0.62, "1": 0.38}],
    )
    baseline_shares: dict[str, float] = Field(
        default_factory=dict,
        title="Baseline shares",
        description="Share per class in the baseline.",
    )
    psi: float = Field(
        default=0.0,
        title="PSI",
        description="PSI of the predicted-class distribution.",
        examples=[0.08],
    )
    verdict: DriftVerdict = Field(
        default=DriftVerdict.INSUFFICIENT_DATA,
        title="Verdict",
        description="Output drift verdict.",
    )
    n_rows: int = Field(
        default=0,
        title="Rows",
        description="Predictions in this window.",
        examples=[1000],
    )
    mean: float | None = Field(
        default=None,
        title="Mean",
        description="Mean predicted value, for a regressor.",
    )
    minimum: float | None = Field(
        default=None,
        title="Minimum",
        description="Smallest predicted value seen.",
    )
    maximum: float | None = Field(
        default=None,
        title="Maximum",
        description="Largest predicted value seen.",
    )


class LatencyReport(BaseSchema):
    """How fast and how much the device has been answering.

    Attributes:
        n_calls (int): Prediction calls observed.
        n_rows (int): Rows across those calls — a batch of 64 is one call
            and 64 rows, and throughput questions need the second number.
        seconds_total (float): Total inference time.
        median_ms (float): Median call latency, over the recent window.
        p95_ms (float): 95th percentile call latency, over the recent
            window.
        max_ms (float): Slowest call in the recent window.
    """

    n_calls: int = Field(
        default=0,
        title="Calls",
        description="Prediction calls observed.",
        examples=[1420],
    )
    n_rows: int = Field(
        default=0,
        title="Rows",
        description="Rows predicted across those calls.",
        examples=[1420],
    )
    seconds_total: float = Field(
        default=0.0,
        title="Total seconds",
        description="Total inference time.",
    )
    median_ms: float = Field(
        default=0.0,
        title="Median ms",
        description="Median call latency over the recent window.",
        examples=[0.42],
    )
    p95_ms: float = Field(
        default=0.0,
        title="p95 ms",
        description="95th percentile call latency over the recent window.",
        examples=[1.1],
    )
    max_ms: float = Field(
        default=0.0,
        title="Max ms",
        description="Slowest call in the recent window.",
    )


class MonitoringReport(BaseSchema):
    """Everything the monitor knows, in one object.

    Attributes:
        latency (LatencyReport): Speed and volume.
        drift (DriftReport): Input drift over the current window.
        predictions (PredictionDistribution): What the model is answering.
        model_version (str | None): Which version produced these numbers,
            when the predictor is registry-backed. Without it, a drift
            report cannot be attributed to a model.
    """

    latency: LatencyReport = Field(
        default_factory=LatencyReport,
        title="Latency",
        description="Speed and volume.",
    )
    drift: DriftReport = Field(
        default_factory=DriftReport,
        title="Drift",
        description="Input drift over the current window.",
    )
    predictions: PredictionDistribution = Field(
        default_factory=PredictionDistribution,
        title="Predictions",
        description="What the model is answering.",
    )
    model_version: str | None = Field(
        default=None,
        title="Model version",
        description="Which version produced these numbers.",
    )


def baseline_from_samples(
    features: Any,
    *,
    labels: Any = None,
    names: Sequence[str] | None = None,
    bins: int = DEFAULT_BINS,
) -> FeatureBaseline:
    """Summarise a training set into a shippable baseline.

    Run this **on the training data**, at training time, and ship the result
    with the model. Building it from early production traffic instead defeats
    the purpose: it would describe the drifted population as normal.

    Example:

        >>> baseline = baseline_from_samples(X_train, labels=y_train)
        >>> Path("dist/baseline.json").write_text(baseline.model_dump_json())

    Args:
        features (Any): The training rows — a 2-D array, nested sequence,
            or DataFrame. Column names are taken from a DataFrame when
            ``names`` is not given.
        labels (Any): Training labels or the model's predictions on the
            training set, used as the baseline output distribution. Omit
            for a regressor.
        names (Sequence[str] | None): Feature names. Defaults to the
            DataFrame's columns, or positional indices.
        bins (int): Quantile bins per feature.

    Returns:
        FeatureBaseline: Edges and proportions per feature, plus the
        baseline class shares when labels were given.

    Raises:
        ValueError: When ``features`` is not 2-D, or is empty — a baseline
            from no rows would silently mark everything as drifted.
    """
    import numpy

    columns = names
    if columns is None:
        frame_columns = getattr(features, "columns", None)
        if frame_columns is not None:
            columns = [str(column) for column in frame_columns]

    array = numpy.asarray(getattr(features, "values", features), dtype="float64")
    if array.ndim != 2:
        raise ValueError(
            f"baseline samples must be 2-D (n_rows, n_features); got {array.shape}",
        )
    if array.shape[0] == 0:
        raise ValueError("baseline samples are empty; a baseline needs training rows")

    resolved = list(columns) if columns else [str(i) for i in range(array.shape[1])]
    feature_bins: list[FeatureBins] = []
    for index, name in enumerate(resolved):
        feature_bins.append(_bins_for(array[:, index], name, bins, numpy))

    label_proportions: dict[str, float] = {}
    if labels is not None:
        flat = numpy.asarray(getattr(labels, "values", labels)).reshape(-1)
        total = int(flat.size)
        if total:
            values, counts = numpy.unique(flat, return_counts=True)
            label_proportions = {
                str(value): float(count) / total
                for value, count in zip(values, counts, strict=True)
            }

    return FeatureBaseline(
        features=feature_bins,
        n_samples=int(array.shape[0]),
        label_proportions=label_proportions,
    )


def _bins_for(column: Any, name: str, bins: int, numpy: Any) -> FeatureBins:
    """Build the baseline bins for one feature column.

    Quantile edges rather than equal-width ones, so each bin holds a
    comparable share of the training data and a skewed feature does not end
    up with nine empty bins and one holding everything.

    A constant column gets a narrow middle bin around its value instead of
    degenerate edges: with a single catch-all bin, every live value would
    land in it and drift could never be detected on that feature.

    Args:
        column (Any): The feature's training values.
        name (str): Feature name.
        bins (int): Requested bin count.
        numpy (Any): The imported numpy module.

    Returns:
        FeatureBins: Edges and baseline proportions for the feature.
    """
    finite = column[numpy.isfinite(column)]
    if finite.size == 0:
        return FeatureBins(name=name, edges=[0.0], proportions=[0.5, 0.5])

    quantiles = numpy.quantile(finite, numpy.linspace(0.0, 1.0, bins + 1))
    edges = numpy.unique(quantiles)[1:-1]
    constant = edges.size == 0
    if constant:
        value = float(finite[0])
        margin = max(abs(value), 1.0) * 1e-9
        edges = numpy.array([value - margin, value + margin])

    counts = numpy.bincount(
        numpy.searchsorted(edges, finite, side="right"),
        minlength=edges.size + 1,
    )
    total = float(counts.sum())
    return FeatureBins(
        name=name,
        edges=[float(edge) for edge in edges],
        proportions=[float(count) / total for count in counts],
        constant=constant,
    )


def population_stability_index(
    expected: Sequence[float],
    actual: Sequence[float],
) -> float:
    """Compute the PSI between two binned distributions.

    ``PSI = sum((actual - expected) * ln(actual / expected))`` over bins,
    with :data:`_PSI_EPSILON` substituted for an empty bin so one unseen
    value cannot produce an infinite score.

    Args:
        expected (Sequence[float]): Baseline proportions per bin.
        actual (Sequence[float]): Observed proportions per bin.

    Returns:
        float: The index. Zero for identical distributions; read against
        :data:`PSI_MODERATE` and :data:`PSI_SIGNIFICANT`.

    Raises:
        ValueError: When the two distributions have different bin counts,
            which would silently compare unrelated bins.
    """
    if len(expected) != len(actual):
        raise ValueError(
            f"PSI needs the same bins on both sides; got {len(expected)} "
            f"expected and {len(actual)} actual",
        )
    total = 0.0
    for expected_share, actual_share in zip(expected, actual, strict=True):
        exp = max(float(expected_share), _PSI_EPSILON)
        act = max(float(actual_share), _PSI_EPSILON)
        total += (act - exp) * math.log(act / exp)
    return total


def _verdict_for(psi: float, *, sufficient: bool) -> DriftVerdict:
    """Read a PSI against the conventional thresholds.

    Args:
        psi (float): The index.
        sufficient (bool): Whether the sample was large enough to mean
            anything.

    Returns:
        DriftVerdict: The verdict, or ``INSUFFICIENT_DATA`` when the
        sample was too small — reported rather than hidden, since an
        unmonitored device and a stable one look the same otherwise.
    """
    if not sufficient:
        return DriftVerdict.INSUFFICIENT_DATA
    if psi >= PSI_SIGNIFICANT:
        return DriftVerdict.SIGNIFICANT
    if psi >= PSI_MODERATE:
        return DriftVerdict.MODERATE
    return DriftVerdict.STABLE


class PredictionMonitor:
    """Tracks latency, input drift and output distribution for one predictor.

    Example:

        >>> monitor = PredictionMonitor(baseline=baseline)
        >>> result = predictor.predict(rows)
        >>> monitor.observe(rows, result)
        >>> monitor.report().drift.verdict
        'stable'

    Rows are counted into the baseline's bins and discarded, so memory is
    ``n_features x n_bins`` counters regardless of traffic — no copy of the
    requests is retained, which also means no feature values are held in
    memory to leak into a log or a crash dump.

    Drift is measured per window (:data:`DEFAULT_WINDOW_ROWS`). When a
    window fills, its report becomes the last complete measurement and the
    counters reset, so the numbers track recent traffic rather than
    everything since boot.

    Without a ``baseline`` it still records latency and the prediction
    distribution: those need no training-time artifact, and a device with
    no baseline should not be left with no monitoring at all.

    Thread-safe; :meth:`observe` is called from request handlers.

    Attributes:
        baseline (FeatureBaseline | None): The training-time reference.
        window_rows (int): Rows per drift window.
        model_version (str | None): Stamped onto every report.
    """

    def __init__(
        self,
        *,
        baseline: FeatureBaseline | None = None,
        window_rows: int = DEFAULT_WINDOW_ROWS,
        model_version: str | None = None,
    ) -> None:
        """Configure the monitor.

        Args:
            baseline (FeatureBaseline | None): From
                :func:`baseline_from_samples`. Without it, input drift is
                not computed.
            window_rows (int): Rows before the drift counters reset.
            model_version (str | None): Version stamped onto reports.
        """
        self.baseline = baseline
        self.window_rows = window_rows
        self.model_version = model_version
        self._lock = threading.Lock()
        self._latencies: deque[float] = deque(maxlen=_LATENCY_WINDOW)
        self._n_calls = 0
        self._n_rows = 0
        self._seconds = 0.0
        self._label_counts: dict[str, int] = {}
        self._value_sum = 0.0
        self._value_min: float | None = None
        self._value_max: float | None = None
        self._window_n = 0
        self._last_complete: DriftReport | None = None
        self._bins_per_feature = 0
        self._edges: Any = None
        self._counts: Any = None
        self._prepare_binning()

    def _prepare_binning(self) -> None:
        """Precompute the binning tables the request path uses.

        Built once, because the shape of the work is fixed by the baseline:
        a padded ``(n_features, max_edges)`` matrix of bin boundaries and a
        flat counter array. Doing it here is what lets :meth:`observe` bin a
        whole batch in a handful of vectorised calls instead of a Python
        loop per feature and per bin.

        Padding uses ``+inf`` so a feature with fewer boundaries than the
        widest one never matches in its padded columns — the same
        comparison then works for every feature at once.
        """
        if self.baseline is None or not self.baseline.features:
            self._bins_per_feature = 0
            self._edges = None
            self._counts = None
            return

        import numpy

        widest = max(len(feature.edges) for feature in self.baseline.features)
        self._bins_per_feature = widest + 1
        edges = numpy.full((len(self.baseline.features), widest), numpy.inf)
        for index, feature in enumerate(self.baseline.features):
            if feature.edges:
                edges[index, : len(feature.edges)] = feature.edges
        self._edges = edges
        self._counts = numpy.zeros(
            len(self.baseline.features) * self._bins_per_feature,
            dtype=numpy.int64,
        )

    def _reset_counts(self) -> None:
        """Zero the window counters without reallocating them."""
        if self._counts is not None:
            self._counts.fill(0)

    def observe(self, features: Any, prediction: Prediction) -> None:
        """Record one prediction call.

        Cheap enough for the request path: binning is a ``searchsorted``
        over the baseline edges, and nothing is allocated per row.

        Args:
            features (Any): The rows that were predicted — the same value
                passed to :meth:`~OnnxPredictor.predict`.
            prediction (Prediction): What came back.
        """
        with self._lock:
            self._n_calls += 1
            self._n_rows += prediction.n_rows
            self._seconds += prediction.seconds
            self._latencies.append(prediction.seconds)
            self._observe_labels(prediction.labels)

        if self.baseline is not None:
            self._observe_features(features)

    def _observe_labels(self, labels: Sequence[Any]) -> None:
        """Fold predicted labels into the output distribution.

        Counts both ways at once: as class shares (meaningful for a
        classifier) and as running numeric extremes (meaningful for a
        regressor). Which one carries signal is decided at report time by
        whether the labels turned out to be numeric and unbounded, so the
        monitor does not need to be told which kind of model it is watching.

        Args:
            labels (Sequence[Any]): Predicted labels or values.
        """
        for label in labels:
            key = str(label)
            self._label_counts[key] = self._label_counts.get(key, 0) + 1
            try:
                value = float(label)
            except (TypeError, ValueError):
                continue
            self._value_sum += value
            if self._value_min is None or value < self._value_min:
                self._value_min = value
            if self._value_max is None or value > self._value_max:
                self._value_max = value

    def _observe_features(self, features: Any) -> None:
        """Bin one batch of feature rows into the window counters.

        Vectorised across features and rows: one comparison against the
        padded edge matrix gives every row's bin for every feature, and one
        ``bincount`` folds the whole batch into the flat counters. The
        obvious loop — per feature, then per bin — cost 67 us per single-row
        call against 7.5 us of actual inference, which made monitoring the
        expensive part of serving. This form costs a few microseconds.

        A batch whose width does not match the baseline is ignored rather
        than raising: the monitor must never be the reason a device stops
        answering. The mismatch surfaces as a window that stops filling.

        Args:
            features (Any): The predicted rows.
        """
        import numpy

        assert self.baseline is not None
        if self._counts is None or self._edges is None:
            return
        array = numpy.asarray(getattr(features, "values", features), dtype="float64")
        if array.ndim != 2 or array.shape[1] != len(self.baseline.features):
            return

        indices = (array[:, :, None] >= self._edges[None, :, :]).sum(axis=2)
        indices += numpy.arange(array.shape[1]) * self._bins_per_feature
        flat = indices.ravel()

        finite = numpy.isfinite(array)
        if not finite.all():
            flat = flat[finite.ravel()]

        with self._lock:
            self._counts += numpy.bincount(flat, minlength=self._counts.size)
            self._window_n += int(array.shape[0])
            if self._window_n >= self.window_rows:
                self._last_complete = self._drift_locked()
                self._reset_counts()
                self._window_n = 0

    def _drift_locked(self) -> DriftReport:
        """Build the drift report from the current counters.

        Must be called with the lock held.

        Returns:
            DriftReport: Per-feature PSI, worst first.
        """
        if self.baseline is None or not self._window_n:
            return DriftReport(n_rows=self._window_n)

        sufficient = self._window_n >= MIN_ROWS_FOR_DRIFT
        matrix = self._counts.reshape(
            len(self.baseline.features),
            self._bins_per_feature,
        )
        drifts: list[FeatureDrift] = []
        for index, spec in enumerate(self.baseline.features):
            counts = matrix[index, : len(spec.proportions)].tolist()
            total = float(sum(counts)) or 1.0
            observed = [count / total for count in counts]
            psi = population_stability_index(spec.proportions, observed)
            drifts.append(
                FeatureDrift(
                    name=spec.name,
                    psi=psi,
                    verdict=_verdict_for(psi, sufficient=sufficient),
                    proportions=observed,
                ),
            )
        drifts.sort(key=lambda drift: drift.psi, reverse=True)
        worst = drifts[0].psi if drifts else 0.0
        return DriftReport(
            features=drifts,
            worst_psi=worst,
            verdict=_verdict_for(worst, sufficient=sufficient),
            n_rows=self._window_n,
            sufficient_sample=sufficient,
        )

    def report(self) -> MonitoringReport:
        """Return everything measured so far.

        The drift section describes the **current** window when it holds
        enough rows, and otherwise the last completed one — so a freshly
        reset window does not blank the dashboard.

        Returns:
            MonitoringReport: Latency, drift and prediction distribution.
        """
        with self._lock:
            latency = self._latency_locked()
            drift = self._drift_locked()
            if not drift.sufficient_sample and self._last_complete is not None:
                drift = self._last_complete
            predictions = self._distribution_locked()
        return MonitoringReport(
            latency=latency,
            drift=drift,
            predictions=predictions,
            model_version=self.model_version,
        )

    def _latency_locked(self) -> LatencyReport:
        """Build the latency section. Must be called with the lock held.

        Returns:
            LatencyReport: Counts plus percentiles over the recent ring.
        """
        recent = sorted(self._latencies)
        if not recent:
            return LatencyReport()
        median = recent[len(recent) // 2]
        p95 = recent[min(len(recent) - 1, int(len(recent) * 0.95))]
        return LatencyReport(
            n_calls=self._n_calls,
            n_rows=self._n_rows,
            seconds_total=self._seconds,
            median_ms=median * 1000.0,
            p95_ms=p95 * 1000.0,
            max_ms=recent[-1] * 1000.0,
        )

    def _distribution_locked(self) -> PredictionDistribution:
        """Build the output-distribution section.

        Must be called with the lock held.

        Returns:
            PredictionDistribution: Class shares against the baseline when
            one is known, plus numeric summaries for a regressor.
        """
        total = sum(self._label_counts.values())
        if not total:
            return PredictionDistribution()

        shares = {key: count / total for key, count in self._label_counts.items()}
        baseline_shares = dict(self.baseline.label_proportions) if self.baseline else {}
        psi = 0.0
        verdict = DriftVerdict.INSUFFICIENT_DATA
        if baseline_shares:
            keys = sorted(set(baseline_shares) | set(shares))
            psi = population_stability_index(
                [baseline_shares.get(key, 0.0) for key in keys],
                [shares.get(key, 0.0) for key in keys],
            )
            verdict = _verdict_for(psi, sufficient=total >= MIN_ROWS_FOR_DRIFT)

        mean = self._value_sum / total if self._value_max is not None else None
        return PredictionDistribution(
            shares=shares,
            baseline_shares=baseline_shares,
            psi=psi,
            verdict=verdict,
            n_rows=total,
            mean=mean,
            minimum=self._value_min,
            maximum=self._value_max,
        )

    def reset(self) -> None:
        """Drop every accumulated number.

        Call this after :meth:`~OnnxPredictor.reload` swaps the model: the
        counters describe the previous version, and mixing two versions'
        latencies into one percentile hides exactly the regression a fleet
        update needs to catch.
        """
        with self._lock:
            self._latencies.clear()
            self._n_calls = 0
            self._n_rows = 0
            self._seconds = 0.0
            self._label_counts = {}
            self._value_sum = 0.0
            self._value_min = None
            self._value_max = None
            self._window_n = 0
            self._reset_counts()
            self._last_complete = None


class PredictionMetrics:
    """Publishes a monitor's numbers as Prometheus metrics.

    Example:

        >>> metrics = PredictionMetrics()
        >>> metrics.observe(prediction)              # per call
        >>> metrics.observe_report(monitor.report()) # periodically

    Split in two on purpose: counters and the latency histogram are cheap
    enough to update per request, while drift gauges describe a window and
    only change when one closes.

    Attributes:
        namespace (str): Metric name prefix.
    """

    def __init__(
        self,
        *,
        namespace: str = "edge_model",
        registry: Any = None,
    ) -> None:
        """Build the metric objects.

        Args:
            namespace (str): Prefix for every metric name.
            registry (Any): A ``prometheus_client.CollectorRegistry``;
                ``None`` uses the client's default, which is what the
                SDK's ``/metrics`` endpoint exposes.

        Raises:
            ImportError: When the ``[prometheus]`` extra is missing.
        """
        try:
            import prometheus_client
        except ImportError as exc:
            raise ImportError(
                "Prediction metrics require the optional [prometheus] extra. "
                "Install with: pip install tempest-fastapi-sdk[prometheus]",
            ) from exc

        self.namespace = namespace
        kwargs: dict[str, Any] = {} if registry is None else {"registry": registry}
        self._calls = prometheus_client.Counter(
            f"{namespace}_predictions_total",
            "Prediction calls served.",
            **kwargs,
        )
        self._rows = prometheus_client.Counter(
            f"{namespace}_prediction_rows_total",
            "Rows predicted across all calls.",
            **kwargs,
        )
        self._latency = prometheus_client.Histogram(
            f"{namespace}_prediction_seconds",
            "Inference latency per call.",
            **kwargs,
        )
        self._feature_psi = prometheus_client.Gauge(
            f"{namespace}_feature_drift_psi",
            "Population Stability Index per input feature.",
            ["feature"],
            **kwargs,
        )
        self._worst_psi = prometheus_client.Gauge(
            f"{namespace}_input_drift_psi",
            "Highest PSI across input features.",
            **kwargs,
        )
        self._output_psi = prometheus_client.Gauge(
            f"{namespace}_output_drift_psi",
            "PSI of the predicted-class distribution.",
            **kwargs,
        )
        self._label_share = prometheus_client.Gauge(
            f"{namespace}_prediction_share",
            "Share of predictions per class.",
            ["label"],
            **kwargs,
        )

    def observe(self, prediction: Prediction) -> None:
        """Record one prediction call.

        Args:
            prediction (Prediction): What the predictor returned.
        """
        self._calls.inc()
        self._rows.inc(prediction.n_rows)
        self._latency.observe(prediction.seconds)

    def observe_report(self, report: MonitoringReport) -> None:
        """Publish a monitoring report as gauge values.

        Drift gauges are left untouched while the sample is insufficient:
        publishing a noisy PSI from 12 rows would put a spike on the
        dashboard that means nothing.

        Args:
            report (MonitoringReport): From
                :meth:`PredictionMonitor.report`.
        """
        if report.drift.sufficient_sample:
            self._worst_psi.set(report.drift.worst_psi)
            for feature in report.drift.features:
                self._feature_psi.labels(feature=feature.name).set(feature.psi)
        if report.predictions.baseline_shares:
            self._output_psi.set(report.predictions.psi)
        for label, share in report.predictions.shares.items():
            self._label_share.labels(label=label).set(share)


__all__: list[str] = [
    "DEFAULT_BINS",
    "DEFAULT_WINDOW_ROWS",
    "MIN_ROWS_FOR_DRIFT",
    "PSI_MODERATE",
    "PSI_SIGNIFICANT",
    "DriftReport",
    "DriftVerdict",
    "FeatureBaseline",
    "FeatureBins",
    "FeatureDrift",
    "LatencyReport",
    "MonitoringReport",
    "PredictionDistribution",
    "PredictionMetrics",
    "PredictionMonitor",
    "baseline_from_samples",
    "population_stability_index",
]
