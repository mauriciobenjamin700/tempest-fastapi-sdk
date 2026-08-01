"""Tests for the inventory gauges and the admin model cards.

Both read a runtime inventory and publish it somewhere else — Prometheus
and the admin dashboard — so both are exercised against handles that never
load anything.
"""

from __future__ import annotations

from typing import Any

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from tempest_fastapi_sdk.admin.dashboard import MetricPartition, MetricValue
from tempest_fastapi_sdk.genai import (
    GenAIMetrics,
    GPUInfo,
    HardwareInfo,
    ModelRegistry,
    make_model_cards,
    runtime_report,
)


class FakeHandle:
    def __init__(
        self,
        model_id: str = "org/model",
        *,
        loaded: bool = True,
        device: str = "cuda",
    ) -> None:
        self.model_id = model_id
        self.device = device
        self._loaded = loaded

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def unload(self) -> None:
        self._loaded = False


def _hardware(free_gb: float = 8.0, gpus: int = 1) -> HardwareInfo:
    return HardwareInfo(
        cpu_cores=8,
        ram_total_bytes=32 * 10**9,
        ram_available_bytes=16 * 10**9,
        has_cuda=gpus > 0,
        gpus=[
            GPUInfo(
                index=index,
                name=f"GPU{index}",
                vram_total_bytes=24 * 10**9,
                vram_free_bytes=int(free_gb * 10**9),
            )
            for index in range(gpus)
        ],
        has_mps=False,
        disk_free_bytes=100 * 10**9,
    )


def _lines(registry: CollectorRegistry, needle: str) -> list[str]:
    """Return the non-comment exposition lines containing ``needle``."""
    return [
        line
        for line in generate_latest(registry).decode().splitlines()
        if needle in line and not line.startswith("#")
    ]


class TestInventoryGauges:
    def test_publishes_loaded_by_kind_and_device(self) -> None:
        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        metrics.observe_inventory(
            runtime_report(
                [FakeHandle(device="cuda"), FakeHandle(device="cpu")],
                probe=False,
            ),
        )
        lines = _lines(registry, "genai_models_loaded")
        assert any('device="cuda"' in line and line.endswith("1.0") for line in lines)
        assert any('device="cpu"' in line and line.endswith("1.0") for line in lines)

    def test_counts_two_of_a_kind_on_one_series(self) -> None:
        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        metrics.observe_inventory(
            runtime_report([FakeHandle(), FakeHandle()], probe=False),
        )
        lines = _lines(registry, "genai_models_loaded")
        assert len(lines) == 1
        assert lines[0].endswith("2.0")

    def test_unloaded_handles_are_not_reported_as_resident(self) -> None:
        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        metrics.observe_inventory(
            runtime_report([FakeHandle(loaded=False)], probe=False),
        )
        assert _lines(registry, "genai_models_loaded") == []
        assert _lines(registry, "genai_models_known") == ["genai_models_known 1.0"]

    def test_a_snapshot_replaces_the_previous_one(self) -> None:
        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        handle = FakeHandle()
        metrics.observe_inventory(runtime_report([handle], probe=False))
        assert _lines(registry, "genai_models_loaded") != []
        handle.unload()
        metrics.observe_inventory(runtime_report([handle], probe=False))
        assert _lines(registry, "genai_models_loaded") == []

    def test_missing_device_is_labelled_unknown(self) -> None:
        class NoDevice:
            @property
            def is_loaded(self) -> bool:
                return True

        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        metrics.observe_inventory(runtime_report([NoDevice()], probe=False))
        assert any(
            'device="unknown"' in line
            for line in _lines(registry, "genai_models_loaded")
        )

    def test_vram_is_published_per_device(self) -> None:
        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        metrics.observe_inventory(
            runtime_report([FakeHandle()], hardware=_hardware(free_gb=8.0, gpus=2)),
        )
        lines = _lines(registry, "genai_gpu_vram_free_bytes")
        assert len(lines) == 2
        assert all(line.endswith("8e+09") for line in lines)

    def test_no_hardware_leaves_vram_untouched(self) -> None:
        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        metrics.observe_inventory(runtime_report([FakeHandle()], probe=False))
        assert _lines(registry, "genai_gpu_vram_free_bytes") == []

    def test_accepts_a_registry_inventory(self) -> None:
        holder = ModelRegistry(max_models=2)
        holder.get("chat", FakeHandle)
        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        metrics.observe_inventory(holder.inventory(probe=False))
        assert _lines(registry, "genai_models_known") == ["genai_models_known 1.0"]


class TestAdminModelCards:
    async def _compute(self, card: Any) -> Any:
        return await card.compute(None)

    def test_builds_three_cards_by_default(self) -> None:
        cards = make_model_cards(ModelRegistry())
        assert [card.label for card in cards] == [
            "Models resident",
            "Resident by device",
            "VRAM free",
        ]

    def test_vram_card_is_optional(self) -> None:
        cards = make_model_cards(ModelRegistry(), include_vram=False)
        assert all(card.label != "VRAM free" for card in cards)

    @pytest.mark.asyncio
    async def test_counts_loaded_against_known(self) -> None:
        holder = ModelRegistry(max_models=4)
        holder.get("a", lambda: FakeHandle(loaded=True))
        holder.get("b", lambda: FakeHandle(loaded=False))
        cards = make_model_cards(holder, include_vram=False)
        value = await self._compute(cards[0])
        assert isinstance(value, MetricValue)
        assert value.value == 1
        assert value.unit == "of 2"

    @pytest.mark.asyncio
    async def test_breaks_down_by_device(self) -> None:
        cards = make_model_cards(
            [
                FakeHandle(device="cuda"),
                FakeHandle(device="cuda"),
                FakeHandle(device="cpu"),
                FakeHandle(device="cpu", loaded=False),
            ],
            include_vram=False,
        )
        partition = await self._compute(cards[1])
        assert isinstance(partition, MetricPartition)
        assert partition.segments == [("cpu", 1.0), ("cuda", 2.0)]
        assert partition.total == 3.0

    @pytest.mark.asyncio
    async def test_reads_the_handles_at_render_time(self) -> None:
        holder = ModelRegistry(max_models=4)
        cards = make_model_cards(holder, include_vram=False)
        empty = await self._compute(cards[0])
        assert empty.value == 0
        holder.get("late", lambda: FakeHandle(loaded=True))
        filled = await self._compute(cards[0])
        assert filled.value == 1

    @pytest.mark.asyncio
    async def test_vram_card_without_cuda_says_so(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tempest_fastapi_sdk.genai import admin as admin_module

        monkeypatch.setattr(
            admin_module,
            "runtime_report",
            lambda models, **_kwargs: runtime_report(
                models,
                hardware=_hardware(gpus=0),
            ),
        )
        cards = make_model_cards([FakeHandle()])
        value = await self._compute(cards[2])
        assert value.value == "-"
        assert value.unit == "no CUDA device"

    @pytest.mark.asyncio
    async def test_vram_card_sums_every_device(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tempest_fastapi_sdk.genai import admin as admin_module

        monkeypatch.setattr(
            admin_module,
            "runtime_report",
            lambda models, **_kwargs: runtime_report(
                models,
                hardware=_hardware(free_gb=4.0, gpus=2),
            ),
        )
        cards = make_model_cards([FakeHandle()])
        value = await self._compute(cards[2])
        assert value.value == 8.0
        assert value.unit == "GB free"
