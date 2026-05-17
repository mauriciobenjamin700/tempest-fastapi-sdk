"""Liveness/readiness endpoints with pluggable health checks."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager

logger = logging.getLogger(__name__)

HealthCheck = Callable[[], Awaitable[bool]]
"""Type alias for async health-check callables.

Each callable returns ``True`` when the dependency is healthy and
``False`` (or raises) otherwise. Exceptions are caught by the
readiness endpoint and translated to ``False``.
"""


def make_health_router(
    *,
    db: AsyncDatabaseManager | None = None,
    checks: dict[str, HealthCheck] | None = None,
    prefix: str = "/health",
    tag: str = "health",
    version: str | None = None,
    expose_checks: bool = True,
) -> APIRouter:
    """Build the canonical ``/health`` router.

    Two endpoints are mounted:

    * ``GET <prefix>/liveness`` — always returns ``{"status": "ok"}``
      so orchestrators can confirm the process is up. Should not
      depend on any external resource (Kubernetes treats failed
      liveness probes as "restart the pod"). This endpoint takes
      precedence over readiness for that reason.
    * ``GET <prefix>/readiness`` — runs every configured check and
      returns ``200`` only when all pass. Returns ``503`` when at
      least one fails.

    Args:
        db (AsyncDatabaseManager | None): When provided, a
            ``database`` check is registered automatically using
            :meth:`AsyncDatabaseManager.health_check`.
        checks (dict[str, HealthCheck] | None): Extra readiness
            checks keyed by name (e.g. ``"redis"``, ``"rabbitmq"``).
        prefix (str): The URL prefix for the router. Defaults to
            ``"/health"`` — keep it at the application root, not
            under ``/api``.
        tag (str): OpenAPI tag applied to both endpoints.
        version (str | None): When provided, attached to the
            readiness payload as ``version``.
        expose_checks (bool): Whether to surface the per-dependency
            breakdown in the readiness payload. Defaults to ``True``
            for development ergonomics; set ``False`` in production
            so unauthenticated probes don't reveal which backends
            (database, Redis, RabbitMQ, etc.) the service depends on.

    Returns:
        APIRouter: A router ready to ``include_router(...)`` on the
        FastAPI app.
    """
    router = APIRouter(prefix=prefix, tags=[tag])
    extra_checks: dict[str, HealthCheck] = dict(checks or {})

    @router.get("/liveness", summary="Liveness probe")
    async def liveness() -> dict[str, str]:
        """Return ``{"status": "ok"}`` if the process is alive."""
        return {"status": "ok"}

    @router.get(
        "/readiness",
        summary="Readiness probe",
        responses={
            status.HTTP_503_SERVICE_UNAVAILABLE: {
                "description": "At least one dependency is not ready.",
            },
        },
    )
    async def readiness() -> JSONResponse:
        """Return per-dependency status and a 503 when any check fails."""
        results: dict[str, bool] = {}
        if db is not None:
            try:
                results["database"] = await db.health_check()
            except Exception as exc:
                logger.warning("Health check 'database' raised: %s", exc)
                results["database"] = False
        for name, check in extra_checks.items():
            try:
                results[name] = await check()
            except Exception as exc:
                logger.warning("Health check %r raised: %s", name, exc)
                results[name] = False

        overall = all(results.values()) if results else True
        payload: dict[str, Any] = {
            "status": "ready" if overall else "not_ready",
        }
        if expose_checks:
            payload["checks"] = results
        if version is not None:
            payload["version"] = version
        return JSONResponse(
            payload,
            status_code=(
                status.HTTP_200_OK if overall else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
        )

    return router


__all__: list[str] = [
    "HealthCheck",
    "make_health_router",
]
