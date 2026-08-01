"""Tests for ONNX and HuggingFace quantization."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from tempest_fastapi_sdk.modelops import (
    CalibrationMethod,
    HFOptimizationLevel,
    HFQuantizationTarget,
    QuantizationBackend,
    QuantizationFormat,
    QuantWeightType,
    analyze_onnx,
    optimize_hf_onnx,
    quantize_hf_bnb,
    quantize_hf_onnx,
    quantize_onnx_dynamic,
    quantize_onnx_static,
)
from tempest_fastapi_sdk.modelops.quantize import (
    _CALIBRATION_ATTRS,
    _ISA_QUANTIZATION_SPECS,
    _OPTIMIZATION_SPECS,
    _ORT_FUSION_MODEL_TYPES,
    _QUANT_FORMAT_ATTRS,
    _QUANT_TYPE_ATTRS,
    _resolve_enum,
)


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


class TestPortedTables:
    """The tables taken over from `optimum` must stay true to the runtime.

    These lock the values in so an upstream rename surfaces as a failure here
    instead of as a silent fallback to the wrong fusion shape.
    """

    def test_every_fusion_target_exists_in_the_runtime(self) -> None:
        optimizer = pytest.importorskip("onnxruntime.transformers.optimizer")
        unknown = {
            value
            for value in _ORT_FUSION_MODEL_TYPES.values()
            if value not in optimizer.MODEL_TYPES
        }
        assert unknown == set()

    def test_every_optimization_level_has_a_spec(self) -> None:
        assert set(_OPTIMIZATION_SPECS) == set(HFOptimizationLevel)

    def test_every_quantization_target_has_a_spec(self) -> None:
        assert set(_ISA_QUANTIZATION_SPECS) == set(HFQuantizationTarget)

    def test_only_the_pre_vnni_x86_targets_tune_reduce_range(self) -> None:
        tunable = {
            target
            for target, spec in _ISA_QUANTIZATION_SPECS.items()
            if spec.tunable_reduce_range
        }
        assert tunable == {
            HFQuantizationTarget.AVX2,
            HFQuantizationTarget.AVX512,
        }

    def test_avx2_alone_takes_unsigned_weights(self) -> None:
        unsigned = {
            target
            for target, spec in _ISA_QUANTIZATION_SPECS.items()
            if spec.weight_type is QuantWeightType.UINT8
        }
        assert unsigned == {HFQuantizationTarget.AVX2}


class TestOptimizeHFOnnx:
    def test_writes_the_graph_and_carries_the_sidecars(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        destination = tmp_path / "optimized"
        result = optimize_hf_onnx(hf_export_dir, destination)
        assert (destination / "model.onnx").is_file()
        assert (destination / "config.json").is_file()
        assert (destination / "tokenizer.json").is_file()
        assert result.output_path == str(destination)

    def test_the_optimized_graph_still_loads(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        destination = tmp_path / "optimized"
        optimize_hf_onnx(hf_export_dir, destination)
        assert analyze_onnx(destination / "model.onnx").inputs[0].name == "x"

    def test_an_unmapped_architecture_is_reported_not_guessed(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        (hf_export_dir / "config.json").write_text(
            json.dumps({"model_type": "some-brand-new-arch"}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="no fusion mapping"):
            optimize_hf_onnx(hf_export_dir, tmp_path / "out")

    def test_a_model_type_override_replaces_the_config_lookup(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        (hf_export_dir / "config.json").unlink()
        destination = tmp_path / "optimized"
        optimize_hf_onnx(hf_export_dir, destination, model_type="bert")
        assert (destination / "model.onnx").is_file()

    def test_an_unknown_override_names_the_supported_set(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        with pytest.raises(ValueError, match="not a fusion type"):
            optimize_hf_onnx(hf_export_dir, tmp_path / "out", model_type="nope")

    def test_a_missing_config_without_an_override_says_so(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        (hf_export_dir / "config.json").unlink()
        with pytest.raises(FileNotFoundError, match=r"config\.json"):
            optimize_hf_onnx(hf_export_dir, tmp_path / "out")

    def test_several_graphs_require_choosing_one(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        shutil.copy2(hf_export_dir / "model.onnx", hf_export_dir / "decoder_model.onnx")
        with pytest.raises(ValueError, match="pass file_name="):
            optimize_hf_onnx(hf_export_dir, tmp_path / "out")

    def test_a_named_graph_disambiguates_a_multi_graph_export(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        shutil.copy2(hf_export_dir / "model.onnx", hf_export_dir / "decoder_model.onnx")
        destination = tmp_path / "optimized"
        optimize_hf_onnx(hf_export_dir, destination, file_name="decoder_model.onnx")
        assert (destination / "decoder_model.onnx").is_file()
        assert not (destination / "model.onnx").exists()

    def test_a_missing_export_directory_raises(self, tmp_path: Path) -> None:
        pytest.importorskip("onnxruntime")
        with pytest.raises(FileNotFoundError, match="export directory"):
            optimize_hf_onnx(tmp_path / "nope", tmp_path / "out")

    def test_o4_without_a_gpu_target_is_refused(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        with pytest.raises(ValueError, match="GPU-only"):
            optimize_hf_onnx(
                hf_export_dir,
                tmp_path / "out",
                level=HFOptimizationLevel.O4,
            )

    @pytest.mark.skipif(
        importlib.util.find_spec("onnxruntime") is not None,
        reason="onnxruntime installed; the missing-extra path can't be exercised",
    )
    def test_without_the_extra_it_names_the_extra(self, tmp_path: Path) -> None:
        with pytest.raises(ImportError, match=r"\[modelops-onnx\]"):
            optimize_hf_onnx(tmp_path / "model", tmp_path / "out")


class TestQuantizeHFOnnx:
    def test_shrinks_the_graph_and_records_the_backend(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        destination = tmp_path / "int8"
        result = quantize_hf_onnx(hf_export_dir, destination)
        assert (destination / "model.onnx").is_file()
        assert result.backend == QuantizationBackend.ONNXRUNTIME_TRANSFORMERS
        assert result.weight_type == QuantWeightType.INT8
        assert result.notes == ["targeted avx512_vnni"]
        assert result.output_size_mb < result.source_size_mb

    def test_it_carries_the_sidecars_so_the_output_stays_loadable(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        destination = tmp_path / "int8"
        quantize_hf_onnx(hf_export_dir, destination)
        assert (destination / "config.json").is_file()
        assert (destination / "tokenizer.json").is_file()

    def test_avx2_quantizes_to_unsigned_weights(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        result = quantize_hf_onnx(
            hf_export_dir, tmp_path / "u8", target=HFQuantizationTarget.AVX2
        )
        assert result.weight_type == QuantWeightType.UINT8

    def test_a_plain_string_target_is_accepted(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        result = quantize_hf_onnx(hf_export_dir, tmp_path / "arm", target="arm64")
        assert result.notes == ["targeted arm64"]

    def test_reduce_range_is_refused_where_it_only_costs_accuracy(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        with pytest.raises(ValueError, match="does not saturate"):
            quantize_hf_onnx(
                hf_export_dir,
                tmp_path / "out",
                target=HFQuantizationTarget.AVX512_VNNI,
                reduce_range=True,
            )

    def test_reduce_range_is_accepted_on_avx512(
        self, hf_export_dir: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        result = quantize_hf_onnx(
            hf_export_dir,
            tmp_path / "out",
            target=HFQuantizationTarget.AVX512,
            reduce_range=True,
        )
        assert Path(result.output_path).is_dir()

    def test_a_missing_export_directory_raises(self, tmp_path: Path) -> None:
        pytest.importorskip("onnxruntime")
        with pytest.raises(FileNotFoundError, match="export directory"):
            quantize_hf_onnx(tmp_path / "nope", tmp_path / "out")

    @pytest.mark.skipif(
        importlib.util.find_spec("onnxruntime") is not None,
        reason="onnxruntime installed; the missing-extra path can't be exercised",
    )
    def test_without_the_extra_it_names_the_extra(self, tmp_path: Path) -> None:
        with pytest.raises(ImportError, match=r"\[modelops-onnx\]"):
            quantize_hf_onnx(tmp_path / "model", tmp_path / "out")


class TestQuantizeHFBnb:
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
