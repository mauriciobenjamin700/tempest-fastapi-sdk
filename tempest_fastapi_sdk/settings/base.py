"""Base application settings driven by pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAppSettings(BaseSettings):
    """Shared configuration for ``Settings`` classes across projects.

    Provides the canonical pydantic-settings config block; concrete
    projects subclass this and add their domain-specific fields
    (database URLs, secrets, third-party keys, etc.).

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
    "BaseAppSettings",
]
