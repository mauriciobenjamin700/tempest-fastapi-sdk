"""The whole OpenPix surface, ready to import.

`pip install tempest-fastapi-sdk` and you have all 373 OpenPix schemas and
all 105 operations, plus the four things the specification does not say.
Nobody runs a generator, and nobody re-derives by hand which header carries
the webhook signature.

.. code-block:: python

    from tempest_fastapi_sdk import HTTPClient
    from tempest_fastapi_sdk.integrations.payment.openpix import (
        Charge,
        OpenPixClient,
        OpenPixEnvironment,
        to_cents,
    )

    http: HTTPClient = HTTPClient(
        base_url=OpenPixEnvironment.SANDBOX.base_url,
        default_headers={"Authorization": "<your AppID>"},
    )
    client: OpenPixClient = OpenPixClient(http)

The generated half is **checked in, not written by hand**:
``scripts/regen_openpix.py`` produces it from the pinned specification in
``vendor/openpix-openapi.yaml``, and a test fails if the files on disk drift
from what that script produces. Editing them directly is how checked-in
generated code rots.

Two halves, and it is worth knowing which is which:

- **Generated** — ``OpenPixClient``, ``DEFAULT_BASE_URL`` and the 373
  schema classes. Whatever the specification says, verbatim.
- **Hand-written** — ``OpenPixEnvironment`` (the spec's two hosts are
  different domains), ``OpenPixEvent`` (28 webhook events), the webhook
  verification, and the money helpers, because the spec says *"Value in
  cents"* and then types the field ``number``.

!!! note "The schemas load on first use, not on import"
    Building 373 Pydantic models is the expensive part. Importing this package
    for ``to_cents`` alone should not pay it, so the generated modules are
    resolved lazily through :pep:`562`.

    Measured on Python 3.11 with ``tempest_fastapi_sdk`` already imported:
    ~11 ms to import this subpackage, ~150 ms on the first access to a
    generated name, ~0.02 ms after that. Machine-dependent numbers; the ratio
    is the point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.integrations.payment.openpix.environment import (
    OpenPixEnvironment as OpenPixEnvironment,
)
from tempest_fastapi_sdk.integrations.payment.openpix.events import (
    OpenPixEvent as OpenPixEvent,
)
from tempest_fastapi_sdk.integrations.payment.openpix.money import (
    cents_to_reais as cents_to_reais,
)
from tempest_fastapi_sdk.integrations.payment.openpix.money import (
    reais_to_cents as reais_to_cents,
)
from tempest_fastapi_sdk.integrations.payment.openpix.money import to_cents as to_cents
from tempest_fastapi_sdk.integrations.payment.openpix.webhooks import (
    OPENPIX_WEBHOOK_PUBLIC_KEY as OPENPIX_WEBHOOK_PUBLIC_KEY,
)
from tempest_fastapi_sdk.integrations.payment.openpix.webhooks import (
    OPENPIX_WEBHOOK_SIGNATURE_HEADER as OPENPIX_WEBHOOK_SIGNATURE_HEADER,
)
from tempest_fastapi_sdk.integrations.payment.openpix.webhooks import (
    OpenPixWebhookEvent as OpenPixWebhookEvent,
)
from tempest_fastapi_sdk.integrations.payment.openpix.webhooks import (
    decode_public_key as decode_public_key,
)
from tempest_fastapi_sdk.integrations.payment.openpix.webhooks import (
    make_openpix_webhook_dependency as make_openpix_webhook_dependency,
)
from tempest_fastapi_sdk.integrations.payment.openpix.webhooks import (
    webhook_verifier as webhook_verifier,
)

if TYPE_CHECKING:  # pragma: no cover - import-time cost is the point
    from tempest_fastapi_sdk.integrations.payment.openpix.client import (
        DEFAULT_BASE_URL as DEFAULT_BASE_URL,
    )
    from tempest_fastapi_sdk.integrations.payment.openpix.client import (
        OpenPixClient as OpenPixClient,
    )
    from tempest_fastapi_sdk.integrations.payment.openpix.schemas import *  # noqa: F403

_HAND_WRITTEN: tuple[str, ...] = (
    "OPENPIX_WEBHOOK_PUBLIC_KEY",
    "OPENPIX_WEBHOOK_SIGNATURE_HEADER",
    "OpenPixEnvironment",
    "OpenPixEvent",
    "OpenPixWebhookEvent",
    "cents_to_reais",
    "decode_public_key",
    "make_openpix_webhook_dependency",
    "reais_to_cents",
    "to_cents",
    "webhook_verifier",
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

    The 373 schema classes are resolved through this hook rather than
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
    "OPENPIX_WEBHOOK_PUBLIC_KEY",
    "OPENPIX_WEBHOOK_SIGNATURE_HEADER",
    "AccountLimit",
    "AccountObjectPayload",
    "AccountRegister",
    "AccountRegisterPayload",
    "AccountRegisterResponse",
    "AccountRegisterResponseTaxId",
    "AccountRegisterTaxId",
    "Application",
    "ApplicationDeletePayload",
    "ApplicationEnumTypePayload",
    "ApplicationPayload",
    "ApplicationPayloadApplication",
    "ApplicationPayloadApplicationType",
    "ApplicationType",
    "BoletoValidateError",
    "BoletoValidateRequest",
    "BoletoValidateResponse",
    "BoletoValidatedInfo",
    "BoletoValidatedInfoFinalBeneficiary",
    "BoletoValidatedInfoIssuingEntity",
    "Charge",
    "ChargeAdditionalInfoItem",
    "ChargePatchPayload",
    "ChargePayload",
    "ChargePayloadAdditionalInfoItem",
    "ChargePayloadDiscountSettings",
    "ChargePayloadDiscountSettingsDiscountFixedDateItem",
    "ChargePayloadDiscountSettingsModality",
    "ChargePayloadFines",
    "ChargePayloadInterests",
    "ChargePayloadInterestsType",
    "ChargePayloadSplitsItem",
    "ChargePayloadSplitsItemSplitType",
    "ChargePaymentMethods",
    "ChargePaymentMethodsPix",
    "ChargePaymentMethodsPixAdditionalInfoItem",
    "ChargeRefund",
    "ChargeRefundPayload",
    "ChargeRefundStatus",
    "ChargeStatus",
    "ChargeType",
    "Company",
    "CompanyBankAccount",
    "CompanyBankAccountBalance",
    "CompanyObjectPayload",
    "CompanyResponse",
    "Customer",
    "CustomerAddress",
    "CustomerPatchPayload",
    "CustomerPatchPayloadAddress",
    "CustomerPayload",
    "CustomerPayloadAddress",
    "CustomerTaxId",
    "DeleteApiV1AccountByAccountIdResponse",
    "DeleteApiV1AccountRegisterByIdResponse",
    "DeleteApiV1ApplicationResponse",
    "DeleteApiV1ChargeByIdResponse",
    "DeleteApiV1QrcodeStaticByIdResponse",
    "DeleteApiV1SubaccountByIdResponse",
    "DeleteApiV1WebhookByIdResponse",
    "Dispute",
    "DisputePayload",
    "DisputePayloadStatus",
    "DisputeStatus",
    "Error",
    "ErrorResponse",
    "FraudMarkers",
    "FundsRecovery",
    "FundsRecoveryDirection",
    "FundsRecoveryEventsItem",
    "FundsRecoveryPayload",
    "FundsRecoverySituationType",
    "FundsRecoveryStatus",
    "GetApiImageQrcodeBase64ByIdResponse",
    "GetApiV1AccountByAccountIdResponse",
    "GetApiV1AccountRegisterResponse",
    "GetApiV1AccountRegisterResponseTaxId",
    "GetApiV1AccountResponse",
    "GetApiV1AccountResponsePageInfo",
    "GetApiV1AccountResponsePageInfoErrorsItem",
    "GetApiV1AccountResponsePageInfoErrorsItemData",
    "GetApiV1CashbackFidelityBalanceByTaxIdResponse",
    "GetApiV1ChargeByIdRefundResponse",
    "GetApiV1ChargeByIdResponse",
    "GetApiV1ChargeResponse",
    "GetApiV1ChargeResponsePageInfo",
    "GetApiV1ChargeResponsePageInfoErrorsItem",
    "GetApiV1ChargeResponsePageInfoErrorsItemData",
    "GetApiV1CompanyResponse",
    "GetApiV1CompanyResponseCompany",
    "GetApiV1CustomerByIdResponse",
    "GetApiV1CustomerResponse",
    "GetApiV1CustomerResponsePageInfo",
    "GetApiV1CustomerResponsePageInfoErrorsItem",
    "GetApiV1CustomerResponsePageInfoErrorsItemData",
    "GetApiV1DisputeByIdResponse",
    "GetApiV1DisputeByIdResponseDispute",
    "GetApiV1DisputeByIdResponseDisputeStatus",
    "GetApiV1DisputeByIdResponseDisputeType",
    "GetApiV1DisputeResponse",
    "GetApiV1DisputeResponseDisputesItem",
    "GetApiV1DisputeResponseDisputesItemType",
    "GetApiV1DisputeResponsePageInfo",
    "GetApiV1DisputeResponsePageInfoErrorsItem",
    "GetApiV1DisputeResponsePageInfoErrorsItemData",
    "GetApiV1InstallmentsByIdResponse",
    "GetApiV1LimitsByAccountIdResponse",
    "GetApiV1PartnerAffiliateResponse",
    "GetApiV1PartnerAffiliateResponseAffiliatesItem",
    "GetApiV1PartnerAffiliateResponsePageInfo",
    "GetApiV1PartnerAffiliateResponsePageInfoErrorsItem",
    "GetApiV1PartnerAffiliateResponsePageInfoErrorsItemData",
    "GetApiV1PartnerCompanyByTaxIdResponse",
    "GetApiV1PartnerCompanyByTaxIdResponsePreRegistration",
    "GetApiV1PartnerCompanyResponse",
    "GetApiV1PartnerCompanyResponsePageInfo",
    "GetApiV1PartnerCompanyResponsePageInfoErrorsItem",
    "GetApiV1PartnerCompanyResponsePageInfoErrorsItemData",
    "GetApiV1PartnerCompanyResponsePreRegistrationsItem",
    "GetApiV1PaymentByIdResponse",
    "GetApiV1PaymentResponse",
    "GetApiV1PaymentResponsePageInfo",
    "GetApiV1PaymentResponsePageInfoErrorsItem",
    "GetApiV1PaymentResponsePageInfoErrorsItemData",
    "GetApiV1PaymentResponsePaymentsItem",
    "GetApiV1PixKeysResponse",
    "GetApiV1PixKeysTokensLogsResponse",
    "GetApiV1PixKeysTokensLogsResponsePageInfo",
    "GetApiV1PixKeysTokensLogsResponsePageInfoErrorsItem",
    "GetApiV1PixKeysTokensLogsResponsePageInfoErrorsItemData",
    "GetApiV1PspResponse",
    "GetApiV1PspResponsePspsItem",
    "GetApiV1QrcodeStaticByIdResponse",
    "GetApiV1QrcodeStaticResponse",
    "GetApiV1QrcodeStaticResponsePageInfo",
    "GetApiV1QrcodeStaticResponsePageInfoErrorsItem",
    "GetApiV1QrcodeStaticResponsePageInfoErrorsItemData",
    "GetApiV1ReceiptByReceiptTypeByEndToEndIdReceiptType",
    "GetApiV1RefundByIdResponse",
    "GetApiV1RefundResponse",
    "GetApiV1RefundResponsePageInfo",
    "GetApiV1RefundResponsePageInfoErrorsItem",
    "GetApiV1RefundResponsePageInfoErrorsItemData",
    "GetApiV1StablecoinQuoteResponse",
    "GetApiV1StablecoinQuoteResponseQuote",
    "GetApiV1StablecoinQuoteResponseQuoteAppliedFeesItem",
    "GetApiV1StatementResponseItem",
    "GetApiV1SubaccountByIdResponse",
    "GetApiV1SubaccountByIdStatementResponseItem",
    "GetApiV1SubaccountByIdStatementResponseItemOperationTyp",
    "GetApiV1SubaccountByIdStatementResponseItemType",
    "GetApiV1SubaccountResponse",
    "GetApiV1SubaccountResponsePageInfo",
    "GetApiV1SubaccountResponsePageInfoErrorsItem",
    "GetApiV1SubaccountResponsePageInfoErrorsItemData",
    "GetApiV1SubaccountResponseSubaccountsItem",
    "GetApiV1SubscriptionsByIdInstallmentsResponse",
    "GetApiV1SubscriptionsByIdResponse",
    "GetApiV1SubscriptionsResponse",
    "GetApiV1TransactionByIdResponse",
    "GetApiV1TransactionResponse",
    "GetApiV1TransactionResponsePageInfo",
    "GetApiV1TransactionResponsePageInfoErrorsItem",
    "GetApiV1TransactionResponsePageInfoErrorsItemData",
    "GetApiV1TransactionType",
    "GetApiV1WebhookEventsResponse",
    "GetApiV1WebhookEventsResponseEventsItem",
    "GetApiV1WebhookIpsResponse",
    "GetApiV1WebhookResponse",
    "GetApiV1WebhookResponsePageInfo",
    "GetApiV1WebhookResponsePageInfoErrorsItem",
    "GetApiV1WebhookResponsePageInfoErrorsItemData",
    "InfractionReports",
    "Installment",
    "InstallmentCobr",
    "InstallmentCobrTriesItem",
    "KeyOrOwnerStatistics",
    "KycOnboardingAccountRegister",
    "KycOnboardingAccountRegisterRepresentativesItem",
    "KycOnboardingAccountRegisterRepresentativesItemTaxId",
    "KycOnboardingAccountRegisterTaxId",
    "KycOnboardingRepresentative",
    "KycOnboardingRequest",
    "NumericWindow",
    "OpenPixClient",
    "OpenPixEnvironment",
    "OpenPixEvent",
    "OpenPixWebhookEvent",
    "Pagination",
    "PaginationErrorsItem",
    "PaginationErrorsItemData",
    "PartnerApplicationPayload",
    "Party",
    "PartyAccount",
    "PartyHolder",
    "PartyPsp",
    "PartyTaxId",
    "PatchApiV1ChargeByIdResponse",
    "PatchApiV1CustomerByCorrelationIdResponse",
    "PatchApiV1InvoiceIntegrationBody",
    "PatchApiV1InvoiceIntegrationResponse",
    "PatchApiV1InvoiceIntegrationResponseIntegration",
    "PayloadAccount",
    "Payment",
    "PaymentApprovePayload",
    "PaymentBoleto",
    "PaymentBoletoFinalBeneficiary",
    "PaymentBoletoIssuingEntity",
    "PaymentCreatePayload",
    "PaymentCreatePayloadBoleto",
    "PaymentCreatePayloadManual",
    "PaymentCreatePayloadManualAccount",
    "PaymentCreatePayloadManualHolder",
    "PaymentCreatePayloadManualHolderTaxId",
    "PaymentCreatePayloadPixKey",
    "PaymentCreatePayloadPixKeyDestinationAliasType",
    "PaymentCreatePayloadPixKeyType",
    "PaymentCreatePayloadQrCode",
    "PaymentDestination",
    "PaymentStatus",
    "PaymentTransaction",
    "PixKey",
    "PixKeyCheck",
    "PixKeyCheckOwner",
    "PixKeyCreate",
    "PixKeyCreateType",
    "PixKeyFraudValidationData",
    "PixKeyFraudValidationResponse",
    "PixKeyTokens",
    "PixKeyType",
    "PixQrCode",
    "PixQrCodePayload",
    "PixWithdrawTransaction",
    "PostApiV1AccountByAccountIdWithdrawBody",
    "PostApiV1AccountByAccountIdWithdrawResponse",
    "PostApiV1AccountByAccountIdWithdrawResponseWithdraw",
    "PostApiV1AccountResponse",
    "PostApiV1ApplicationResponse",
    "PostApiV1CashbackFidelityBody",
    "PostApiV1CashbackFidelityResponse",
    "PostApiV1CashbackFidelityResponseCashback",
    "PostApiV1ChargeByIdRefundResponse",
    "PostApiV1ChargeResponse",
    "PostApiV1CustomerResponse",
    "PostApiV1DecodeEmvBody",
    "PostApiV1DecodeEmvResponse",
    "PostApiV1DecodeEmvResponseCobLocation",
    "PostApiV1DecodeEmvResponseCobLocationPayload",
    "PostApiV1DecodeEmvResponseCobLocationPayloadAdditionalI",
    "PostApiV1DecodeEmvResponseCobLocationPayloadCalendar",
    "PostApiV1DecodeEmvResponseCobLocationPayloadDebtor",
    "PostApiV1DecodeEmvResponseCobLocationPayloadValue",
    "PostApiV1DecodeEmvResponseEmv",
    "PostApiV1DecodeEmvResponseEmvAdditionalDataFieldTemplat",
    "PostApiV1DecodeEmvResponseEmvMerchantAccountInformation",
    "PostApiV1DecodeEmvResponseEmvUnreservedTemplates",
    "PostApiV1DecodeEmvResponseRecLocation",
    "PostApiV1DecodeEmvResponseRecLocationPayload",
    "PostApiV1DecodeEmvResponseRecLocationPayloadCalendar",
    "PostApiV1DecodeEmvResponseRecLocationPayloadLink",
    "PostApiV1DecodeEmvResponseRecLocationPayloadLinkDebtor",
    "PostApiV1DecodeEmvResponseRecLocationPayloadReceiver",
    "PostApiV1DecodeEmvResponseRecLocationPayloadUpdatesItem",
    "PostApiV1DecodeEmvResponseRecLocationPayloadValue",
    "PostApiV1DisputeIdEvidenceBody",
    "PostApiV1DisputeIdEvidenceBodyDocumentsItem",
    "PostApiV1DisputeIdEvidenceResponse",
    "PostApiV1DisputeIdEvidenceResponseDocumentsItem",
    "PostApiV1InstallmentsByIdCobrBody",
    "PostApiV1InstallmentsByIdCobrRetryBody",
    "PostApiV1InvoiceByCorrelationIdCancelResponse",
    "PostApiV1InvoiceIntegrationBody",
    "PostApiV1InvoiceIntegrationCertificateBody",
    "PostApiV1InvoiceIntegrationCertificateResponse",
    "PostApiV1InvoiceIntegrationCertificateResponseIntegrati",
    "PostApiV1InvoiceIntegrationResponse",
    "PostApiV1InvoiceIntegrationResponseIntegration",
    "PostApiV1InvoiceIntegrationResponseIntegrationMetadata",
    "PostApiV1InvoiceIntegrationResponseIntegrationMetadataN",
    "PostApiV1InvoiceIntegrationTestResponse",
    "PostApiV1InvoiceIntegrationTestResponseIntegration",
    "PostApiV1InvoiceIntegrationTestResponseInvoice",
    "PostApiV1InvoiceResponse",
    "PostApiV1InvoiceResponseInvoice",
    "PostApiV1InvoiceResponseInvoiceCharge",
    "PostApiV1InvoiceResponseInvoiceCustomer",
    "PostApiV1KycOnboardingResponse",
    "PostApiV1PartnerApplicationBody",
    "PostApiV1PartnerApplicationBodyApplication",
    "PostApiV1PartnerApplicationResponse",
    "PostApiV1PaymentApproveResponse",
    "PostApiV1PaymentBody",
    "PostApiV1PaymentBodyBoleto",
    "PostApiV1PaymentBodyManual",
    "PostApiV1PaymentBodyManualAccount",
    "PostApiV1PaymentBodyManualHolder",
    "PostApiV1PaymentBodyManualHolderTaxId",
    "PostApiV1PaymentBodyPixKey",
    "PostApiV1PaymentBodyQrCode",
    "PostApiV1PaymentResponse",
    "PostApiV1PixKeysCheckBody",
    "PostApiV1QrcodeStaticResponse",
    "PostApiV1RefundResponse",
    "PostApiV1StablecoinDepositApproveBody",
    "PostApiV1StablecoinDepositApproveResponse",
    "PostApiV1SubaccountByIdCreditBody",
    "PostApiV1SubaccountByIdCreditResponse",
    "PostApiV1SubaccountByIdDebitBody",
    "PostApiV1SubaccountByIdDebitResponse",
    "PostApiV1SubaccountByIdWithdrawResponse",
    "PostApiV1SubaccountByIdWithdrawResponseWithdraw",
    "PostApiV1SubaccountResponse",
    "PostApiV1SubscriptionsResponse",
    "PostApiV1TransferResponse",
    "PostApiV1WebhookBody",
    "PostApiV1WebhookResponse",
    "PreRegistrationObject",
    "PreRegistrationObjectPayload",
    "PreRegistrationPayloadObject",
    "PreRegistrationUserObject",
    "Psp",
    "PutApiV1InvoiceIntegrationBody",
    "PutApiV1InvoiceIntegrationResponse",
    "Refund",
    "RefundPayload",
    "RefundStatus",
    "StablecoinDepositError",
    "StablecoinDepositGetResponse",
    "StablecoinDepositListItem",
    "StablecoinDepositListResponse",
    "StablecoinDepositQuote",
    "StablecoinDepositRequest",
    "StablecoinDepositRequestCurrency",
    "StablecoinDepositRequestNetwork",
    "StablecoinDepositResponse",
    "StablecoinSubAccountCreateError",
    "StablecoinSubAccountCreateRequest",
    "StablecoinSubAccountCreateResponse",
    "StablecoinSubAccountGetResponse",
    "StablecoinSubAccountItem",
    "StablecoinSubAccountListResponse",
    "SubAccount",
    "SubAccountPayload",
    "SubAccountTransferPayload",
    "SubAccountTransferResponsePayload",
    "SubAccountTransferResponsePayloadDestinationSubaccount",
    "SubAccountTransferResponsePayloadOriginSubaccount",
    "SubAccountWithdrawPayload",
    "Subscription",
    "SubscriptionAddtionalInfoItem",
    "SubscriptionFrequency",
    "SubscriptionPayload",
    "SubscriptionPayloadAdditionalInfoItem",
    "SubscriptionPayloadCustomer",
    "SubscriptionPayloadCustomerAddress",
    "SubscriptionPayloadPixRecurringOptions",
    "SubscriptionPayloadType",
    "SubscriptionPixRecurringOptions",
    "SubscriptionPixRecurringOptionsJourney",
    "SubscriptionPixRecurringOptionsRetryPolicy",
    "SubscriptionPixRecurringOptionsStatus",
    "SubscriptionType",
    "TaxIdObjectPayload",
    "TaxIdObjectPayloadType",
    "TokenBucketLog",
    "TokenBucketLogOperation",
    "Transaction",
    "Transaction2",
    "TransactionStatus",
    "TransactionType",
    "TransactionWebhookSentItem",
    "TransferCreatePayload",
    "TransferTransaction",
    "Webhook",
    "WebhookEventEnum",
    "WebhookPayload",
    "WithdrawTransaction",
    "cents_to_reais",
    "decode_public_key",
    "make_openpix_webhook_dependency",
    "reais_to_cents",
    "to_cents",
    "webhook_verifier",
]
"""The whole surface: the thin layer plus every generated name.

Written by ``scripts/regen_openpix.py`` — the list is the union of
``_HAND_WRITTEN``, ``client.__all__`` and ``schemas.__all__``, and
``tests/integrations/payment/openpix/test_generated_drift.py`` fails when it
drifts from that.

It lists the generated names because a strict type-checker rejects them
otherwise. Measured with basedpyright against the installed wheel: a
consumer writing ``from tempest_fastapi_sdk.integrations.payment.openpix
import ChargePayload`` got *"ChargePayload" is not exported from module*,
with the advice to import from the private ``.schemas`` submodule instead.
The ``TYPE_CHECKING`` wildcard above makes the symbol **visible** but does
not mark it **re-exported**; only ``__all__`` (or an ``X as X`` alias per
name) does. mypy accepted the wildcard either way, which is how this shipped.

The cost is that ``from ... import *`` now pulls every generated name and
pays the lazy load. Importing names explicitly, which is what the rest of
the SDK's docs do, still costs nothing until one of them is a generated one.
"""
