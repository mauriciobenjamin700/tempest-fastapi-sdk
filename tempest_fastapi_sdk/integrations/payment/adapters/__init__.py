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
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
        MercadoPagoCardProvider as MercadoPagoCardProvider,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
        MercadoPagoOrderDelivery as MercadoPagoOrderDelivery,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
        MercadoPagoPixProvider as MercadoPagoPixProvider,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
        make_mercado_pago_webhook_delivery_dependency as make_mercado_pago_webhook_delivery_dependency,  # noqa: E501
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_router import (
        DEFAULT_WEBHOOK_PATH as DEFAULT_WEBHOOK_PATH,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_router import (
        DEFAULT_WITHDRAW_PATH as DEFAULT_WITHDRAW_PATH,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_router import (
        MercadoPagoDeliveryHandler as MercadoPagoDeliveryHandler,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_router import (
        MercadoPagoWebhookAckSchema as MercadoPagoWebhookAckSchema,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_router import (
        MercadoPagoWithdrawConfig as MercadoPagoWithdrawConfig,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_router import (
        make_mercado_pago_router as make_mercado_pago_router,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation import (  # noqa: E501
        SIMULATION_TAG as SIMULATION_TAG,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation import (  # noqa: E501
        MercadoPagoSimulatedDeliverySchema as MercadoPagoSimulatedDeliverySchema,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation import (  # noqa: E501
        MercadoPagoSimulationResultSchema as MercadoPagoSimulationResultSchema,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation import (  # noqa: E501
        mercado_pago_provider_for as mercado_pago_provider_for,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation import (  # noqa: E501
        simulation_api_for as simulation_api_for,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
        OpenPixPayoutProvider as OpenPixPayoutProvider,
    )
    from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
        OpenPixPixProvider as OpenPixPixProvider,
    )

_EXPORTS: dict[str, str] = {
    "MercadoPagoCardProvider": "mercado_pago",
    "MercadoPagoOrderDelivery": "mercado_pago",
    "MercadoPagoPixProvider": "mercado_pago",
    "make_mercado_pago_webhook_delivery_dependency": "mercado_pago",
    "DEFAULT_WEBHOOK_PATH": "mercado_pago_router",
    "DEFAULT_WITHDRAW_PATH": "mercado_pago_router",
    "MercadoPagoDeliveryHandler": "mercado_pago_router",
    "MercadoPagoWebhookAckSchema": "mercado_pago_router",
    "MercadoPagoWithdrawConfig": "mercado_pago_router",
    "make_mercado_pago_router": "mercado_pago_router",
    "SIMULATION_TAG": "mercado_pago_simulation",
    "MercadoPagoSimulatedDeliverySchema": "mercado_pago_simulation",
    "MercadoPagoSimulationResultSchema": "mercado_pago_simulation",
    "mercado_pago_provider_for": "mercado_pago_simulation",
    "simulation_api_for": "mercado_pago_simulation",
    "OpenPixPayoutProvider": "openpix",
    "OpenPixPixProvider": "openpix",
    "PAYOUT_REJECTED_STATUSES": "openpix",
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


__all__: list[str] = [
    "DEFAULT_WEBHOOK_PATH",
    "DEFAULT_WITHDRAW_PATH",
    "SIMULATION_TAG",
    "MercadoPagoCardProvider",
    "MercadoPagoDeliveryHandler",
    "MercadoPagoOrderDelivery",
    "MercadoPagoPixProvider",
    "MercadoPagoSimulatedDeliverySchema",
    "MercadoPagoSimulationResultSchema",
    "MercadoPagoWebhookAckSchema",
    "MercadoPagoWithdrawConfig",
    "OpenPixPayoutProvider",
    "OpenPixPixProvider",
    "make_mercado_pago_router",
    "make_mercado_pago_webhook_delivery_dependency",
    "mercado_pago_provider_for",
    "simulation_api_for",
]
