"""Tests for static model analysis."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from tempest_fastapi_sdk.modelops import (
    REMOTE_PROVIDERS,
    ModelFormat,
    analyze_model,
    analyze_onnx,
    analyze_ort,
    default_providers,
    export_onnx_to_ort,
)
from tempest_fastapi_sdk.modelops._fs import size_mb, size_ratio


class TestDefaultProviders:
    def test_drops_the_remote_provider(self) -> None:
        fake = SimpleNamespace(
            get_available_providers=lambda: [
                "AzureExecutionProvider",
                "CPUExecutionProvider",
            ]
        )
        assert default_providers(fake) == ["CPUExecutionProvider"]

    def test_azure_is_the_only_remote_provider(self) -> None:
        assert frozenset({"AzureExecutionProvider"}) == REMOTE_PROVIDERS


class TestFileSizes:
    def test_missing_path_is_zero(self, tmp_path: Path) -> None:
        assert size_mb(tmp_path / "nope") == 0.0

    def test_directory_is_summed_recursively(self, tmp_path: Path) -> None:
        (tmp_path / "nested").mkdir()
        (tmp_path / "a.bin").write_bytes(b"x" * 1024)
        (tmp_path / "nested" / "b.bin").write_bytes(b"x" * 1024)
        assert size_mb(tmp_path) == pytest.approx(2048 / 1024**2)

    def test_ratio_guards_a_zero_divisor(self) -> None:
        assert size_ratio(0.0, 1.0) == 1.0
        assert size_ratio(1.0, 0.0) == 1.0
        assert size_ratio(4.0, 1.0) == pytest.approx(4.0)


class TestAnalyzeOnnx:
    def test_reads_parameters_opset_and_shapes(self, tiny_onnx: Path) -> None:
        metrics = analyze_onnx(tiny_onnx)
        assert metrics.name == "tiny"
        assert metrics.format == ModelFormat.ONNX
        assert metrics.n_parameters == 8 * 64 + 64
        assert metrics.opset == 17
        assert metrics.disk_size_mb > 0.0
        assert metrics.gflops is None
        assert [spec.name for spec in metrics.inputs] == ["x"]
        assert metrics.inputs[0].shape == ["batch", 8]
        assert metrics.outputs[0].shape == ["batch", 64]

    def test_name_can_be_overridden(self, tiny_onnx: Path) -> None:
        assert analyze_onnx(tiny_onnx, name="classifier").name == "classifier"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        pytest.importorskip("onnx")
        with pytest.raises(FileNotFoundError):
            analyze_onnx(tmp_path / "nope.onnx")

    @pytest.mark.skipif(
        importlib.util.find_spec("onnx") is not None,
        reason="onnx installed; the missing-extra path can't be exercised",
    )
    def test_without_the_extra_it_names_the_extra(self, tmp_path: Path) -> None:
        with pytest.raises(ImportError, match=r"\[modelops-onnx\]"):
            analyze_onnx(tmp_path / "any.onnx")


class TestAnalyzeOrt:
    def test_reports_shapes_but_not_parameters(
        self, tiny_onnx: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        [result] = export_onnx_to_ort(tiny_onnx, tmp_path / "out")
        metrics = analyze_ort(result.output_path)
        assert metrics.format == ModelFormat.ORT
        assert metrics.n_parameters == 0
        assert metrics.disk_size_mb > 0.0
        assert metrics.inputs[0].shape == ["batch", 8]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        pytest.importorskip("onnxruntime")
        with pytest.raises(FileNotFoundError):
            analyze_ort(tmp_path / "nope.ort")


class TestAnalyzeModel:
    def test_dispatches_on_the_suffix(self, tiny_onnx: Path) -> None:
        assert analyze_model(tiny_onnx).format == ModelFormat.ONNX

    def test_refuses_a_torch_checkpoint(self, tmp_path: Path) -> None:
        checkpoint = tmp_path / "weights.pt"
        checkpoint.write_bytes(b"not really a checkpoint")
        with pytest.raises(ValueError, match="analyze_torch"):
            analyze_model(checkpoint)


class TestAnalyzeTorch:
    def test_counts_parameters_and_flops(self) -> None:
        torch = pytest.importorskip("torch")
        from tempest_fastapi_sdk.modelops import analyze_torch

        metrics = analyze_torch(
            torch.nn.Linear(128, 10), example_input=torch.randn(1, 128)
        )
        assert metrics.format == ModelFormat.TORCH
        assert metrics.n_parameters == 128 * 10 + 10
        assert metrics.n_trainable_parameters == metrics.n_parameters
        assert metrics.gflops is not None

    def test_flops_are_skipped_without_an_example_input(self) -> None:
        torch = pytest.importorskip("torch")
        from tempest_fastapi_sdk.modelops import analyze_torch

        assert analyze_torch(torch.nn.Linear(4, 2)).gflops is None

    @pytest.mark.skipif(
        importlib.util.find_spec("torch") is not None,
        reason="torch installed; the missing-extra path can't be exercised",
    )
    def test_without_torch_it_says_so(self) -> None:
        from tempest_fastapi_sdk.modelops import analyze_torch

        with pytest.raises(ImportError, match="torch"):
            analyze_torch(object())
