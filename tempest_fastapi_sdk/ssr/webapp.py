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

import inspect
import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeAlias

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, Response

if TYPE_CHECKING:
    from fastapi import FastAPI
    from tempest_core import Theme

BuildMode = Literal["wasm", "server"]

ShellSource: TypeAlias = str | Path | Callable[[], str] | Callable[[Request], str]
"""Where the app shell comes from when the generated one is overridden.

* ``str`` — the HTML document itself.
* ``Path`` — a file read from disk on every request.
* callable — invoked per request, so the document can carry a value that
  changes per response (a Content-Security-Policy nonce). It takes either
  no argument or the :class:`~fastapi.Request`, whichever it declares.
"""

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


def _check_shell(shell: ShellSource) -> None:
    """Reject a shell string that is obviously meant to be a path.

    ``shell="dist/index.html"`` is a natural thing to write and would
    otherwise be served as the literal document, producing a blank page
    with no error anywhere. A string with no markup in it is treated as
    that mistake.

    Args:
        shell (ShellSource): The shell override to validate.

    Raises:
        ValueError: When ``shell`` is a string carrying no ``<``.
    """
    if isinstance(shell, str) and "<" not in shell:
        raise ValueError(
            "shell as a str is the HTML document itself, not a path; "
            f"pass Path({shell!r}) to read it from disk.",
        )


