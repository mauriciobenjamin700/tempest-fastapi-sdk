"""Tests for edge monitoring: latency, input drift and output distribution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.modelops.monitoring import (
    MIN_ROWS_FOR_DRIFT,
    PSI_MODERATE,
    PSI_SIGNIFICANT,
    DriftVerdict,
    FeatureBaseline,
    PredictionMonitor,
    baseline_from_samples,
    population_stability_index,
)
from tempest_fastapi_sdk.modelops.router import make_prediction_router
from tempest_fastapi_sdk.modelops.serving import OnnxPredictor, Prediction

numpy = pytest.importorskip("numpy")


def _normal(rows: int, seed: int, *, loc: float = 0.0, cols: int = 3) -> Any:
    """Return a reproducible normal sample.

    Args:
        rows (int): Row count.
        seed (int): Generator seed.
        loc (float): Distribution mean.
        cols (int): Feature count.

    Returns:
        Any: The sample array.
    """
    return numpy.random.RandomState(seed).normal(loc=loc, size=(rows, cols))


@pytest.fixture
def baseline() -> FeatureBaseline:
    """A baseline from 2000 standard-normal rows with balanced labels."""
    labels = numpy.array([0, 1] * 1000)
    return baseline_from_samples(_normal(2000, 0), labels=labels)


def _prediction(labels: list[Any], *, seconds: float = 0.001) -> Prediction:
    """Build a prediction result for a batch.

    Args:
        labels (list[Any]): Predicted labels.
        seconds (float): Reported duration.

    Returns:
        Prediction: The result object.
    """
    return Prediction(labels=labels, n_rows=len(labels), seconds=seconds)


class TestPsi:
    def test_identical_distributions_score_zero(self) -> None:
        assert population_stability_index([0.5, 0.5], [0.5, 0.5]) == 0.0

    def test_it_grows_with_the_shift(self) -> None:
        small = population_stability_index([0.5, 0.5], [0.45, 0.55])
        large = population_stability_index([0.5, 0.5], [0.1, 0.9])
        assert 0 < small < large

    def test_an_empty_bin_stays_finite(self) -> None:
        """One unseen value must not produce an unbounded score."""
        value = population_stability_index([0.5, 0.5], [1.0, 0.0])
        assert numpy.isfinite(value)

    def test_mismatched_bins_are_refused(self) -> None:
        with pytest.raises(ValueError, match="same bins"):
            population_stability_index([0.5, 0.5], [0.3, 0.3, 0.4])

    def test_the_thresholds_are_the_documented_convention(self) -> None:
        """Pinned: the docs state these values and their provenance."""
        assert PSI_MODERATE == 0.1
        assert PSI_SIGNIFICANT == 0.25


class TestBaseline:
    def test_it_summarises_without_keeping_rows(self) -> None:
        base = baseline_from_samples(_normal(500, 1))
        assert base.n_samples == 500
        assert len(base.features) == 3
        for feature in base.features:
            assert abs(sum(feature.proportions) - 1.0) < 1e-9
            assert len(feature.proportions) == len(feature.edges) + 1

    def test_it_names_features_from_the_caller(self) -> None:
        base = baseline_from_samples(_normal(200, 2), names=["a", "b", "c"])
        assert [feature.name for feature in base.features] == ["a", "b", "c"]

    def test_it_defaults_to_positional_names(self) -> None:
        base = baseline_from_samples(_normal(200, 2))
        assert [feature.name for feature in base.features] == ["0", "1", "2"]

    def test_it_records_label_proportions(self) -> None:
        labels = numpy.array([0] * 150 + [1] * 50)
        base = baseline_from_samples(_normal(200, 3), labels=labels)
        assert base.label_proportions == {"0": 0.75, "1": 0.25}

    def test_a_constant_feature_still_gets_usable_bins(self) -> None:
        """A single catch-all bin would make drift undetectable there."""
        rows = numpy.ones((300, 1))
        base = baseline_from_samples(rows)
        assert base.features[0].constant is True

        monitor = PredictionMonitor(baseline=base, window_rows=10_000)
        monitor.observe(numpy.full((200, 1), 5.0), _prediction([0] * 200))
        assert monitor.report().drift.verdict == DriftVerdict.SIGNIFICANT

    def test_empty_samples_are_refused(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            baseline_from_samples(numpy.zeros((0, 3)))

    def test_one_dimensional_samples_are_refused(self) -> None:
        with pytest.raises(ValueError, match="2-D"):
            baseline_from_samples(numpy.zeros(10))

    def test_it_round_trips_as_json(self) -> None:
        """The baseline ships next to the model, so it must serialise."""
        base = baseline_from_samples(_normal(300, 4))
        restored = FeatureBaseline.model_validate_json(base.model_dump_json())
        assert restored.features[0].edges == base.features[0].edges


class TestInputDrift:
    def test_the_same_distribution_reads_stable(
        self,
        baseline: FeatureBaseline,
    ) -> None:
        monitor = PredictionMonitor(baseline=baseline, window_rows=10_000)
        monitor.observe(_normal(500, 5), _prediction([0] * 500))
        report = monitor.report().drift
        assert report.verdict == DriftVerdict.STABLE
        assert report.worst_psi < PSI_MODERATE

    def test_a_shifted_distribution_reads_significant(
        self,
        baseline: FeatureBaseline,
    ) -> None:
        monitor = PredictionMonitor(baseline=baseline, window_rows=10_000)
        monitor.observe(_normal(500, 6, loc=2.0), _prediction([0] * 500))
        report = monitor.report().drift
        assert report.verdict == DriftVerdict.SIGNIFICANT
        assert report.worst_psi > PSI_SIGNIFICANT

    def test_features_are_ranked_worst_first(
        self,
        baseline: FeatureBaseline,
    ) -> None:
        rows = _normal(500, 7)
        rows[:, 1] += 3.0
        monitor = PredictionMonitor(baseline=baseline, window_rows=10_000)
        monitor.observe(rows, _prediction([0] * 500))
        features = monitor.report().drift.features
        assert features[0].name == "1"
        assert features[0].psi > features[-1].psi

    def test_a_small_sample_says_so_instead_of_saying_stable(
        self,
        baseline: FeatureBaseline,
    ) -> None:
        monitor = PredictionMonitor(baseline=baseline, window_rows=10_000)
        monitor.observe(_normal(MIN_ROWS_FOR_DRIFT - 1, 8), _prediction([0] * 99))
        report = monitor.report().drift
        assert report.sufficient_sample is False
        assert report.verdict == DriftVerdict.INSUFFICIENT_DATA

    def test_a_closed_window_survives_the_reset(
        self,
        baseline: FeatureBaseline,
    ) -> None:
        """A freshly reset window must not blank the dashboard."""
        monitor = PredictionMonitor(baseline=baseline, window_rows=200)
        monitor.observe(_normal(200, 9, loc=2.0), _prediction([0] * 200))
        report = monitor.report().drift
        assert report.n_rows == 200
        assert report.verdict == DriftVerdict.SIGNIFICANT

    def test_memory_does_not_grow_with_traffic(
        self,
        baseline: FeatureBaseline,
    ) -> None:
        monitor = PredictionMonitor(baseline=baseline, window_rows=100)
        for seed in range(20):
            monitor.observe(_normal(100, seed), _prediction([0] * 100))
        expected = len(baseline.features) * monitor._bins_per_feature
        assert monitor._counts.size == expected
        assert monitor._counts.nbytes < 4096

    def test_a_mismatched_width_never_breaks_serving(
        self,
        baseline: FeatureBaseline,
    ) -> None:
        """The monitor must never be the reason a device stops answering."""
        monitor = PredictionMonitor(baseline=baseline, window_rows=10_000)
        monitor.observe(numpy.zeros((10, 7)), _prediction([0] * 10))
        assert monitor.report().latency.n_calls == 1

    def test_without_a_baseline_it_still_records_latency(self) -> None:
        monitor = PredictionMonitor()
        monitor.observe(_normal(10, 10), _prediction([0] * 10))
        report = monitor.report()
        assert report.latency.n_calls == 1
        assert report.drift.verdict == DriftVerdict.INSUFFICIENT_DATA


class TestOutputDistribution:
    def test_it_reports_class_shares(self, baseline: FeatureBaseline) -> None:
        monitor = PredictionMonitor(baseline=baseline, window_rows=10_000)
        monitor.observe(_normal(200, 11), _prediction([0] * 150 + [1] * 50))
        distribution = monitor.report().predictions
        assert distribution.shares == {"0": 0.75, "1": 0.25}
        assert distribution.baseline_shares == {"0": 0.5, "1": 0.5}

    def test_a_collapsed_output_is_flagged_even_with_stable_inputs(
        self,
        baseline: FeatureBaseline,
    ) -> None:
        """The failure input drift alone would miss."""
        monitor = PredictionMonitor(baseline=baseline, window_rows=10_000)
        monitor.observe(_normal(500, 12), _prediction([1] * 500))
        report = monitor.report()
        assert report.drift.verdict == DriftVerdict.STABLE
        assert report.predictions.verdict == DriftVerdict.SIGNIFICANT

    def test_a_regressor_gets_summary_statistics(self) -> None:
        monitor = PredictionMonitor()
        monitor.observe(_normal(3, 13), _prediction([1.5, 2.5, 3.5]))
        distribution = monitor.report().predictions
        assert distribution.minimum == 1.5
        assert distribution.maximum == 3.5
        assert abs((distribution.mean or 0.0) - 2.5) < 1e-9

    def test_without_baseline_labels_no_verdict_is_invented(self) -> None:
        monitor = PredictionMonitor(baseline=baseline_from_samples(_normal(200, 14)))
        monitor.observe(_normal(200, 15), _prediction([1] * 200))
        distribution = monitor.report().predictions
        assert distribution.psi == 0.0
        assert distribution.verdict == DriftVerdict.INSUFFICIENT_DATA


class TestLatency:
    def test_it_counts_calls_and_rows_separately(self) -> None:
        monitor = PredictionMonitor()
        monitor.observe(_normal(64, 16), _prediction([0] * 64))
        monitor.observe(_normal(64, 17), _prediction([0] * 64))
        latency = monitor.report().latency
        assert latency.n_calls == 2
        assert latency.n_rows == 128

    def test_percentiles_are_ordered(self) -> None:
        monitor = PredictionMonitor()
        for index in range(100):
            monitor.observe(
                _normal(1, index),
                _prediction([0], seconds=(index + 1) / 1000.0),
            )
        latency = monitor.report().latency
        assert latency.median_ms <= latency.p95_ms <= latency.max_ms

    def test_reset_clears_everything(self, baseline: FeatureBaseline) -> None:
        monitor = PredictionMonitor(baseline=baseline, window_rows=10_000)
        monitor.observe(_normal(500, 18, loc=2.0), _prediction([1] * 500))
        monitor.reset()
        report = monitor.report()
        assert report.latency.n_calls == 0
        assert report.predictions.shares == {}
        assert report.drift.verdict == DriftVerdict.INSUFFICIENT_DATA


class TestMonitoredRouter:
    @pytest.fixture
    def model_path(self, tmp_path: Path) -> Path:
        """Export a small classifier to serve."""
        pytest.importorskip("sklearn")
        pytest.importorskip("skl2onnx")
        pytest.importorskip("onnxruntime")
        from sklearn.linear_model import LogisticRegression

        from tempest_fastapi_sdk.modelops.sklearn import export_sklearn_to_onnx

        features = _normal(200, 19)
        target = (features[:, 0] > 0).astype(int)
        model = LogisticRegression().fit(features, target)
        export = export_sklearn_to_onnx(model, features[:10], tmp_path / "m.onnx")
        return Path(export.path)

    def test_the_monitor_endpoint_is_absent_without_a_monitor(
        self,
        model_path: Path,
    ) -> None:
        app = FastAPI()
        app.include_router(make_prediction_router(OnnxPredictor(model_path)))
        client = TestClient(app)
        assert client.get("/api/predict/monitor").status_code == 404

    def test_requests_feed_the_monitor(self, model_path: Path) -> None:
        monitor = PredictionMonitor(
            baseline=baseline_from_samples(_normal(500, 20)),
            window_rows=10_000,
        )
        app = FastAPI()
        app.include_router(
            make_prediction_router(OnnxPredictor(model_path), monitor=monitor),
        )
        client = TestClient(app)
        for _ in range(3):
            response = client.post(
                "/api/predict/",
                json={"rows": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]},
            )
            assert response.status_code == 200

        body = client.get("/api/predict/monitor").json()
        assert body["latency"]["n_calls"] == 3
        assert body["latency"]["n_rows"] == 6
        assert body["predictions"]["n_rows"] == 6

    def test_a_rejected_request_is_not_counted(self, model_path: Path) -> None:
        """A 422 never reached the model, so it is not a prediction."""
        monitor = PredictionMonitor()
        app = FastAPI()
        app.include_router(
            make_prediction_router(OnnxPredictor(model_path), monitor=monitor),
        )
        client = TestClient(app)
        assert client.post("/api/predict/", json={"rows": [[1.0]]}).status_code == 422
        assert monitor.report().latency.n_calls == 0


class TestPrometheusMetrics:
    def test_it_publishes_counts_and_drift(self, baseline: FeatureBaseline) -> None:
        prometheus = pytest.importorskip("prometheus_client")
        from tempest_fastapi_sdk.modelops.monitoring import PredictionMetrics

        registry = prometheus.CollectorRegistry()
        metrics = PredictionMetrics(namespace="test_edge", registry=registry)
        monitor = PredictionMonitor(baseline=baseline, window_rows=10_000)

        prediction = _prediction([0] * 200)
        monitor.observe(_normal(200, 21, loc=2.0), prediction)
        metrics.observe(prediction)
        metrics.observe_report(monitor.report())

        assert registry.get_sample_value("test_edge_predictions_total") == 1.0
        assert registry.get_sample_value("test_edge_prediction_rows_total") == 200.0
        assert (registry.get_sample_value("test_edge_input_drift_psi") or 0.0) > (
            PSI_SIGNIFICANT
        )
        assert (
            registry.get_sample_value(
                "test_edge_prediction_share",
                {"label": "0"},
            )
            == 1.0
        )

    def test_an_insufficient_sample_publishes_no_drift_spike(
        self,
        baseline: FeatureBaseline,
    ) -> None:
        prometheus = pytest.importorskip("prometheus_client")
        from tempest_fastapi_sdk.modelops.monitoring import PredictionMetrics

        registry = prometheus.CollectorRegistry()
        metrics = PredictionMetrics(namespace="test_small", registry=registry)
        monitor = PredictionMonitor(baseline=baseline, window_rows=10_000)
        monitor.observe(_normal(5, 22, loc=9.0), _prediction([0] * 5))
        metrics.observe_report(monitor.report())

        assert registry.get_sample_value("test_small_input_drift_psi") == 0.0
