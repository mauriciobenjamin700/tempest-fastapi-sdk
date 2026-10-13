"""Signature introspection shared by the router factories.

A factory that starts passing a new keyword to a method the consumer may
override breaks the old override at runtime with a ``TypeError`` — mypy
never sees it, because the override lives in the consumer. The factories
read the bound method's signature once and forward the keyword only when
it is accepted.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable


def accepts_keyword(func: Callable[..., object], name: str) -> bool:
    """Tell whether ``func`` can be called with the keyword ``name``.

    Args:
        func (Callable[..., object]): The callable, usually a bound method.
        name (str): The keyword argument.

    Returns:
        bool: ``True`` when ``func`` declares ``name`` as a parameter that
        can be passed by keyword, or takes ``**kwargs``; ``False`` when it
        does not, or when its signature cannot be read.
    """
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    parameter = parameters.get(name)
    if parameter is not None:
        return parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    return any(
        candidate.kind is inspect.Parameter.VAR_KEYWORD
        for candidate in parameters.values()
    )


__all__: list[str] = ["accepts_keyword"]
