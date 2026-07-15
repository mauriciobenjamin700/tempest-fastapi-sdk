"""CORS middleware helper aligned with :class:`CORSSettings`."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from tempest_fastapi_sdk.settings.mixins import CORSSettings


def apply_cors(
    app: FastAPI,
    settings: CORSSettings | None = None,
    *,
    origins: list[str] | None = None,
    origin_regex: str | None = None,
    allow_credentials: bool | None = None,
    allow_methods: list[str] | None = None,
    allow_headers: list[str] | None = None,
    expose_headers: list[str] | None = None,
    max_age: int | None = None,
) -> None:
    """Attach ``CORSMiddleware`` to ``app`` using SDK conventions.

    All overrides are optional; when provided they take precedence
    over the matching field on ``settings``. When neither is provided,
    a permissive default suitable for local development is used
    (``origins=["*"]``, no credentials).

    Args:
        app (FastAPI): The application to mutate.
        settings (CORSSettings | None): Source of the defaults. When
            ``None``, a fresh :class:`CORSSettings` instance is built.
        origins (list[str] | None): Override for
            :attr:`CORSSettings.CORS_ORIGINS`.
        origin_regex (str | None): Override for
            :attr:`CORSSettings.CORS_ORIGIN_REGEX`. Matched against the
            request ``Origin`` for session-varying origins (dev tunnels,
            preview deploys). An empty string disables it.
        allow_credentials (bool | None): Override.
        allow_methods (list[str] | None): Override.
        allow_headers (list[str] | None): Override.
        expose_headers (list[str] | None): Override.
        max_age (int | None): Override.
    """
    cfg = settings or CORSSettings()
    resolved_regex = origin_regex if origin_regex is not None else cfg.CORS_ORIGIN_REGEX
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if origins is not None else cfg.CORS_ORIGINS,
        allow_origin_regex=resolved_regex or None,
        allow_credentials=(
            allow_credentials
            if allow_credentials is not None
            else cfg.CORS_ALLOW_CREDENTIALS
        ),
        allow_methods=(
            allow_methods if allow_methods is not None else cfg.CORS_ALLOW_METHODS
        ),
        allow_headers=(
            allow_headers if allow_headers is not None else cfg.CORS_ALLOW_HEADERS
        ),
        expose_headers=(
            expose_headers if expose_headers is not None else cfg.CORS_EXPOSE_HEADERS
        ),
        max_age=max_age if max_age is not None else cfg.CORS_MAX_AGE,
    )


__all__: list[str] = [
    "apply_cors",
]
