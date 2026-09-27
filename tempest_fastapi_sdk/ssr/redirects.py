"""Redirect back to the screen a form came from, without an open redirect.

After a ``POST`` the natural answer is ``303`` to the page that sent it,
and the ``Referer`` header names that page. The header is client input:
followed blindly it turns the application into an open redirect
(``Referer: https://evil.example/``). :func:`redirect_back` follows it
only when it names **this** host and a path under the allowed prefix,
and always answers a **relative** ``Location`` built from the validated
path — never the header as received.
"""

from __future__ import annotations

from typing import Final
from urllib.parse import unquote, urlsplit

from fastapi import Request
from fastapi.responses import RedirectResponse

_ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})


def _prefix_matches(path: str, allowed_prefix: str) -> bool:
    """Tell whether a path sits under a prefix, on a segment boundary.

    ``"/admin"`` admits ``/admin`` and ``/admin/buckets`` but not
    ``/administrator``.

    Args:
        path (str): The request path to check.
        allowed_prefix (str): The prefix; ``"/"`` admits every path.

    Returns:
        bool: ``True`` when ``path`` is the prefix or below it.
    """
    base = allowed_prefix.rstrip("/")
    if not base:
        return True
    return path == base or path.startswith(base + "/")


def _is_safe_path(path: str) -> bool:
    """Tell whether a path is safe to emit as a relative ``Location``.

    Rejects what a browser would resolve off this origin or outside the
    prefix after the check: a leading ``//`` (protocol-relative URL), a
    backslash (browsers read ``\\`` as ``/``), control characters and
    whitespace, and ``.`` / ``..`` segments, including percent-encoded
    ones (``%2e%2e``), which browsers normalize away.

    Args:
        path (str): The path component of the ``Referer``.

    Returns:
        bool: ``True`` when the path is absolute, single-origin and free
        of dot segments.
    """
    if not path.startswith("/") or path.startswith("//"):
        return False
    decoded = unquote(path)
    if "\\" in decoded or decoded.startswith("//"):
        return False
    if any(ord(char) < 0x21 or ord(char) == 0x7F for char in decoded):
        return False
    return all(segment not in (".", "..") for segment in decoded.split("/"))


def back_url(request: Request, *, allowed_prefix: str = "/") -> str | None:
    """Return the validated relative URL of the page the request came from.

    Args:
        request (Request): The current request.
        allowed_prefix (str): Only a ``Referer`` path under this prefix
            is accepted (segment boundary: ``"/admin"`` does not admit
            ``/administrator``). ``"/"`` accepts any path on this host.

    Returns:
        str | None: ``path`` (plus ``?query`` when present) of the
        ``Referer``, or ``None`` when the header is missing, is not an
        absolute ``http``/``https`` URL, names another host (or carries
        user info), or its path is outside the prefix or unsafe.
    """
    referer = request.headers.get("referer")
    if not referer:
        return None
    try:
        parts = urlsplit(referer.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return None
    if not parts.netloc or parts.netloc.lower() != request.url.netloc.lower():
        return None
    if not _is_safe_path(parts.path) or not _prefix_matches(
        unquote(parts.path),
        allowed_prefix,
    ):
        return None
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


def redirect_back(
    request: Request,
    *,
    fallback: str,
    allowed_prefix: str = "/",
    status_code: int = 303,
) -> RedirectResponse:
    """Redirect to the page the request came from, or to a fallback.

    The ``Referer`` is followed only when it names this host (compared
    with the request's own ``Host``) and a path under ``allowed_prefix``;
    anything else — another host, no host, a ``//evil.example`` path, a
    ``javascript:`` URL, a path outside the prefix — goes to
    ``fallback``. The ``Location`` sent is always the relative path, so
    even a header that passed cannot change the origin.

    Args:
        request (Request): The current request.
        fallback (str): Where to go when the ``Referer`` is refused. It
            is emitted as given, so it comes from code, never from the
            request.
        allowed_prefix (str): The path prefix the ``Referer`` must sit
            under. ``"/"`` (the default) accepts any path on this host.
        status_code (int): The redirect status. ``303`` (the default)
            makes the browser follow with a ``GET``, which is what a form
            post wants.

    Returns:
        RedirectResponse: The redirect.

    Example:
        ```python
        from fastapi import FastAPI, Request
        from fastapi.responses import RedirectResponse

        from tempest_fastapi_sdk.ssr import redirect_back

        app: FastAPI = FastAPI()


        @app.post("/admin/buckets/{name}/delete")
        async def delete_bucket(request: Request, name: str) -> RedirectResponse:
            return redirect_back(request, fallback="/admin", allowed_prefix="/admin")
        ```
    """
    target = back_url(request, allowed_prefix=allowed_prefix)
    return RedirectResponse(
        target if target is not None else fallback,
        status_code=status_code,
    )


__all__: list[str] = ["back_url", "redirect_back"]
