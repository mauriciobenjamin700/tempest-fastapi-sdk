"""Credential-header detection shared by the caching middlewares.

:class:`~tempest_fastapi_sdk.api.middlewares.response_cache.ResponseCacheMiddleware`
and :class:`~tempest_fastapi_sdk.api.middlewares.idempotency.IdempotencyMiddleware`
both run before routing, so they cannot see which dependency authenticates a
request. They decide whether a response belongs to one caller by looking at
the request headers that carry credentials. That list has to cover every
carrier the SDK itself ships, not only ``Authorization`` and ``Cookie``:
:func:`~tempest_fastapi_sdk.api.dependencies.auth.make_token_dependency`
reads ``X-Token``, and API keys conventionally travel in ``X-API-Key``.
"""

from __future__ import annotations

import hashlib

from starlette.requests import Request

DEFAULT_CREDENTIAL_HEADERS: tuple[str, ...] = (
    "authorization",
    "cookie",
    "x-token",
    "x-api-key",
)
"""Request headers treated as credentials when nothing else is configured.

``authorization`` and ``cookie`` cover bearer tokens and sessions; ``x-token``
is the default header of ``make_token_dependency``; ``x-api-key`` is the
conventional API-key header. A service that authenticates through another
header passes it in the middleware's ``credential_headers=``.
"""


def normalize_credential_headers(headers: tuple[str, ...]) -> tuple[str, ...]:
    """Lowercase and de-duplicate a credential header list, keeping order.

    Args:
        headers (tuple[str, ...]): Header names as configured.

    Returns:
        tuple[str, ...]: The lowercase names, each once.

    Raises:
        ValueError: When the list is empty. With no credential header every
            request looks anonymous, which is exactly the failure this list
            exists to prevent.
    """
    normalized: tuple[str, ...] = tuple(
        dict.fromkeys(name.strip().lower() for name in headers if name.strip())
    )
    if not normalized:
        raise ValueError("credential_headers must name at least one header")
    return normalized


def credential_digest(request: Request, headers: tuple[str, ...]) -> str | None:
    """Return a digest of the credential headers present on ``request``.

    Args:
        request (Request): The inbound request.
        headers (tuple[str, ...]): Lowercase credential header names.

    Returns:
        str | None: A sha256 hex digest over every listed header, or ``None``
        when the request carries none of them.
    """
    values: list[str] = [request.headers.get(name, "") for name in headers]
    if not any(values):
        return None
    raw: str = "|".join(
        f"{name}={value}" for name, value in zip(headers, values, strict=True)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__: list[str] = [
    "DEFAULT_CREDENTIAL_HEADERS",
    "credential_digest",
    "normalize_credential_headers",
]
