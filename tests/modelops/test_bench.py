"""Tests for the benchmark loop and its ONNX Runtime front end."""

from __future__ import annotations

from pathlib import Path

import pytest

from tempest_fastapi_sdk.modelops import (
    EnergySource,
    NullPowerSampler,
    benchmark,
    benchmark_models,
    benchmark_onnx,
)
from tempest_fastapi_sdk.modelops.bench import (
    _percentile,
    _random_feeds,
    _resolve_shape,
)
from tests.modelops.conftest import FakeInputSpec, FakeSession


class TestPercentile:
    def test_single_value(self) -> None:
        assert _percentile([4.2], 0.95) == pytest.approx(4.2)

    def test_interpolates_between_neighbours(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0]
        assert _percentile(values, 0.0) == pytest.approx(1.0)
        assert _percentile(values, 1.0) == pytest.approx(4.0)
        assert _percentile(values, 0.5) == pytest.approx(2.5)

    def test_does_not_need_sorted_input(self) -> None:
        assert _percentile([4.0, 1.0, 3.0, 2.0], 0.5) == pytest.approx(2.5)


class TestBenchmark:
    def test_counts_warmup_and_repetitions_separately(self) -> None:
        calls: list[int] = []
        profile = benchmark(
            lambda: calls.append(1),
            name="counter",
            n_warmup=3,
            n_repetitions=5,
            power_sampler=NullPowerSampler(),
            cpu_energy_sampler=NullPowerSampler(),
        )
        assert len(calls) == 8
        assert profile.name == "counter"
        assert profile.runtime.n_warmup == 3
        assert profile.runtime.n_repetitions == 5

    def test_rejects_zero_repetitions(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            benchmark(lambda: None, n_repetitions=0)

    def test_latency_statistics_are_coherent(self) -> None:
        runtime = benchmark(
            lambda: None,
            n_warmup=0,
            n_repetitions=20,
            power_sampler=NullPowerSampler(),
            cpu_energy_sampler=NullPowerSampler(),
        ).runtime
        assert runtime.latency_ms_min <= runtime.latency_ms_median
        assert runtime.latency_ms_median <= runtime.latency_ms_p95
        assert runtime.latency_ms_p95 <= runtime.latency_ms_max
        assert runtime.latency_ms_iqr >= 0.0
        assert runtime.throughput_per_s > 0.0

    def test_samples_are_dropped_unless_requested(self) -> None:
        kwargs = {
            "n_warmup": 0,
            "n_repetitions": 4,
            "power_sampler": NullPowerSampler(),
            "cpu_energy_sampler": NullPowerSampler(),
        }
        assert benchmark(lambda: None, **kwargs).samples == []
        kept = benchmark(lambda: None, keep_samples=True, **kwargs).samples
        assert [sample.index for sample in kept] == [0, 1, 2, 3]

    def test_energy_is_unavailable_without_samplers(self) -> None:
        runtime = benchmark(
            lambda: None,
            n_warmup=0,
            n_repetitions=2,
            power_sampler=NullPowerSampler(),
            cpu_energy_sampler=NullPowerSampler(),
        ).runtime
        assert runtime.energy_per_inference_j is None
        assert runtime.energy_source == EnergySource.UNAVAILABLE
        assert runtime.gpu_memory_peak_mb is None

    def test_sync_brackets_every_timed_call(self) -> None:
        syncs: list[int] = []
        benchmark(
            lambda: None,
            n_warmup=1,
            n_repetitions=3,
            sync=lambda: syncs.append(1),
            power_sampler=NullPowerSampler(),
            cpu_energy_sampler=NullPowerSampler(),
        )
        assert len(syncs) == 1 + 3 * 2

    def test_no_gpu_sampler_is_resolved_for_a_cpu_run(self) -> None:
        runtime = benchmark(
            lambda: None,
            n_warmup=0,
            n_repetitions=2,
            device="cpu",
            cpu_energy_sampler=NullPowerSampler(),
        ).runtime
        assert runtime.gpu_energy_j is None
        assert runtime.gpu_memory_peak_mb is None


class TestResolveShape:
    def test_keeps_fixed_dimensions(self) -> None:
        shape = _resolve_shape("x", [1, 3, 224, 224], batch_size=4, dynamic_dims={})
        assert shape == [1, 3, 224, 224]

    def test_leading_dimension_falls_back_to_batch_size(self) -> None:
        shape = _resolve_shape("x", ["batch", 8], batch_size=4, dynamic_dims={})
        assert shape == [4, 8]

    def test_named_dimensions_come_from_dynamic_dims(self) -> None:
        shape = _resolve_shape(
            "images",
            ["batch", 3, "height", "width"],
            batch_size=2,
            dynamic_dims={"height": 224, "width": 224},
        )
        assert shape == [2, 3, 224, 224]

    def test_unresolved_dimension_raises_instead_of_guessing(self) -> None:
        with pytest.raises(ValueError, match="unresolved dimension"):
            _resolve_shape(
                "images",
                ["batch", 3, "height", "width"],
                batch_size=1,
                dynamic_dims={"height": 224},
            )


class TestRandomFeeds:
    def test_float_inputs_get_noise_and_ints_get_zeros(self) -> None:
        np = pytest.importorskip("numpy")
        session = FakeSession(
            [
                FakeInputSpec("pixels", "tensor(float)", ["batch", 4]),
                FakeInputSpec("ids", "tensor(int64)", ["batch", 4]),
            ]
        )
        feeds = _random_feeds(
            session, batch_size=2, dynamic_dims={}, input_shapes={}, seed=7
        )
        assert feeds["pixels"].shape == (2, 4)
        assert feeds["pixels"].dtype == np.dtype("float32")
        assert feeds["ids"].dtype == np.dtype("int64")
        assert not feeds["ids"].any()

    def test_explicit_shapes_bypass_resolution(self) -> None:
        pytest.importorskip("numpy")
        session = FakeSession([FakeInputSpec("x", "tensor(float)", ["a", "b", "c"])])
        feeds = _random_feeds(
            session,
            batch_size=1,
            dynamic_dims={},
            input_shapes={"x": [2, 3, 4]},
            seed=0,
        )
        assert feeds["x"].shape == (2, 3, 4)

    def test_seed_makes_the_inputs_reproducible(self) -> None:
        np = pytest.importorskip("numpy")
        session = FakeSession([FakeInputSpec("x", "tensor(float)", [1, 4])])
        kwargs = {"batch_size": 1, "dynamic_dims": {}, "input_shapes": {}}
        first = _random_feeds(session, seed=3, **kwargs)
        second = _random_feeds(session, seed=3, **kwargs)
        assert np.array_equal(first["x"], second["x"])


class TestBenchmarkOnnx:
    def test_profiles_a_real_model(self, tiny_onnx: Path) -> None:
        pytest.importorskip("onnxruntime")
        profile = benchmark_onnx(
            tiny_onnx,
            n_warmup=1,
            n_repetitions=3,
            power_sampler=NullPowerSampler(),
            cpu_energy_sampler=NullPowerSampler(),
        )
        assert profile.name == "tiny"
        assert profile.runtime.n_repetitions == 3
        assert profile.runtime.provider is not None
        assert "Azure" not in profile.runtime.provider
        assert profile.static is not None
        assert profile.static.n_parameters == 576

    def test_missing_model_raises(self, tmp_path: Path) -> None:
        pytest.importorskip("onnxruntime")
        with pytest.raises(FileNotFoundError):
            benchmark_onnx(tmp_path / "nope.onnx")

    def test_report_is_json_serializable(self, tiny_onnx: Path) -> None:
        pytest.importorskip("onnxruntime")
        profile = benchmark_onnx(
            tiny_onnx,
            n_warmup=0,
            n_repetitions=2,
            power_sampler=NullPowerSampler(),
            cpu_energy_sampler=NullPowerSampler(),
        )
        assert "latency_ms_median" in profile.model_dump_json()


class TestBenchmarkModels:
    def test_ranks_two_models_against_each_other(
        self, tiny_onnx: Path, bigger_onnx: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        report = benchmark_models(
            [tiny_onnx, bigger_onnx],
            quality={"tiny": 0.80, "bigger": 0.86},
            n_warmup=1,
            n_repetitions=3,
            power_sampler=NullPowerSampler(),
            cpu_energy_sampler=NullPowerSampler(),
        )
        assert {profile.name for profile in report.profiles} == {"tiny", "bigger"}
        assert all(point.quality is not None for point in report.pareto)
        assert report.weights
        assert sum(report.weights.values()) == pytest.approx(1.0)
        scores = [profile.composite_score for profile in report.profiles]
        assert scores == sorted(scores, key=lambda value: value or 0.0)
