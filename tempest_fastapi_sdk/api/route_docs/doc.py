"""The value type of the route-docs catalog."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RouteDoc:
    """The OpenAPI text of one operation, in one language.

    Attributes:
        summary (str | None): The operation's ``summary``. ``None`` lets
            FastAPI derive it from the endpoint name — kept for the
            English entries of routes that never declared one, so their
            document is unchanged.
        description (str | None): The operation's ``description``.
            ``None`` lets FastAPI use the endpoint docstring, for the
            same reason. May hold :meth:`str.format` placeholders
            (``{prefix}``) that the router fills in.
    """

    summary: str | None = None
    description: str | None = None


__all__: list[str] = ["RouteDoc"]
