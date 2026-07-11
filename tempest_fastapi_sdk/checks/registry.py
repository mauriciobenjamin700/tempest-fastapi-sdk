"""The system-check registry and the run / startup entrypoints."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from tempest_fastapi_sdk.checks.messages import CheckLevel, CheckMessage

#: A check: called with the context (usually the settings object, or
#: ``None``) and returning any number of messages. Sync only.
CheckFn = Callable[[Any], Iterable[CheckMessage]]


class SystemCheckError(Exception):
    """Raised when a run produces a message at or above the threshold.

    Attributes:
        messages (list[CheckMessage]): The serious messages that
            triggered the failure.
    """

    def __init__(self, messages: list[CheckMessage]) -> None:
        """Initialize with the offending messages.

        Args:
            messages (list[CheckMessage]): The serious messages.
        """
        self.messages: list[CheckMessage] = messages
        rendered = "\n".join(str(m) for m in messages)
        super().__init__(f"System checks failed:\n{rendered}")


class CheckRegistry:
    """A registry of system-check functions, filterable by tag.

    Register a check with :meth:`register` or the :meth:`check`
    decorator, optionally tagging it (``"security"``, ``"database"``,
    …) so a run can target a subset. Use your own instance for isolated
    sets, or the process-wide :data:`default_registry` via the
    module-level helpers.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._checks: list[tuple[CheckFn, frozenset[str]]] = []

    def register(self, func: CheckFn, *tags: str) -> CheckFn:
        """Register ``func`` under zero or more ``tags``.

        Args:
            func (CheckFn): The check callable.
            *tags (str): Optional tags for selective runs.

        Returns:
            CheckFn: ``func`` unchanged, so this doubles as a decorator.
        """
        self._checks.append((func, frozenset(tags)))
        return func

    def check(self, *tags: str) -> Callable[[CheckFn], CheckFn]:
        """Decorator form of :meth:`register`.

        Args:
            *tags (str): Optional tags for selective runs.

        Returns:
            Callable[[CheckFn], CheckFn]: The registering decorator.
        """

        def decorator(func: CheckFn) -> CheckFn:
            return self.register(func, *tags)

        return decorator

    def run(
        self,
        context: Any = None,
        *,
        tags: Iterable[str] | None = None,
    ) -> list[CheckMessage]:
        """Run the registered checks and collect their messages.

        Args:
            context (Any): Passed to every check — typically the
                settings object under inspection, or ``None``.
            tags (Iterable[str] | None): When given, only checks
                carrying at least one of these tags run.

        Returns:
            list[CheckMessage]: Every message emitted, in registration
            order.
        """
        wanted = frozenset(tags) if tags is not None else None
        messages: list[CheckMessage] = []
        for func, func_tags in self._checks:
            if wanted is not None and not (wanted & func_tags):
                continue
            messages.extend(func(context))
        return messages

    def clear(self) -> None:
        """Drop every registered check (test isolation)."""
        self._checks.clear()


#: The process-wide registry backing the module-level helpers and the
#: ``tempest check-config`` CLI command.
default_registry = CheckRegistry()


def register_check(func: CheckFn, *tags: str) -> CheckFn:
    """Register a check on :data:`default_registry`.

    Args:
        func (CheckFn): The check callable.
        *tags (str): Optional tags.

    Returns:
        CheckFn: ``func`` unchanged.
    """
    return default_registry.register(func, *tags)


def check(*tags: str) -> Callable[[CheckFn], CheckFn]:
    """Decorator registering a check on :data:`default_registry`.

    Args:
        *tags (str): Optional tags.

    Returns:
        Callable[[CheckFn], CheckFn]: The registering decorator.
    """
    return default_registry.check(*tags)


def run_checks(
    context: Any = None,
    *,
    tags: Iterable[str] | None = None,
    registry: CheckRegistry | None = None,
) -> list[CheckMessage]:
    """Run checks and return every message, without raising.

    Args:
        context (Any): Passed to every check (usually the settings).
        tags (Iterable[str] | None): Optional tag filter.
        registry (CheckRegistry | None): Registry to run; ``None`` uses
            :data:`default_registry`.

    Returns:
        list[CheckMessage]: Every emitted message.
    """
    return (registry or default_registry).run(context, tags=tags)


def run_system_checks(
    context: Any = None,
    *,
    tags: Iterable[str] | None = None,
    fail_level: CheckLevel = CheckLevel.ERROR,
    registry: CheckRegistry | None = None,
) -> list[CheckMessage]:
    """Run checks and raise if any reaches ``fail_level``.

    Call this from a FastAPI lifespan to fail fast on a misconfigured
    deployment instead of serving traffic with, say, an empty JWT
    secret.

    Args:
        context (Any): Passed to every check (usually the settings).
        tags (Iterable[str] | None): Optional tag filter.
        fail_level (CheckLevel): The threshold that makes the run fail.
            Defaults to ``ERROR``.
        registry (CheckRegistry | None): Registry to run; ``None`` uses
            :data:`default_registry`.

    Returns:
        list[CheckMessage]: Every emitted message (including the
        non-serious ones), when the run passes.

    Raises:
        SystemCheckError: When at least one message reaches
            ``fail_level``.
    """
    messages = run_checks(context, tags=tags, registry=registry)
    serious = [m for m in messages if m.is_serious(fail_level)]
    if serious:
        raise SystemCheckError(serious)
    return messages


__all__: list[str] = [
    "CheckFn",
    "CheckRegistry",
    "SystemCheckError",
    "check",
    "default_registry",
    "register_check",
    "run_checks",
    "run_system_checks",
]
