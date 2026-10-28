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
import json
import mimetypes
import os
import re
from collections.abc import Callable, Mapping, Sequence
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeAlias

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.datastructures import Headers
from starlette.staticfiles import NotModifiedResponse

from tempest_fastapi_sdk.api.route_docs import RouteDocs

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

#: ``Cache-Control`` for a path declared immutable: one year (the
#: practical maximum RFC 9111 implementations honor) plus the
#: ``immutable`` directive, so a browser does not even revalidate it on
#: reload. Only safe for content-addressed paths, whose name changes
#: whenever their bytes do.
_IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"

#: Default file name of the immutable-path manifest a build may ship at
#: its root. See :func:`make_web_app_router` for the format.
_IMMUTABLE_MANIFEST = "immutable.json"

#: Root-level entry points that are referenced by a fixed name — so their
#: name cannot change when their bytes do — and therefore are never
#: served as immutable, whatever a pattern or manifest says.
_NEVER_IMMUTABLE: frozenset[str] = frozenset(
    {
        _INDEX,
        "sw.js",
        "bootstrap.js",
        "register.js",
        "manifest.webmanifest",
    },
)


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


def _is_not_modified(request_headers: Headers, response_headers: Headers) -> bool:
    """Decide whether a conditional GET/HEAD may be answered ``304``.

    Ported from Starlette's ``StaticFiles.is_not_modified`` (Starlette's
    ``FileResponse`` stamps ``ETag``/``Last-Modified`` but never evaluates
    the preconditions, and the ``StaticFiles`` method lives on a mount
    class this router does not use), extended with the ``*`` form RFC
    9110 §13.1.2 defines.

    ``If-None-Match`` uses the weak comparison (a ``W/`` prefix on either
    side is ignored) over its comma-separated list, and when present it
    is the only precondition evaluated: RFC 9110 §13.1.3 says a recipient
    must ignore ``If-Modified-Since`` when ``If-None-Match`` is present.
    An unparseable ``If-Modified-Since`` is ignored, as the RFC requires.

    Args:
        request_headers (Headers): The incoming request headers.
        response_headers (Headers): The headers of the response that would
            be sent — must carry ``etag`` and ``last-modified``.

    Returns:
        bool: ``True`` when the client's cached copy is still current.
    """
    if_none_match = request_headers.get("if-none-match")
    if if_none_match is not None:
        if if_none_match.strip() == "*":
            return True
        etag = response_headers.get("etag", "").removeprefix("W/")
        candidates = {
            tag.strip().removeprefix("W/") for tag in if_none_match.split(",")
        }
        return bool(etag) and etag in candidates
    if_modified_since = request_headers.get("if-modified-since")
    last_modified = response_headers.get("last-modified")
    if if_modified_since is None or last_modified is None:
        return False
    try:
        since = parsedate_to_datetime(if_modified_since)
        modified = parsedate_to_datetime(last_modified)
    except (TypeError, ValueError):
        return False
    if since.tzinfo is None or modified.tzinfo is None:
        return False
    return modified <= since


