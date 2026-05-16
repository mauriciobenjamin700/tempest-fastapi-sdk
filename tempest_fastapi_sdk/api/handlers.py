"""FastAPI exception handlers for ``AppException`` and friends."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tempest_fastapi_sdk.exceptions.base import AppException


async def app_exception_handler(
    request: Request,
    exc: AppException,
) -> JSONResponse:
    """Serialize an :class:`AppException` to the SDK JSON envelope.

    Emits the shape::

        {
            "detail": "<message>",
            "code": "<machine-readable code>",
            "details": {...}
        }

    Args:
        request (Request): The incoming HTTP request. Unused — kept
            for signature compatibility with FastAPI handlers.
        exc (AppException): The exception raised.

    Returns:
        JSONResponse: The serialized response.
    """
    del request
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "code": exc.code,
            "details": exc.details,
        },
        headers=exc.headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register the SDK's exception handlers on a FastAPI app.

    Wires :class:`AppException` to :func:`app_exception_handler` so
    every subclass returned by routers, services and repositories is
    serialized consistently.

    Args:
        app (FastAPI): The FastAPI application to wire.
    """
    app.add_exception_handler(AppException, app_exception_handler)  # type: ignore[arg-type]


__all__: list[str] = [
    "app_exception_handler",
    "register_exception_handlers",
]
