"""Base application settings driven by pydantic-settings."""

from typing import Any

from pydantic._internal._model_construction import ModelMetaclass
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettingsMeta(ModelMetaclass):
    """Metaclass that validates the position of settings bases.

    Every settings mixin the SDK ships subclasses
    :class:`BaseAppSettings`, so C3 linearization forbids
    ``BaseAppSettings`` from preceding a mixin in the base list. Python
    already rejects that, but the message it emits
    (``Cannot create a consistent method resolution order (MRO) for
    bases BaseAppSettings, RedisSettings``) never names the fix, and
    under the pydantic mypy plugin the same line also reports a
    misleading ``[metaclass]`` error. This metaclass pre-checks the base
    ordering and raises an instruction instead.
    """

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> type:
        """Reject a base that any later base already subclasses.

        Args:
            mcs (type[AppSettingsMeta]): The metaclass itself.
            name (str): Name of the class being created.
            bases (tuple[type, ...]): The declared base classes, in
                declaration order.
            namespace (dict[str, Any]): The class body namespace.
            **kwargs (Any): Extra class-creation keyword arguments,
                forwarded to pydantic's ``ModelMetaclass``.

        Returns:
            type: The created class.

        Raises:
            TypeError: When a base is listed before one of its own
                subclasses. The message names both classes and states
                that the general base must move to the end of the list.
                It keeps the phrase ``method resolution order (MRO)`` so
                code (and searches) keyed on Python's own wording still
                match.
        """
        for index, base in enumerate(bases):
            subclass = next(
                (
                    other
                    for other in bases[index + 1 :]
                    if isinstance(other, type)
                    and other is not base
                    and issubclass(other, base)
                ),
                None,
            )
            if subclass is not None:
                raise TypeError(
                    f"{name}: {base.__name__} must be the LAST base — "
                    f"{subclass.__name__} already subclasses it, so listing "
                    f"{base.__name__} before it is an invalid method "
                    f"resolution order (MRO). Move {base.__name__} to the end "
                    f"of the base list: "
                    f"class {name}({subclass.__name__}, {base.__name__})."
                )
        return super().__new__(mcs, name, bases, namespace, **kwargs)


class BaseAppSettings(BaseSettings, metaclass=AppSettingsMeta):
    """Shared configuration for ``Settings`` classes across projects.

    Provides the canonical pydantic-settings config block; concrete
    projects subclass this and add their domain-specific fields
    (database URLs, secrets, third-party keys, etc.).

    Every SDK settings mixin (``DatabaseSettings``, ``RedisSettings``,
    …) subclasses this class, so a composed ``Settings`` must list
    ``BaseAppSettings`` **last**::

        class Settings(DatabaseSettings, RedisSettings, BaseAppSettings):
            ...

    Listing it earlier is an invalid MRO and fails at class creation
    with the actionable message raised by :class:`AppSettingsMeta`.

    The defaults:

    * ``env_file=".env"`` — load environment variables from a local
      ``.env`` file when present.
    * ``extra="ignore"`` — silently drop unexpected env vars instead
      of raising at startup.
    * ``case_sensitive=True`` — env var names are matched exactly.
    * ``frozen=True`` — settings are immutable after construction.
    * ``str_strip_whitespace=True`` — trim accidental whitespace
      around env values.
    * ``from_attributes=True`` — allow building from objects with
      attribute access (rarely needed for settings, but harmless).

    Attributes:
        model_config (SettingsConfigDict): The pydantic-settings
            configuration.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=True,
        frozen=True,
        str_strip_whitespace=True,
        from_attributes=True,
    )


__all__: list[str] = [
    "AppSettingsMeta",
    "BaseAppSettings",
]
