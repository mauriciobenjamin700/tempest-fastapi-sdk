"""Tests for composite scoring and Pareto annotation."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.modelops import (
    DEFAULT_COST_WEIGHTS,
    BenchmarkProfile,
    ModelFormat,
    RuntimeAggregate,
    StaticModelMetrics,
    composite_scores,
    pareto_points,
    rank,
)


def _profile(
    name: str,
    *,
    latency: float,
    rss: float | None = 100.0,
    energy: float | None = None,
    disk: float | None = 5.0,
    gflops: float | None = None,
    quality: float | None = None,
) -> BenchmarkProfile:
    """Build a profile with hand-picked measurements.

    Args:
        name (str): Model name.
        latency (float): Median latency in ms.
        rss (float | None): Peak RSS in MB.
        energy (float | None): Energy per inference in J.
        disk (float | None): Artifact size in MB; ``None`` drops the static
            block entirely.
        gflops (float | None): Static GFLOPs.
        quality (float | None): Task quality.

    Returns:
        BenchmarkProfile: The synthetic profile.
    """
    runtime = RuntimeAggregate(
        device="cpu",
        n_warmup=0,
        n_repetitions=1,
        latency_ms_median=latency,
        latency_ms_iqr=0.0,
        latency_ms_p95=latency,
        latency_ms_p99=latency,
        latency_ms_mean=latency,
        latency_ms_std=0.0,
        latency_ms_min=latency,
        latency_ms_max=latency,
        throughput_per_s=1000.0 / latency,
        rss_peak_mb=rss,
        energy_per_inference_j=energy,
    )
    static = (
        None
        if disk is None
        else StaticModelMetrics(
            name=name, format=ModelFormat.ONNX, disk_size_mb=disk, gflops=gflops
        )
    )
    return BenchmarkProfile(name=name, runtime=runtime, static=static, quality=quality)


class TestCompositeScores:
    def test_rejects_an_empty_set(self) -> None:
        with pytest.raises(ValueError, match="empty profile list"):
            composite_scores([])

    def test_rejects_an_unknown_dimension(self) -> None:
        with pytest.raises(ValueError, match="unknown ranking dimension"):
            composite_scores([_profile("a", latency=1.0)], weights={"nonsense": 1.0})

    def test_rejects_a_non_numeric_dimension(self) -> None:
        with pytest.raises(ValueError, match="not numeric"):
            composite_scores([_profile("a", latency=1.0)], weights={"device": 1.0})

    def test_cheapest_scores_zero_and_dearest_scores_one(self) -> None:
        scores, _ = composite_scores(
            [
                _profile("cheap", latency=1.0, rss=10.0, disk=1.0),
                _profile("dear", latency=9.0, rss=90.0, disk=9.0),
            ]
        )
        assert scores == [pytest.approx(0.0), pytest.approx(1.0)]

    def test_unmeasured_dimensions_are_dropped_and_weights_renormalized(
        self,
    ) -> None:
        _, effective = composite_scores(
            [_profile("a", latency=1.0), _profile("b", latency=2.0)]
        )
        assert "energy_per_inference_j" not in effective
        assert sum(effective.values()) == pytest.approx(1.0)
        assert set(effective) < set(DEFAULT_COST_WEIGHTS)

    def test_a_profile_missing_one_dimension_is_scored_on_the_rest(self) -> None:
        profiles = [
            _profile("full", latency=1.0, energy=1.0),
            _profile("partial", latency=9.0, energy=None),
        ]
        scores, effective = composite_scores(profiles)
        without_energy, _ = composite_scores(
            profiles,
            weights={
                dimension: weight
                for dimension, weight in DEFAULT_COST_WEIGHTS.items()
                if dimension != "energy_per_inference_j"
            },
        )
        assert "energy_per_inference_j" in effective
        assert scores[1] == pytest.approx(without_energy[1])

    def test_a_constant_dimension_carries_no_signal(self) -> None:
        scores, _ = composite_scores(
            [
                _profile("a", latency=5.0, rss=1.0, disk=1.0),
                _profile("b", latency=5.0, rss=1.0, disk=1.0),
            ]
        )
        assert scores == [pytest.approx(0.0), pytest.approx(0.0)]

    def test_static_dimensions_resolve_without_a_static_block(self) -> None:
        scores, _ = composite_scores(
            [
                _profile("a", latency=1.0, disk=None),
                _profile("b", latency=2.0, disk=None),
            ],
            weights={"latency_ms_median": 0.5, "disk_size_mb": 0.5},
        )
        assert scores == [pytest.approx(0.0), pytest.approx(1.0)]


class TestParetoPoints:
    def test_a_strictly_worse_model_is_dominated(self) -> None:
        points = pareto_points(
            [
                _profile("good", latency=1.0, rss=10.0, disk=1.0, quality=0.9),
                _profile("bad", latency=9.0, rss=90.0, disk=9.0, quality=0.5),
            ]
        )
        assert [point.is_pareto for point in points] == [True, False]

    def test_a_trade_off_keeps_both_on_the_frontier(self) -> None:
        points = pareto_points(
            [
                _profile("fast", latency=1.0, rss=10.0, disk=1.0, quality=0.70),
                _profile("accurate", latency=9.0, rss=90.0, disk=9.0, quality=0.95),
            ]
        )
        assert all(point.is_pareto for point in points)

    def test_without_quality_it_degrades_to_a_cost_frontier(self) -> None:
        points = pareto_points(
            [
                _profile("cheap", latency=1.0, rss=10.0, disk=1.0),
                _profile("dear", latency=9.0, rss=90.0, disk=9.0),
            ]
        )
        assert [point.is_pareto for point in points] == [True, False]

    def test_an_unmeasured_axis_is_skipped_not_assumed_best(self) -> None:
        points = pareto_points(
            [
                _profile("measured", latency=5.0, rss=50.0, disk=5.0, energy=1.0),
                _profile("unmeasured", latency=9.0, rss=90.0, disk=9.0),
            ]
        )
        assert [point.is_pareto for point in points] == [True, False]

    def test_memory_falls_back_to_rss_without_gpu_memory(self) -> None:
        [point] = pareto_points([_profile("a", latency=1.0, rss=42.0)])
        assert point.memory_mb == pytest.approx(42.0)


class TestRank:
    def test_sorts_by_composite_score_and_attaches_quality(self) -> None:
        report = rank(
            [
                _profile("dear", latency=9.0, rss=90.0, disk=9.0),
                _profile("cheap", latency=1.0, rss=10.0, disk=1.0),
            ],
            quality={"cheap": 0.8, "dear": 0.9},
        )
        assert [profile.name for profile in report.profiles] == ["cheap", "dear"]
        assert report.profiles[0].quality == pytest.approx(0.8)
        assert report.profiles[0].composite_score == pytest.approx(0.0)
        assert all(profile.is_pareto for profile in report.profiles)

    def test_records_the_host_and_the_effective_weights(self) -> None:
        report = rank([_profile("a", latency=1.0), _profile("b", latency=2.0)])
        assert report.hardware is not None
        assert report.hardware.cpu_cores >= 1
        assert sum(report.weights.values()) == pytest.approx(1.0)

    def test_does_not_mutate_the_input_profiles(self) -> None:
        original = _profile("a", latency=1.0)
        rank([original, _profile("b", latency=2.0)])
        assert original.composite_score is None
        assert original.is_pareto is False
