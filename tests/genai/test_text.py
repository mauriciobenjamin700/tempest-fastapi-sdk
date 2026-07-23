"""Tests for TextGenerator — pure logic + state (no torch in CI)."""

from __future__ import annotations

import importlib.util

import pytest

from tempest_fastapi_sdk.genai import (
    GenerationConfig,
    HardwareInfo,
    ModelDtype,
    TextGenerator,
    auto_dtype_name,
    resolve_device,
)
from tempest_fastapi_sdk.genai.schemas import GPUInfo


def _gpu_hw() -> HardwareInfo:
    return HardwareInfo(
        cpu_cores=8,
        ram_total_bytes=32 * 10**9,
        ram_available_bytes=16 * 10**9,
        has_cuda=True,
        gpus=[
            GPUInfo(
                index=0,
                name="T",
                vram_total_bytes=24 * 10**9,
                vram_free_bytes=24 * 10**9,
            )
        ],
    )


def _cpu_hw() -> HardwareInfo:
    return HardwareInfo(
        cpu_cores=4, ram_total_bytes=8 * 10**9, ram_available_bytes=6 * 10**9
    )


class TestResolvers:
    def test_resolve_fixed_device_passthrough(self) -> None:
        assert resolve_device("cpu", _gpu_hw()) == "cpu"

    def test_resolve_auto_prefers_cuda(self) -> None:
        assert resolve_device("auto", _gpu_hw()) == "cuda"

    def test_resolve_auto_falls_back_to_cpu(self) -> None:
        assert resolve_device("auto", _cpu_hw()) == "cpu"

    def test_auto_dtype(self) -> None:
        assert auto_dtype_name("cuda") == "bfloat16"
        assert auto_dtype_name("cpu") == "float32"