def _render_shell(shell: ShellSource, request: Request) -> str | Path:
    """Resolve a shell override for one request.

    Args:
        shell (ShellSource): The configured override.
        request (Request): The incoming request, handed to a callable
            that declares a parameter.

    Returns:
        str | Path: The HTML document, or the path to read it from.
    """
    if isinstance(shell, (str, Path)):
        return shell
    if inspect.signature(shell).parameters:
        return shell(request)  # type: ignore[call-arg]
    return shell()  # type: ignore[call-arg]


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
    shell: ShellSource | None = None,
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
        shell (ShellSource | None): Replaces the artifact's
            ``index.html`` — the only part of the document an application
            owns, and where ``<html lang>``, meta tags, a favicon link or
            a CSP nonce have to go. ``None`` (default) keeps the
            generated shell. The override also answers the SPA fallback,
            so a deep link renders the same document.

    Returns:
        APIRouter: A router serving the static build.

    Raises:
        ValueError: When ``directory`` is not a static (wasm) tempestweb
            build (use :func:`build_web_app` for a server build), or when
            ``shell`` is a string with no markup in it — that is a path
            written where a document was expected.
    """
    root = Path(directory).resolve()
    mode = detect_build_mode(root)
    if mode != "wasm":
        raise ValueError(
            f"{root} is a {mode!r} build; make_web_app_router serves static "
            "(wasm) builds only — use build_web_app() for a server build"
        )
    if shell is not None:
        _check_shell(shell)
    headers = (
        dict(security_headers)
        if security_headers is not None
        else {"X-Content-Type-Options": "nosniff"}
    )

    def _file_response(path: Path) -> FileResponse:
        """Build a ``FileResponse`` with cache + security headers.

        Notes:
            A service worker served from the root is allowed to claim
            the whole origin scope, which is what lets it control every
            page of the app.
        """
        response = FileResponse(path, media_type=_media_type(path))
        if path.name in _ALWAYS_REVALIDATE:
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = asset_cache_control
        if path.name == "sw.js":
            response.headers["Service-Worker-Allowed"] = "/"
        for header, value in headers.items():
            response.headers.setdefault(header, value)
        return response

    router = APIRouter()
    index_path = root / _INDEX

    def _shell_response(request: Request) -> Response:
        """Serve the shell, generated or overridden.

        Args:
            request (Request): The incoming request, handed to a shell
                callable that declares a parameter.

        Returns:
            Response: The shell document, always ``no-cache`` so a
            redeploy (or a per-request nonce) is never served stale.
        """
        if shell is None:
            return _file_response(index_path)
        rendered = _render_shell(shell, request)
        if isinstance(rendered, Path):
            return _file_response(rendered)
        response: Response = HTMLResponse(rendered)
        response.headers["Cache-Control"] = "no-cache"
        for header, value in headers.items():
            response.headers.setdefault(header, value)
        return response

    @router.get("/", name="tempestweb_index")
    async def index(request: Request) -> Response:
        """Serve the SPA shell (``index.html``, or the override).

        Args:
            request (Request): The incoming request.

        Returns:
            Response: The shell document.
        """
        return _shell_response(request)

    @router.get("/{resource:path}", name="tempestweb_asset")
    async def asset(resource: str, request: Request) -> Response:
        """Serve a build asset, or fall back to the SPA shell.

        Args:
            resource (str): The request path under the build root.
            request (Request): The incoming request.

        Returns:
            Response: The requested file, or the shell when the path does
            not resolve to a file and ``spa_fallback`` is on.

        Raises:
            HTTPException: ``404`` when the path is missing (or escapes the
                build root) and the SPA fallback is disabled.
        """
        resolved = _resolve_within(root, resource)
        if resolved is not None and resolved.is_file():
            return _file_response(resolved)
        if spa_fallback:
            return _shell_response(request)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

    return router


def build_web_app(
    directory: str | Path,
    *,
    title: str | None = None,
    shell: ShellSource | None = None,
    theme: Theme | None = None,
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
        shell (ShellSource | None): Replaces the artifact's
            ``index.html``. The generated shell is what ``tempestweb
            build`` emitted, so anything the application owns —
            ``<html lang="pt-BR">``, ``<meta name="description">``, Open
            Graph tags, a favicon link, a CSP nonce — has to enter here.
            ``None`` (default) serves the generated file unchanged.

            Whatever you supply must keep the script tag that boots the
            client; the practical way to write one is to read the
            generated ``index.html`` once and edit the head.
        theme (Theme | None): The palette handed to each session's
            ``App``, which ``view`` reads back as ``app.theme``. This is
            the half CSS cannot reach: a widget bakes its resolved colors
            into an inline ``style``, so rebranding custom properties
            alone leaves those at the Material baseline. A widget follows
            the palette only where ``view`` passes it down —
            ``Button(label=..., variant=Variant.SOLID,
            color_scheme="primary", theme=app.theme)`` — because every
            widget defaults to the baseline theme, and the
            ``tempestweb.components`` helpers do not forward ``app.theme``
            for you. ``None`` (default) keeps the baseline. Pair this with
            ``tempestweb.html.theme_css(theme)`` in the shell head, which
            covers what the base stylesheet paints.

    Returns:
        FastAPI: The configured server app (owns ``/ws`` + ``/sse``).

    Raises:
        ValueError: When ``directory`` is not a server tempestweb build
            (use :func:`make_web_app_router` for a static build), or when
            ``shell`` is a string with no markup in it — that is a path
            written where a document was expected.

    Notes:
        The tempestweb runtime is imported lazily because only the server
        build mode needs it; a wasm build must not pay for the import.

    Example:
        ```python
        from pathlib import Path

        from fastapi import Request

        from tempest_fastapi_sdk.ssr import build_web_app

        generated: str = (Path("dist/server") / "index.html").read_text()


        def shell(request: Request) -> str:
            "Serve the generated shell with the document language fixed."
            nonce = getattr(request.state, "csp_nonce", "")
            return generated.replace('<html lang="en">', '<html lang="pt-BR">').replace(
                "<script",
                f'<script nonce="{nonce}"',
                1,
            )


        web = build_web_app("dist/server", shell=shell)
        ```
    """
    root = Path(directory).resolve()
    mode = detect_build_mode(root)
    if mode != "server":
        raise ValueError(
            f"{root} is a {mode!r} build; build_web_app hosts server builds "
            "only — use make_web_app_router() for a static (wasm) build"
        )
    if shell is not None:
        _check_shell(shell)

    from fastapi.staticfiles import StaticFiles
    from tempestweb.cli.loader import load_app
    from tempestweb.server import create_app

    loaded = load_app(root / "app.py")
    app: FastAPI = create_app(
        loaded.make_state,
        loaded.view,
        title=title or root.name,
        theme=theme,
    )
    app.mount("/static", StaticFiles(directory=str(root / "static")), name="static")
    index_path = root / _INDEX

    @app.get("/", name="tempestweb_index")
    async def index(request: Request) -> Response:
        """Serve the app shell that mounts the client over WebSocket.

        Args:
            request (Request): The incoming request, handed to a shell
                callable that declares a parameter.

        Returns:
            Response: The generated shell, or the override.
        """
        if shell is None:
            return FileResponse(index_path, media_type="text/html")
        rendered = _render_shell(shell, request)
        if isinstance(rendered, Path):
            return FileResponse(rendered, media_type="text/html")
        return HTMLResponse(rendered)

    return app


__all__: list[str] = [
    "BuildMode",
    "ShellSource",
    "build_web_app",
    "detect_build_mode",
    "make_web_app_router",
]
