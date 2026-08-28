"""The whole Mercado Pago surface, ready to import.

``pip install tempest-fastapi-sdk`` and you have the Mercado Pago schemas
and all 143 operations — Checkout Pro preferences, payments, orders,
subscriptions, Point, chargebacks — plus the pieces the specification does
not describe.

.. code-block:: python

    from tempest_fastapi_sdk import HTTPClient
    from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
        DEFAULT_BASE_URL,
        MercadoPagoClient,
        PreferenceRequest,
    )

    http: HTTPClient = HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": "Bearer <your access token>"},
    )
    client: MercadoPagoClient = MercadoPagoClient(http)

The generated half is **checked in, not written by hand**:
``scripts/regen_mercado_pago.py`` produces it from the pinned specification
in ``vendor/mercadopago-openapi.yaml``, and a drift test fails if the files
on disk differ from what that script produces.

Two halves, and it is worth knowing which is which:

- **Generated** — ``MercadoPagoClient`` and the schema classes. Whatever
  Mercado Pago's own OpenAPI says, verbatim. Unlike OpenPix, 142 of the 143
  operations carry an ``operationId``, so the method names are the
  provider's, not ours.
- **Hand-written** — ``DEFAULT_BASE_URL`` (the spec declares a single
  server: what separates test from production is the token, not the host),
  ``MercadoPagoEvent``, the webhook verification, the money helpers (the
  spec types money as ``number`` and states it in **reais**), and the Pix QR
  the spec never declares on a payment.

!!! warning "Money here is reais, not cents"
    The mirror image of OpenPix, which states cents inside a float. Use
    :func:`to_cents` / :func:`from_cents` at the boundary and keep integers
    inside your own code — mixing the two units up is a factor-of-100 error
    in the direction nobody notices until a customer is charged 100x.

!!! warning "The Pix QR is not on the generated ``Payment``"
    The specification never declares ``point_of_interaction`` on a payment,
    and ``BaseSchema`` is ``extra="ignore"`` — so the QR the API returns is
    dropped during validation, silently. Use :func:`create_pix_payment` /
    :func:`get_pix_payment`, or :func:`parse_pix_payment` over a body you
    already have. Details in
    :mod:`~tempest_fastapi_sdk.integrations.payment.mercado_pago.pix`.

!!! danger "The webhook signature is ported, not yet seen live"
    The vendored specification does not describe it, so the algorithm comes
    from Mercado Pago's own validator (``mercadopago/sdk-nodejs``, commit
    ``99857f33``) and every rule is pinned by a test. What remains unmeasured
    is a real delivery. See
    :mod:`~tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks`
    for what is verified and what is still assumed — and note that QR Code
    notifications are not signed at all.

!!! note "The schemas load on first use, not on import"
    Building the models is the expensive part, and importing this package
    for ``to_cents`` alone should not pay it, so the generated modules are
    resolved lazily through :pep:`562`. Measured: the generated schemas cost
    0.76 s and 107 MB of RSS on first access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.integrations.payment.mercado_pago.environment import (
    DEFAULT_BASE_URL as DEFAULT_BASE_URL,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.events import (
    MercadoPagoEvent as MercadoPagoEvent,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.money import (
    format_amount as format_amount,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.money import (
    from_cents as from_cents,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.money import (
    to_cents as to_cents,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.pix import (
    PAYMENTS_PATH as PAYMENTS_PATH,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.pix import (
    PixPayment as PixPayment,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.pix import (
    PixPointOfInteraction as PixPointOfInteraction,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.pix import (
    PixTransactionData as PixTransactionData,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.pix import (
    create_pix_payment as create_pix_payment,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.pix import (
    get_pix_payment as get_pix_payment,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.pix import (
    parse_pix_payment as parse_pix_payment,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    DEFAULT_SIGNATURE_VERSIONS as DEFAULT_SIGNATURE_VERSIONS,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    MERCADO_PAGO_REQUEST_ID_HEADER as MERCADO_PAGO_REQUEST_ID_HEADER,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    MERCADO_PAGO_SIGNATURE_HEADER as MERCADO_PAGO_SIGNATURE_HEADER,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    MercadoPagoWebhookEvent as MercadoPagoWebhookEvent,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    SignatureHeader as SignatureHeader,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    build_manifest as build_manifest,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    parse_signature_header as parse_signature_header,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    sign_manifest as sign_manifest,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    verify_signature as verify_signature,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.integrations.payment.mercado_pago.client import *  # noqa: F403
    from tempest_fastapi_sdk.integrations.payment.mercado_pago.schemas import *  # noqa: F403

_HAND_WRITTEN: tuple[str, ...] = (
    "DEFAULT_BASE_URL",
    "DEFAULT_SIGNATURE_VERSIONS",
    "MERCADO_PAGO_REQUEST_ID_HEADER",
    "MERCADO_PAGO_SIGNATURE_HEADER",
    "PAYMENTS_PATH",
    "MercadoPagoEvent",
    "MercadoPagoWebhookEvent",
    "PixPayment",
    "PixPointOfInteraction",
    "PixTransactionData",
    "SignatureHeader",
    "build_manifest",
    "create_pix_payment",
    "format_amount",
    "from_cents",
    "get_pix_payment",
    "parse_pix_payment",
    "parse_signature_header",
    "sign_manifest",
    "to_cents",
    "verify_signature",
)
"""Names this package defines itself, always eagerly available."""


_GENERATED_MODULES: tuple[str, ...] = ("schemas", "client")
"""Submodules holding the generated code, in dependency order."""


def _generated_names() -> dict[str, str]:
    """Map every generated name to the submodule that defines it.

    Returns:
        dict[str, str]: ``{name: submodule}``, built by importing the
        generated modules. Called only from :func:`__getattr__` and
        :func:`__dir__`, so importing this package does not trigger it.

    Imports go through :func:`importlib.import_module` rather than
    ``from . import schemas``. The ``from`` form asks the **package** for
    the attribute, which lands back in :func:`__getattr__`, which calls
    this — unbounded recursion, and the traceback blames the last frame
    rather than the loop.
    """
    from importlib import import_module

    mapping: dict[str, str] = {}
    for module_name in _GENERATED_MODULES:
        module = import_module(f"{__name__}.{module_name}")
        mapping.update(dict.fromkeys(module.__all__, module_name))
    return mapping


def __getattr__(name: str) -> Any:
    """Resolve a generated name, or a generated submodule, on first access.

    Args:
        name (str): The attribute being looked up.

    Returns:
        Any: The generated class, client, constant or submodule.

    Raises:
        AttributeError: If no generated module defines ``name``.

    The 332 schema classes are resolved through this hook rather than
    imported at module level: the list is regenerated from the
    specification, and importing them eagerly would build every model on
    ``import``. The cost is that a **typo** also loads the generated
    modules before failing — paid once, on an access that was going to
    raise anyway.
    """
    from importlib import import_module

    if name in _GENERATED_MODULES:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module

    module_name = _generated_names().get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List everything importable from this package.

    Returns:
        list[str]: Hand-written names plus every generated one, sorted, so
        autocompletion and ``help()`` see the whole surface.

    This does load the generated modules. That is the right trade: someone
    running ``dir()`` is exploring the API, which is exactly when the full
    list is worth the wait.
    """
    return sorted({*_HAND_WRITTEN, *_generated_names()})


