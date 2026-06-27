"""Shared enum base classes for string- and integer-valued enums.

Both bases mix in a value type (:class:`str` or :class:`int`) so that
members are genuine instances of that type: they compare equal to their
raw value (``Member == "VALUE"`` / ``Member == 1``), serialize cleanly
outside Pydantic, and bind directly to ``String``/``Integer`` database
columns as their value.

The introspection helpers (``values``/``keys``/``choices``/``to_dict``)
and the lenient constructor (``from_value``/``has_value``/``has_key``)
are shared between both bases via :class:`_EnumHelpers` so there is a
single implementation regardless of the underlying value type.
"""

from enum import Enum
from typing import Any, Final

_MISSING: Final[object] = object()
"""Sentinel marking "no default supplied" in :meth:`_EnumHelpers.from_value`.

A distinct object is required so that ``None`` remains a valid explicit
default a caller can ask for.
"""


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
    def choices(cls) -> list[tuple[Any, str]]:
        """Return ``(value, name)`` pairs for every member.

        Handy for building HTML ``<select>`` options, Django-style form
        ``choices``, or any UI that needs both the stored value and a
        human-facing label.

        Returns:
            list[tuple[Any, str]]: ``(value, name)`` pairs in definition
            order. The value element is ``str`` for :class:`BaseStrEnum`
            and ``int`` for :class:`BaseIntEnum`.
        """
        return [(member.value, member.name) for member in cls]  # type: ignore[attr-defined]

    @classmethod
    def to_dict(cls) -> dict[str, Any]:
        """Return a name-to-value mapping of the members.

        Returns:
            dict[str, Any]: Mapping of each member name to its value.
        """
        return {member.name: member.value for member in cls}  # type: ignore[attr-defined]

    @classmethod
    def has_value(cls, value: Any) -> bool:
        """Report whether ``value`` is the value of some member.

        Args:
            value (Any): The raw value to test.

        Returns:
            bool: ``True`` if a member carries this value, else ``False``.
        """
        return value in cls._value2member_map_  # type: ignore[attr-defined]

    @classmethod
    def has_key(cls, key: str) -> bool:
        """Report whether ``key`` is the name of some member.

        Args:
            key (str): The member name to test (case-sensitive).

        Returns:
            bool: ``True`` if a member has this name, else ``False``.
        """
        return key in cls.__members__  # type: ignore[attr-defined]

    @classmethod
    def from_value(cls, value: Any, *, default: Any = _MISSING) -> Any:
        """Resolve a member from a raw value or member name.

        Lookup order:

        1. Exact value match (the canonical ``cls(value)`` lookup).
        2. Member-name match -- exact, then case-insensitive (so both
           ``"RED"`` and ``"red"`` resolve to ``Color.RED``).

        Args:
            value (Any): The raw value or member name to resolve.
            default (Any): Returned when ``value`` matches no member. When
                omitted, an unmatched ``value`` raises ``ValueError``
                instead. Pass ``default=None`` to opt into a ``None``
                fallback explicitly.

        Returns:
            Any: The matching enum member, or ``default`` when supplied
            and nothing matched.

        Raises:
            ValueError: If ``value`` matches no member and no ``default``
                was supplied.
        """
        try:
            return cls(value)  # type: ignore[call-arg]
        except ValueError:
            pass
        if isinstance(value, str):
            members: dict[str, Any] = cls.__members__  # type: ignore[attr-defined]
            member = members.get(value) or members.get(value.upper())
            if member is not None:
                return member
        if default is not _MISSING:
            return default
        raise ValueError(f"{value!r} is not a valid {cls.__name__}")


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