class TestInit:
    def test_auto_dtype_on_gpu(self) -> None:
        gen = TextGenerator("m", hardware=_gpu_hw())
        assert gen.device == "cuda"
        assert gen.dtype == ModelDtype.BFLOAT16

    def test_auto_dtype_on_cpu(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        assert gen.device == "cpu"
        assert gen.dtype == ModelDtype.FLOAT32

    def test_quantization_accepted(self) -> None:
        gen = TextGenerator("m", quantization="int4", hardware=_gpu_hw())
        assert gen.quantization == ModelDtype.INT4

    def test_bad_quantization_rejected(self) -> None:
        with pytest.raises(ValueError, match="quantization"):
            TextGenerator("m", quantization="float16", hardware=_cpu_hw())

    def test_not_loaded_initially(self) -> None:
        assert TextGenerator("m", hardware=_cpu_hw()).is_loaded is False


class TestState:
    def test_unload_when_not_loaded_is_noop(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        gen.unload()  # no raise
        assert gen.is_loaded is False

    def test_unload_if_idle_without_threshold(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        assert gen.unload_if_idle() is False

    def test_unload_if_idle_not_loaded(self) -> None:
        gen = TextGenerator("m", idle_unload_seconds=0.0, hardware=_cpu_hw())
        # not loaded -> nothing to unload
        assert gen.unload_if_idle() is False

    def test_seconds_idle_advances(self) -> None:
        import time

        gen = TextGenerator("m", hardware=_cpu_hw())
        gen._last_used = time.monotonic() - 5
        assert gen.seconds_idle >= 5


class TestResolveControl:
    def test_reads_from_config(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        cfg = GenerationConfig(seed=7, stop=["END"])
        seed, stop = gen._resolve_control({}, cfg)
        assert seed == 7
        assert stop == ["END"]

    def test_overrides_win_over_config(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        cfg = GenerationConfig(seed=7, stop=["END"])
        overrides: dict[str, object] = {"seed": 99, "stop": ["STOP"]}
        seed, stop = gen._resolve_control(overrides, cfg)
        assert seed == 99
        assert stop == ["STOP"]

    def test_pops_seed_and_stop_from_overrides(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        overrides: dict[str, object] = {"seed": 1, "stop": ["x"], "temperature": 0.5}
        gen._resolve_control(overrides, None)
        assert "seed" not in overrides
        assert "stop" not in overrides
        assert overrides == {"temperature": 0.5}

    def test_absent_yields_none_and_empty(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        seed, stop = gen._resolve_control({}, None)
        assert seed is None
        assert stop == []


class TestAssembleKwargs:
    def test_wires_stop_strings_and_tokenizer(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        sentinel = object()
        kwargs = gen._assemble_kwargs({}, None, ["END"], sentinel)
        assert kwargs["stop_strings"] == ["END"]
        assert kwargs["tokenizer"] is sentinel

    def test_no_stop_leaves_kwargs_clean(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        kwargs = gen._assemble_kwargs({}, None, [], object())
        assert "stop_strings" not in kwargs
        assert "tokenizer" not in kwargs


@pytest.mark.model
class TestSeedStopWithModel:
    async def test_seed_makes_sampling_reproducible(
        self, tiny_causal_lm: object
    ) -> None:
        cfg = GenerationConfig(seed=123, max_new_tokens=8, do_sample=True)
        first = await tiny_causal_lm.generate("hello", config=cfg)  # type: ignore[attr-defined]
        second = await tiny_causal_lm.generate("hello", config=cfg)  # type: ignore[attr-defined]
        assert first == second

    async def test_stop_path_runs_on_real_model(self, tiny_causal_lm: object) -> None:
        cfg = GenerationConfig(max_new_tokens=16, stop=["a"], do_sample=False)
        out = await tiny_causal_lm.generate("hello", config=cfg)  # type: ignore[attr-defined]
        assert isinstance(out, str)


class TestParseToolCalls:
    def test_qwen_hermes_single_block(self) -> None:
        from tempest_fastapi_sdk.genai.text import _parse_tool_calls

        text = (
            '<tool_call>{"name": "get_weather", '
            '"arguments": {"city": "SP"}}</tool_call>'
        )
        content, calls = _parse_tool_calls(text)
        assert content == ""
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "get_weather"
        assert calls[0]["function"]["arguments"] == {"city": "SP"}

    def test_multiple_blocks_and_surrounding_text(self) -> None:
        from tempest_fastapi_sdk.genai.text import _parse_tool_calls

        text = (
            "Sure.\n"
            '<tool_call>{"name": "a", "arguments": {}}</tool_call>\n'
            '<tool_call>{"name": "b", "arguments": {"x": 1}}</tool_call>'
        )
        content, calls = _parse_tool_calls(text)
        assert content == "Sure."
        assert [c["function"]["name"] for c in calls] == ["a", "b"]

    def test_llama_bare_json_with_parameters(self) -> None:
        from tempest_fastapi_sdk.genai.text import _parse_tool_calls

        text = '{"name": "search", "parameters": {"q": "pix"}}'
        content, calls = _parse_tool_calls(text)
        assert content == ""
        assert calls[0]["function"]["name"] == "search"
        assert calls[0]["function"]["arguments"] == {"q": "pix"}

    def test_arguments_as_json_string(self) -> None:
        from tempest_fastapi_sdk.genai.text import _parse_tool_calls

        text = '<tool_call>{"name": "f", "arguments": "{\\"a\\": 2}"}</tool_call>'
        _content, calls = _parse_tool_calls(text)
        assert calls[0]["function"]["arguments"] == {"a": 2}

    def test_plain_text_has_no_calls(self) -> None:
        from tempest_fastapi_sdk.genai.text import _parse_tool_calls

        content, calls = _parse_tool_calls("Just a normal answer.")
        assert content == "Just a normal answer."
        assert calls == []

    def test_malformed_json_ignored(self) -> None:
        from tempest_fastapi_sdk.genai.text import _parse_tool_calls

        _content, calls = _parse_tool_calls("<tool_call>{not json}</tool_call>")
        assert calls == []


@pytest.mark.model
class TestToolCallingWithModel:
    async def test_chat_with_tools_returns_pipeline_shape(
        self, tiny_instruct_lm: object
    ) -> None:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the weather for a city.",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ]
        messages = [{"role": "user", "content": "What is the weather in Sao Paulo?"}]
        result = await tiny_instruct_lm.chat_with_tools(  # type: ignore[attr-defined]
            messages, tools, config=GenerationConfig(max_new_tokens=64)
        )
        assert isinstance(result["content"], str)
        assert isinstance(result["tool_calls"], list)
        for call in result["tool_calls"]:
            assert isinstance(call["function"]["name"], str)


class TestWithoutExtra:
    @pytest.mark.skipif(
        importlib.util.find_spec("transformers") is not None,
        reason="transformers installed; the missing-extra path can't be exercised",
    )
    async def test_generate_without_transformers_raises(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        # transformers not installed -> helpful ImportError
        with pytest.raises(ImportError, match=r"\[genai\]"):
            await gen.generate("hi")
