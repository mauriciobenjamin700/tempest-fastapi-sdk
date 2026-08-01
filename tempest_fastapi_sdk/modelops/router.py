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

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from tempest_fastapi_sdk.modelops.monitoring import MonitoringReport
from tempest_fastapi_sdk.modelops.serving import OnnxPredictor, PredictorInfo
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
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

    Attributes:
        name (str): The logical artifact key in the registry.
        cache_dir (Path): Where downloaded versions live.
        current_version (str | None): The version currently loaded.
    """

    def __init__(
        self,
        registry: ArtifactRegistry[Any],
        name: str,
        cache_dir: str | Path,
    ) -> None:
        """Configure the source.

        Args:
            registry (ArtifactRegistry[Any]): The registry to ask.
            name (str): The logical artifact key.
            cache_dir (str | Path): Where to keep downloaded versions.
        """
        self._registry = registry
        self.name = name
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.current_version: str | None = None

    def _path_for(self, version: str) -> Path:
        """Return the local path for a version.

        Args:
            version (str): The version label.

        Returns:
            Path: Where that version is (or would be) cached.
        """
        safe = version.replace("/", "_")
        return self.cache_dir / f"{self.name}-{safe}.onnx"

    async def fetch(self) -> tuple[str, Path] | None:
        """Download the current version if it is not already cached.

        Returns:
            tuple[str, Path] | None: The version and its local path, or
            ``None`` when the registry has no current version — which is
            a normal state before the first activation, not an error.

        Raises:
            RuntimeError: When the registry row names an object the
                storage client cannot produce.
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
        await minio.fget_object(str(row.file_key), path, bucket=bucket)
        return version, path

    async def sync(self, predictor: OnnxPredictor) -> str | None:
        """Reload ``predictor`` when the registry has a different version.

        Safe to call on a schedule: it is a no-op when the current
        version is already loaded.

        Args:
            predictor (OnnxPredictor): The predictor to update.

        Returns:
            str | None: The version now loaded, or ``None`` when the
            registry had nothing to offer.
        """
        found = await self.fetch()
        if found is None:
            return None
        version, path = found
        if version == self.current_version:
            return version
        predictor.reload(path)
        self.current_version = version
        return version


def make_prediction_router(
    predictor: OnnxPredictor,
    *,
    source: RegistryModelSource | None = None,
    monitor: PredictionMonitor | None = None,
    metrics: PredictionMetrics | None = None,
    prefix: str = "/api/predict",
    tags: list[str] | None = None,
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

    Example:

        >>> predictor = OnnxPredictor("dist/classifier.onnx")
        >>> app.include_router(make_prediction_router(predictor))

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

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    router = APIRouter(prefix=prefix, tags=list(tags or ["prediction"]))

    @router.post("/", response_model=PredictResponseSchema)
    async def predict(body: PredictRequestSchema) -> PredictResponseSchema:
        """Predict for a batch of rows.

        Args:
            body (PredictRequestSchema): The rows to score.

        Returns:
            PredictResponseSchema: Labels, scores and timing.

        Raises:
            HTTPException: ``422`` when the rows do not match the model's
                expected width — a client error, reported as one rather
                than as a 500.
        """
        try:
            result = predictor.predict(body.rows)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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

    @router.get("/model", response_model=PredictorInfo)
    async def model_info() -> PredictorInfo:
        """Report what is loaded and how it is running.

        Returns:
            PredictorInfo: The current model's description, including the
            providers actually in use.
        """
        return predictor.info

    if source is not None:
        _source = source

        @router.post("/model/sync", response_model=PredictorInfo)
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
            return predictor.info

    if monitor is not None:
        _monitor = monitor

        @router.get("/monitor", response_model=MonitoringReport)
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
    "PredictRequestSchema",
    "PredictResponseSchema",
    "RegistryModelSource",
    "make_prediction_router",
]
