"""Serving a predictor over HTTP, and swapping its model without a deploy.

Two things a device on a shelf needs that a notebook does not: an endpoint
someone can call, and a way to receive a new model without a technician.

`make_prediction_router` provides the first.
:class:`RegistryModelSource` provides the second, over the
:class:`~tempest_fastapi_sdk.artifacts.ArtifactRegistry` this SDK already
ships — the device asks which version is current, downloads it if it does
not have it, and reloads. A bad file leaves the previous model serving,
because a fleet update that can take a device offline is worse than one
that occasionally does nothing.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, status
from fastapi.params import Depends
from pydantic import Field

from tempest_fastapi_sdk.artifacts.digest import _read_file_digest
from tempest_fastapi_sdk.modelops.monitoring import MonitoringReport
from tempest_fastapi_sdk.modelops.serving import OnnxPredictor, PredictorInfo
from tempest_fastapi_sdk.schemas.base import BaseSchema

_UNPROCESSABLE_CONTENT: int = 422
"""``422``, spelled as the number rather than a Starlette constant.

Starlette 1.x renamed ``HTTP_422_UNPROCESSABLE_ENTITY`` to
``HTTP_422_UNPROCESSABLE_CONTENT`` and made the old name warn. The warning is
a ``StarletteDeprecationWarning``, which subclasses ``UserWarning`` rather than
``DeprecationWarning`` — so a consumer running ``filterwarnings = ["error"]``
gets it **raised** while this route builds its response, and the 422 the route
promises becomes a 500.

The new name is not usable either: measured on 2026-08-30, it is absent from
starlette 0.46.0, the floor ``fastapi>=0.141.1`` allows. Neither constant spans
the supported range, so the number is the only spelling that does. The other
statuses on this router keep their constants — only these four names warn.
"""

DEFAULT_MAX_PREDICT_ROWS: int = 10_000
"""Rows one ``POST /predict`` may carry before it is refused with ``422``.

