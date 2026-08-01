"""Tests for ``tempest model`` commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app
from tempest_fastapi_sdk.cli.model import _parse_dims
from tests.modelops.conftest import _build_gemm_model

runner = CliRunner()

_WIDE_TERM: dict[str, str] = {"COLUMNS": "200"}


@pytest.fixture
def onnx_model(tmp_path: Path) -> Path:
    """Write a small real ONNX model for the commands to chew on.

    Returns:
        Path: Path to the written ``.onnx`` file.
    """
    pytest.importorskip("onnx")
    path = tmp_path / "tiny.onnx"
    _build_gemm_model(path, features=8, units=64)
    return path


class TestParseDims:
    def test_parses_repeated_pairs(self) -> None:
        assert _parse_dims(["height=224", "width=128"]) == {
            "height": 224,
            "width": 128,
        }

    def test_empty_input_is_an_empty_mapping(self) -> None:
        assert _parse_dims([]) == {}


class TestAnalyze:
    def test_prints_the_summary(self, onnx_model: Path) -> None:
        result = runner.invoke(app, ["model", "analyze", str(onnx_model)])
        assert result.exit_code == 0, result.stdout
        assert "parameters : 576" in result.stdout
        assert "opset      : 17" in result.stdout
        assert "input      : x ['batch', 8]" in result.stdout

    def test_json_output_is_machine_readable(self, onnx_model: Path) -> None:
        result = runner.invoke(app, ["model", "analyze", str(onnx_model), "--json"])
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["n_parameters"] == 576
        assert payload["format"] == "onnx"

    def test_missing_file_exits_two(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["model", "analyze", str(tmp_path / "no.onnx")])
        assert result.exit_code == 2
        assert "error: model not found" in (result.stdout + str(result.stderr))

    def test_unsupported_suffix_exits_two(self, tmp_path: Path) -> None:
        checkpoint = tmp_path / "weights.pt"
        checkpoint.write_bytes(b"x")
        result = runner.invoke(app, ["model", "analyze", str(checkpoint)])
        assert result.exit_code == 2
        assert "analyze_torch" in (result.stdout + str(result.stderr))


class TestBench:
    def test_reports_latency_and_energy_provenance(self, onnx_model: Path) -> None:
        pytest.importorskip("onnxruntime")
        result = runner.invoke(
            app,
            ["model", "bench", str(onnx_model), "-n", "3", "-w", "1"],
            env=_WIDE_TERM,
        )
        assert result.exit_code == 0, result.stdout
        assert "latency ms : median" in result.stdout
        assert "throughput :" in result.stdout
        assert "energy     :" in result.stdout
        assert "3 reps, 1 warm-up" in result.stdout

    def test_json_output_carries_the_full_profile(self, onnx_model: Path) -> None:
        pytest.importorskip("onnxruntime")
        result = runner.invoke(
            app, ["model", "bench", str(onnx_model), "-n", "2", "-w", "0", "--json"]
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["runtime"]["n_repetitions"] == 2
        assert payload["static"]["n_parameters"] == 576

    def test_a_malformed_dim_is_rejected(self, onnx_model: Path) -> None:
        pytest.importorskip("onnxruntime")
        result = runner.invoke(
            app, ["model", "bench", str(onnx_model), "-d", "height"], env=_WIDE_TERM
        )
        assert result.exit_code == 2
        assert "--dim must be 'name=value'" in (result.stdout + str(result.stderr))

    def test_a_non_integer_dim_is_rejected(self, onnx_model: Path) -> None:
        pytest.importorskip("onnxruntime")
        result = runner.invoke(
            app,
            ["model", "bench", str(onnx_model), "-d", "height=tall"],
            env=_WIDE_TERM,
        )
        assert result.exit_code == 2
        assert "must be an integer" in (result.stdout + str(result.stderr))


class TestQuantize:
    def test_writes_the_quantized_model_and_warns(
        self, onnx_model: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        destination = tmp_path / "tiny.int8.onnx"
        result = runner.invoke(
            app, ["model", "quantize", str(onnx_model), str(destination)]
        )
        assert result.exit_code == 0, result.stdout
        assert destination.is_file()
        assert "onnxruntime_dynamic" in result.stdout
        assert "re-measure accuracy" in result.stdout

    def test_an_unknown_weight_type_exits_two(
        self, onnx_model: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        result = runner.invoke(
            app,
            [
                "model",
                "quantize",
                str(onnx_model),
                str(tmp_path / "out.onnx"),
                "--weight-type",
                "int3",
            ],
        )
        assert result.exit_code == 2
        assert not (tmp_path / "out.onnx").exists()


class TestExportOrt:
    def test_writes_the_ort_file_and_its_config(
        self, onnx_model: Path, tmp_path: Path
    ) -> None:
        pytest.importorskip("onnxruntime")
        output = tmp_path / "mobile"
        result = runner.invoke(
            app,
            ["model", "export-ort", str(onnx_model), "-o", str(output)],
            env=_WIDE_TERM,
        )
        assert result.exit_code == 0, result.stdout
        assert "wrote" in result.stdout
        assert "config:" in result.stdout
        assert list(output.glob("*.ort"))

    def test_an_unknown_style_exits_two(self, onnx_model: Path, tmp_path: Path) -> None:
        pytest.importorskip("onnxruntime")
        result = runner.invoke(
            app,
            [
                "model",
                "export-ort",
                str(onnx_model),
                "-o",
                str(tmp_path / "out"),
                "--style",
                "turbo",
            ],
        )
        assert result.exit_code == 2


class TestOptimize:
    def test_writes_the_optimized_graph(self, onnx_model: Path, tmp_path: Path) -> None:
        pytest.importorskip("onnxruntime")
        destination = tmp_path / "tiny.opt.onnx"
        result = runner.invoke(
            app, ["model", "optimize", str(onnx_model), str(destination)]
        )
        assert result.exit_code == 0, result.stdout
        assert destination.is_file()
        assert "wrote" in result.stdout


class TestHardware:
    def test_reports_cores_and_sampler_availability(self) -> None:
        result = runner.invoke(app, ["model", "hardware"], env=_WIDE_TERM)
        assert result.exit_code == 0, result.stdout
        assert "cpu cores" in result.stdout
        assert "energy measurement" in result.stdout

    def test_json_output_lists_both_samplers(self) -> None:
        result = runner.invoke(app, ["model", "hardware", "--json"])
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert isinstance(payload["gpu_energy_available"], bool)
        assert isinstance(payload["cpu_energy_available"], bool)
        assert payload["hardware"]["cpu_cores"] >= 1


class TestHelp:
    def test_the_group_is_registered_on_the_root_app(self) -> None:
        result = runner.invoke(app, ["model", "--help"], env=_WIDE_TERM)
        assert result.exit_code == 0, result.stdout
        for command in ("analyze", "bench", "export-ort", "optimize", "quantize"):
            assert command in result.stdout
