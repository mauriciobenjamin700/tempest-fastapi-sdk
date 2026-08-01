"""Tests for ONNX and HuggingFace quantization."""

from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.modelops import (
    CalibrationMethod,
    HFOptimizationLevel,
    QuantizationBackend,
    QuantizationFormat,
    QuantWeightType,
    analyze_onnx,
    export_hf_to_onnx,
    optimize_hf_onnx,
    quantize_hf_bnb,
    quantize_hf_onnx,
    quantize_onnx_dynamic,
    quantize_onnx_static,
)
from tempest_fastapi_sdk.modelops.quantize import (
    _CALIBRATION_ATTRS,
    _QUANT_FORMAT_ATTRS,
    _QUANT_TYPE_ATTRS,
    _resolve_enum,
)


def _block_import(monkeypatch: pytest.MonkeyPatch, prefix: str) -> None:
    """Make any import starting with ``prefix`` raise ``ImportError``.

    Args:
        monkeypatch (pytest.MonkeyPatch): Patcher.
        prefix (str): Module prefix to block, e.g. ``"optimum"``.
    """
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == prefix or name.startswith(f"{prefix}."):
            raise ImportError(f"no module named {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


class TestEnumMaps:
    def test_every_weight_type_maps_to_a_runtime_member(self) -> None:
        assert set(_QUANT_TYPE_ATTRS) == set(QuantWeightType)

    def test_every_format_and_method_maps(self) -> None:
        assert set(_QUANT_FORMAT_ATTRS) == set(QuantizationFormat)
        assert set(_CALIBRATION_ATTRS) == set(CalibrationMethod)

    def test_an_unsupported_member_is_reported_not_guessed(self) -> None:
        quantization = pytest.importorskip("onnxruntime.quantization")
        with pytest.raises(ValueError, match="needs a newer onnxruntime"):
            _resolve_enum(quantization, "QuantType", "QNotAThing", "int999")


class TestQuantizeOnnxDynamic:
    def test_shrinks_the_model_and_records_the_backend(
        self, tiny_onnx: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        destination = tmp_path / "tiny.int8.onnx"
        result = quantize_onnx_dynamic(tiny_onnx, destination)
        assert destination.is_file()
        assert result.backend == QuantizationBackend.ONNXRUNTIME_DYNAMIC
        assert result.weight_type == QuantWeightType.INT8
        assert result.compression_ratio > 1.0
        assert result.output_size_mb < result.source_size_mb

    def test_a_plain_string_weight_type_is_accepted(
        self, tiny_onnx: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        result = quantize_onnx_dynamic(
            tiny_onnx, tmp_path / "u8.onnx", weight_type="uint8"
        )
        assert result.weight_type == QuantWeightType.UINT8

    def test_the_output_is_still_a_loadable_graph(
        self, tiny_onnx: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        destination = tmp_path / "tiny.int8.onnx"
        quantize_onnx_dynamic(tiny_onnx, destination)
        assert analyze_onnx(destination).inputs[0].name == "x"

    def test_missing_input_raises(self, tmp_path: Path) -> None:
        pytest.importorskip("onnxruntime")
        with pytest.raises(FileNotFoundError):
            quantize_onnx_dynamic(tmp_path / "nope.onnx", tmp_path / "out.onnx")

    @pytest.mark.skipif(
        importlib.util.find_spec("onnxruntime") is not None,
        reason="onnxruntime installed; the missing-extra path can't be exercised",
    )
    def test_without_the_extra_it_names_the_extra(self, tmp_path: Path) -> None:
        with pytest.raises(ImportError, match=r"\[modelops-onnx\]"):
            quantize_onnx_dynamic(tmp_path / "a.onnx", tmp_path / "b.onnx")


class TestQuantizeOnnxStatic:
    def test_calibrates_and_reports_the_batch_count(
        self, tiny_onnx: Path, tmp_path: Path
    ) -> None:
        np = pytest.importorskip("numpy")
        pytest.importorskip("onnxruntime")
        batches = [
            {"x": np.full((1, 8), value, dtype=np.float32)} for value in (0.0, 1.0)
        ]
        result = quantize_onnx_static(
            tiny_onnx, tmp_path / "tiny.qdq.onnx", calibration_inputs=batches
        )
        assert result.backend == QuantizationBackend.ONNXRUNTIME_STATIC
        assert result.notes == ["calibrated on 2 batch(es)"]
        assert Path(result.output_path).is_file()

    def test_refuses_to_run_without_calibration_data(
        self, tiny_onnx: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        with pytest.raises(ValueError, match="quantize_onnx_dynamic"):
            quantize_onnx_static(
                tiny_onnx, tmp_path / "out.onnx", calibration_inputs=[]
            )


class TestHuggingFacePaths:
    def test_export_without_the_extra_names_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _block_import(monkeypatch, "optimum")
        with pytest.raises(ImportError, match=r"\[modelops-quant\]"):
            export_hf_to_onnx("distilbert-base-uncased", tmp_path / "out")

    def test_optimize_without_the_extra_names_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _block_import(monkeypatch, "optimum")
        with pytest.raises(ImportError, match=r"\[modelops-quant\]"):
            optimize_hf_onnx(tmp_path / "model", tmp_path / "out")

    def test_quantize_without_the_extra_names_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _block_import(monkeypatch, "optimum")
        with pytest.raises(ImportError, match=r"\[modelops-quant\]"):
            quantize_hf_onnx(tmp_path / "model", tmp_path / "out")

    def test_o4_without_a_gpu_target_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        optimum = pytest.importorskip("optimum.onnxruntime")
        assert optimum is not None
        with pytest.raises(ValueError, match="GPU-only"):
            optimize_hf_onnx(
                tmp_path / "model",
                tmp_path / "out",
                level=HFOptimizationLevel.O4,
            )

    def test_bnb_rejects_an_unsupported_bit_width(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="bits must be 4 or 8"):
            quantize_hf_bnb("some/model", tmp_path / "out", bits=3)

    @pytest.mark.skipif(
        importlib.util.find_spec("bitsandbytes") is not None,
        reason="bitsandbytes installed; the missing-extra path can't be exercised",
    )
    def test_bnb_without_the_extras_names_them(self, tmp_path: Path) -> None:
        with pytest.raises(ImportError, match=r"\[genai-quant\]"):
            quantize_hf_bnb("some/model", tmp_path / "out")
