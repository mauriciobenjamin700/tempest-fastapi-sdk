"""One meaning for "this path is exempt", shared by every middleware.

Five middlewares in this package take an ``exempt_paths`` argument, and
until now the name meant two different things depending on which one you
passed it to:

===================================  ==========
Middleware                           Matched by
===================================  ==========
``ResponseCacheMiddleware``          equality
``RateLimitMiddleware``              equality
``GracefulShutdownMiddleware``       equality
``AccessLogMiddleware``              prefix
``HoneypotBanMiddleware``            prefix
===================================  ==========

A service that exempts its SSE route from logging and from caching passes
*the same tuple* to both, and gets an exemption in one and a no-op in the
other. That is what happened in production: the tuple held ``/api/sse``,
the route was ``/api/sse/stream``, the access log honoured it and the
response cache did not — and because the response cache's miss meant
*draining a stream that never ends*, the whole notification feature
answered ``504`` (see :mod:`._streaming`).

The fix is not to pick the more convenient meaning. Widening equality to
prefix would have made ``exempt_paths=("/health",)`` on
``RateLimitMiddleware`` silently stop rate-limiting ``/health-admin`` too
— an exemption is a hole, and holes must be exactly the size you asked
for. So ``exempt_paths`` means equality everywhere, ``exempt_prefixes``
means prefix everywhere, and this class is the only implementation of
either.
"""

from __future__ import annotations

from collections.abc import Sequence


class PathExemption:
    """Decides whether a request path is exempt from a middleware.

    A path is exempt when it equals one of ``paths`` or starts with one of
    ``prefixes``. Both default to empty, and an instance built from two
    empty collections exempts nothing — :meth:`matches` short-circuits to
    ``False`` without touching the path.
    """

    __slots__ = ("_exact", "_prefixes")

    def __init__(
        self,
        *,
        paths: Sequence[str] = (),
        prefixes: Sequence[str] = (),
    ) -> None:
        """Configure the exemption.

        Args:
            paths (Sequence[str]): Paths matched by **equality**. A path
                exempted this way covers itself and nothing beneath it, so
                ``"/api/sse"`` does not exempt ``"/api/sse/stream"``.
            prefixes (Sequence[str]): Paths matched by **prefix**. A prefix
                exempts every path beneath it, so ``"/api/sse"`` covers
                ``"/api/sse/stream"`` and ``"/api/sse-legacy"`` alike —
                prefix matching is textual, not path-segment aware.
        """
        self._exact: frozenset[str] = frozenset(paths)
        self._prefixes: tuple[str, ...] = tuple(prefixes)

    def matches(self, path: str) -> bool:
        """Return whether ``path`` is exempt.

        Args:
            path (str): The request path, as ``request.url.path`` gives it.

        Returns:
            bool: ``True`` when ``path`` equals one of the configured paths
            or starts with one of the configured prefixes.
        """
        if path in self._exact:
            return True
        return any(path.startswith(prefix) for prefix in self._prefixes)

    def __bool__(self) -> bool:
        """Return whether anything is exempt at all.

        Returns:
            bool: ``True`` when at least one path or prefix is configured.
        """
        return bool(self._exact or self._prefixes)


__all__: list[str] = ["PathExemption"]
