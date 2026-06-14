"""FastAPI dependency that gates a route on a feature flag."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from tempest_fastapi_sdk.exceptions import AppException
from tempest_fastapi_sdk.flags.service import FeatureFlags


def make_flag_dependency(
    flags: FeatureFlags,
    name: str,
    *,
    enabled: bool = True,
    status_code: int = 404,
    detail: str = "Feature not available",
    code: str = "FEATURE_DISABLED",
) -> Callable[[], Awaitable[None]]:
    """Build a dependency that allows a route only when a flag matches.

    Mount it via ``Depends`` (or in a router's ``dependencies=[...]``)
    to gate an endpoint behind a flag. When the flag's state differs
    from ``enabled``, the dependency raises an :class:`AppException`
    rendered through the SDK envelope — ``404`` by default so a disabled
    feature simply looks absent.

    Args:
        flags (FeatureFlags): The feature-flag service to query.
        name (str): The flag name to check.
        enabled (bool): The flag state the route requires. ``True``
            (default) allows the route when the flag is on; ``False``
            allows it only when the flag is off (e.g. a deprecation
            kill-switch).
        status_code (int): Status code raised when the gate fails.
            Defaults to ``404`` (hide the feature); use ``403`` to
            signal it exists but is forbidden.
        detail (str): Message for the error envelope.
        code (str): Machine-readable error code for the envelope.

    Returns:
        Callable[[], Awaitable[None]]: An async FastAPI dependency.
    """

    async def _dependency() -> None:
        if await flags.is_enabled(name) != enabled:
            raise AppException(detail, code=code, status_code=status_code)

    return _dependency


__all__: list[str] = ["make_flag_dependency"]
