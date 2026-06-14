"""The :class:`FeatureFlags` service over a pluggable backend."""

from __future__ import annotations

from tempest_fastapi_sdk.flags.backends import FeatureFlagBackend


class FeatureFlags:
    """Read and toggle boolean feature flags over a backend.

    Wrap any :class:`FeatureFlagBackend` and branch on flags in services
    or gate routes with
    :func:`tempest_fastapi_sdk.flags.make_flag_dependency`::

        flags = FeatureFlags(RedisFeatureFlagBackend(redis.client))
        if await flags.is_enabled("new_checkout"):
            ...
    """

    def __init__(
        self,
        backend: FeatureFlagBackend,
        *,
        default: bool = False,
    ) -> None:
        """Initialize the service.

        Args:
            backend (FeatureFlagBackend): The storage backend.
            default (bool): Value returned for a flag the backend has
                no entry for (``get`` → ``None``). Defaults to ``False``
                so unknown flags are off.
        """
        self._backend: FeatureFlagBackend = backend
        self._default: bool = default

    async def is_enabled(self, name: str, *, default: bool | None = None) -> bool:
        """Return whether ``name`` is enabled.

        Args:
            name (str): The flag name.
            default (bool | None): Per-call override of the service
                default for an unset flag. ``None`` uses the service
                default.

        Returns:
            bool: The flag value, or the resolved default when unset.
        """
        value = await self._backend.get(name)
        if value is not None:
            return value
        return self._default if default is None else default

    async def enable(self, name: str) -> None:
        """Turn ``name`` on (delegates to the backend)."""
        await self._backend.set(name, True)

    async def disable(self, name: str) -> None:
        """Turn ``name`` off (delegates to the backend)."""
        await self._backend.set(name, False)

    async def set(self, name: str, enabled: bool) -> None:
        """Set ``name`` to ``enabled`` (delegates to the backend)."""
        await self._backend.set(name, enabled)

    async def all(self) -> dict[str, bool]:
        """Return every known flag as a ``{name: enabled}`` mapping."""
        return await self._backend.all()


__all__: list[str] = ["FeatureFlags"]
