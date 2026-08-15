"""Guarded access to the ``tempest_core`` widget primitives used by ``ui``.

Every module under :mod:`tempest_fastapi_sdk.ui` builds widget trees out of
a handful of ``tempest_core`` primitives. Those live behind the optional
``[ssr]`` extra, so importing them directly would make
``import tempest_fastapi_sdk.ui`` fail on an install without the extra.

This module centralises the guarded import: when ``tempest_core`` is
present the real classes are re-exported; when it is absent, placeholders
take their place that raise a helpful :class:`ImportError` **on
construction**. The result is that importing the package (and reading its
type hints) always works, and the missing extra is reported at the moment
someone actually tries to build a widget.

Container choice, measured against the ``tempestweb`` HTML renderer:

* :data:`Column` / :data:`Row` are flex containers — the renderer injects
  ``display: flex`` by widget type even with no explicit style, so they
  are used only where a flex box is wanted.
* :data:`Stack` renders as a bare ``<div>`` with **no** injected style,
  which is what non-flex semantic elements (``<select>``, ``<table>``,
  ``<ul>``, ``<form>``) need. ``tests/test_ui_core_contract.py`` pins that
  behaviour so an upstream change shows up as a failure rather than as
  silently broken markup.
"""

from __future__ import annotations

from typing import Any, TypeAlias

Widget: TypeAlias = Any
"""A ``tempest_core`` widget.

``tempest_core`` ships no type information, so its widget classes reach
the type checker as ``Any`` regardless. Naming that alias keeps the
signatures readable — a function returning ``Widget`` says more than one
returning ``Any`` — without pretending to a precision the dependency does
not provide.
"""

try:
    from tempest_core import Column as Column
    from tempest_core import Row as Row
    from tempest_core import Style as Style
    from tempest_core import Text as Text
    from tempest_core.style import Edge as Edge
    from tempest_core.widgets import Component as Component
    from tempest_core.widgets import Stack as Stack

    CORE_IMPORT_ERROR: ImportError | None = None
except ImportError as exc:  # pragma: no cover - exercised only without [ssr]
    CORE_IMPORT_ERROR = exc

    _MESSAGE = (
        "tempest_fastapi_sdk.ui requires the optional [ssr] extra. "
        "Install with: pip install tempest-fastapi-sdk[ssr]"
    )

    class _MissingCore:
        """Placeholder used when ``tempest_core`` is not installed.

        Construction always fails with a message naming the missing extra,
        so the error surfaces at first use instead of at import time.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            """Reject construction when the ``[ssr]`` extra is missing.

            Args:
                *args (Any): Ignored positional arguments.
                **kwargs (Any): Ignored keyword arguments.

            Raises:
                ImportError: Always, pointing at the ``[ssr]`` extra.
            """
            raise ImportError(_MESSAGE) from CORE_IMPORT_ERROR

    Column = _MissingCore  # type: ignore[assignment, misc]
    Row = _MissingCore  # type: ignore[assignment, misc]
    Stack = _MissingCore  # type: ignore[assignment, misc]
    Style = _MissingCore  # type: ignore[assignment, misc]
    Text = _MissingCore  # type: ignore[assignment, misc]
    Edge = _MissingCore  # type: ignore[assignment, misc]
    Component = _MissingCore  # type: ignore[assignment, misc]


def require_core() -> None:
    """Fail fast when the ``[ssr]`` extra is missing.

    Call this from functions that build widget trees but do not construct
    a placeholder class themselves (so the error would otherwise surface
    later, from deeper inside the renderer).

    Raises:
        ImportError: When ``tempest_core`` is not importable.
    """
    if CORE_IMPORT_ERROR is not None:
        raise ImportError(
            "tempest_fastapi_sdk.ui requires the optional [ssr] extra. "
            "Install with: pip install tempest-fastapi-sdk[ssr]",
        ) from CORE_IMPORT_ERROR


__all__: list[str] = [
    "CORE_IMPORT_ERROR",
    "Column",
    "Component",
    "Edge",
    "Row",
    "Stack",
    "Style",
    "Text",
    "Widget",
    "require_core",
]
