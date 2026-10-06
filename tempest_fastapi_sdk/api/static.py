"""Security-hardened static file serving."""

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from tempest_fastapi_sdk.utils._security_headers import (
    DEFAULT_STATIC_SECURITY_HEADERS as DEFAULT_STATIC_SECURITY_HEADERS,
)


class HardenedStaticFiles(StaticFiles):
    """``StaticFiles`` that stamps anti-XSS headers on every response.

    Defense in depth for serving user-uploaded content: if a malicious
    file ever lands on disk (an upload-validation bypass, a manual
    operator action), serving it does not become a stored-XSS primitive
    against any same-origin SPA.

    Use exactly like :class:`starlette.staticfiles.StaticFiles` —
    ``app.mount("/uploads", HardenedStaticFiles(directory=...))``. Pass
    ``security_headers=`` to override or extend the defaults
    (:data:`DEFAULT_STATIC_SECURITY_HEADERS`). Existing headers set by
    the parent are preserved (``setdefault`` semantics).
    """

    def __init__(
        self,
        *args: object,
        security_headers: dict[str, str] | None = None,
        **kwargs: object,
    ) -> None:
        """Initialize.

        Args:
            *args: Positional arguments forwarded to ``StaticFiles``.
            security_headers (dict[str, str] | None): Headers to stamp
                on every response. Defaults to
                :data:`DEFAULT_STATIC_SECURITY_HEADERS`.
            **kwargs: Keyword arguments forwarded to ``StaticFiles``
                (``directory``, ``packages``, ``html``, ``check_dir`` …).
        """
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.security_headers: dict[str, str] = (
            dict(security_headers)
            if security_headers is not None
            else dict(DEFAULT_STATIC_SECURITY_HEADERS)
        )

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Delegate to ``StaticFiles`` and stamp the security headers.

        Args:
            path (str): The requested file path.
            scope (Scope): The ASGI scope.

        Returns:
            Response: The file response with security headers applied.
        """
        response = await super().get_response(path, scope)
        for header, value in self.security_headers.items():
            response.headers.setdefault(header, value)
        return response


__all__: list[str] = [
    "DEFAULT_STATIC_SECURITY_HEADERS",
    "HardenedStaticFiles",
]
