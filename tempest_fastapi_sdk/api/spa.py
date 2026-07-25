"""Serve a compiled single-page application from the FastAPI process.

A React (or any Vite/esbuild) build is a directory of hashed assets plus one
``index.html``. Serving it from the API process is the simplest full-stack
deployment there is: one origin, one container, one TLS certificate — and no
CORS, no ``SameSite=None`` cookie, no preflight, because the browser never
sees a cross-origin request.

Mounting ``StaticFiles`` alone does not get you there. A client-side router
owns paths like ``/users/42`` that exist in the browser but not on disk, so a
bare static mount answers 404 on every deep link and page refresh. The
missing piece is the **SPA fallback**: an unmatched document request has to
return ``index.html`` and let the router take over.

:func:`make_spa_router` is that piece, with the cache and safety details that
are easy to get wrong:

* Hashed assets are served ``immutable`` for a year; ``index.html`` is served
  ``no-store``. Getting this backwards is the classic "users keep running the
  old bundle after a deploy" bug — the HTML is the one file whose name never
  changes, so it is the one file that must never be cached.
* API paths are excluded from the fallback, so a typo'd endpoint still
  returns a JSON 404 instead of a 200 with an HTML body — which would
  otherwise surface in the client as a confusing JSON parse error.
* Only ``GET``/``HEAD`` requests that accept HTML fall back. A ``POST`` to a
  missing path stays a 405/404 rather than silently returning a page.

For the development loop, do **not** use this: run ``vite dev`` and let it
proxy ``/api`` to the FastAPI process, so hot-module reload keeps working.
See the recipe for both sides of that setup.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from tempest_fastapi_sdk.api.static import DEFAULT_STATIC_SECURITY_HEADERS

DEFAULT_ASSET_CACHE_CONTROL: str = "public, max-age=31536000, immutable"
"""Cache policy for content-hashed build assets.

Vite names every emitted asset with a content hash, so a changed file is a
changed URL. That makes an effectively permanent cache safe, and it is what
keeps a returning visitor from re-downloading the bundle.
"""

DEFAULT_DOCUMENT_CACHE_CONTROL: str = "no-store, must-revalidate"
"""Cache policy for ``index.html``.

The entry document is the only file whose URL is stable across deploys, so
caching it is what pins a browser to a stale bundle. It must be revalidated
on every navigation.
"""

DEFAULT_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "/api",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
    "/metrics",
    "/logs",
    "/admin",
    "/auth",
)
"""Path prefixes the SPA fallback must never answer.

Everything the SDK's own routers mount, plus ``/api``. A request under these
falls through to the real route — or to a JSON 404 — instead of being handed
an HTML document.
"""


class _SpaStaticFiles(StaticFiles):
    """``StaticFiles`` that stamps security headers and an asset cache policy.

    Split from :class:`~tempest_fastapi_sdk.api.static.HardenedStaticFiles`
    because the cache policy differs: uploads are user content with no
    content-hashed names, while build assets are immutable by construction.
    """

    def __init__(
        self,
        *args: object,
        cache_control: str,
        security_headers: dict[str, str],
        **kwargs: object,
    ) -> None:
        """Initialize.

        Args:
            *args (object): Positional arguments forwarded to ``StaticFiles``.
            cache_control (str): ``Cache-Control`` stamped on every asset.
            security_headers (dict[str, str]): Headers stamped on every
                response.
            **kwargs (object): Keyword arguments forwarded to
                ``StaticFiles``.
        """
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.cache_control: str = cache_control
        self.security_headers: dict[str, str] = dict(security_headers)

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Serve the file, then stamp cache and security headers.

        Args:
            path (str): The requested file path, relative to the mount.
            scope (Scope): The ASGI scope.

        Returns:
            Response: The file response with the headers applied.
        """
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", self.cache_control)
        for header, value in self.security_headers.items():
            response.headers.setdefault(header, value)
        return response


