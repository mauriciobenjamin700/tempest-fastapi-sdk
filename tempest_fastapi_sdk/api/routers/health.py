"""Liveness/readiness endpoints with pluggable health checks."""

from __future__ import annotations

import asyncio
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
``False`` (or raises) otherwise. Exceptions and timeouts are caught
by the readiness endpoint and translated to ``False``.
"""


async def _run_check(name: str, check: HealthCheck, timeout: float | None) -> bool:
    """Run one readiness check, translating every failure to ``False``.

    Only the check name and the exception **type** are logged, never the
    exception message: database and broker drivers routinely put the
    connection DSN, user and password included, in their messages.

    Args:
        name (str): The check name, used in the log line.
        check (HealthCheck): The check to await.
        timeout (float | None): Seconds to wait before cancelling the
            check and counting it as failed. ``None`` waits forever.

    Returns:
        bool: The check result, or ``False`` when it raised or timed out.
    """
    try:
        return bool(await asyncio.wait_for(check(), timeout=timeout))
    except TimeoutError:
        logger.warning("Health check %r timed out after %ss", name, timeout)
        return False
    except Exception as exc:
        logger.warning("Health check %r raised %s", name, type(exc).__name__)
        return False


def make_health_router(
    *,
    db: AsyncDatabaseManager | None = None,
    checks: dict[str, HealthCheck] | None = None,
    prefix: str = "/health",
    tag: str = "health",
    version: str | None = None,
    expose_checks: bool = True,
    timeout: float | None = 3.0,
) -> APIRouter:
    """Build the canonical ``/health`` router.

    Two endpoints are mounted:

    * ``GET <prefix>/liveness`` — always returns ``{"status": "ok"}``
      so orchestrators can confirm the process is up. Should not
      depend on any external resource (Kubernetes treats failed
      liveness probes as "restart the pod"). This endpoint takes
      precedence over readiness for that reason.
    * ``GET <prefix>/readiness`` — runs every configured check
      concurrently, each bounded by ``timeout``, and returns ``200``
      only when all pass. Returns ``503`` when at least one fails,
      raises or times out. One hung dependency fails only its own
      check, and the response still arrives after about ``timeout``.

    A failing check is logged with its name and the exception type
    only. The exception message is never logged, because driver
    messages often carry the connection DSN with its credentials.

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
        timeout (float | None): Seconds each readiness check may take
            before it is cancelled and counted as failed. Defaults to
            3 seconds; keep it below the probe timeout of the
            orchestrator. ``None`` disables the bound. Cancellation
            only interrupts a check at an ``await``: a check that blocks
            the event loop with synchronous I/O cannot be timed out.

    Returns:
        APIRouter: A router ready to ``include_router(...)`` on the
        FastAPI app.

    Raises:
        ValueError: When ``timeout`` is not positive.
    """
    if timeout is not None and timeout <= 0:
        raise ValueError(f"timeout must be positive or None, got {timeout!r}")
    router = APIRouter(prefix=prefix, tags=[tag])
    all_checks: dict[str, HealthCheck] = {}
    if db is not None:
        all_checks["database"] = db.health_check
    all_checks.update(checks or {})

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
        outcomes: list[bool] = await asyncio.gather(
            *(_run_check(name, check, timeout) for name, check in all_checks.items()),
        )
        results: dict[str, bool] = dict(zip(all_checks, outcomes, strict=True))

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
