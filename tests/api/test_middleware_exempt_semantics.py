"""``exempt_paths`` means the same thing in every middleware that takes it.

Through 0.285.0 it did not: three middlewares matched by equality and two
by prefix, under one argument name. A service passing the same tuple to
``AccessLogMiddleware`` and ``ResponseCacheMiddleware`` got an exemption in
one and a silent no-op in the other — and because the response cache's
no-op meant draining an endless stream, the miss was a dead route rather
than a missing cache entry.

The structural guard is :class:`TestEveryMiddlewareSharesTheMatcher`: any
middleware that grows an ``exempt_paths`` argument must route it through
:class:`PathExemption`, so a sixth one cannot reintroduce a private
meaning. The behavioural guards below pin what that meaning is.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from tempest_fastapi_sdk.api.middlewares._exempt import PathExemption
from tempest_fastapi_sdk.api.middlewares.access_log import AccessLogMiddleware
from tempest_fastapi_sdk.api.middlewares.graceful import GracefulShutdownMiddleware
from tempest_fastapi_sdk.api.middlewares.honeypot import HoneypotBanMiddleware
from tempest_fastapi_sdk.api.middlewares.rate_limit import RateLimitMiddleware
from tempest_fastapi_sdk.api.middlewares.response_cache import ResponseCacheMiddleware

EXEMPTING_MIDDLEWARES: tuple[type[Any], ...] = (
    AccessLogMiddleware,
    GracefulShutdownMiddleware,
    HoneypotBanMiddleware,
    RateLimitMiddleware,
    ResponseCacheMiddleware,
)


class TestEveryMiddlewareSharesTheMatcher:
    """No middleware may implement path exemption on its own again."""

    @pytest.mark.parametrize(
        "middleware", EXEMPTING_MIDDLEWARES, ids=lambda m: m.__name__
    )
    def test_accepts_both_arguments(self, middleware: type[Any]) -> None:
        """Both argument names exist, so neither meaning needs a workaround."""
        parameters = inspect.signature(middleware.__init__).parameters
        assert "exempt_paths" in parameters
        assert "exempt_prefixes" in parameters

    @pytest.mark.parametrize(
        "middleware", EXEMPTING_MIDDLEWARES, ids=lambda m: m.__name__
    )
    def test_stores_a_path_exemption(self, middleware: type[Any]) -> None:
        """The argument is routed through the shared matcher, not a local set."""
        source = inspect.getsource(middleware)
        assert "PathExemption(" in source, (
            f"{middleware.__name__} builds its own exemption; route "
            "exempt_paths/exempt_prefixes through PathExemption instead"
        )

    def test_no_middleware_matches_prefixes_by_hand(self) -> None:
        """``startswith`` over an exemption tuple is how the drift started."""
        for middleware in EXEMPTING_MIDDLEWARES:
            source = inspect.getsource(middleware)
            assert "self._exempt)" not in source.replace(" ", ""), (
                f"{middleware.__name__} iterates its exemption directly"
            )
            assert "startswith(prefix) for prefix in self._exempt" not in source


class TestExemptPathsIsEquality:
    """``exempt_paths`` covers the path it names and nothing beneath it."""

    def test_exact_path_is_exempt(self) -> None:
        """The named path matches."""
        exemption = PathExemption(paths=("/api/sse",))
        assert exemption.matches("/api/sse") is True

    def test_child_path_is_not_exempt(self) -> None:
        """This is the production defect, stated as an assertion.

        The service listed ``/api/sse`` and served ``/api/sse/stream``.
        Equality says no, which is why the exemption has to be spelled with
        ``exempt_prefixes`` when a subtree is meant.
        """
        exemption = PathExemption(paths=("/api/sse",))
        assert exemption.matches("/api/sse/stream") is False

    def test_sibling_path_is_not_exempt(self) -> None:
        """Equality never widens a hole to a neighbouring route."""
        exemption = PathExemption(paths=("/health",))
        assert exemption.matches("/health-admin") is False


class TestExemptPrefixesIsPrefix:
    """``exempt_prefixes`` covers everything textually beneath it."""

    def test_child_path_is_exempt(self) -> None:
        """A subtree exemption reaches the route that hangs."""
        exemption = PathExemption(prefixes=("/api/sse",))
        assert exemption.matches("/api/sse/stream") is True

    def test_prefix_is_textual_not_segmented(self) -> None:
        """Documented behaviour: the match is on characters, not segments.

        Pinned so nobody "fixes" it into segment matching without deciding
        to, since a service may well be relying on the textual reach.
        """
        exemption = PathExemption(prefixes=("/api/sse",))
        assert exemption.matches("/api/sse-legacy") is True

    def test_unrelated_path_is_not_exempt(self) -> None:
        """A prefix still bounds the hole."""
        exemption = PathExemption(prefixes=("/api/sse",))
        assert exemption.matches("/api/orders") is False


class TestEmptyExemptionShortCircuits:
    """The default configuration exempts nothing."""

    def test_nothing_configured_matches_nothing(self) -> None:
        """An unconfigured middleware must not exempt a route by accident."""
        exemption = PathExemption()
        assert exemption.matches("/anything") is False
        assert bool(exemption) is False

    def test_configured_exemption_is_truthy(self) -> None:
        """``__bool__`` lets a caller skip the check entirely when unused."""
        assert bool(PathExemption(paths=("/x",))) is True
        assert bool(PathExemption(prefixes=("/x",))) is True
