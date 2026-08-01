"""What is loaded in memory right now, and what it is costing.

A self-hosted service can hold several models at once — a language model,
an embedder, a reranker, a diffusion pipeline — each holding gigabytes of
VRAM for as long as it stays loaded. Until this module, nothing could
answer the operational question: *what is resident right now?* The
registry knew how many entries it held, not which, and every loader kept
its idle clock to itself.

* :func:`describe_model` turns any SDK loader (or any object that walks
  like one) into a :class:`LoadedModel`, reading whatever it exposes and
  reporting ``None`` for the rest rather than guessing.
* :func:`runtime_report` puts those next to the host's memory picture in
  a :class:`ModelRuntimeReport` — the two halves of "is this box healthy"
  in one payload.

Both are pure Python: this module imports and tests with no extra
installed, and `describe_model` never triggers a load.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from tempest_fastapi_sdk.genai.schemas import HardwareInfo
from tempest_fastapi_sdk.schemas.base import BaseSchema

_MODEL_ID_ATTRS: tuple[str, ...] = ("model_id", "model_size", "model_path")
"""Attributes an SDK loader may name its model with, in priority order.

``SpeechToText`` takes a Whisper size (``"base"``), ``OnnxEmbedder`` takes
a path to a graph, and everything else takes a Hub id. They are the same
question asked of different loaders, so the inventory resolves them into
one field instead of making a caller branch on the class.
"""


class LoadedModel(BaseSchema):
    """One model handle, as it stands right now.

    Every field except ``kind`` and ``loaded`` is optional, because the
    inventory reports what a handle actually exposes. A third-party object
    that only implements ``is_loaded`` still appears — with the rest
    ``None`` — which beats omitting it from a memory audit.

    Attributes:
        key (str | None): The registry key, when it came from one.
        kind (str): The handle's class name (``"TextGenerator"``). The SDK
            reports the class rather than inventing a taxonomy over it.
        model_id (str | None): Hub id, Whisper size or graph path.
        device (str | None): Where it is resident (``cuda`` / ``cpu``).
        dtype (str | None): Compute precision, when the handle resolves one.
        loaded (bool): Whether the weights are in memory *now*.
        seconds_idle (float | None): Time since the last use, when the
            handle tracks it.
        idle_unload_seconds (float | None): The handle's configured idle
            threshold, when it has one.
        unloadable (bool): Whether the handle exposes ``unload()``.
    """

    key: str | None = Field(
        default=None,
        title="Registry key",
        description="The key this handle is registered under, when any.",
        examples=["chat-lm"],
    )
    kind: str = Field(
        title="Kind",
        description="The handle's class name.",
        examples=["TextGenerator"],
    )
    model_id: str | None = Field(
        default=None,
        title="Model id",
        description="Hub id, Whisper size or graph path.",
        examples=["Qwen/Qwen2.5-0.5B-Instruct"],
    )
    device: str | None = Field(
        default=None,
        title="Device",
        description="Where the weights are resident.",
        examples=["cuda"],
    )
    dtype: str | None = Field(
        default=None,
        title="Dtype",
        description="Compute precision, when the handle resolves one.",
        examples=["bfloat16"],
    )
    loaded: bool = Field(
        title="Loaded",
        description="Whether the weights are in memory right now.",
        examples=[True],
    )
    seconds_idle: float | None = Field(
        default=None,
        title="Seconds idle",
        description="Time since last use, when the handle tracks it.",
        examples=[42.5],
    )
    idle_unload_seconds: float | None = Field(
        default=None,
        title="Idle unload threshold",
        description="Configured idle threshold, when the handle has one.",
        examples=[300.0],
    )
    unloadable: bool = Field(
        default=False,
        title="Unloadable",
        description="Whether the handle exposes unload().",
        examples=[True],
    )

    @property
    def idle_past_threshold(self) -> bool:
        """Return whether this handle is due to be unloaded.

        Returns:
            bool: ``True`` only when the handle is loaded, tracks its idle
            time, has a threshold configured, and has crossed it. Any
            missing piece answers ``False`` — an unknown is not a reason to
            free someone's VRAM.
        """
        if not self.loaded:
            return False
        if self.seconds_idle is None or self.idle_unload_seconds is None:
            return False
        return self.seconds_idle >= self.idle_unload_seconds


class ModelRuntimeReport(BaseSchema):
    """The models resident right now, next to the host's memory picture.

    Attributes:
        models (list[LoadedModel]): Every known handle, loaded first and
            then by longest idle — the order an operator reads when
            looking for what to free.
        loaded_count (int): How many are resident.
        total_count (int): How many handles are known, loaded or not.
        hardware (HardwareInfo | None): The host snapshot, when probed.
    """

    models: list[LoadedModel] = Field(
        default_factory=list,
        title="Models",
        description="Known handles, loaded first, then longest-idle first.",
    )
    loaded_count: int = Field(
        default=0,
        title="Loaded count",
        description="How many handles are resident in memory.",
        examples=[2],
    )
    total_count: int = Field(
        default=0,
        title="Total count",
        description="How many handles are known, loaded or not.",
        examples=[4],
    )
    hardware: HardwareInfo | None = Field(
        default=None,
        title="Hardware",
        description="Host memory picture, when probed.",
    )


def _first_attr(obj: Any, names: tuple[str, ...]) -> str | None:
    """Return the first attribute present on ``obj``, as a string.

    Args:
        obj (Any): The handle to read.
        names (tuple[str, ...]): Attribute names, in priority order.

    Returns:
        str | None: The first value found, or ``None`` when none are.
    """
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return str(value)
    return None


def _float_or_none(obj: Any, name: str) -> float | None:
    """Read a numeric attribute without letting a property raise.

    A handle's ``seconds_idle`` is a property, and a foreign implementation
    may raise from it. An inventory that dies because one entry misbehaves
    is worse than one that reports that entry as unknown.

    Args:
        obj (Any): The handle to read.
        name (str): The attribute name.

    Returns:
        float | None: The value as a float, or ``None`` when absent, not
        numeric, or raising.
    """
    try:
        value = getattr(obj, name, None)
    except Exception:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def describe_model(model: Any, *, key: str | None = None) -> LoadedModel:
    """Describe one model handle without loading or touching it.

    Reads only attributes, so it is safe to call on a lazily-configured
    handle: a generator that has never loaded reports ``loaded=False`` and
    stays unloaded.

    Example:

        >>> info = describe_model(generator, key="chat-lm")
        >>> info.kind, info.loaded
        ('TextGenerator', True)

    Args:
        model (Any): Any SDK loader — ``TextGenerator``, ``Embedder``,
            ``ImageGenerator``, ``Reranker``, ``SpeechToText``,
            ``OnnxEmbedder``, ``ClassifierModerator`` — or any object
            exposing a similar surface.
        key (str | None): The registry key to record alongside it.

    Returns:
        LoadedModel: What the handle exposes; ``None`` for what it does
        not, never a guess.
    """
    dtype = getattr(model, "dtype", None)
    device = getattr(model, "device", None)
    return LoadedModel(
        key=key,
        kind=type(model).__name__,
        model_id=_first_attr(model, _MODEL_ID_ATTRS),
        device=str(device) if device is not None else None,
        dtype=str(getattr(dtype, "value", dtype)) if dtype is not None else None,
        loaded=bool(getattr(model, "is_loaded", False)),
        seconds_idle=_float_or_none(model, "seconds_idle"),
        idle_unload_seconds=_float_or_none(model, "idle_unload_seconds"),
        unloadable=callable(getattr(model, "unload", None)),
    )


def runtime_report(
    models: dict[str, Any] | list[Any],
    *,
    hardware: HardwareInfo | None = None,
    probe: bool = True,
) -> ModelRuntimeReport:
    """Describe every handle you hold, next to the host's memory.

    Sorting puts loaded handles first and, among them, the longest-idle
    first: that is the order someone reads when a card is full and they
    need to decide what to free.

    Example:

        >>> report = runtime_report({"chat": generator, "embed": embedder})
        >>> report.loaded_count
        1

    Args:
        models (dict[str, Any] | list[Any]): The handles, either keyed
            (the keys land on ``LoadedModel.key``) or as a plain list.
        hardware (HardwareInfo | None): A snapshot to reuse instead of
            probing again.
        probe (bool): When no ``hardware`` is given, probe the host.
            Pass ``False`` to skip it — probing reads NVML and is the only
            part of this call that costs anything.

    Returns:
        ModelRuntimeReport: The handles plus, optionally, the host
        snapshot.
    """
    if isinstance(models, dict):
        described = [describe_model(item, key=key) for key, item in models.items()]
    else:
        described = [describe_model(item) for item in models]
    described.sort(
        key=lambda item: (not item.loaded, -(item.seconds_idle or 0.0)),
    )
    snapshot = hardware
    if snapshot is None and probe:
        from tempest_fastapi_sdk.genai.hardware import probe_hardware

        snapshot = probe_hardware()
    return ModelRuntimeReport(
        models=described,
        loaded_count=sum(1 for item in described if item.loaded),
        total_count=len(described),
        hardware=snapshot,
    )


__all__: list[str] = [
    "LoadedModel",
    "ModelRuntimeReport",
    "describe_model",
    "runtime_report",
]
