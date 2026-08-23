"""Providers translated into the canonical payment contract.

One module per provider. An adapter knows its provider and the contract in
:mod:`tempest_fastapi_sdk.integrations.payment.base`; the contract knows no
adapter, and each provider's subpackage keeps mirroring only the third
party's API.

Resolution is lazy, for the same reason it is in the provider packages: an
adapter talks to its provider's generated surface, so importing it eagerly
here would make ``import tempest_fastapi_sdk.integrations.payment`` build
every schema of every provider the SDK bundles. Measured, that is 0.5 s and
9 MB per provider that nobody asked for.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
        OpenPixPixProvider as OpenPixPixProvider,
    )

_EXPORTS: dict[str, str] = {
    "OpenPixPixProvider": "openpix",
    "EVENT_MAP": "openpix",
    "PROVIDER_NAME": "openpix",
    "STATUS_MAP": "openpix",
}
"""Public name to the adapter module that defines it."""


def __getattr__(name: str) -> Any:
    """Resolve an adapter, or an adapter module, on first access.

    Args:
        name (str): The attribute being looked up.

    Returns:
        Any: The adapter class or module.

    Raises:
        AttributeError: If no adapter module defines ``name``.
    """
    from importlib import import_module

    module_name = _EXPORTS.get(name)
    if module_name is None:
        if name in set(_EXPORTS.values()):
            return import_module(f"{__name__}.{name}")
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{module_name}"), name)


def __dir__() -> list[str]:
    """List the adapters this package exposes.

    Returns:
        list[str]: Public names plus the adapter module names.
    """
    return sorted(set(_EXPORTS) | set(_EXPORTS.values()))


__all__: list[str] = ["OpenPixPixProvider"]