Without a limit, one request decides how much memory and how many seconds of
inference the device spends on it: every row is parsed, coerced and scored
before that request is answered. Ten thousand rows is well past the batch
sizes the serving guidance here measures, and still bounds the worst
request. Raise it per router with ``max_rows=``; ``None`` removes the limit
for a device that trusts its callers.
"""


if TYPE_CHECKING:
    from collections.abc import Sequence

    from tempest_fastapi_sdk.artifacts.registry import ArtifactRegistry
    from tempest_fastapi_sdk.modelops.monitoring import (
        PredictionMetrics,
        PredictionMonitor,
    )


class PredictRequestSchema(BaseSchema):
    """Request body for ``POST /predict``.

    Attributes:
        rows (list[list[float]]): One list of features per row. Always a
            list of lists, even for a single prediction — accepting both
            shapes would make a client's off-by-one silently produce a
            different answer instead of an error.
    """

    rows: list[list[float]] = Field(
        title="Rows",
        description="One list of feature values per row.",
        examples=[[[5.1, 3.5, 1.4, 0.2]]],
    )


class PredictResponseSchema(BaseSchema):
    """Response body for ``POST /predict``.

    Attributes:
        labels (list[Any]): Predicted class or value per row.
        probabilities (list[list[float]]): Class scores, when available.
        n_rows (int): Rows predicted.
        seconds (float): Inference duration on the device.
        model_version (str | None): Which version answered, when the
            predictor is registry-backed. Without it a client cannot tell
            whether a changed answer came from a changed model.
    """

    labels: list[Any] = Field(
        default_factory=list,
        title="Labels",
        description="Predicted class or value per row.",
    )
    probabilities: list[list[float]] = Field(
        default_factory=list,
        title="Probabilities",
        description="Class scores per row, when available.",
    )
    n_rows: int = Field(
        default=0,
        title="Rows",
        description="Rows predicted.",
    )
    seconds: float = Field(
        default=0.0,
        title="Seconds",
        description="Inference duration on the device.",
    )
    model_version: str | None = Field(
        default=None,
        title="Model version",
        description="Which model version produced this answer.",
    )


class RegistryModelSource:
    """Resolves the current model version from an artifact registry.

    Example:

        >>> source = RegistryModelSource(registry, "fraud-classifier", "models/")
        >>> await source.sync(predictor)     # from a periodic task

    The device holds one file per version under ``cache_dir``, so a
    rollback is a reload rather than a re-download. Nothing is deleted
    automatically: on a device with a small disk you want to decide when
    old versions go, not discover they went.

    A download lands in a ``.part`` file and is renamed into place only
    once it is complete (and, when the row carries a digest, verified), so
    a connection dropped mid-file is retried on the next sync instead of
    being mistaken for a cached version. :meth:`sync` holds a lock across
    fetch and reload, so a periodic task and ``POST /model/sync`` racing
    each other download and reload a version once.

    Attributes:
        name (str): The logical artifact key in the registry.
        cache_dir (Path): Where downloaded versions live.
        checksum_field (str | None): Row attribute holding the expected hex
            SHA-256 of the file.
        current_version (str | None): The version currently loaded.
    """

    def __init__(
        self,
        registry: ArtifactRegistry[Any],
        name: str,
        cache_dir: str | Path,
        *,
        checksum_field: str | None = "sha256",
    ) -> None:
        """Configure the source.

        Args:
            registry (ArtifactRegistry[Any]): The registry to ask.
            name (str): The logical artifact key.
            cache_dir (str | Path): Where to keep downloaded versions.
            checksum_field (str | None): Row attribute holding the expected
                hex SHA-256 of the file. The SDK's
                :class:`~tempest_fastapi_sdk.artifacts.ArtifactVersionMixin`
                does not declare one, so this is opt-in by schema: declare a
                ``sha256`` column on your version model (written by whoever
                publishes the file) and every download is verified before it
                is cached or loaded. A row without the attribute, or with an
                empty value, is downloaded unverified. ``None`` turns the
                check off.
        """
        self._registry = registry
        self.name = name
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.checksum_field = checksum_field
        self.current_version: str | None = None
        self._lock = asyncio.Lock()

    def _path_for(self, version: str) -> Path:
        """Return the local path for a version.

        Args:
            version (str): The version label.

        Returns:
            Path: Where that version is (or would be) cached.
        """
        safe = version.replace("/", "_")
        return self.cache_dir / f"{self.name}-{safe}.onnx"

    def _expected_digest(self, row: Any) -> str | None:
        """Return the digest the row advertises, when it advertises one.

        Args:
            row (Any): The registry row.

        Returns:
            str | None: The lowercase hex SHA-256, or ``None``.
        """
        if self.checksum_field is None:
            return None
        value = getattr(row, self.checksum_field, None)
        if not value:
            return None
        return str(value).strip().lower()

    async def fetch(self) -> tuple[str, Path] | None:
        """Download the current version if it is not already cached.

        Returns:
            tuple[str, Path] | None: The version and its local path, or
            ``None`` when the registry has no current version — which is
            a normal state before the first activation, not an error.

        Raises:
            RuntimeError: When the registry row names an object the
                storage client cannot produce, or when the downloaded file
                does not match the SHA-256 the row advertises. A mismatched
                file is deleted, never cached.
        """
        row = await self._registry.current(self.name)
        if row is None:
            return None
        version = str(row.version)
        path = self._path_for(version)
        if path.exists():
            return version, path

        minio = self._registry.minio
        bucket = self._registry.bucket
        if minio is None or bucket is None:
            raise RuntimeError(
                f"{self.name} version {version} is registered but the registry "
                "has no object-storage client to download it with",
            )
        partial = path.with_name(f"{path.name}.part")
        try:
            await minio.fget_object(str(row.file_key), partial, bucket=bucket)
            expected = self._expected_digest(row)
            if expected is not None:
                actual, _ = await asyncio.to_thread(_read_file_digest, partial)
                if actual != expected:
                    raise RuntimeError(
                        f"{self.name} version {version}: downloaded file has "
                        f"SHA-256 {actual}, the registry row advertises "
                        f"{expected}; refusing to load it",
                    )
            os.replace(partial, path)
        finally:
            partial.unlink(missing_ok=True)
        return version, path

    async def sync(self, predictor: OnnxPredictor) -> str | None:
        """Reload ``predictor`` when the registry has a different version.

        Safe to call on a schedule, and concurrently: calls are serialised,
        and a no-op when the current version is already loaded. The reload
        itself (session build plus warm-up) runs in a worker thread, so the
        event loop keeps answering requests while a large model loads.

        Args:
            predictor (OnnxPredictor): The predictor to update.

        Returns:
            str | None: The version now loaded, or ``None`` when the
            registry had nothing to offer.
        """
        async with self._lock:
            found = await self.fetch()
            if found is None:
                return None
            version, path = found
            if version == self.current_version:
                return version
            await asyncio.to_thread(predictor.reload, path)
            self.current_version = version
            return version


def _public_info(info: PredictorInfo, *, expose_path: bool) -> PredictorInfo:
    """Return ``info`` as the HTTP surface shows it.

    Args:
        info (PredictorInfo): The predictor's description.
        expose_path (bool): Keep the absolute file path.

    Returns:
        PredictorInfo: ``info`` itself, or a copy whose ``path`` is only the
        file name — enough to tell versions apart without telling a caller
        how the device's disk is laid out.
    """
    if expose_path:
        return info
    return info.model_copy(update={"path": Path(info.path).name})


def make_prediction_router(
    predictor: OnnxPredictor,
    *,
    source: RegistryModelSource | None = None,
    monitor: PredictionMonitor | None = None,
    metrics: PredictionMetrics | None = None,
    prefix: str = "/api/predict",
    tags: list[str] | None = None,
    max_rows: int | None = DEFAULT_MAX_PREDICT_ROWS,
    dependencies: Sequence[Depends] | None = None,
    admin_dependencies: Sequence[Depends] | None = None,
    expose_model_path: bool = False,
) -> APIRouter:
    """Build a router serving one predictor.

    Endpoints:

    * ``POST {prefix}/`` — predict for a batch of rows.
    * ``GET  {prefix}/model`` — what is loaded, which providers are
      **actually** in use, and the thread configuration.
    * ``POST {prefix}/model/sync`` — check the registry and reload if a
      newer version is current (only with a ``source``).
    * ``GET  {prefix}/monitor`` — latency, input drift and prediction
      distribution (only with a ``monitor``).

    Inference and reload run in a worker thread (``asyncio.to_thread``):
    both are blocking ONNX Runtime calls, and on the event loop a 1 s
    inference would stall every other request for 1 s.

    The router ships **no authentication of its own**. ``GET /model``,
    ``POST /model/sync`` and ``GET /monitor`` are operational — pass the
    guard as ``admin_dependencies`` to protect them while ``POST /`` stays
    as open as the rest of the service, or as ``dependencies`` to protect
    every route.

    Example:

        >>> predictor = OnnxPredictor("dist/classifier.onnx")
        >>> app.include_router(
        ...     make_prediction_router(
        ...         predictor,
        ...         admin_dependencies=[Depends(require_operator)],
        ...     ),
        ... )

    Args:
        predictor (OnnxPredictor): The loaded model.
        source (RegistryModelSource | None): Registry-backed updates.
            Without it the sync endpoint is not mounted, since there
            would be nothing to sync against.
        monitor (PredictionMonitor | None): Records every request and
            serves the monitor endpoint. Without it the endpoint is not
            mounted.
        metrics (PredictionMetrics | None): Publishes the same numbers to
            Prometheus. Independent of ``monitor``: a device can export
            latency without carrying a drift baseline.
        prefix (str): URL prefix.
        tags (list[str] | None): OpenAPI tags.
        max_rows (int | None): Rows one request may carry; more is a
            ``422``. See :data:`DEFAULT_MAX_PREDICT_ROWS`. ``None`` removes
            the limit.
        dependencies (Sequence[Depends] | None): Dependencies run before
            **every** route, e.g. ``[Depends(require_token)]``.
        admin_dependencies (Sequence[Depends] | None): Dependencies run
            before the operational routes only — ``/model``,
            ``/model/sync`` and ``/monitor``.
        expose_model_path (bool): Report the model's absolute path in
            ``/model`` and ``/model/sync``. Off by default, which reports the
            file name only: the absolute path describes the device's disk
            layout and tells a caller nothing it needs.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.

    Raises:
        ValueError: When ``max_rows`` is not positive.
    """
    if max_rows is not None and max_rows < 1:
        raise ValueError(f"max_rows must be positive or None, got {max_rows}")
    router = APIRouter(
        prefix=prefix,
        tags=list(tags or ["prediction"]),
        dependencies=list(dependencies or []),
    )
    admin = list(admin_dependencies or [])

    @router.post("/", response_model=PredictResponseSchema)
    async def predict(body: PredictRequestSchema) -> PredictResponseSchema:
        """Predict for a batch of rows.

        Args:
            body (PredictRequestSchema): The rows to score.

        Returns:
            PredictResponseSchema: Labels, scores and timing.

        Raises:
            HTTPException: ``422`` when the batch holds more than
                ``max_rows`` rows, or the rows do not match the model's
                expected width — client errors, reported as such rather
                than as a 500.
        """
        if max_rows is not None and len(body.rows) > max_rows:
            raise HTTPException(
                status_code=_UNPROCESSABLE_CONTENT,
                detail=f"a request holds at most {max_rows} rows, got {len(body.rows)}",
            )
        try:
            result = await asyncio.to_thread(predictor.predict, body.rows)
        except ValueError as exc:
            raise HTTPException(
                status_code=_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        if monitor is not None:
            monitor.observe(body.rows, result)
        if metrics is not None:
            metrics.observe(result)
        return PredictResponseSchema(
            labels=result.labels,
            probabilities=result.probabilities,
            n_rows=result.n_rows,
            seconds=result.seconds,
            model_version=source.current_version if source else None,
        )

    @router.get("/model", response_model=PredictorInfo, dependencies=admin)
    async def model_info() -> PredictorInfo:
        """Report what is loaded and how it is running.

        Returns:
            PredictorInfo: The current model's description, including the
            providers actually in use.
        """
        return _public_info(predictor.info, expose_path=expose_model_path)

    if source is not None:
        _source = source

        @router.post(
            "/model/sync",
            response_model=PredictorInfo,
            dependencies=admin,
        )
        async def sync_model() -> PredictorInfo:
            """Reload from the registry if a newer version is current.

            A monitor, when present, is reset on an actual version change:
            its counters describe the previous model, and mixing two
            versions into one latency percentile hides exactly the
            regression a fleet update needs to catch.

            Returns:
                PredictorInfo: The model in service after the check —
                unchanged when nothing newer was published.

            Raises:
                HTTPException: ``503`` when the registry could not be
                    reached or the new file failed to load. The previous
                    model keeps serving either way.
            """
            before = _source.current_version
            try:
                after = await _source.sync(predictor)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"model sync failed, still serving the previous "
                    f"version: {exc}",
                ) from exc
            if monitor is not None and after != before:
                monitor.reset()
                monitor.model_version = after
            return _public_info(predictor.info, expose_path=expose_model_path)

    if monitor is not None:
        _monitor = monitor

        @router.get(
            "/monitor",
            response_model=MonitoringReport,
            dependencies=admin,
        )
        async def monitor_report() -> MonitoringReport:
            """Report latency, input drift and prediction distribution.

            Returns:
                MonitoringReport: What the device has measured. Drift
                comes from the current window once it holds enough rows,
                and from the last complete window before that.
            """
            report = _monitor.report()
            if metrics is not None:
                metrics.observe_report(report)
            return report

    return router


__all__: list[str] = [
    "DEFAULT_MAX_PREDICT_ROWS",
    "PredictRequestSchema",
    "PredictResponseSchema",
    "RegistryModelSource",
    "make_prediction_router",
]
