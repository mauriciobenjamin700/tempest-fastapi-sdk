"""Programmatic uvicorn entry point for SDK consumers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tempest_fastapi_sdk.settings.mixins import ServerSettings


def run_server(
    app: str | FastAPI = "src.api.app:app",
    *,
    settings: ServerSettings | None = None,
    host: str | None = None,
    port: int | None = None,
    reload: bool | None = None,
    **uvicorn_kwargs: Any,
) -> None:
    """Run uvicorn against ``app`` using the SDK's defaults.

    This is the canonical helper imported from ``src/server.py``. It
    keeps the entry point a single line and centralizes the
    ``host`` / ``port`` / ``reload`` defaults across every service.

    Resolution order for each kwarg: explicit ``host`` / ``port`` /
    ``reload`` argument → matching field on ``settings`` → SDK
    default (``"127.0.0.1"`` / ``8000`` / ``False``).

    Args:
        app (str | FastAPI): Either an import string (preferred for
            reload support — ``"src.api.app:app"``) or the FastAPI
            instance itself.
        settings (ServerSettings | None): Optional settings object;
            ``SERVER_HOST`` / ``SERVER_PORT`` / ``SERVER_RELOAD`` are
            read from it when present and not overridden.
        host (str | None): Override bind interface.
        port (int | None): Override TCP port.
        reload (bool | None): Override uvicorn auto-reload.
        **uvicorn_kwargs (Any): Forwarded verbatim to
            :func:`uvicorn.run` (workers, log_config, ssl_*, etc.).

    Returns:
        None: Blocks until uvicorn exits.
    """
    import uvicorn

    resolved_host: str = (
        host
        if host is not None
        else getattr(settings, "SERVER_HOST", None) or "127.0.0.1"
    )
    resolved_port: int = (
        port
        if port is not None
        else getattr(settings, "SERVER_PORT", None) or 8000
    )
    resolved_reload: bool = (
        reload
        if reload is not None
        else bool(getattr(settings, "SERVER_RELOAD", False))
    )

    uvicorn.run(
        app,
        host=resolved_host,
        port=resolved_port,
        reload=resolved_reload,
        **uvicorn_kwargs,
    )


__all__: list[str] = [
    "run_server",
]
