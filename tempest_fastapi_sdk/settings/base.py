"""Base application settings driven by pydantic-settings."""

from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic._internal._model_construction import ModelMetaclass
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
)


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
    * ``hide_input_in_errors=True`` — a validation error never echoes
      the input. On a ``BaseSettings`` the input of a model-level error
      is the whole merged environment: without this flag a missing
      required field prints ``input_value={'JWT_SECRET': ...}`` into
      the boot traceback and the container log, and a malformed value
      (a URL carrying a password) is echoed as is. Override it in your
      own ``model_config`` to get the input back.

    Two extension points live here because every mixin needs them:

    * :meth:`production_violations` — the cooperative hook
      :class:`~tempest_fastapi_sdk.settings.EnvironmentSettings` calls
      when ``ENV=production``. Each mixin overrides it, calls
      ``super()`` and appends the development values it owns.
    * :attr:`DEPRECATED_ENV_ALIASES` — old environment variable names a
      mixin still accepts (through ``validation_alias=AliasChoices``)
      after a rename. :meth:`settings_customise_sources` refuses to
      build when the old and the new name are both set to different
      values, instead of letting one of them win silently.

    Attributes:
        model_config (SettingsConfigDict): The pydantic-settings
            configuration.
        DEPRECATED_ENV_ALIASES (ClassVar[Mapping[str, str]]): Old
            environment variable name mapped to the name that replaced
            it. Declared per class and collected across the whole MRO,
            so a subclass adds entries without repeating the parent's.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=True,
        frozen=True,
        str_strip_whitespace=True,
        from_attributes=True,
        hide_input_in_errors=True,
    )

    DEPRECATED_ENV_ALIASES: ClassVar[Mapping[str, str]] = {}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Keep pydantic-settings' default sources, after a rename check.

        The source order is pydantic-settings' own (init kwargs, then
        the process environment, then the ``.env`` file, then the
        secrets directory). The only addition is
        :func:`reject_conflicting_env_aliases`, run against the
        environment and ``.env`` sources before any field is read.

        A ``Settings`` class that overrides this hook itself replaces
        the check; call
        ``reject_conflicting_env_aliases(settings_cls, (env_settings,
        dotenv_settings))`` from the override to keep it.

        Args:
            settings_cls (type[BaseSettings]): The class being built.
            init_settings (PydanticBaseSettingsSource): Keyword
                arguments passed to the constructor.
            env_settings (PydanticBaseSettingsSource): The process
                environment.
            dotenv_settings (PydanticBaseSettingsSource): The ``.env``
                file.
            file_secret_settings (PydanticBaseSettingsSource): The
                secrets directory.

        Returns:
            tuple[PydanticBaseSettingsSource, ...]: The four sources,
            in pydantic-settings' default priority order.

        Raises:
            SettingsError: When a deprecated environment variable and
                its replacement are both set to different values.
        """
        reject_conflicting_env_aliases(settings_cls, (env_settings, dotenv_settings))
        return init_settings, env_settings, dotenv_settings, file_secret_settings

    def production_violations(self) -> list[str]:
        """List the development-only values this settings object holds.

        :class:`~tempest_fastapi_sdk.settings.EnvironmentSettings` calls
        this when ``ENV=production`` and refuses to build when the list
        is not empty. The base implementation contributes nothing; each
        SDK mixin overrides it **cooperatively** — call ``super()`` first,
        then append — so a composed ``Settings`` collects the checks of
        every mixin it lists. A service adds its own rule the same way,
        and can drop one it has deliberately accepted by filtering the
        list ``super()`` returns.

        Every entry names the field and the reason, never the value: the
        list ends up in a boot error, and the value is often a secret.

        Returns:
            list[str]: One ``"FIELD: reason"`` entry per violation.
        """
        return []


def _deprecated_env_aliases(settings_cls: type[BaseSettings]) -> dict[str, str]:
    """Collect ``DEPRECATED_ENV_ALIASES`` from every class in the MRO.

    Args:
        settings_cls (type[BaseSettings]): The settings class being built.

    Returns:
        dict[str, str]: Old name mapped to its replacement, merged with
        the most derived declaration winning.
    """
    merged: dict[str, str] = {}
    for klass in reversed(settings_cls.__mro__):
        declared: Any = klass.__dict__.get("DEPRECATED_ENV_ALIASES")
        if isinstance(declared, Mapping):
            merged.update(declared)
    return merged


def reject_conflicting_env_aliases(
    settings_cls: type[BaseSettings],
    sources: tuple[PydanticBaseSettingsSource, ...],
) -> None:
    """Refuse a deprecated env var set next to its replacement.

    A renamed field reads both names through
    ``validation_alias=AliasChoices(new, old)``, and ``AliasChoices``
    resolves a clash by taking the first name it finds. Left alone,
    ``STORAGE_SECRET_KEY`` set by the new deploy script and a stale
    ``MINIO_SECRET_KEY`` in the ``.env`` would both look configured
    while only one of them reaches the client. Setting both names to
    the **same** value is accepted, so a deploy can carry the two during
    a migration window.

    Values are compared as the raw strings the sources read, across the
    environment and the ``.env`` file together: the old name in one and
    the new name in the other still counts as a clash.

    Args:
        settings_cls (type[BaseSettings]): The settings class being
            built; its MRO supplies ``DEPRECATED_ENV_ALIASES``.
        sources (tuple[PydanticBaseSettingsSource, ...]): The sources to
            inspect. Only environment-backed ones
            (``EnvSettingsSource`` and its ``.env`` subclass) are read.

    Raises:
        SettingsError: When at least one pair clashes. The message names
            every clashing pair and never includes a value.
    """
    aliases: dict[str, str] = _deprecated_env_aliases(settings_cls)
    if not aliases:
        return
    clashes: list[str] = []
    for old, new in aliases.items():
        old_value: str | None = None
        new_value: str | None = None
        for source in sources:
            if not isinstance(source, EnvSettingsSource):
                continue
            fold: bool = not source.case_sensitive
            env_vars: Mapping[str, str | None] = source.env_vars
            if old_value is None:
                old_value = env_vars.get(old.lower() if fold else old)
            if new_value is None:
                new_value = env_vars.get(new.lower() if fold else new)
        if old_value is not None and new_value is not None and old_value != new_value:
            clashes.append(f"{new} and {old}")
    if clashes:
        raise SettingsError(
            "Conflicting environment variables: "
            + "; ".join(clashes)
            + ". Each pair is one setting under its current and its "
            "deprecated name, set to different values. Remove the "
            "deprecated name (the second of each pair) from the "
            "environment and the .env file."
        )


__all__: list[str] = [
    "AppSettingsMeta",
    "BaseAppSettings",
    "reject_conflicting_env_aliases",
]
