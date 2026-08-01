"""Tests for ONNX export, ORT conversion and graph optimization."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tempest_fastapi_sdk.modelops import (
    ORT_CONFIG_SUFFIXES,
    GraphOptimizationLevel,
    ModelFormat,
    OrtOptimizationStyle,
    export_onnx_to_ort,
    optimize_onnx_graph,
)


class TestExportOnnxToOrt:
    def test_writes_an_ort_file_and_its_operator_config(
        self, tiny_onnx: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        output = tmp_path / "mobile"
        [result] = export_onnx_to_ort(tiny_onnx, output)
        written = Path(result.output_path)
        assert written.suffix == ".ort"
        assert written.parent == output
        assert result.format == ModelFormat.ORT
        assert result.source_path == str(tiny_onnx)
        assert result.output_size_mb > 0.0
        assert result.optimization_style == OrtOptimizationStyle.FIXED
        assert result.extra_files
        assert any(
            config.endswith(ORT_CONFIG_SUFFIXES[0]) for config in result.extra_files
        )

    def test_runtime_style_is_recorded(self, tiny_onnx: Path, tmp_path: Path) -> None:
        pytest.importorskip("onnxruntime")
        results = export_onnx_to_ort(
            tiny_onnx,
            tmp_path / "runtime",
            optimization_style=OrtOptimizationStyle.RUNTIME,
        )
        assert results
        assert all(
            result.optimization_style == OrtOptimizationStyle.RUNTIME
            for result in results
        )

    def test_a_plain_string_style_is_accepted(
        self, tiny_onnx: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        [result] = export_onnx_to_ort(
            tiny_onnx, tmp_path / "str", optimization_style="fixed"
        )
        assert result.optimization_style == OrtOptimizationStyle.FIXED

    def test_converts_a_whole_directory(
        self, tiny_onnx: Path, bigger_onnx: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        results = export_onnx_to_ort(tiny_onnx.parent, tmp_path / "batch")
        produced = {Path(result.output_path).stem for result in results}
        assert {tiny_onnx.stem, bigger_onnx.stem} <= produced
        assert all(Path(result.source_path).suffix == ".onnx" for result in results)

    def test_missing_input_raises(self, tmp_path: Path) -> None:
        pytest.importorskip("onnxruntime")
        with pytest.raises(FileNotFoundError):
            export_onnx_to_ort(tmp_path / "nope.onnx")

    @pytest.mark.skipif(
        importlib.util.find_spec("onnx") is not None,
        reason="onnx installed; the missing-extra path can't be exercised",
    )
    def test_without_the_extra_it_names_the_extra(self, tmp_path: Path) -> None:
        with pytest.raises(ImportError, match=r"\[modelops-onnx\]"):
            export_onnx_to_ort(tmp_path / "any.onnx")


class TestOptimizeOnnxGraph:
    def test_writes_an_optimized_graph(self, tiny_onnx: Path, tmp_path: Path) -> None:
        pytest.importorskip("onnxruntime")
        destination = tmp_path / "nested" / "tiny.opt.onnx"
        result = optimize_onnx_graph(tiny_onnx, destination)
        assert destination.is_file()
        assert result.format == ModelFormat.ONNX
        assert result.output_size_mb > 0.0
        assert result.size_ratio > 0.0
        assert result.opset == 17

    def test_a_plain_string_level_is_accepted(
        self, tiny_onnx: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        result = optimize_onnx_graph(tiny_onnx, tmp_path / "basic.onnx", level="basic")
        assert Path(result.output_path).is_file()

    def test_every_declared_level_maps_to_the_runtime(self) -> None:
        onnxruntime = pytest.importorskip("onnxruntime")
        from tempest_fastapi_sdk.modelops.export import _GRAPH_LEVEL_ATTRS

        for level in GraphOptimizationLevel:
            attribute = _GRAPH_LEVEL_ATTRS[level]
            assert attribute.startswith("ORT_")
            if level is not GraphOptimizationLevel.LAYOUT:
                assert hasattr(onnxruntime.GraphOptimizationLevel, attribute)

    def test_missing_input_raises(self, tmp_path: Path) -> None:
        pytest.importorskip("onnxruntime")
        with pytest.raises(FileNotFoundError):
            optimize_onnx_graph(tmp_path / "nope.onnx", tmp_path / "out.onnx")


class TestExportTorchToOnnx:
    def test_round_trips_a_linear_module(self, tmp_path: Path) -> None:
        torch = pytest.importorskip("torch")
        pytest.importorskip("onnx")
        from tempest_fastapi_sdk.modelops import analyze_onnx, export_torch_to_onnx

        destination = tmp_path / "linear.onnx"
        result = export_torch_to_onnx(
            torch.nn.Linear(16, 4),
            destination,
            example_input=torch.randn(1, 16),
            input_names=["features"],
            output_names=["logits"],
            dynamic_axes={"features": {0: "batch"}},
        )
        assert result.opset == 17
        assert result.format == ModelFormat.ONNX
        assert analyze_onnx(destination).n_parameters == 16 * 4 + 4
