"""Serve a compiled ``tempestweb`` build from a FastAPI service.

``tempestweb build`` emits one of two deployable artifacts under
``dist/`` (see the tempestweb CLI):

- **wasm** — a fully static SPA (``index.html`` + ``bootstrap.js`` +
  the Pyodide package archive + service worker + ``client/`` assets)
  that runs entirely in the browser. Nothing but a file server is
  needed to host it.
- **server** — a live app driven over WebSocket/SSE by the tempestweb
  server engine (``server.py`` importing the project's ``app.py``).

This module hosts either from the SDK, each with the shape that fits it:

- :func:`make_web_app_router` returns an :class:`~fastapi.APIRouter`
  that serves a **static** build (the wasm artifact) with a single-page
  history fallback — include it *last* so your API routes win.
- :func:`build_web_app` returns a :class:`~fastapi.FastAPI` app for a
  **server** build (it owns the WebSocket/SSE routes, so it is a
  sub-application you mount, not a router) — the same wiring the
  artifact's own ``server.py`` does, done in-process.

``tempestweb`` is imported lazily (only :func:`build_web_app` needs it),
so importing this module never hard-requires the optional dependency.
The SDK only *serves* an already-built ``dist/`` — building stays in the
tempestweb CLI / CI flow.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

if TYPE_CHECKING:
    from fastapi import FastAPI

BuildMode = Literal["wasm", "server"]

#: MIME types for extensions Python's ``mimetypes`` may not know, so a
#: tempestweb build is served with the types browsers require (a wrong
#: type on ``.wasm``/``.mjs`` breaks module/streaming loads).
_EXTRA_MEDIA_TYPES: dict[str, str] = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".wasm": "application/wasm",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
}

#: Files served with ``no-cache`` regardless of the asset cache policy:
#: the SPA shell and the service worker must always be revalidated so a
#: new deploy is picked up immediately.
_ALWAYS_REVALIDATE: frozenset[str] = frozenset({"index.html", "sw.js"})

_INDEX = "index.html"


def detect_build_mode(directory: str | Path) -> BuildMode:
    """Detect whether ``directory`` holds a wasm or server build.

    Args:
        directory (str | Path): A ``tempestweb build`` output directory.

    Returns:
        BuildMode: ``"wasm"`` for a static SPA artifact, ``"server"`` for
        a live WebSocket/SSE artifact.

    Raises:
        ValueError: When the directory does not look like a tempestweb
            build (no ``index.html``, or neither a ``bootstrap.js`` nor a
            ``server.py`` marker).
    """
    root = Path(directory)
    if not (root / _INDEX).is_file():
        raise ValueError(f"{root} is not a tempestweb build: no {_INDEX}")
    if (root / "bootstrap.js").is_file():
        return "wasm"
    if (root / "server.py").is_file() and (root / "app.py").is_file():
        return "server"
    raise ValueError(
        f"{root} is not a recognizable tempestweb build "
        "(expected bootstrap.js for wasm or server.py for server mode)"
    )


def _media_type(path: Path) -> str | None:
    """Resolve the media type for a file to serve.

    Args:
        path (Path): The file being served.

    Returns:
        str | None: The MIME type, or ``None`` to let the response guess.
    """
    override = _EXTRA_MEDIA_TYPES.get(path.suffix.lower())
    if override is not None:
        return override
    guessed, _encoding = mimetypes.guess_type(str(path))
    return guessed


def _resolve_within(root: Path, resource: str) -> Path | None:
    """Resolve ``resource`` under ``root``, rejecting traversal escapes.

    Args:
        root (Path): The resolved build root directory.
        resource (str): The request path relative to the build root.

    Returns:
        Path | None: The resolved path when it stays inside ``root``,
        otherwise ``None``.
    """
    candidate = (root / resource).resolve()
    if candidate == root or root in candidate.parents:
        return candidate
    return None


def make_web_app_router(
    directory: str | Path,
    *,
    asset_cache_control: str = "public, max-age=3600",
    security_headers: dict[str, str] | None = None,
    spa_fallback: bool = True,
) -> APIRouter:
    """Serve a static ``tempestweb`` (wasm) build as an ``APIRouter``.

    The router serves every file in the build directory and, for any
    unmatched path, falls back to ``index.html`` so client-side routing
    works on a hard refresh (single-page-app history fallback). The wasm
    artifact references ``/sw.js`` at the site root, so mount this at the
    application root — ``app.include_router(router)`` — and include it
    **after** your API routers so specific routes win over the catch-all.

    Caching: ``index.html`` and ``sw.js`` are always sent ``no-cache``
    (so a redeploy is seen immediately); every other asset uses
    ``asset_cache_control``. No Content-Security-Policy is imposed —
    a wasm build runs first-party code and boots Pyodide (which needs
    ``wasm-unsafe-eval``); pass ``security_headers`` to add your own.

    Args:
        directory (str | Path): The build directory (``dist/wasm``).
        asset_cache_control (str): ``Cache-Control`` for non-shell assets.
        security_headers (dict[str, str] | None): Extra headers stamped on
            every response. Defaults to ``{"X-Content-Type-Options":
            "nosniff"}``.
        spa_fallback (bool): When ``True`` (default), unmatched paths serve
            ``index.html``; when ``False`` they return ``404``.

    Returns:
        APIRouter: A router serving the static build.

    Raises:
        ValueError: When ``directory`` is not a static (wasm) tempestweb
            build. Use :func:`build_web_app` for a server build.
    """
    root = Path(directory).resolve()
    mode = detect_build_mode(root)
    if mode != "wasm":
        raise ValueError(
            f"{root} is a {mode!r} build; make_web_app_router serves static "
            "(wasm) builds only — use build_web_app() for a server build"
        )
    headers = (
        dict(security_headers)
        if security_headers is not None
        else {"X-Content-Type-Options": "nosniff"}
    )

    def _file_response(path: Path) -> FileResponse:
        """Build a ``FileResponse`` with cache + security headers."""
        response = FileResponse(path, media_type=_media_type(path))
        if path.name in _ALWAYS_REVALIDATE:
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = asset_cache_control
        if path.name == "sw.js":
            # Let a root-served worker claim the whole origin scope.
            response.headers["Service-Worker-Allowed"] = "/"
        for header, value in headers.items():
            response.headers.setdefault(header, value)
        return response

    router = APIRouter()
    index_path = root / _INDEX

    @router.get("/", name="tempestweb_index")
    async def index() -> FileResponse:
        """Serve the SPA shell (``index.html``)."""
        return _file_response(index_path)

    @router.get("/{resource:path}", name="tempestweb_asset")
    async def asset(resource: str) -> FileResponse:
        """Serve a build asset, or fall back to the SPA shell.

        Args:
            resource (str): The request path under the build root.

        Returns:
            FileResponse: The requested file, or ``index.html`` when the
            path does not resolve to a file and ``spa_fallback`` is on.

        Raises:
            HTTPException: ``404`` when the path is missing (or escapes the
                build root) and the SPA fallback is disabled.
        """
        resolved = _resolve_within(root, resource)
        if resolved is not None and resolved.is_file():
            return _file_response(resolved)
        if spa_fallback:
            return _file_response(index_path)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

    return router


def build_web_app(
    directory: str | Path,
    *,
    title: str | None = None,
) -> FastAPI:
    """Build a FastAPI app hosting a ``tempestweb`` **server** build.

    Loads the artifact's ``app.py`` (its ``make_state`` + ``view``
    contract), wires the tempestweb server engine (WebSocket + SSE) via
    ``tempestweb.server.create_app``, serves the shared client under
    ``/static`` and the shell at ``/`` — the same wiring the artifact's
    generated ``server.py`` performs, done in-process. Mount the result
    (``app.mount("/", web)``) or run it directly with uvicorn.

    Args:
        directory (str | Path): The build directory (``dist/server``).
        title (str | None): OpenAPI title; defaults to the directory name.

    Returns:
        FastAPI: The configured server app (owns ``/ws`` + ``/sse``).

    Raises:
        ValueError: When ``directory`` is not a server tempestweb build.
            Use :func:`make_web_app_router` for a static (wasm) build.
    """
    root = Path(directory).resolve()
    mode = detect_build_mode(root)
    if mode != "server":
        raise ValueError(
            f"{root} is a {mode!r} build; build_web_app hosts server builds "
            "only — use make_web_app_router() for a static (wasm) build"
        )

    # Imported lazily: only the server path needs the tempestweb runtime.
    from fastapi.staticfiles import StaticFiles
    from tempestweb.cli.loader import load_app
    from tempestweb.server import create_app

    loaded = load_app(root / "app.py")
    app: FastAPI = create_app(loaded.make_state, loaded.view, title=title or root.name)
    app.mount("/static", StaticFiles(directory=str(root / "static")), name="static")
    index_path = root / _INDEX

    @app.get("/", name="tempestweb_index")
    async def index() -> FileResponse:
        """Serve the app shell that mounts the client over WebSocket."""
        return FileResponse(index_path, media_type="text/html")

    return app


__all__: list[str] = [
    "BuildMode",
    "build_web_app",
    "detect_build_mode",
    "make_web_app_router",
]