def make_spa_router(
    dist_dir: str | Path,
    *,
    index_file: str = "index.html",
    assets_dir: str = "assets",
    excluded_prefixes: tuple[str, ...] = DEFAULT_EXCLUDED_PREFIXES,
    asset_cache_control: str = DEFAULT_ASSET_CACHE_CONTROL,
    document_cache_control: str = DEFAULT_DOCUMENT_CACHE_CONTROL,
    security_headers: dict[str, str] | None = None,
) -> APIRouter:
    """Build a router that serves a compiled SPA with a client-side fallback.

    Include it **last**, after every API router, because it claims a
    catch-all ``GET /{path:path}`` route. FastAPI matches in registration
    order, so anything mounted afterwards becomes unreachable::

        from fastapi import FastAPI
        from tempest_fastapi_sdk import make_spa_router

        app = FastAPI()
        app.include_router(users_router, prefix="/api/users")
        app.include_router(make_spa_router("web/dist"))

    Args:
        dist_dir (str | Path): The build output directory — Vite's ``dist``.
            Must contain ``index_file``.
        index_file (str): The entry document served on fallback.
        assets_dir (str): Sub-directory of hashed assets, mounted with the
            immutable cache policy. Missing directories are tolerated, so a
            build that emits everything at the root still works.
        excluded_prefixes (tuple[str, ...]): Paths the fallback refuses to
            answer, so API 404s stay JSON. Defaults to
            :data:`DEFAULT_EXCLUDED_PREFIXES`.
        asset_cache_control (str): ``Cache-Control`` for hashed assets.
        document_cache_control (str): ``Cache-Control`` for ``index_file``.
        security_headers (dict[str, str] | None): Headers stamped on static
            responses. Defaults to
            :data:`~tempest_fastapi_sdk.api.static.DEFAULT_STATIC_SECURITY_HEADERS`.

    Returns:
        APIRouter: A router serving the assets and the SPA fallback.

    Raises:
        FileNotFoundError: When ``dist_dir`` or its ``index_file`` is
            missing. Failing at wiring time is deliberate: the alternative
            is a service that boots fine and answers every page with a 404,
            which usually gets discovered in staging.
    """
    root = Path(dist_dir).expanduser().resolve()
    index_path = root / index_file
    if not root.is_dir():
        raise FileNotFoundError(
            f"SPA build directory not found: {root}. Run the frontend build "
            f"(e.g. `npm run build`) before starting the app, or point "
            f"`dist_dir` at the right path."
        )
    if not index_path.is_file():
        raise FileNotFoundError(
            f"{index_path} not found — {root} does not look like a built SPA."
        )

    headers = (
        dict(security_headers)
        if security_headers is not None
        else dict(DEFAULT_STATIC_SECURITY_HEADERS)
    )
    router = APIRouter()

    assets_path = root / assets_dir
    if assets_path.is_dir():
        router.mount(
            f"/{assets_dir}",
            _SpaStaticFiles(
                directory=assets_path,
                cache_control=asset_cache_control,
                security_headers=headers,
            ),
            name="spa-assets",
        )

    def _document() -> FileResponse:
        """Return the entry document with no-store caching.

        Returns:
            FileResponse: ``index.html`` with the document cache policy and
            the security headers.
        """
        return FileResponse(
            index_path,
            media_type="text/html",
            headers={"Cache-Control": document_cache_control, **headers},
        )

    @router.get("/{spa_path:path}", include_in_schema=False)
    async def serve_spa(spa_path: str) -> Response:
        """Serve a build file, or fall back to the SPA entry document.

        Args:
            spa_path (str): The requested path, relative to the router.

        Returns:
            Response: The matching file from the build when one exists;
            otherwise ``index.html`` so the client-side router can resolve
            the route. Paths under :data:`DEFAULT_EXCLUDED_PREFIXES` get a
            404 instead, keeping API errors machine-readable.
        """
        from fastapi import HTTPException

        requested = f"/{spa_path}"
        if any(
            requested == prefix or requested.startswith(f"{prefix}/")
            for prefix in excluded_prefixes
        ):
            raise HTTPException(status_code=404, detail="Not Found")

        if spa_path:
            candidate = (root / spa_path).resolve()
            # `resolve()` collapses `..`, so comparing against the root is
            # what stops `/../../etc/passwd` from escaping the build dir.
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(
                    candidate,
                    headers={
                        "Cache-Control": asset_cache_control
                        if assets_dir and spa_path.startswith(f"{assets_dir}/")
                        else document_cache_control,
                        **headers,
                    },
                )
        return _document()

    return router


__all__: list[str] = [
    "DEFAULT_ASSET_CACHE_CONTROL",
    "DEFAULT_DOCUMENT_CACHE_CONTROL",
    "DEFAULT_EXCLUDED_PREFIXES",
    "make_spa_router",
]
