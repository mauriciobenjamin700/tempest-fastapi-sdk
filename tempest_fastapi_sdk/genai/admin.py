"""Dashboard cards showing what the service is holding in memory.

:func:`make_model_cards` turns the runtime inventory into
:class:`~tempest_fastapi_sdk.admin.MetricCard` entries for
``AdminSite(dashboard_cards=[...])`` — so the operator who already watches
orders and signups on that page also sees which models are resident, on
what device, and how much VRAM is left.

The cards ignore the ``AsyncSession`` the dashboard hands them: this data
comes from process memory, not the database. That is why they are built
here rather than by the caller — the signature match is the only awkward
part, and it belongs in one place.

Imports with no extra installed. ``prometheus_client`` is not involved;
for the metrics counterpart see
:meth:`~tempest_fastapi_sdk.genai.GenAIMetrics.observe_inventory`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.admin.dashboard import (
    MetricCard,
    MetricPartition,
    MetricValue,
)
from tempest_fastapi_sdk.genai.inventory import runtime_report

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _report(models: Any, *, probe: bool) -> Any:
    """Build the inventory from whatever the caller handed us.

    Args:
        models (Any): A ``ModelRegistry``, or a dict/list of handles.
        probe (bool): Whether to read the host memory picture.

    Returns:
        ModelRuntimeReport: The inventory.
    """
    if hasattr(models, "inventory"):
        return models.inventory(probe=probe)
    return runtime_report(models, probe=probe)


def make_model_cards(
    models: Any,
    *,
    include_vram: bool = True,
) -> list[MetricCard]:
    """Build dashboard cards describing the models resident right now.

    Example:

        >>> site = AdminSite(
        ...     dashboard_cards=[*make_model_cards(registry)],
        ... )

    The handles are read at render time, not at call time, so a registry
    that is empty when the site is built still reports correctly once
    models load.

    Args:
        models (Any): A
            :class:`~tempest_fastapi_sdk.genai.ModelRegistry`, or a dict or
            list of model handles you hold yourself.
        include_vram (bool): Add the free-VRAM card. It is the only one
            that probes the host, so drop it on a CPU-only box or when the
            dashboard should stay free of NVML reads.

    Returns:
        list[MetricCard]: Two cards — resident count and per-device
        breakdown — plus free VRAM when ``include_vram`` is on.
    """

    async def loaded_count(_session: AsyncSession) -> MetricValue:
        """Return how many handles are resident, out of how many known."""
        report = _report(models, probe=False)
        return MetricValue(
            value=report.loaded_count,
            unit=f"of {report.total_count}",
        )

    async def per_device(_session: AsyncSession) -> MetricPartition:
        """Return the resident handles broken down by device."""
        report = _report(models, probe=False)
        counts: dict[str, float] = {}
        for model in report.models:
            if not model.loaded:
                continue
            key = model.device or "unknown"
            counts[key] = counts.get(key, 0.0) + 1.0
        return MetricPartition(segments=sorted(counts.items()))

    async def vram_free(_session: AsyncSession) -> MetricValue:
        """Return free VRAM across CUDA devices, in GB."""
        report = _report(models, probe=True)
        hardware = report.hardware
        if hardware is None or not hardware.gpus:
            return MetricValue(value="-", unit="no CUDA device")
        total = sum(gpu.vram_free_bytes for gpu in hardware.gpus)
        return MetricValue(value=round(total / 10**9, 1), unit="GB free")

    cards = [
        MetricCard(
            label="Models resident",
            compute=loaded_count,
            help_text="Handles with weights in memory right now.",
        ),
        MetricCard(
            label="Resident by device",
            compute=per_device,
            help_text="Where the loaded weights are.",
        ),
    ]
    if include_vram:
        cards.append(
            MetricCard(
                label="VRAM free",
                compute=vram_free,
                help_text="Across every CUDA device.",
            ),
        )
    return cards


__all__: list[str] = ["make_model_cards"]
