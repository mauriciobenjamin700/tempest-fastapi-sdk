"""Shared enum base classes for string- and integer-valued enums.

Both bases mix in a value type (:class:`str` or :class:`int`) so that
members are genuine instances of that type: they compare equal to their
raw value (``Member == "VALUE"`` / ``Member == 1``), serialize cleanly
outside Pydantic, and bind directly to ``String``/``Integer`` database
columns as their value.

The introspection helpers (``values``/``keys``/``choices``/``to_dict``)
and the lenient constructor (``from_value``/``has_value``/``has_key``)
are shared between both bases via :class:`_EnumHelpers` so there is a
single implementation regardless of the underlying value type. That mixin
also renders members as their value under ``str()`` and f-strings, so a
member interpolated into a log line, a query string or a raw column value
never leaks ``"Class.MEMBER"``.
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

    Besides the introspection helpers, this mixin normalizes text
    conversion: ``__str__`` and ``__format__`` render the member's
    **value**, never ``"Class.MEMBER"``. Sitting first in the MRO — ahead
    of the mixed-in value type and of ``Enum`` — makes this the single
    implementation both bases inherit.
    """

    def __str__(self) -> str:
        """Render the member as its value.

        ``Enum`` would otherwise return ``"Class.MEMBER"`` here, the classic
        mixin footgun: a member interpolated into an f-string, a log line, a
        query string or a raw column value silently becomes
        ``"OrderStatus.PAID"`` instead of ``"paid"``. Rendering the value
        matches :class:`enum.StrEnum` and makes ``str(member)`` a safe,
        explicit way to reach the stored representation.

        Returns:
            str: The member's value as text — the value itself for a
            :class:`BaseStrEnum`, its decimal form for a
            :class:`BaseIntEnum`.
        """
        return str(self.value)  # type: ignore[attr-defined]

    def __format__(self, format_spec: str) -> str:
        """Format the member's value under ``format_spec``.

        Overriding ``__str__`` alone is not enough: f-strings and
        :func:`format` go through ``Enum.__format__``, which ignores it.
        Delegating to the value's own ``__format__`` also keeps numeric
        specs working on :class:`BaseIntEnum` (``f"{Priority.HIGH:03d}"``
        -> ``"002"``).

        Args:
            format_spec (str): The standard format specification.

        Returns:
            str: The formatted value.
        """
        return format(self.value, format_spec)  # type: ignore[attr-defined]

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

    Mixing in ``str`` makes every member a genuine string instance, so
    members compare equal to their values (``Member == "VALUE"``),
    serialize cleanly outside Pydantic, and bind directly to ``String``
    database columns as their value.

    Note:
        Deliberately a ``str`` + ``Enum`` mixin rather than
        :class:`enum.StrEnum`, which is only available from Python 3.11 and
        would not carry the shared helpers. Text conversion, however,
        matches ``StrEnum``: ``str(member)`` and ``f"{member}"`` both render
        the bare value (``"paid"``), never ``"OrderStatus.PAID"`` — see
        :meth:`_EnumHelpers.__str__`.
    """


class BaseIntEnum(_EnumHelpers, int, Enum):
    """Base class for integer-valued enums.

    Mixing in ``int`` makes every member a genuine integer instance, so
    members compare equal to their values (``Member == 1``), serialize
    cleanly outside Pydantic, and bind directly to ``Integer`` database
    columns as their value.

    Note:
        As with :class:`BaseStrEnum`, ``str(member)`` and ``f"{member}"``
        render the value (``"2"``), not ``"Priority.HIGH"``, and numeric
        format specs keep working (``f"{Priority.HIGH:03d}"`` -> ``"002"``).
    """


class Locale(BaseStrEnum):
    """BCP-47 locale tags (``language-REGION``) as a string enum.

    A canonical, dependency-free list of the locales apps commonly target,
    so a service never hard-codes a bare ``"pt-BR"`` string again. Each
    member's value is the BCP-47 tag itself, so members compare equal to and
    bind to a ``String`` column as that tag (e.g. ``Locale.PT_BR == "pt-BR"``).

    Pair it with :class:`tempest_fastapi_sdk.LocaleColumnMixin` to add a
    ``locale`` column to a model, and with
    :class:`tempest_fastapi_sdk.MessageCatalog` to resolve localized text.

    Note:
        This is a pragmatic, extensible set of widely used locales — not an
        exhaustive registry of every BCP-47 tag. A project needing a tag not
        listed here can store the raw string (the column is a plain ``str``);
        add the member upstream when it becomes common.
    """

    PT_BR = "pt-BR"
    PT_PT = "pt-PT"
    EN_US = "en-US"
    EN_GB = "en-GB"
    ES_ES = "es-ES"
    ES_MX = "es-MX"
    ES_AR = "es-AR"
    FR_FR = "fr-FR"
    FR_CA = "fr-CA"
    DE_DE = "de-DE"
    IT_IT = "it-IT"
    NL_NL = "nl-NL"
    RU_RU = "ru-RU"
    UK_UA = "uk-UA"
    PL_PL = "pl-PL"
    CS_CZ = "cs-CZ"
    RO_RO = "ro-RO"
    EL_GR = "el-GR"
    SV_SE = "sv-SE"
    NB_NO = "nb-NO"
    DA_DK = "da-DK"
    FI_FI = "fi-FI"
    HU_HU = "hu-HU"
    TR_TR = "tr-TR"
    AR_SA = "ar-SA"
    HE_IL = "he-IL"
    HI_IN = "hi-IN"
    ZH_CN = "zh-CN"
    ZH_TW = "zh-TW"
    JA_JP = "ja-JP"
    KO_KR = "ko-KR"
    TH_TH = "th-TH"
    VI_VN = "vi-VN"
    ID_ID = "id-ID"
    MS_MY = "ms-MY"


def normalize_locale_tag(value: str | Locale) -> Locale:
    """Coerce a loose locale string into a :class:`Locale` member.

    Accepts any case and either separator — ``"pt_BR"``, ``"PT-BR"``,
    ``"pt-br"`` all resolve to :attr:`Locale.PT_BR` — and the bare primary
    subtag (``"pt"`` -> ``PT_BR``, ``"en"`` -> ``EN_US``; the first member
    declared for that subtag wins). This is the normalizer behind
    :data:`tempest_fastapi_sdk.LocaleField`, mirroring ``normalize_uf`` for
    ``UFField``.

    Args:
        value (str | Locale): The raw locale value; a :class:`Locale` is
            returned unchanged.

    Returns:
        Locale: The matching member.

    Raises:
        ValueError: If ``value`` matches no supported locale.
    """
    if isinstance(value, Locale):
        return value
    key = str(value).strip().replace("_", "-").lower()
    for locale in Locale:
        if locale.value.lower() == key:
            return locale
    primary = key.split("-", 1)[0]
    for locale in Locale:
        if locale.value.split("-", 1)[0].lower() == primary:
            return locale
    raise ValueError(f"invalid locale {value!r}")


__all__: list[str] = [
    "BaseIntEnum",
    "BaseStrEnum",
    "Locale",
    "normalize_locale_tag",
]
