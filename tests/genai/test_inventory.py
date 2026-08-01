"""Tests for the runtime model inventory.

No model is ever loaded: the inventory reads attributes only, which is the
property that makes it safe to call on a live service.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.genai import (
    GPUInfo,
    HardwareInfo,
    LoadedModel,
    ModelRegistry,
    describe_model,
    make_genai_router,
    runtime_report,
)


class FakeHandle:
    """A loader-shaped object with a configurable surface."""

    def __init__(
        self,
        model_id: str = "org/model",
        *,
        loaded: bool = True,
        device: str = "cuda",
        dtype: str | None = "bfloat16",
        seconds_idle: float | None = 10.0,
        idle_unload_seconds: float | None = 300.0,
    ) -> None:
        self.model_id = model_id
        self.device = device
        if dtype is not None:
            self.dtype = dtype
        self._loaded = loaded
        self._seconds_idle = seconds_idle
        if idle_unload_seconds is not None:
            self.idle_unload_seconds = idle_unload_seconds
        self.unload_calls = 0

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def seconds_idle(self) -> float:
        if self._seconds_idle is None:
            raise RuntimeError("this handle has no idle clock")
        return self._seconds_idle

    def unload(self) -> None:
        self.unload_calls += 1
        self._loaded = False

    def unload_if_idle(self) -> bool:
        threshold = getattr(self, "idle_unload_seconds", None)
        if threshold is None or not self._loaded:
            return False
        if self._seconds_idle is None or self._seconds_idle < threshold:
            return False
        self.unload()
        return True


class BareHandle:
    """The minimum an object can expose and still be inventoried."""

    @property
    def is_loaded(self) -> bool:
        return True


class WhisperShaped:
    """Names its model ``model_size``, the way ``SpeechToText`` does."""

    def __init__(self) -> None:
        self.model_size = "base"
        self.device = "cpu"

    @property
    def is_loaded(self) -> bool:
        return False


class OnnxShaped:
    """Names its model ``model_path``, the way ``OnnxEmbedder`` does."""

    def __init__(self) -> None:
        self.model_path = "/models/embed.onnx"

    @property
    def is_loaded(self) -> bool:
        return True


def _hardware() -> HardwareInfo:
    return HardwareInfo(
        cpu_cores=8,
        ram_total_bytes=32 * 10**9,
        ram_available_bytes=16 * 10**9,
        has_cuda=True,
        gpus=[
            GPUInfo(
                index=0,
                name="Test GPU",
                vram_total_bytes=24 * 10**9,
                vram_free_bytes=8 * 10**9,
            )
        ],
        has_mps=False,
        disk_free_bytes=100 * 10**9,
    )


class TestDescribeModel:
    def test_reads_the_full_surface(self) -> None:
        info = describe_model(FakeHandle("org/lm"), key="chat")
        assert info.key == "chat"
        assert info.kind == "FakeHandle"
        assert info.model_id == "org/lm"
        assert info.device == "cuda"
        assert info.dtype == "bfloat16"
        assert info.loaded is True
        assert info.seconds_idle == 10.0
        assert info.idle_unload_seconds == 300.0
        assert info.unloadable is True

    def test_a_bare_handle_still_appears(self) -> None:
        info = describe_model(BareHandle())
        assert info.kind == "BareHandle"
        assert info.loaded is True
        assert info.model_id is None
        assert info.device is None
        assert info.seconds_idle is None
        assert info.unloadable is False

    def test_falls_back_to_model_size(self) -> None:
        assert describe_model(WhisperShaped()).model_id == "base"

    def test_falls_back_to_model_path(self) -> None:
        assert describe_model(OnnxShaped()).model_id == "/models/embed.onnx"

    def test_a_raising_idle_clock_reports_unknown(self) -> None:
        info = describe_model(FakeHandle(seconds_idle=None))
        assert info.seconds_idle is None
        assert info.loaded is True

    def test_never_triggers_a_load(self) -> None:
        class Exploding:
            @property
            def is_loaded(self) -> bool:
                return False

            def load(self) -> None:
                raise AssertionError("describe_model must not load")

        assert describe_model(Exploding()).loaded is False

    def test_enum_dtype_is_reported_by_value(self) -> None:
        from tempest_fastapi_sdk.genai import ModelDtype

        handle = FakeHandle(dtype=None)
        handle.dtype = ModelDtype.FLOAT16
        assert describe_model(handle).dtype == "float16"


class TestIdlePastThreshold:
    def test_true_only_when_every_piece_is_known(self) -> None:
        info = describe_model(FakeHandle(seconds_idle=400.0))
        assert info.idle_past_threshold is True

    def test_false_below_the_threshold(self) -> None:
        assert (
            describe_model(FakeHandle(seconds_idle=10.0)).idle_past_threshold is False
        )

    def test_false_when_not_loaded(self) -> None:
        handle = FakeHandle(loaded=False, seconds_idle=9999.0)
        assert describe_model(handle).idle_past_threshold is False

    def test_false_without_a_threshold(self) -> None:
        handle = FakeHandle(seconds_idle=9999.0, idle_unload_seconds=None)
        assert describe_model(handle).idle_past_threshold is False

    def test_false_without_an_idle_clock(self) -> None:
        assert describe_model(BareHandle()).idle_past_threshold is False


class TestRuntimeReport:
    def test_counts_loaded_against_total(self) -> None:
        report = runtime_report(
            {
                "a": FakeHandle(loaded=True),
                "b": FakeHandle(loaded=False),
                "c": FakeHandle(loaded=True),
            },
            probe=False,
        )
        assert report.total_count == 3
        assert report.loaded_count == 2

    def test_loaded_first_then_longest_idle(self) -> None:
        report = runtime_report(
            {
                "fresh": FakeHandle(seconds_idle=5.0),
                "cold": FakeHandle(loaded=False, seconds_idle=900.0),
                "stale": FakeHandle(seconds_idle=600.0),
            },
            probe=False,
        )
        assert [item.key for item in report.models] == ["stale", "fresh", "cold"]

    def test_accepts_a_plain_list(self) -> None:
        report = runtime_report([FakeHandle(), FakeHandle()], probe=False)
        assert report.total_count == 2
        assert all(item.key is None for item in report.models)

    def test_probe_false_skips_the_hardware_snapshot(self) -> None:
        assert runtime_report([FakeHandle()], probe=False).hardware is None

    def test_injected_hardware_is_reused(self) -> None:
        snapshot = _hardware()
        report = runtime_report([FakeHandle()], hardware=snapshot)
        assert report.hardware is not None
        assert report.hardware.gpus[0].vram_free_bytes == 8 * 10**9

    def test_empty_input_is_a_valid_report(self) -> None:
        report = runtime_report([], probe=False)
        assert report.models == []
        assert report.loaded_count == 0
        assert report.total_count == 0


class TestRegistryInventory:
    def _registry(self) -> tuple[ModelRegistry, FakeHandle, FakeHandle]:
        registry = ModelRegistry(max_models=4)
        chat = FakeHandle("org/lm", seconds_idle=900.0)
        embed = FakeHandle("org/embed", seconds_idle=5.0)
        registry.get("chat", lambda: chat)
        registry.get("embed", lambda: embed)
        return registry, chat, embed

    def test_reports_the_held_keys(self) -> None:
        registry, _chat, _embed = self._registry()
        report = registry.inventory(probe=False)
        assert {item.key for item in report.models} == {"chat", "embed"}
        assert report.loaded_count == 2

    def test_items_is_a_copy(self) -> None:
        registry, _chat, _embed = self._registry()
        snapshot = registry.items()
        registry.evict("chat")
        assert set(snapshot) == {"chat", "embed"}
        assert len(registry) == 1

    def test_unload_idle_frees_only_the_stale_one(self) -> None:
        registry, chat, embed = self._registry()
        assert registry.unload_idle() == ["chat"]
        assert chat.unload_calls == 1
        assert embed.unload_calls == 0

    def test_unloaded_entries_stay_registered(self) -> None:
        registry, _chat, _embed = self._registry()
        registry.unload_idle()
        assert len(registry) == 2
        assert "chat" in registry

    def test_handles_without_the_hook_are_skipped(self) -> None:
        registry = ModelRegistry(max_models=2)

        class NoHook:
            def unload(self) -> None:
                return None

        registry.get("plain", NoHook)
        assert registry.unload_idle() == []

    def test_inventory_reuses_an_injected_snapshot(self) -> None:
        registry, _chat, _embed = self._registry()
        report = registry.inventory(hardware=_hardware())
        assert report.hardware is not None
        assert report.hardware.cpu_cores == 8


class TestModelsRoute:
    def _client(self, models: Any) -> TestClient:
        app = FastAPI()
        app.include_router(make_genai_router(models=models))
        return TestClient(app)

    def test_serves_a_registry(self) -> None:
        registry = ModelRegistry(max_models=2)
        registry.get("chat", lambda: FakeHandle("org/lm"))
        response = self._client(registry).get("/api/genai/models?probe=false")
        assert response.status_code == 200
        body = response.json()
        assert body["loaded_count"] == 1
        assert body["models"][0]["key"] == "chat"
        assert body["models"][0]["model_id"] == "org/lm"

    def test_serves_a_plain_dict(self) -> None:
        response = self._client({"chat": FakeHandle("org/lm")}).get(
            "/api/genai/models?probe=false",
        )
        assert response.json()["models"][0]["key"] == "chat"

    def test_probe_false_omits_the_hardware(self) -> None:
        response = self._client([FakeHandle()]).get("/api/genai/models?probe=false")
        assert response.json()["hardware"] is None

    def test_models_alone_is_enough_to_build_the_router(self) -> None:
        router = make_genai_router(models=ModelRegistry())
        paths = {route.path for route in router.routes}  # type: ignore[attr-defined]
        assert "/api/genai/models" in paths
        assert "/api/genai/generate" not in paths

    def test_an_empty_collection_still_mounts(self) -> None:
        response = self._client([]).get("/api/genai/models?probe=false")
        assert response.status_code == 200
        assert response.json()["total_count"] == 0

    def test_no_object_at_all_still_refuses(self) -> None:
        with pytest.raises(ValueError, match="at least one GenAI object"):
            make_genai_router()


class TestLoaderUniformity:
    """The three loaders that used to lack an idle clock now have one."""

    def test_classifier_moderator(self) -> None:
        from tempest_fastapi_sdk.genai import ClassifierModerator

        moderator = ClassifierModerator(
            "org/toxicity",
            device="cpu",
            idle_unload_seconds=0.0,
        )
        assert moderator.seconds_idle >= 0.0
        assert moderator.unload_if_idle() is False
        moderator._model = object()
        assert moderator.unload_if_idle() is True
        assert moderator.is_loaded is False

    def test_speech_to_text(self) -> None:
        from tempest_fastapi_sdk.genai.audio import SpeechToText

        stt = SpeechToText("base", device="cpu", idle_unload_seconds=0.0)
        assert stt.seconds_idle >= 0.0
        assert stt.unload_if_idle() is False
        stt._model = object()
        assert stt.unload_if_idle() is True
        assert stt.is_loaded is False

    def test_onnx_embedder_can_unload(self) -> None:
        from tempest_fastapi_sdk.genai import OnnxEmbedder

        embedder = OnnxEmbedder("model.onnx", tokenizer="org/tok")
        embedder._session = object()
        assert embedder.is_loaded is True
        embedder.unload()
        assert embedder.is_loaded is False

    @pytest.mark.parametrize(
        "factory",
        [
            lambda: __import__(
                "tempest_fastapi_sdk.genai", fromlist=["TextGenerator"]
            ).TextGenerator("org/lm", device="cpu"),
            lambda: __import__(
                "tempest_fastapi_sdk.genai", fromlist=["Embedder"]
            ).Embedder("org/emb", device="cpu"),
            lambda: __import__(
                "tempest_fastapi_sdk.genai", fromlist=["ImageGenerator"]
            ).ImageGenerator("org/img", device="cpu"),
        ],
    )
    def test_real_loaders_describe_cleanly(self, factory: Any) -> None:
        info = describe_model(factory())
        assert isinstance(info, LoadedModel)
        assert info.model_id is not None
        assert info.device == "cpu"
        assert info.loaded is False
        assert info.unloadable is True
