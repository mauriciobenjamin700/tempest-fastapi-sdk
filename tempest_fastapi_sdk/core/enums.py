"""Shared enum base classes for string- and integer-valued enums.

Both bases mix in a value type (:class:`str` or :class:`int`) so that
members are genuine instances of that type: they compare equal to their
raw value (``Member == "VALUE"`` / ``Member == 1``), serialize cleanly
outside Pydantic, and bind directly to ``String``/``Integer`` database
columns as their value.

The introspection helpers (``values``/``keys``/``to_dict``) are shared
between both bases via :class:`_EnumHelpers` so there is a single
implementation regardless of the underlying value type.
"""

from enum import Enum
from typing import Any


class _EnumHelpers:
    """Mixin adding introspection helpers to an :class:`~enum.Enum`.

    Not meant to be used directly; mix it into an ``Enum`` subclass
    alongside a value type. Use :class:`BaseStrEnum` or
    :class:`BaseIntEnum` instead.
    """

    @classmethod
    def values(cls) -> list[Any]:
        """Return the value of every member.

        Returns:
            list[Any]: The member values, in definition order. The
            concrete element type is ``str`` for :class:`BaseStrEnum`
            and ``int`` for :class:`BaseIntEnum`.
        """
        return [member.value for member in cls]  # type: ignore[attr-defined]

    @classmethod
    def keys(cls) -> list[str]:
        """Return the name of every member.

        Returns:
            list[str]: The member names, in definition order.
        """
        return list(cls.__members__.keys())  # type: ignore[attr-defined]

    @classmethod
    def to_dict(cls) -> dict[str, Any]:
        """Return a name-to-value mapping of the members.

        Returns:
            dict[str, Any]: Mapping of each member name to its value.
        """
        return {member.name: member.value for member in cls}  # type: ignore[attr-defined]


class BaseStrEnum(_EnumHelpers, str, Enum):  # noqa: UP042
    """Base class for string-valued enums.

    Note:
        Deliberately a ``str`` + ``Enum`` mixin rather than
        :class:`enum.StrEnum`. The two differ in ``str(member)``
        (``"Cls.MEMBER"`` here vs. the bare value under ``StrEnum``);
        keeping the mixin form preserves the behaviour consumers already
        rely on across services.


    Mixing in ``str`` makes every member a genuine string instance, so
    members compare equal to their values (``Member == "VALUE"``),
    serialize cleanly outside Pydantic, and bind directly to ``String``
    database columns as their value.
    """


class BaseIntEnum(_EnumHelpers, int, Enum):
    """Base class for integer-valued enums.

    Mixing in ``int`` makes every member a genuine integer instance, so
    members compare equal to their values (``Member == 1``), serialize
    cleanly outside Pydantic, and bind directly to ``Integer`` database
    columns as their value.
    """


__all__: list[str] = [
    "BaseIntEnum",
    "BaseStrEnum",
]