def _conditional_file_response(
    path: Path,
    request: Request,
    *,
    media_type: str | None,
    headers: Mapping[str, str],
) -> Response:
    """Serve ``path`` honoring ``If-None-Match`` / ``If-Modified-Since``.

    The file is stat'ed up front so ``ETag``, ``Last-Modified`` and
    ``Content-Length`` exist before the response is sent — Starlette only
    computes them inside ``__call__`` otherwise — and the preconditions
    can be compared against them. A satisfied precondition yields an
    empty ``304`` that keeps the headers RFC 9110 §15.4.5 lists
    (``Cache-Control``, ``ETag``, ``Vary``, ...) plus ``headers``; the
    body is never read.

    Args:
        path (Path): The file to serve.
        request (Request): The incoming request, whose preconditions are
            evaluated.
        media_type (str | None): The ``Content-Type`` of a full response.
        headers (Mapping[str, str]): Headers stamped on the ``200`` and on
            the ``304`` alike (cache policy and security headers).

    Returns:
        Response: A ``FileResponse``, or a ``304`` with no body.
    """
    response = FileResponse(
        path,
        media_type=media_type,
        headers=dict(headers),
        stat_result=os.stat(path),
    )
    if not _is_not_modified(request.headers, response.headers):
        return response
    not_modified = NotModifiedResponse(response.headers)
    for header, value in headers.items():
        not_modified.headers.setdefault(header, value)
    return not_modified


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a build-relative glob into an anchored regular expression.

    ``**`` matches across directories (``client-*/**``), ``*`` and ``?``
    stay within one path segment, and every other character is literal —
    the subset needed to name content-hashed paths. Owned here rather than
    built on :mod:`fnmatch` (whose ``*`` crosses ``/``) or
    ``PurePath.full_match`` (Python 3.13+, while the package supports
    3.12).

    Args:
        pattern (str): A ``/``-separated pattern relative to the build root.

    Returns:
        re.Pattern[str]: The compiled, fully anchored expression.

    Raises:
        ValueError: When ``pattern`` is empty or absolute.
    """
    if not pattern or pattern.startswith("/"):
        raise ValueError(
            f"immutable pattern {pattern!r} must be a non-empty path relative "
            "to the build root (no leading '/')",
        )
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(parts) + r"\Z")


def _read_immutable_manifest(root: Path, name: str) -> list[str]:
    """Read the immutable-path patterns a build declares, if it ships any.

    Args:
        root (Path): The resolved build root.
        name (str): The manifest file name, relative to ``root``.

    Returns:
        list[str]: The declared patterns; ``[]`` when the file is absent.

    Raises:
        ValueError: When the file exists but is not a JSON object whose
            ``"immutable"`` key holds a list of strings — a malformed
            manifest fails the boot instead of silently caching nothing.
    """
    manifest = root / name
    if not manifest.is_file():
        return []
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{manifest} is not valid JSON: {exc}") from exc
    patterns = document.get("immutable") if isinstance(document, dict) else None
    if not isinstance(patterns, list) or not all(
        isinstance(item, str) for item in patterns
    ):
        raise ValueError(
            f'{manifest} must be a JSON object with an "immutable" list of '
            "glob strings",
        )
    return patterns


def make_web_app_router(
    directory: str | Path,
    *,
    asset_cache_control: str = "public, max-age=3600",
    security_headers: dict[str, str] | None = None,
    spa_fallback: bool = True,
    shell: ShellSource | None = None,
    immutable: Sequence[str] = (),
    immutable_manifest: str | None = _IMMUTABLE_MANIFEST,
    docs_locale: str | None = None,
) -> APIRouter:
    """Serve a static ``tempestweb`` (wasm) build as an ``APIRouter``.

    The router serves every file in the build directory and, for any
    unmatched path, falls back to ``index.html`` so client-side routing
    works on a hard refresh (single-page-app history fallback). The wasm
    artifact references ``/sw.js`` at the site root, so mount this at the
    application root — ``app.include_router(router)`` — and include it
    **after** your API routers so specific routes win over the catch-all.
    Both ``GET`` and ``HEAD`` are answered; the ``HEAD`` routes stay out
    of the OpenAPI schema.

    Caching: ``index.html`` and ``sw.js`` are always sent ``no-cache``
    (so a redeploy is seen immediately), and so is a ``shell`` override.
    A path matching ``immutable`` or the build's immutable manifest is
    sent ``public, max-age=31536000, immutable``; every other asset uses
    ``asset_cache_control``. Every file response carries ``ETag`` and
    ``Last-Modified`` and honors ``If-None-Match`` / ``If-Modified-Since``
    with an empty ``304 Not Modified``, so ``no-cache`` means "revalidate"
    and not "download again". No Content-Security-Policy is imposed — a
    wasm build runs first-party code and boots Pyodide (which needs
    ``wasm-unsafe-eval``); pass ``security_headers`` to add your own.

    The immutable manifest is a JSON file at the build root,
    ``{"immutable": ["client-3f2a9c/**", "tempestweb-pkg.3f2a9c.zip"]}``:
    one list of glob patterns relative to the root, where ``**`` crosses
    directories and ``*`` / ``?`` stay within a segment. Other keys are
    ignored. It is read once, when the router is built. The fixed-name
    entry points (``index.html``, ``sw.js``, ``bootstrap.js``,
    ``register.js``, ``manifest.webmanifest``) and the manifest itself
    are never immutable, whatever a pattern says — their names cannot
    change when their bytes do.

    Args:
        directory (str | Path): The build directory (``dist/wasm``).
        asset_cache_control (str): ``Cache-Control`` for non-shell assets
            that are not declared immutable.
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
        immutable (Sequence[str]): Glob patterns, relative to the build
            root, naming content-addressed paths to cache for a year with
            ``immutable``. Only list paths whose name changes whenever
            their bytes do. Empty (default) declares nothing.
        immutable_manifest (str | None): File name, relative to the build
            root, of the manifest whose patterns are added to
            ``immutable``. A missing file declares nothing; ``None`` skips
            the lookup entirely.
        docs_locale (str | None): Language of the OpenAPI ``summary``
            and ``description`` text — ``"en-US"`` or ``"pt-BR"``.
            ``None`` (default) reads ``API_DOCS_LOCALE`` through
            :class:`~tempest_fastapi_sdk.settings.ServerSettings`,
            whose default is ``"en-US"``.

    Returns:
        APIRouter: A router serving the static build.

    Raises:
        ValueError: When ``directory`` is not a static (wasm) tempestweb
            build (use :func:`build_web_app` for a server build), when
            ``shell`` is a string with no markup in it — that is a path
            written where a document was expected —, when a pattern is
            empty or absolute, or when the manifest exists but is
            malformed.
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
    patterns = list(immutable)
    never_immutable = set(_NEVER_IMMUTABLE)
    if immutable_manifest is not None:
        patterns.extend(_read_immutable_manifest(root, immutable_manifest))
        never_immutable.add(immutable_manifest)
    immutable_matchers = [_glob_to_regex(pattern) for pattern in patterns]

    def _cache_control(path: Path) -> str:
        """Pick the ``Cache-Control`` policy for a file under the build root.

        Args:
            path (Path): The resolved file being served.

        Returns:
            str: ``no-cache`` for the shell and the worker, the immutable
            policy for a declared content-addressed path, otherwise
            ``asset_cache_control``.
        """
        if path.name in _ALWAYS_REVALIDATE:
            return "no-cache"
        relative = path.relative_to(root).as_posix()
        if relative not in never_immutable and any(
            matcher.match(relative) for matcher in immutable_matchers
        ):
            return _IMMUTABLE_CACHE_CONTROL
        return asset_cache_control

    def _file_response(
        path: Path,
        request: Request,
        *,
        cache_control: str,
    ) -> Response:
        """Serve one file with cache + security headers, conditionally.

        A service worker served from the root is allowed to claim the
        whole origin scope, which is what lets it control every page of
        the app — hence ``Service-Worker-Allowed: /`` on ``sw.js``.

        Args:
            path (Path): The file to serve.
            request (Request): The incoming request (its preconditions).
            cache_control (str): The ``Cache-Control`` value to send.

        Returns:
            Response: The file, or an empty ``304``.
        """
        response_headers = {"Cache-Control": cache_control}
        if path.name == "sw.js":
            response_headers["Service-Worker-Allowed"] = "/"
        for header, value in headers.items():
            response_headers.setdefault(header, value)
        return _conditional_file_response(
            path,
            request,
            media_type=_media_type(path),
            headers=response_headers,
        )

    route_docs: RouteDocs = RouteDocs(docs_locale)
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
            return _file_response(index_path, request, cache_control="no-cache")
        rendered = _render_shell(shell, request)
        if isinstance(rendered, Path):
            return _file_response(rendered, request, cache_control="no-cache")
        response: Response = HTMLResponse(rendered)
        response.headers["Cache-Control"] = "no-cache"
        for header, value in headers.items():
            response.headers.setdefault(header, value)
        return response

    async def index(request: Request) -> Response:
        """Serve the SPA shell (``index.html``, or the override).

        Args:
            request (Request): The incoming request.

        Returns:
            Response: The shell document.
        """
        return _shell_response(request)

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
            if resolved == index_path:
                return _shell_response(request)
            return _file_response(
                resolved,
                request,
                cache_control=_cache_control(resolved),
            )
        if spa_fallback:
            return _shell_response(request)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

    router.add_api_route(
        "/",
        index,
        methods=["GET"],
        name="tempestweb_index",
        summary=route_docs.summary("web_app.index"),
        description=route_docs.description("web_app.index"),
    )
    router.add_api_route(
        "/",
        index,
        methods=["HEAD"],
        name="tempestweb_index_head",
        include_in_schema=False,
    )
    router.add_api_route(
        "/{resource:path}",
        asset,
        methods=["GET"],
        name="tempestweb_asset",
        summary=route_docs.summary("web_app.asset"),
        description=route_docs.description("web_app.asset"),
    )
    router.add_api_route(
        "/{resource:path}",
        asset,
        methods=["HEAD"],
        name="tempestweb_asset_head",
        include_in_schema=False,
    )
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
            ``App``. This is the half CSS cannot reach: a themed
            component resolves its colors at construction and bakes them
            into an inline ``style``, so rebranding the custom properties
            alone leaves those at the Material baseline. The build
            installs this palette around the ``view`` call, so every
            component the view constructs resolves against it with no
            call-site change; a ``theme=`` passed straight to a widget
            still wins. Needs ``tempestweb`` 0.67.0, which is where the
            floor on ``tempest-core`` reaches the version that connects
            the two — hence the extra's floor. ``None`` (default) keeps
            the Material baseline. Pair this with
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
            Response: The generated shell, or the override — a file-backed
            shell answers a matching ``If-None-Match`` /
            ``If-Modified-Since`` with an empty ``304``.
        """
        if shell is None:
            return _conditional_file_response(
                index_path,
                request,
                media_type="text/html",
                headers={},
            )
        rendered = _render_shell(shell, request)
        if isinstance(rendered, Path):
            return _conditional_file_response(
                rendered,
                request,
                media_type="text/html",
                headers={},
            )
        return HTMLResponse(rendered)

    return app


__all__: list[str] = [
    "BuildMode",
    "ShellSource",
    "build_web_app",
    "detect_build_mode",
    "make_web_app_router",
]