__all__: list[str] = [
    "DEFAULT_BASE_URL",
    "DEFAULT_SIGNATURE_VERSIONS",
    "MERCADO_PAGO_REQUEST_ID_HEADER",
    "MERCADO_PAGO_SIGNATURE_HEADER",
    "PAYMENTS_PATH",
    "AddOrderTransactionBody",
    "AddOrderTransactionResponse",
    "Address",
    "AttachClaimFileResponse",
    "AuthorizedPayment",
    "AuthorizedPaymentPayment",
    "AuthorizedPaymentSearchResult",
    "AuthorizedPaymentStatus",
    "AutoRecurring",
    "AutoRecurringFreeTrial",
    "AutoRecurringFrequencyType",
    "BankAccount",
    "BankAccountAccountType",
    "CancelPaymentBody",
    "CaptureOrderResponse",
    "Card",
    "CardCardholder",
    "CardDetails",
    "CardIssuer",
    "CardPaymentMethod",
    "CardSecurityCode",
    "CardSecurityCodeMode",
    "CardToken",
    "CardTokenCardholder",
    "CardTokenRequest",
    "CardTokenRequestCardholder",
    "CardTokenStatus",
    "Claim",
    "ClaimEvidence",
    "ClaimEvidenceType",
    "ClaimHistoryEntry",
    "ClaimHistoryEntryChangedBy",
    "ClaimMessage",
    "ClaimMessageAttachmentsItem",
    "ClaimMessageFrom",
    "ClaimMessageFromRole",
    "ClaimPlayersItem",
    "ClaimPlayersItemRole",
    "ClaimReason",
    "ClaimResource",
    "ClaimSearchResult",
    "ClaimStage",
    "ClaimStatus",
    "ClaimType",
    "ConfirmCashoutQrBody",
    "ConfirmCashoutQrBodyStatus",
    "CreateAdvancedPaymentBody",
    "CreateAdvancedPaymentBodyPayer",
    "CreateAdvancedPaymentBodyWalletPayment",
    "CreateAdvancedPaymentResponse",
    "CreateAdvancedPaymentResponsePayer",
    "CreateAdvancedPaymentResponseWalletPayment",
    "CreateMerchantOrderBody",
    "CreateMerchantOrderBodyPayer",
    "CreateMerchantOrderBodySiteId",
    "CreateMerchantOrderResponse",
    "CreateMerchantOrderResponseCollector",
    "CreateMerchantOrderResponseOrderStatus",
    "CreateMerchantOrderResponsePayer",
    "CreateOAuthTokenBody",
    "CreateOAuthTokenBodyGrantType",
    "CreateOAuthTokenResponse",
    "CreatePayoutBody",
    "CreatePayoutBodyTransactionsItem",
    "CreatePointPaymentIntentBody",
    "CreatePointPaymentIntentBodyPayment",
    "CreatePointPaymentIntentBodyPaymentType",
    "CreatePointPaymentIntentResponse",
    "CreatePointPaymentIntentResponseStatus",
    "CreatePointRefundIntentBody",
    "CreatePointRefundIntentResponse",
    "CreateQrIntegratorConfigBody",
    "CreateRefundResponse",
    "CreateRefundResponseSource",
    "CreateStoreBody",
    "CreateTerminalActionBody",
    "CreateTerminalActionBodyConfig",
    "CreateTerminalActionBodyContent",
    "CreateTerminalActionBodyType",
    "CreateTerminalActionResponse",
    "CreateTerminalActionResponseStatus",
    "CreateWalletAgreementBody",
    "CreateWalletAgreementBodyAgreementData",
    "CreateWalletAgreementBodyExternalUser",
    "CreateWalletAgreementResponse",
    "CreateWalletDiscountBody",
    "CreateWalletDiscountResponse",
    "CreateWalletDiscountResponseDiscount",
    "CreateWalletPayerTokenBody",
    "CreateWalletPayerTokenResponse",
    "CurrencyId",
    "Customer",
    "CustomerCreateRequest",
    "CustomerRequest",
    "CustomerResponse",
    "CustomerResponseStatus",
    "CustomerSearchResult",
    "DigitalWallet",
    "DigitalWalletProvider",
    "Error",
    "ErrorCause",
    "ExportSubscriptionsSort",
    "GetAdvancedPaymentResponse",
    "GetAdvancedPaymentResponsePayer",
    "GetAdvancedPaymentResponseWalletPayment",
    "GetChargebackResponse",
    "GetChargebackResponseDocumentationStatus",
    "GetClaimFileResponse",
    "GetInstallmentsResponseItem",
    "GetInstallmentsResponseItemIssuer",
    "GetInstallmentsResponseItemPayerCostsItem",
    "GetMerchantOrderResponse",
    "GetMerchantOrderResponseCollector",
    "GetMerchantOrderResponsePayer",
    "GetPointRefundIntentResponse",
    "GetRefundResponse",
    "GetRefundResponseSource",
    "GetTerminalActionResponse",
    "GetWalletAgreementResponse",
    "GetWalletAgreementResponseAgreementData",
    "GetWalletAgreementResponseExternalUser",
    "GetWalletAgreementResponseSiteId",
    "GetWalletAgreementResponseStatus",
    "Identification",
    "ListIdentificationTypesResponseItem",
    "ListIdentificationTypesResponseItemType",
    "ListPaymentMethodsResponseItem",
    "ListPaymentMethodsResponseItemDeferredCapture",
    "ListPaymentMethodsResponseItemFinancialInstitutionsItem",
    "ListPaymentMethodsResponseItemStatus",
    "ListPointDevicesResponse",
    "ListPointDevicesResponseDevicesItem",
    "ListPointDevicesResponseDevicesItemOperatingMode",
    "ListRefundsResponse",
    "ListRefundsResponseSource",
    "ListTerminalsResponse",
    "ListTerminalsResponseTerminalsItem",
    "MediationResolution",
    "MediationResolutionType",
    "MercadoPagoClient",
    "MercadoPagoEvent",
    "MercadoPagoWebhookEvent",
    "MerchantAnalyticsResponse",
    "MerchantAnalyticsResponsePeriod",
    "MerchantAnalyticsResponseTopCustomersItem",
    "MerchantCreateRequest",
    "MerchantListResponse",
    "MerchantOrder",
    "MerchantOrderPaymentsItem",
    "MerchantOrderStatus",
    "MerchantResponse",
    "MerchantResponseStatus",
    "MerchantUpdateRequest",
    "Money",
    "Order",
    "OrderConfig",
    "OrderConfigOnline",
    "OrderConfigOnlineTransactionSecurity",
    "OrderConfigOnlineTransactionSecurityLiabilityShift",
    "OrderConfigOnlineTransactionSecurityValidation",
    "OrderItem",
    "OrderPayer",
    "OrderPayerAddress",
    "OrderPayerEntityType",
    "OrderPayerIdentification",
    "OrderPayerPhone",
    "OrderPayment",
    "OrderPaymentMethod",
    "OrderPaymentMethodType",
    "OrderRefundRequest",
    "OrderRefundRequestTransactionsItem",
    "OrderRequest",
    "OrderRequestAdditionalInfo",
    "OrderRequestAdditionalInfoPayer",
    "OrderRequestCaptureMode",
    "OrderRequestIntegrationData",
    "OrderRequestProcessingMode",
    "OrderRequestType",
    "OrderSearchResult",
    "OrderSearchResultPaging",
    "OrderShipment",
    "OrderShipmentAddress",
    "OrderStatus",
    "OrderStatusDetail",
    "OrderTransactionPayment",
    "OrderTransactionPaymentPaymentMethod",
    "OrderTransactionPaymentPaymentMethodTransactionSecurity",
    "OrderTransactionPaymentPaymentMethodTransactionSecurity2",
    "OrderTransactionPaymentStatus",
    "OrderTransactionPaymentStatusDetail",
    "OrderTransactions",
    "OrderTransactions2",
    "Pagination",
    "Payer",
    "PayerType",
    "Payment",
    "PaymentAdditionalInfo",
    "PaymentAdditionalInfoPayer",
    "PaymentAdditionalInfoShipments",
    "PaymentAnalyticsResponse",
    "PaymentAnalyticsResponsePeriod",
    "PaymentCard",
    "PaymentCardCardholder",
    "PaymentFees",
    "PaymentItem",
    "PaymentListResponse",
    "PaymentMethod",
    "PaymentMethodListResponse",
    "PaymentMethodStoreRequest",
    "PaymentMethodType",
    "PaymentOperationType",
    "PaymentPayer",
    "PaymentPayer2",
    "PaymentPaymentTypeId",
    "PaymentProcessingMode",
    "PaymentRequest",
    "PaymentResponse",
    "PaymentResponseStatus",
    "PaymentSearchResult",
    "PaymentStatus",
    "PaymentTransactionDetails",
    "PaymentUpdateRequest",
    "PaymentUpdateRequestStatus",
    "Phone",
    "PixPayment",
    "PixPointOfInteraction",
    "PixTransactionData",
    "Pos",
    "PosRequest",
    "Preference",
    "PreferenceBackUrls",
    "PreferenceItem",
    "PreferencePayer",
    "PreferencePaymentMethods",
    "PreferencePaymentMethodsExcludedPaymentMethodsItem",
    "PreferencePaymentMethodsExcludedPaymentTypesItem",
    "PreferenceRequest",
    "PreferenceRequestAutoReturn",
    "PreferenceRequestDifferentialPricing",
    "PreferenceShipments",
    "PreferenceShipmentsMode",
    "ProcessTransactionIntentBody",
    "ProcessTransactionIntentBodyPointOfInteraction",
    "ProcessTransactionIntentBodyTransaction",
    "Refund",
    "RefundListResponse",
    "RefundOrderResponse",
    "RefundRefundMode",
    "RefundRequest",
    "RefundResponse",
    "RefundResponseStatus",
    "RefundSource",
    "RefundStatus",
    "ReportConfig",
    "ReportConfigColumnsItem",
    "ReportConfigFrequency",
    "ReportConfigFrequencyType",
    "ReportConfigSeparator",
    "ReportConfigSftpInfo",
    "ReportEntry",
    "ReportEntryStatus",
    "ReportListResult",
    "ReportRequest",
    "ReportTask",
    "ReportTaskStatus",
    "SaveCardRequest",
    "SearchMerchantOrdersResponse",
    "SearchMerchantOrdersResponseCollector",
    "SearchMerchantOrdersResponsePayer",
    "SearchPaymentsRange",
    "SearchPosResponse",
    "SearchPreferencesResponse",
    "SearchStoresResponse",
    "SearchSubscriptionPlansCriteria",
    "SendMessageRequest",
    "SignatureHeader",
    "Store",
    "StoreBusinessHours",
    "StoreBusinessHoursMondayItem",
    "StoreLocation",
    "StoreRequest",
    "StoreRequestBusinessHours",
    "StoreRequestBusinessHoursMondayItem",
    "StoreRequestLocation",
    "StoredPaymentMethodResponse",
    "Subscription",
    "SubscriptionCreateRequest",
    "SubscriptionPlan",
    "SubscriptionPlanRequest",
    "SubscriptionPlanRequestPaymentMethodsAllowed",
    "SubscriptionPlanRequestPaymentMethodsAllowedPaymentMeth",
    "SubscriptionPlanRequestPaymentMethodsAllowedPaymentType",
    "SubscriptionPlanStatus",
    "SubscriptionRequest",
    "SubscriptionRequestStatus",
    "SubscriptionResponse",
    "SubscriptionResponseFrequency",
    "SubscriptionResponseStatus",
    "SubscriptionSearchResult",
    "SubscriptionSummarized",
    "SubscriptionUpdateRequest",
    "UpdateAdvancedPaymentBody",
    "UpdateAdvancedPaymentBodyWalletPayment",
    "UpdateAdvancedPaymentResponse",
    "UpdateAdvancedPaymentResponsePayer",
    "UpdateAdvancedPaymentResponseWalletPayment",
    "UpdateCardRequest",
    "UpdateCardRequestCardholder",
    "UpdateChargebackBody",
    "UpdateChargebackBodyFilesItem",
    "UpdateMerchantOrderBody",
    "UpdateMerchantOrderBodyPayer",
    "UpdateOrderTransactionBody",
    "UpdateTerminalOperationModeBody",
    "UpdateTerminalOperationModeBodyTerminalsItem",
    "UploadShippingEvidenceBody",
    "UploadShippingEvidenceBodyType",
    "ValidateWalletCouponBody",
    "ValidateWalletCouponResponse",
    "ValidateWalletCouponResponseStatus",
    "ValidationResponse",
    "ValidationResponseErrorsItem",
    "WebhookEvent",
    "WebhookEventEventType",
    "WebhookEventListResponse",
    "WebhookNotification",
    "WebhookNotificationAction",
    "WebhookNotificationData",
    "WebhookNotificationType",
    "WebhookSignatureHeader",
    "build_manifest",
    "create_pix_payment",
    "format_amount",
    "from_cents",
    "get_pix_payment",
    "parse_pix_payment",
    "parse_signature_header",
    "sign_manifest",
    "to_cents",
    "verify_signature",
]
"""Every public name, generated and hand-written.

Rewritten by ``scripts/regen_mercado_pago.py``. A wildcard under
``TYPE_CHECKING`` makes the generated symbols visible to a type-checker but
does **not** mark them re-exported, so listing them here is what lets a
consumer import them under basedpyright and Pylance strict.
"""
