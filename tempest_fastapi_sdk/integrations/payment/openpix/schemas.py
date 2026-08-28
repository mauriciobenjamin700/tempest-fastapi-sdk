"""Pydantic schemas generated from the OpenPix OpenAPI specification.

Do not edit by hand — rerun `tempest openapi-client` to refresh.

Field names are Python-idiomatic; the wire name is attached as a
Pydantic ``alias`` whenever the two differ, and every model enables
``populate_by_name`` so both spellings are accepted on input. Call
``model_dump(by_alias=True)`` to serialize back to the wire shape.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, Field

from tempest_fastapi_sdk import BaseSchema, BaseStrEnum


class ApplicationEnumTypePayload(BaseStrEnum):
    """Allowed values for ApplicationEnumTypePayload."""

    API = "API"
    PLUGIN = "PLUGIN"
    ORACLE = "ORACLE"


class ApplicationPayloadApplicationType(BaseStrEnum):
    """Allowed values for ApplicationPayloadApplicationType."""

    API = "API"


class ApplicationType(BaseStrEnum):
    """Allowed values for ApplicationType."""

    API = "API"
    POS = "POS"
    PLUGIN = "PLUGIN"
    CHECKOUT = "CHECKOUT"
    MASTER = "MASTER"


class ChargePayloadDiscountSettingsModality(BaseStrEnum):
    """Allowed values for ChargePayloadDiscountSettingsModality."""

    FIXED_VALUE_UNTIL_SPECIFIED_DATE = "FIXED_VALUE_UNTIL_SPECIFIED_DATE"
    PERCENTAGE_UNTIL_SPECIFIED_DATE = "PERCENTAGE_UNTIL_SPECIFIED_DATE"
    VALUE_PER_RUNNING_DAY_ADVANCE = "VALUE_PER_RUNNING_DAY_ADVANCE"
    VALUE_PER_BUSINESS_DAY_ADVANCE = "VALUE_PER_BUSINESS_DAY_ADVANCE"
    PERCENTAGE_PER_RUNNING_DAY_ADVANCE = "PERCENTAGE_PER_RUNNING_DAY_ADVANCE"
    PERCENTAGE_PER_BUSINESS_DAY_ADVANCE = "PERCENTAGE_PER_BUSINESS_DAY_ADVANCE"


class ChargePayloadInterestsType(BaseStrEnum):
    """Allowed values for ChargePayloadInterestsType."""

    FIXED = "FIXED"
    PERCENTAGE = "PERCENTAGE"


class ChargePayloadSplitsItemSplitType(BaseStrEnum):
    """Allowed values for ChargePayloadSplitsItemSplitType."""

    SPLIT_INTERNAL_TRANSFER = "SPLIT_INTERNAL_TRANSFER"
    SPLIT_SUB_ACCOUNT = "SPLIT_SUB_ACCOUNT"
    SPLIT_PARTNER = "SPLIT_PARTNER"


class ChargeRefundStatus(BaseStrEnum):
    """Allowed values for ChargeRefundStatus."""

    IN_PROCESSING = "IN_PROCESSING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class ChargeStatus(BaseStrEnum):
    """Allowed values for ChargeStatus."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"


class ChargeType(BaseStrEnum):
    """Allowed values for ChargeType."""

    DYNAMIC = "DYNAMIC"
    OVERDUE = "OVERDUE"
    BOLETO = "BOLETO"


class DisputePayloadStatus(BaseStrEnum):
    """Allowed values for DisputePayloadStatus."""

    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    IN_REVIEW = "IN_REVIEW"


class DisputeStatus(BaseStrEnum):
    """Allowed values for DisputeStatus."""

    IN_REVIEW = "IN_REVIEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"


class FundsRecoveryDirection(BaseStrEnum):
    """Allowed values for FundsRecoveryDirection."""

    SENT = "SENT"
    RECEIVED = "RECEIVED"


class FundsRecoverySituationType(BaseStrEnum):
    """Allowed values for FundsRecoverySituationType."""

    SCAM = "SCAM"
    ACCOUNT_TAKEOVER = "ACCOUNT_TAKEOVER"
    COERCION = "COERCION"
    FRAUDULENT_ACCESS = "FRAUDULENT_ACCESS"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class FundsRecoveryStatus(BaseStrEnum):
    """Allowed values for FundsRecoveryStatus."""

    CREATED = "CREATED"
    TRACKED = "TRACKED"
    AWAITING_ANALYSIS = "AWAITING_ANALYSIS"
    ANALYSED = "ANALYSED"
    REFUNDING = "REFUNDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class GetApiV1DisputeByIdResponseDisputeStatus(BaseStrEnum):
    """Allowed values for GetApiV1DisputeByIdResponseDisputeStatus."""

    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"


class GetApiV1DisputeByIdResponseDisputeType(BaseStrEnum):
    """Allowed values for GetApiV1DisputeByIdResponseDisputeType."""

    MED = "MED"
    DISPUTE = "DISPUTE"
    CHARGEBACK = "CHARGEBACK"


class GetApiV1DisputeResponseDisputesItemType(BaseStrEnum):
    """Allowed values for GetApiV1DisputeResponseDisputesItemType."""

    MED = "MED"
    CHARGEBACK = "CHARGEBACK"


class GetApiV1ReceiptByReceiptTypeByEndToEndIdReceiptType(BaseStrEnum):
    """Allowed values for GetApiV1ReceiptByReceiptTypeByEndToEndIdReceiptType."""

    PIX_IN = "pix-in"
    PIX_OUT = "pix-out"
    PIX_REFUND = "pix-refund"


class GetApiV1SubaccountByIdStatementResponseItemOperationTyp(BaseStrEnum):
    """Allowed values for GetApiV1SubaccountByIdStatementResponseItemOperationType."""

    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    TRANSFER_CREDIT = "TRANSFER_CREDIT"
    TRANSFER_DEBIT = "TRANSFER_DEBIT"
    WITHDRAWAL = "WITHDRAWAL"
    WITHDRAWAL_REVERSAL = "WITHDRAWAL_REVERSAL"
    WITHDRAWAL_FEE = "WITHDRAWAL_FEE"
    WITHDRAWAL_FEE_REVERSAL = "WITHDRAWAL_FEE_REVERSAL"


class GetApiV1SubaccountByIdStatementResponseItemType(BaseStrEnum):
    """Allowed values for GetApiV1SubaccountByIdStatementResponseItemType."""

    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class GetApiV1TransactionType(BaseStrEnum):
    """Allowed values for GetApiV1TransactionType."""

    PAYMENT = "PAYMENT"
    WITHDRAW = "WITHDRAW"
    REFUND = "REFUND"
    FEE = "FEE"
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"
    BALANCE_BLOCK = "BALANCE_BLOCK"
    BALANCE_UNBLOCK = "BALANCE_UNBLOCK"
    REVERSAL = "REVERSAL"


class PaymentCreatePayloadPixKeyDestinationAliasType(BaseStrEnum):
    """Allowed values for PaymentCreatePayloadPixKeyDestinationAliasType."""

    CPF = "CPF"
    CNPJ = "CNPJ"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    RANDOM = "RANDOM"


class PaymentCreatePayloadPixKeyType(BaseStrEnum):
    """Allowed values for PaymentCreatePayloadPixKeyType."""

    PIX_KEY = "PIX_KEY"
    QR_CODE = "QR_CODE"
    MANUAL = "MANUAL"
    BOLETO = "BOLETO"


class PaymentStatus(BaseStrEnum):
    """Allowed values for PaymentStatus."""

    CREATED = "CREATED"
    FAILED = "FAILED"
    CONFIRMED = "CONFIRMED"
    DENIED = "DENIED"


class PixKeyCreateType(BaseStrEnum):
    """Allowed values for PixKeyCreateType."""

    CNPJ = "CNPJ"
    EVP = "EVP"


class PixKeyType(BaseStrEnum):
    """Allowed values for PixKeyType."""

    CPF = "CPF"
    CNPJ = "CNPJ"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    EVP = "EVP"


class RefundStatus(BaseStrEnum):
    """Allowed values for RefundStatus."""

    IN_PROCESSING = "IN_PROCESSING"
    REFUNDED = "REFUNDED"
    NOT_ACCOMPLISHED = "NOT_ACCOMPLISHED"


class StablecoinDepositRequestCurrency(BaseStrEnum):
    """Allowed values for StablecoinDepositRequestCurrency."""

    USDT = "USDT"
    USDC = "USDC"
    BRLA = "BRLA"


class StablecoinDepositRequestNetwork(BaseStrEnum):
    """Allowed values for StablecoinDepositRequestNetwork."""

    POLYGON = "POLYGON"
    ETHEREUM = "ETHEREUM"
    BASE = "BASE"
    CELO = "CELO"
    TRON = "TRON"


class SubscriptionFrequency(BaseStrEnum):
    """Allowed values for SubscriptionFrequency."""

    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    BIMONTHLY = "BIMONTHLY"
    QUARTERLY = "QUARTERLY"
    SEMIANNUALLY = "SEMIANNUALLY"
    ANNUALLY = "ANNUALLY"


class SubscriptionPayloadType(BaseStrEnum):
    """Allowed values for SubscriptionPayloadType."""

    PIX_RECURRING = "PIX_RECURRING"
    RECURRENT = "RECURRENT"


class SubscriptionPixRecurringOptionsJourney(BaseStrEnum):
    """Allowed values for SubscriptionPixRecurringOptionsJourney."""

    PAYMENT_ON_APPROVAL = "PAYMENT_ON_APPROVAL"
    ONLY_RECURRENCY = "ONLY_RECURRENCY"


class SubscriptionPixRecurringOptionsRetryPolicy(BaseStrEnum):
    """Allowed values for SubscriptionPixRecurringOptionsRetryPolicy."""

    NON_PERMITED = "NON_PERMITED"
    THREE_RETRIES_7_DAYS = "THREE_RETRIES_7_DAYS"


class SubscriptionPixRecurringOptionsStatus(BaseStrEnum):
    """Allowed values for SubscriptionPixRecurringOptionsStatus."""

    CREATED = "CREATED"
    CANCELED = "CANCELED"
    APPROVED = "APPROVED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class SubscriptionType(BaseStrEnum):
    """Allowed values for SubscriptionType."""

    PIX_RECURRING = "PIX_RECURRING"
    RECURRENT = "RECURRENT"
    IN_INSTALLMENT = "IN_INSTALLMENT"
    PIX_CREDIARY = "PIX_CREDIARY"


class TaxIdObjectPayloadType(BaseStrEnum):
    """Allowed values for TaxIdObjectPayloadType."""

    BR_CNPJ = "BR:CNPJ"
    BR_CPF = "BR:CPF"


class TokenBucketLogOperation(BaseStrEnum):
    """Allowed values for TokenBucketLogOperation."""

    ADD = "ADD"
    REMOVE = "REMOVE"


class TransactionStatus(BaseStrEnum):
    """Allowed values for TransactionStatus."""

    PAYMENT = "PAYMENT"
    WITHDRAW = "WITHDRAW"
    REFUND = "REFUND"
    FEE = "FEE"


class TransactionType(BaseStrEnum):
    """Allowed values for TransactionType."""

    CREATED = "CREATED"
    CONFIRMED = "CONFIRMED"
    REFUNDED = "REFUNDED"
    PARTIAL_REFUND = "PARTIAL_REFUND"
    IN_PROCESSING = "IN_PROCESSING"
    REJECTED = "REJECTED"


class WebhookEventEnum(BaseStrEnum):
    """Allowed values for WebhookEventEnum."""

    OPENPIX_CHARGE_CREATED = "OPENPIX:CHARGE_CREATED"
    OPENPIX_CHARGE_COMPLETED = "OPENPIX:CHARGE_COMPLETED"
    OPENPIX_CHARGE_EXPIRED = "OPENPIX:CHARGE_EXPIRED"
    OPENPIX_CHARGE_COMPLETED_NOT_SAME_CUSTOMER_PAYER = (
        "OPENPIX:CHARGE_COMPLETED_NOT_SAME_CUSTOMER_PAYER"
    )
    OPENPIX_TRANSACTION_RECEIVED = "OPENPIX:TRANSACTION_RECEIVED"
    OPENPIX_TRANSACTION_REFUND_RECEIVED = "OPENPIX:TRANSACTION_REFUND_RECEIVED"
    PIX_TRANSACTION_REFUND_RECEIVED_CONFIRMED = (
        "PIX_TRANSACTION_REFUND_RECEIVED_CONFIRMED"
    )
    PIX_TRANSACTION_REFUND_SENT_CONFIRMED = "PIX_TRANSACTION_REFUND_SENT_CONFIRMED"
    PIX_TRANSACTION_REFUND_RECEIVED_REJECTED = (
        "PIX_TRANSACTION_REFUND_RECEIVED_REJECTED"
    )
    PIX_TRANSACTION_REFUND_SENT_REJECTED = "PIX_TRANSACTION_REFUND_SENT_REJECTED"
    OPENPIX_MOVEMENT_CONFIRMED = "OPENPIX:MOVEMENT_CONFIRMED"
    OPENPIX_MOVEMENT_FAILED = "OPENPIX:MOVEMENT_FAILED"
    OPENPIX_MOVEMENT_REMOVED = "OPENPIX:MOVEMENT_REMOVED"
    OPENPIX_DISPUTE_CREATED = "OPENPIX:DISPUTE_CREATED"
    OPENPIX_DISPUTE_ACCEPTED = "OPENPIX:DISPUTE_ACCEPTED"
    OPENPIX_DISPUTE_REJECTED = "OPENPIX:DISPUTE_REJECTED"
    OPENPIX_DISPUTE_CANCELED = "OPENPIX:DISPUTE_CANCELED"
    ACCOUNT_REGISTER_APPROVED = "ACCOUNT_REGISTER_APPROVED"
    ACCOUNT_REGISTER_REJECTED = "ACCOUNT_REGISTER_REJECTED"
    ACCOUNT_REGISTER_PENDING = "ACCOUNT_REGISTER_PENDING"
    PIX_AUTOMATIC_APPROVED = "PIX_AUTOMATIC_APPROVED"
    PIX_AUTOMATIC_REJECTED = "PIX_AUTOMATIC_REJECTED"
    PIX_AUTOMATIC_COBR_CREATED = "PIX_AUTOMATIC_COBR_CREATED"
    PIX_AUTOMATIC_COBR_APPROVED = "PIX_AUTOMATIC_COBR_APPROVED"
    PIX_AUTOMATIC_COBR_REJECTED = "PIX_AUTOMATIC_COBR_REJECTED"
    PIX_AUTOMATIC_COBR_TRY_REJECTED = "PIX_AUTOMATIC_COBR_TRY_REJECTED"
    PIX_AUTOMATIC_COBR_TRY_REQUESTED = "PIX_AUTOMATIC_COBR_TRY_REQUESTED"
    PIX_AUTOMATIC_COBR_COMPLETED = "PIX_AUTOMATIC_COBR_COMPLETED"


class AccountLimit(BaseSchema):
    """Schema generated for AccountLimit.

    Attributes:
        pix_day_limit (int | None): Pix day total limit in cents
        pix_night_limit (int | None): Pix night total limit in cents
        pix_out_same_holder_day_limit (int | None): Pix outbound day limit for transfers
            between same-holder accounts (cents)
        pix_out_different_holder_day_limit (int | None): Pix outbound day limit for
            transfers between different-holder accounts (cents)
        pix_out_same_holder_night_limit (int | None): Pix outbound night limit for
            transfers between same-holder accounts (cents)
        pix_out_different_holder_night_limit (int | None): Pix outbound night limit for
            transfers between different-holder accounts (cents)
        pix_in_same_holder_day_limit (int | None): Pix inbound day limit for transfers
            between same-holder accounts (cents)
        pix_in_different_holder_day_limit (int | None): Pix inbound day limit for
            transfers between different-holder accounts (cents)
        pix_in_same_holder_night_limit (int | None): Pix inbound night limit for
            transfers between same-holder accounts (cents)
        pix_in_different_holder_night_limit (int | None): Pix inbound night limit for
            transfers between different-holder accounts (cents)
        day_start_at (str | None): Start time of the day window (HH:mm)
        night_start_at (str | None): Start time of the night window (HH:mm)
        boleto_emission_limit (int | None): Maximum number of boletos that can be
            emitted per day
        boleto_maximum_value_limit (int | None): Maximum value (in cents) allowed per
            boleto emission
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pix_day_limit: int | None = Field(
        validation_alias="pixDayLimit",
        serialization_alias="pixDayLimit",
        description="Pix day total limit in cents",
        default=None,
    )
    pix_night_limit: int | None = Field(
        validation_alias="pixNightLimit",
        serialization_alias="pixNightLimit",
        description="Pix night total limit in cents",
        default=None,
    )
    pix_out_same_holder_day_limit: int | None = Field(
        validation_alias="pixOutSameHolderDayLimit",
        serialization_alias="pixOutSameHolderDayLimit",
        description=(
            "Pix outbound day limit for transfers between same-holder accounts (cents)"
        ),
        default=None,
    )
    pix_out_different_holder_day_limit: int | None = Field(
        validation_alias="pixOutDifferentHolderDayLimit",
        serialization_alias="pixOutDifferentHolderDayLimit",
        description=(
            "Pix outbound day limit for transfers between different-holder accounts "
            "(cents)"
        ),
        default=None,
    )
    pix_out_same_holder_night_limit: int | None = Field(
        validation_alias="pixOutSameHolderNightLimit",
        serialization_alias="pixOutSameHolderNightLimit",
        description=(
            "Pix outbound night limit for transfers between same-holder accounts "
            "(cents)"
        ),
        default=None,
    )
    pix_out_different_holder_night_limit: int | None = Field(
        validation_alias="pixOutDifferentHolderNightLimit",
        serialization_alias="pixOutDifferentHolderNightLimit",
        description=(
            "Pix outbound night limit for transfers between different-holder accounts "
            "(cents)"
        ),
        default=None,
    )
    pix_in_same_holder_day_limit: int | None = Field(
        validation_alias="pixInSameHolderDayLimit",
        serialization_alias="pixInSameHolderDayLimit",
        description=(
            "Pix inbound day limit for transfers between same-holder accounts (cents)"
        ),
        default=None,
    )
    pix_in_different_holder_day_limit: int | None = Field(
        validation_alias="pixInDifferentHolderDayLimit",
        serialization_alias="pixInDifferentHolderDayLimit",
        description=(
            "Pix inbound day limit for transfers between different-holder accounts "
            "(cents)"
        ),
        default=None,
    )
    pix_in_same_holder_night_limit: int | None = Field(
        validation_alias="pixInSameHolderNightLimit",
        serialization_alias="pixInSameHolderNightLimit",
        description=(
            "Pix inbound night limit for transfers between same-holder accounts (cents)"
        ),
        default=None,
    )
    pix_in_different_holder_night_limit: int | None = Field(
        validation_alias="pixInDifferentHolderNightLimit",
        serialization_alias="pixInDifferentHolderNightLimit",
        description=(
            "Pix inbound night limit for transfers between different-holder accounts "
            "(cents)"
        ),
        default=None,
    )
    day_start_at: str | None = Field(
        validation_alias="dayStartAt",
        serialization_alias="dayStartAt",
        description="Start time of the day window (HH:mm)",
        examples=["06:00"],
        default=None,
    )
    night_start_at: str | None = Field(
        validation_alias="nightStartAt",
        serialization_alias="nightStartAt",
        description="Start time of the night window (HH:mm)",
        examples=["20:00"],
        default=None,
    )
    boleto_emission_limit: int | None = Field(
        validation_alias="boletoEmissionLimit",
        serialization_alias="boletoEmissionLimit",
        description="Maximum number of boletos that can be emitted per day",
        default=None,
    )
    boleto_maximum_value_limit: int | None = Field(
        validation_alias="boletoMaximumValueLimit",
        serialization_alias="boletoMaximumValueLimit",
        description="Maximum value (in cents) allowed per boleto emission",
        default=None,
    )


class AccountObjectPayload(BaseSchema):
    """Schema generated for AccountObjectPayload.

    Attributes:
        client_id (str | None): The client ID from the company bank account that is
            related to this preregistration/company.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    client_id: str | None = Field(
        validation_alias="clientId",
        serialization_alias="clientId",
        description=(
            "The client ID from the company bank account that is related to this "
            "preregistration/company."
        ),
        default=None,
    )


class AccountRegisterPayload(BaseSchema):
    """Schema generated for AccountRegisterPayload.

    Attributes:
        official_name (str): Official name of the company
        trade_name (str): Trade name of the company
        tax_id (str): Tax ID of the company
        annual_revenue (float): Annual revenue of the company
    """

    model_config = ConfigDict(populate_by_name=True)

    official_name: str = Field(
        validation_alias="officialName",
        serialization_alias="officialName",
        description="Official name of the company",
    )
    trade_name: str = Field(
        validation_alias="tradeName",
        serialization_alias="tradeName",
        description="Trade name of the company",
    )
    tax_id: str = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="Tax ID of the company",
    )
    annual_revenue: float = Field(
        validation_alias="annualRevenue",
        serialization_alias="annualRevenue",
        description="Annual revenue of the company",
    )


class AccountRegisterResponseTaxId(BaseSchema):
    """Schema generated for AccountRegisterResponseTaxId.

    Attributes:
        tax_id (str | None): The tax ID value
        type (str | None): The type of tax ID
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="The tax ID value",
        default=None,
    )
    type: str | None = Field(description="The type of tax ID", default=None)


class AccountRegisterTaxId(BaseSchema):
    """Schema generated for AccountRegisterTaxId.

    Attributes:
        tax_id (str | None): The tax ID value
        type (str | None): The type of tax ID
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="The tax ID value",
        default=None,
    )
    type: str | None = Field(description="The type of tax ID", default=None)


class Application(BaseSchema):
    """Schema generated for Application.

    Attributes:
        name (str | None): Name of the application
        is_active (bool | None): Whether the application is active
        type (ApplicationType | None): Type of the application (API, POS, PLUGIN,
            CHECKOUT)
        client_id (str | None): Client ID for authentication
        client_secret (str | None): Client secret for authentication
        app_id (str | None): Unique application identifier
        company_bank_account (str | None): ID of the linked company bank account
        scopes (list[str]): List of scopes assigned to the application for access
            control
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = Field(description="Name of the application", default=None)
    is_active: bool | None = Field(
        validation_alias="isActive",
        serialization_alias="isActive",
        description="Whether the application is active",
        default=None,
    )
    type: ApplicationType | None = Field(
        description="Type of the application (API, POS, PLUGIN, CHECKOUT)",
        default=None,
    )
    client_id: str | None = Field(
        validation_alias="clientId",
        serialization_alias="clientId",
        description="Client ID for authentication",
        default=None,
    )
    client_secret: str | None = Field(
        validation_alias="clientSecret",
        serialization_alias="clientSecret",
        description="Client secret for authentication",
        default=None,
    )
    app_id: str | None = Field(
        validation_alias="appID",
        serialization_alias="appID",
        description="Unique application identifier",
        default=None,
    )
    company_bank_account: str | None = Field(
        validation_alias="companyBankAccount",
        serialization_alias="companyBankAccount",
        description="ID of the linked company bank account",
        default=None,
    )
    scopes: list[str] = Field(
        description="List of scopes assigned to the application for access control",
        default_factory=list,
    )


class ApplicationDeletePayload(BaseSchema):
    """Schema generated for ApplicationDeletePayload.

    Attributes:
        client_id (str | None): The client ID of the application to delete
    """

    model_config = ConfigDict(populate_by_name=True)

    client_id: str | None = Field(
        validation_alias="clientId",
        serialization_alias="clientId",
        description="The client ID of the application to delete",
        default=None,
    )


class ApplicationPayloadApplication(BaseSchema):
    """Schema generated for ApplicationPayloadApplication.

    Attributes:
        name (str | None): Name of the application
        type (ApplicationPayloadApplicationType | None): Type of the application (API)
        scopes (list[str]): List of scopes to assign to the application. When provided,
            checkScopes will be enabled automatically.
    """

    name: str | None = Field(description="Name of the application", default=None)
    type: ApplicationPayloadApplicationType | None = Field(
        description="Type of the application (API)",
        default=None,
    )
    scopes: list[str] = Field(
        description=(
            "List of scopes to assign to the application. When provided, checkScopes "
            "will be enabled automatically."
        ),
        default_factory=list,
    )


class BoletoValidateError(BaseSchema):
    """Schema generated for BoletoValidateError.

    Attributes:
        error (str | None): Human readable error message.
        error_code (str | None): Machine readable error code, present for
            provider/business errors.
    """

    model_config = ConfigDict(populate_by_name=True)

    error: str | None = Field(description="Human readable error message.", default=None)
    error_code: str | None = Field(
        validation_alias="errorCode",
        serialization_alias="errorCode",
        description=(
            "Machine readable error code, present for provider/business errors."
        ),
        default=None,
    )


class BoletoValidateRequest(BaseSchema):
    """Schema generated for BoletoValidateRequest.

    Attributes:
        barcode (str): The boleto barcode. Must have 44, 47 or 48 digits.
    """

    barcode: str = Field(
        description="The boleto barcode. Must have 44, 47 or 48 digits.",
        examples=["34195148200000003001095517077320772982609000"],
    )


class BoletoValidatedInfoFinalBeneficiary(BaseSchema):
    """Final beneficiary, when available.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = Field(examples=["WOOVI"], default=None)
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        examples=["44720743000101"],
        default=None,
    )


class BoletoValidatedInfoIssuingEntity(BaseSchema):
    """Issuing institution, when available.

    Attributes:
        code (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    code: str | None = Field(examples=["341"], default=None)
    name: str | None = Field(examples=["ITAU UNIBANCO S/A"], default=None)


class ChargeAdditionalInfoItem(BaseSchema):
    """Schema generated for ChargeAdditionalInfoItem.

    Attributes:
        key (str | None): key of object
        value (str | None): value of object
    """

    model_config = ConfigDict(extra="allow")

    key: str | None = Field(description="key of object", default=None)
    value: str | None = Field(description="value of object", default=None)


class ChargePatchPayload(BaseSchema):
    """Schema generated for ChargePatchPayload.

    Attributes:
        expires_date (str | None): Expiration date of the charge. Only in ISO 8601
            format.
    """

    model_config = ConfigDict(populate_by_name=True)

    expires_date: str | None = Field(
        validation_alias="expiresDate",
        serialization_alias="expiresDate",
        description="Expiration date of the charge. Only in ISO 8601 format.",
        default=None,
    )


class ChargePayloadAdditionalInfoItem(BaseSchema):
    """Schema generated for ChargePayloadAdditionalInfoItem.

    Attributes:
        key (str | None): key of object
        value (str | None): value of object
    """

    key: str | None = Field(description="key of object", default=None)
    value: str | None = Field(description="value of object", default=None)


class ChargePayloadDiscountSettingsDiscountFixedDateItem(BaseSchema):
    """Schema generated for ChargePayloadDiscountSettingsDiscountFixedDateItem.

    Attributes:
        days_active (int | None): Offset in days from charge creation. The discount is
            valid for payments up to and including this many days after the charge was
            created. On persistence, the server normalizes this offset into an absolute
            calendar date (`data`, format `YYYY-MM-DD`) — that is the field returned by
            the GET endpoint.
        value (int | None): Discount value. Units depend on modality:   -
            `FIXED_VALUE_UNTIL_SPECIFIED_DATE`: cents.   -
            `PERCENTAGE_UNTIL_SPECIFIED_DATE`: basis points (e.g. 100 = 1.00%).
        data (date | None): Server-computed absolute date (`YYYY-MM-DD`) corresponding
            to `daysActive` at charge-creation time. Read-only — populated automatically
            and returned on GET; do not send on POST.
    """

    model_config = ConfigDict(populate_by_name=True)

    days_active: int | None = Field(
        validation_alias="daysActive",
        serialization_alias="daysActive",
        description=(
            "Offset in days from charge creation. The discount is valid for payments "
            "up to and including this many days after the charge was created. On "
            "persistence, the server normalizes this offset into an absolute calendar "
            "date (`data`, format `YYYY-MM-DD`) — that is the field returned by the "
            "GET endpoint."
        ),
        ge=1,
        default=None,
    )
    value: int | None = Field(
        description=(
            "Discount value. Units depend on modality:\n  - "
            "`FIXED_VALUE_UNTIL_SPECIFIED_DATE`: cents.\n  - "
            "`PERCENTAGE_UNTIL_SPECIFIED_DATE`: basis points (e.g. 100 = 1.00%)."
        ),
        default=None,
    )
    data: date | None = Field(
        description=(
            "Server-computed absolute date (`YYYY-MM-DD`) corresponding to "
            "`daysActive` at charge-creation time. Read-only — populated automatically "
            "and returned on GET; do not send on POST."
        ),
        default=None,
    )


class ChargePayloadFines(BaseSchema):
    """Fines configuration. This property is only considered for charges of type
    OVERDUE.

    Attributes:
        value (int | None): Value in basis points of fines to be applied when the charge
            hits the deadline
        type (ChargePayloadInterestsType | None): Type of fine calculation to be applied
    """

    value: int | None = Field(
        description=(
            "Value in basis points of fines to be applied when the charge hits the "
            "deadline"
        ),
        default=None,
    )
    type: ChargePayloadInterestsType | None = Field(
        description="Type of fine calculation to be applied",
        default=None,
    )


class ChargePayloadInterests(BaseSchema):
    """Interests configuration. This property is only considered for charges of type
    OVERDUE.

    Attributes:
        value (int | None): Value in basis points of interests to be applied daily after
            the charge hits the deadline
        type (ChargePayloadInterestsType | None): Type of interest calculation to be
            applied
    """

    value: int | None = Field(
        description=(
            "Value in basis points of interests to be applied daily after the charge "
            "hits the deadline"
        ),
        default=None,
    )
    type: ChargePayloadInterestsType | None = Field(
        description="Type of interest calculation to be applied",
        default=None,
    )


class ChargePayloadSplitsItem(BaseSchema):
    """Schema generated for ChargePayloadSplitsItem.

    Attributes:
        value (int): how much value of that charge will be splitted
        pix_key (str): the pixKey of the company bank account that will receive this
            split
        split_type (ChargePayloadSplitsItemSplitType | None): The type of the split.
            Each of these ones will be processed in specific way. [See
            here](https://developers.openpix.com.br/docs/splits/split-introduction) how
            each one will be processed.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int = Field(description="how much value of that charge will be splitted")
    pix_key: str = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        description=(
            "the pixKey of the company bank account that will receive this split"
        ),
    )
    split_type: ChargePayloadSplitsItemSplitType | None = Field(
        validation_alias="splitType",
        serialization_alias="splitType",
        description=(
            "The type of the split. Each of these ones will be processed in specific "
            "way. [See "
            "here](https://developers.openpix.com.br/docs/splits/split-introduction) "
            "how each one will be processed."
        ),
        default=None,
    )


class ChargePaymentMethodsPixAdditionalInfoItem(BaseSchema):
    """Schema generated for ChargePaymentMethodsPixAdditionalInfoItem.

    Attributes:
        key (str | None): Undocumented in the spec.
        value (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    key: str | None = None
    value: str | None = None


class ChargeRefund(BaseSchema):
    """Schema generated for ChargeRefund.

    Attributes:
        value (int | None): Value in cents of this refund
        status (ChargeRefundStatus | None): Undocumented in the spec.
        correlation_id (str | None): Your correlation ID to keep track of this refund
        end_to_end_id (str | None): The endToEndId of this refund
        time (str | None): Time of this refund
        comment (str | None): Comment of this refund
        refund_id (str | None): Unique refund ID for this refund. The specification
            declares this field on `Refund` (a Pix transaction refund) but not on
            `ChargeRefund`, while the API returns it on both.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    value: int | None = Field(description="Value in cents of this refund", default=None)
    status: ChargeRefundStatus | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID to keep track of this refund",
        default=None,
    )
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        description="The endToEndId of this refund",
        default=None,
    )
    time: str | None = Field(description="Time of this refund", default=None)
    comment: str | None = Field(description="Comment of this refund", default=None)
    refund_id: str | None = Field(
        validation_alias="refundId",
        serialization_alias="refundId",
        description=(
            "Unique refund ID for this refund. The specification declares this field "
            "on `Refund` (a Pix transaction refund) but not on `ChargeRefund`, while "
            "the API returns it on both."
        ),
        default=None,
    )


class ChargeRefundPayload(BaseSchema):
    """Schema generated for ChargeRefundPayload.

    Attributes:
        correlation_id (str): Your correlation ID to keep track for this refund
        value (int | None): Value in cents for this refund
        comment (str | None): Comment for this refund. Maximum length of 140 characters.
    """

    model_config = ConfigDict(populate_by_name=True)

    correlation_id: str = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID to keep track for this refund",
    )
    value: int | None = Field(
        description="Value in cents for this refund",
        default=None,
    )
    comment: str | None = Field(
        description="Comment for this refund. Maximum length of 140 characters.",
        max_length=140,
        default=None,
    )


class Company(BaseSchema):
    """Schema generated for Company.

    Attributes:
        official_name (str | None): Official name of the company
        trade_name (str | None): Trade name of the company
        tax_id (str | None): Tax ID of the company
        correlation_id (str | None): Correlation ID of the company
    """

    model_config = ConfigDict(populate_by_name=True)

    official_name: str | None = Field(
        validation_alias="officialName",
        serialization_alias="officialName",
        description="Official name of the company",
        default=None,
    )
    trade_name: str | None = Field(
        validation_alias="tradeName",
        serialization_alias="tradeName",
        description="Trade name of the company",
        default=None,
    )
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="Tax ID of the company",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Correlation ID of the company",
        default=None,
    )


class CompanyBankAccountBalance(BaseSchema):
    """Schema generated for CompanyBankAccountBalance.

    Attributes:
        total (int | None): Total amount in cents
        blocked (int | None): Total blocked amount in cents (security + withdraw safety)
        available (int | None): Available amount in cents
        blocked_by_security (int | None): Amount blocked due to security restrictions
            (e.g., PIX_OUT blocking)
        blocked_by_withdraw_safety (int | None): Amount blocked as minimum balance
            reserve (withdraw safety value)
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    total: int | None = Field(description="Total amount in cents", default=None)
    blocked: int | None = Field(
        description="Total blocked amount in cents (security + withdraw safety)",
        default=None,
    )
    available: int | None = Field(description="Available amount in cents", default=None)
    blocked_by_security: int | None = Field(
        validation_alias="blockedBySecurity",
        serialization_alias="blockedBySecurity",
        description=(
            "Amount blocked due to security restrictions (e.g., PIX_OUT blocking)"
        ),
        default=None,
    )
    blocked_by_withdraw_safety: int | None = Field(
        validation_alias="blockedByWithdrawSafety",
        serialization_alias="blockedByWithdrawSafety",
        description="Amount blocked as minimum balance reserve (withdraw safety value)",
        default=None,
    )


class CustomerAddress(BaseSchema):
    """Schema generated for CustomerAddress.

    Attributes:
        zipcode (str | None): Undocumented in the spec.
        street (str | None): Undocumented in the spec.
        number (str | None): Undocumented in the spec.
        neighborhood (str | None): Undocumented in the spec.
        city (str | None): Undocumented in the spec.
        state (str | None): Undocumented in the spec.
        complement (str | None): Undocumented in the spec.
        country (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    zipcode: str | None = None
    street: str | None = None
    number: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    complement: str | None = None
    country: str | None = None


class CustomerPatchPayloadAddress(BaseSchema):
    """Schema generated for CustomerPatchPayloadAddress.

    Attributes:
        zipcode (str | None): Undocumented in the spec.
        street (str | None): Undocumented in the spec.
        number (str | None): Undocumented in the spec.
        neighborhood (str | None): Undocumented in the spec.
        city (str | None): Undocumented in the spec.
        state (str | None): Undocumented in the spec.
        complement (str | None): Undocumented in the spec.
        country (str | None): Undocumented in the spec.
    """

    zipcode: str | None = None
    street: str | None = None
    number: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    complement: str | None = None
    country: str | None = None


class CustomerPayloadAddress(BaseSchema):
    """Schema generated for CustomerPayloadAddress.

    Attributes:
        zipcode (str | None): Undocumented in the spec.
        street (str | None): Undocumented in the spec.
        number (str | None): Undocumented in the spec.
        neighborhood (str | None): Undocumented in the spec.
        city (str | None): Undocumented in the spec.
        state (str | None): Undocumented in the spec.
        complement (str | None): Undocumented in the spec.
        country (str | None): Undocumented in the spec.
    """

    zipcode: str | None = None
    street: str | None = None
    number: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    complement: str | None = None
    country: str | None = None


class CustomerTaxId(BaseSchema):
    """Schema generated for CustomerTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class DeleteApiV1AccountByAccountIdResponse(BaseSchema):
    """Schema generated for DeleteApiV1AccountByAccountIdResponse.

    Attributes:
        status (str | None): Operation status
        account_id (str | None): ID of the Account
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(
        description="Operation status",
        examples=["OK"],
        default=None,
    )
    account_id: str | None = Field(
        validation_alias="accountId",
        serialization_alias="accountId",
        description="ID of the Account",
        examples=["6290ccfd42831958a405debc"],
        default=None,
    )


class DeleteApiV1AccountRegisterByIdResponse(BaseSchema):
    """Schema generated for DeleteApiV1AccountRegisterByIdResponse.

    Attributes:
        message (str | None): Undocumented in the spec.
        account_register_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    message: str | None = Field(
        examples=["Account register successfully deleted"],
        default=None,
    )
    account_register_id: str | None = Field(
        validation_alias="accountRegisterId",
        serialization_alias="accountRegisterId",
        examples=["12345678901234"],
        default=None,
    )


class DeleteApiV1ApplicationResponse(BaseSchema):
    """Schema generated for DeleteApiV1ApplicationResponse.

    Attributes:
        success (bool | None): Indicates the operation was successful
    """

    model_config = ConfigDict(extra="allow")

    success: bool | None = Field(
        description="Indicates the operation was successful",
        default=None,
    )


class DeleteApiV1ChargeByIdResponse(BaseSchema):
    """Schema generated for DeleteApiV1ChargeByIdResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        id (str | None): the id previously informed to be found and deleted
    """

    model_config = ConfigDict(extra="allow")

    status: str | None = None
    id: str | None = Field(
        description="the id previously informed to be found and deleted",
        default=None,
    )


class DeleteApiV1QrcodeStaticByIdResponse(BaseSchema):
    """Schema generated for DeleteApiV1QrcodeStaticByIdResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    status: str | None = None
    id: str | None = None


class DeleteApiV1SubaccountByIdResponse(BaseSchema):
    """Schema generated for DeleteApiV1SubaccountByIdResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        pix_key (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(examples=["OK"], default=None)
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        examples=["destination@test.com"],
        default=None,
    )


class DeleteApiV1WebhookByIdResponse(BaseSchema):
    """Schema generated for DeleteApiV1WebhookByIdResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    status: str | None = None


class Dispute(BaseSchema):
    """Schema generated for Dispute.

    Attributes:
        status (DisputeStatus | None): Undocumented in the spec.
        name (str | None): The name of the payer who created this dispute.
        email (str | None): The Email of the payer who created this dispute.
        phone_number (str | None): The phone number of the payer who created this
            dispute.
        value (int | None): The value of the dispute.
        dispute_reason (str | None): Reason provided to justify the dispute.
        end_to_end_id (str | None): The endToEndId of the dispute (Is the same of the
            endToEndId transaction related).
        created_at (datetime | None): Undocumented in the spec.
        updated_at (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: DisputeStatus | None = None
    name: str | None = Field(
        description="The name of the payer who created this dispute.",
        default=None,
    )
    email: str | None = Field(
        description="The Email of the payer who created this dispute.",
        default=None,
    )
    phone_number: str | None = Field(
        validation_alias="phoneNumber",
        serialization_alias="phoneNumber",
        description="The phone number of the payer who created this dispute.",
        default=None,
    )
    value: int | None = Field(description="The value of the dispute.", default=None)
    dispute_reason: str | None = Field(
        validation_alias="disputeReason",
        serialization_alias="disputeReason",
        description="Reason provided to justify the dispute.",
        default=None,
    )
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        description=(
            "The endToEndId of the dispute (Is the same of the endToEndId transaction "
            "related)."
        ),
        default=None,
    )
    created_at: datetime | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    updated_at: datetime | None = Field(
        validation_alias="updatedAt",
        serialization_alias="updatedAt",
        default=None,
    )


class DisputePayload(BaseSchema):
    """Schema generated for DisputePayload.

    Attributes:
        status (DisputePayloadStatus | None): Undocumented in the spec.
        name (str): The name of the payer who created this dispute.
        email (str): The Email of the payer who created this dispute.
        phone_number (str): The phone number of the payer who created this dispute.
        value (int): The value of the dispute.
        dispute_reason (str): Reason provided to justify the dispute.
        end_to_end_id (str): The endToEndId of the dispute (Is the same of the
            endToEndId transaction related).
    """

    model_config = ConfigDict(populate_by_name=True)

    status: DisputePayloadStatus | None = None
    name: str = Field(description="The name of the payer who created this dispute.")
    email: str = Field(description="The Email of the payer who created this dispute.")
    phone_number: str = Field(
        validation_alias="phoneNumber",
        serialization_alias="phoneNumber",
        description="The phone number of the payer who created this dispute.",
    )
    value: int = Field(description="The value of the dispute.")
    dispute_reason: str = Field(
        validation_alias="disputeReason",
        serialization_alias="disputeReason",
        description="Reason provided to justify the dispute.",
    )
    end_to_end_id: str = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        description=(
            "The endToEndId of the dispute (Is the same of the endToEndId transaction "
            "related)."
        ),
    )


class Error(BaseSchema):
    """Schema generated for Error.

    Attributes:
        error (str): Undocumented in the spec.
    """

    error: str


class ErrorResponse(BaseSchema):
    """Schema generated for ErrorResponse.

    Attributes:
        error (str | list[dict[str, Any]] | None): Error message
        success (bool | None): Undocumented in the spec.
    """

    error: str | list[dict[str, Any]] | None = Field(
        description="Error message",
        default=None,
    )
    success: bool | None = Field(examples=[False], default=None)


class FundsRecoveryEventsItem(BaseSchema):
    """Schema generated for FundsRecoveryEventsItem.

    Attributes:
        id (str | None): Event id
        event (str | None): Event name
        timestamp (str | None): When the event occurred, in ISO 8601 format
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(description="Event id", default=None)
    event: str | None = Field(description="Event name", default=None)
    timestamp: str | None = Field(
        description="When the event occurred, in ISO 8601 format",
        default=None,
    )


class FundsRecoveryPayload(BaseSchema):
    """Schema generated for FundsRecoveryPayload.

    Attributes:
        transaction_end_to_end_id (str): The endToEndId of the Pix transaction sent from
            your account that you want to recover
        situation_type (FundsRecoverySituationType): The situation that motivated the
            funds recovery:   - `SCAM`: scam (e.g. fake sale, fake invoice, social
            engineering)   - `ACCOUNT_TAKEOVER`: account takeover   - `COERCION`:
            coercion (e.g. kidnapping, extortion)   - `FRAUDULENT_ACCESS`: fraudulent
            access to credentials   - `OTHER`: other kind of fraud   - `UNKNOWN`:
            unknown kind of fraud
        details (str): Detailed description of what happened. The more context, the
            better for the analysis.
    """

    model_config = ConfigDict(populate_by_name=True)

    transaction_end_to_end_id: str = Field(
        validation_alias="transactionEndToEndId",
        serialization_alias="transactionEndToEndId",
        description=(
            "The endToEndId of the Pix transaction sent from your account that you "
            "want to recover"
        ),
    )
    situation_type: FundsRecoverySituationType = Field(
        validation_alias="situationType",
        serialization_alias="situationType",
        description=(
            "The situation that motivated the funds recovery:\n  - `SCAM`: scam (e.g. "
            "fake sale, fake invoice, social engineering)\n  - `ACCOUNT_TAKEOVER`: "
            "account takeover\n  - `COERCION`: coercion (e.g. kidnapping, extortion)\n "
            " - `FRAUDULENT_ACCESS`: fraudulent access to credentials\n  - `OTHER`: "
            "other kind of fraud\n  - `UNKNOWN`: unknown kind of fraud"
        ),
    )
    details: str = Field(
        description=(
            "Detailed description of what happened. The more context, the better for "
            "the analysis."
        ),
    )


class GetApiImageQrcodeBase64ByIdResponse(BaseSchema):
    """Schema generated for GetApiImageQrcodeBase64ByIdResponse.

    Attributes:
        success (bool | None): Undocumented in the spec.
        image_base64 (str | None): Base64 encoded PNG image with data URL format
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    success: bool | None = Field(examples=[True], default=None)
    image_base64: str | None = Field(
        validation_alias="imageBase64",
        serialization_alias="imageBase64",
        description="Base64 encoded PNG image with data URL format",
        examples=["data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA..."],
        default=None,
    )


class GetApiV1AccountRegisterResponseTaxId(BaseSchema):
    """Schema generated for GetApiV1AccountRegisterResponseTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        examples=["12345678901234"],
        default=None,
    )
    type: str | None = Field(examples=["BR_CNPJ"], default=None)


class GetApiV1AccountResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1AccountResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class GetApiV1CashbackFidelityBalanceByTaxIdResponse(BaseSchema):
    """Schema generated for GetApiV1CashbackFidelityBalanceByTaxIdResponse.

    Attributes:
        balance (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    balance: int | None = None
    status: str | None = None


class GetApiV1ChargeResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1ChargeResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class GetApiV1CompanyResponseCompany(BaseSchema):
    """Schema generated for GetApiV1CompanyResponseCompany.

    Attributes:
        official_name (str | None): Undocumented in the spec.
        trade_name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    official_name: str | None = Field(
        validation_alias="officialName",
        serialization_alias="officialName",
        default=None,
    )
    trade_name: str | None = Field(
        validation_alias="tradeName",
        serialization_alias="tradeName",
        default=None,
    )
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class GetApiV1CustomerResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1CustomerResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class GetApiV1DisputeByIdResponseDispute(BaseSchema):
    """Schema generated for GetApiV1DisputeByIdResponseDispute.

    Attributes:
        status (GetApiV1DisputeByIdResponseDisputeStatus | None): Undocumented in the
            spec.
        name (str | None): The name of the payer who created this dispute.
        email (str | None): The Email of the payer who created this dispute.
        phone_number (str | None): The phone number of the payer who created this
            dispute.
        value (str | None): The value of the dispute.
        dispute_reason (str | None): Reason provided to justify the dispute.
        end_to_end_id (str | None): The endToEndId of the dispute (Is the same of the
            endToEndId transaction related).
        type (GetApiV1DisputeByIdResponseDisputeType | None): The type of the dispute
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: GetApiV1DisputeByIdResponseDisputeStatus | None = None
    name: str | None = Field(
        description="The name of the payer who created this dispute.",
        default=None,
    )
    email: str | None = Field(
        description="The Email of the payer who created this dispute.",
        default=None,
    )
    phone_number: str | None = Field(
        validation_alias="phoneNumber",
        serialization_alias="phoneNumber",
        description="The phone number of the payer who created this dispute.",
        default=None,
    )
    value: str | None = Field(description="The value of the dispute.", default=None)
    dispute_reason: str | None = Field(
        validation_alias="disputeReason",
        serialization_alias="disputeReason",
        description="Reason provided to justify the dispute.",
        default=None,
    )
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        description=(
            "The endToEndId of the dispute (Is the same of the endToEndId transaction "
            "related)."
        ),
        default=None,
    )
    type: GetApiV1DisputeByIdResponseDisputeType | None = Field(
        description="The type of the dispute",
        default=None,
    )


class GetApiV1DisputeResponseDisputesItem(BaseSchema):
    """Schema generated for GetApiV1DisputeResponseDisputesItem.

    Attributes:
        status (DisputeStatus | None): Undocumented in the spec.
        name (str | None): The name of the payer who created this dispute.
        email (str | None): The Email of the payer who created this dispute.
        phone_number (str | None): The phone number of the payer who created this
            dispute.
        value (int | None): The value of the dispute.
        dispute_reason (str | None): Reason provided to justify the dispute.
        end_to_end_id (str | None): The endToEndId of the dispute (Is the same of the
            endToEndId transaction related).
        created_at (datetime | None): Undocumented in the spec.
        updated_at (datetime | None): Undocumented in the spec.
        type (GetApiV1DisputeResponseDisputesItemType | None): The type of the dispute
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: DisputeStatus | None = None
    name: str | None = Field(
        description="The name of the payer who created this dispute.",
        default=None,
    )
    email: str | None = Field(
        description="The Email of the payer who created this dispute.",
        default=None,
    )
    phone_number: str | None = Field(
        validation_alias="phoneNumber",
        serialization_alias="phoneNumber",
        description="The phone number of the payer who created this dispute.",
        default=None,
    )
    value: int | None = Field(description="The value of the dispute.", default=None)
    dispute_reason: str | None = Field(
        validation_alias="disputeReason",
        serialization_alias="disputeReason",
        description="Reason provided to justify the dispute.",
        default=None,
    )
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        description=(
            "The endToEndId of the dispute (Is the same of the endToEndId transaction "
            "related)."
        ),
        default=None,
    )
    created_at: datetime | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    updated_at: datetime | None = Field(
        validation_alias="updatedAt",
        serialization_alias="updatedAt",
        default=None,
    )
    type: GetApiV1DisputeResponseDisputesItemType | None = Field(
        description="The type of the dispute",
        default=None,
    )


class GetApiV1DisputeResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1DisputeResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class GetApiV1PartnerAffiliateResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1PartnerAffiliateResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class GetApiV1PartnerCompanyResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1PartnerCompanyResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class GetApiV1PaymentResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1PaymentResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class GetApiV1PixKeysTokensLogsResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1PixKeysTokensLogsResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class GetApiV1PspResponsePspsItem(BaseSchema):
    """Schema generated for GetApiV1PspResponsePspsItem.

    Attributes:
        name (str | None): The name of the PSP
        ispb (str | None): The ISPB code of the PSP
        code (str | None): The code of the PSP
        compe (str | None): The COMPE code of the PSP
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = Field(
        description="The name of the PSP",
        examples=["BCO DO BRASIL S.A."],
        default=None,
    )
    ispb: str | None = Field(
        description="The ISPB code of the PSP",
        examples=["00000000"],
        default=None,
    )
    code: str | None = Field(
        description="The code of the PSP",
        examples=["00000000"],
        default=None,
    )
    compe: str | None = Field(
        description="The COMPE code of the PSP",
        examples=["001"],
        default=None,
    )


class GetApiV1QrcodeStaticResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1QrcodeStaticResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class GetApiV1RefundResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1RefundResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class GetApiV1StablecoinQuoteResponseQuoteAppliedFeesItem(BaseSchema):
    """Schema generated for GetApiV1StablecoinQuoteResponseQuoteAppliedFeesItem.

    Attributes:
        type (str | None): Undocumented in the spec.
        amount (float | None): Undocumented in the spec.
        currency (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    type: str | None = Field(examples=["In Fee"], default=None)
    amount: float | None = Field(examples=[1.5], default=None)
    currency: str | None = Field(examples=["BRL"], default=None)


class GetApiV1StatementResponseItem(BaseSchema):
    """Schema generated for GetApiV1StatementResponseItem.

    Attributes:
        id (str | None): Unique identifier for the ledger entry
        time (datetime | None): Date and time of the transaction
        description (str | None): Description of the transaction
        balance (int | None): Account balance after this transaction
        value (int | None): Transaction amount
        type (str | None): Type of transaction
        transaction_id (str | None): Transaction tracking ID
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = Field(
        description="Unique identifier for the ledger entry",
        examples=["507f1f77bcf86cd799439011"],
        default=None,
    )
    time: datetime | None = Field(
        description="Date and time of the transaction",
        examples=["2023-12-01T10:30:00.000Z"],
        default=None,
    )
    description: str | None = Field(
        description="Description of the transaction",
        examples=["Payment received from customer"],
        default=None,
    )
    balance: int | None = Field(
        description="Account balance after this transaction",
        examples=[1500.5],
        default=None,
    )
    value: int | None = Field(
        description="Transaction amount",
        examples=[100],
        default=None,
    )
    type: str | None = Field(
        description="Type of transaction",
        examples=["CREDIT"],
        default=None,
    )
    transaction_id: str | None = Field(
        validation_alias="transactionId",
        serialization_alias="transactionId",
        description="Transaction tracking ID",
        examples=["txn_123456789"],
        default=None,
    )


class GetApiV1SubaccountByIdStatementResponseItem(BaseSchema):
    """Schema generated for GetApiV1SubaccountByIdStatementResponseItem.

    Attributes:
        id (str | None): Undocumented in the spec.
        time (datetime | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        balance (int | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        type (GetApiV1SubaccountByIdStatementResponseItemType | None): Undocumented in
            the spec.
        operation_type (GetApiV1SubaccountByIdStatementResponseItemOperationTyp | None):
            | operationType           | Descrição
            |
            |-------------------------|---------------------------------------------------|
            | CREDIT                  | Valor recebido
            | | DEBIT                   | Valor enviado
            | | TRANSFER_CREDIT         | Crédito de transferência interna entre
            subcontas  | | TRANSFER_DEBIT          | Débito de transferência interna
            entre subcontas   | | WITHDRAWAL              | Saque iniciado a partir da
            subconta               | | WITHDRAWAL_REVERSAL     | Estorno de um saque
            processado anteriormente      | | WITHDRAWAL_FEE          | Taxa cobrada por
            uma operação de saque            | | WITHDRAWAL_FEE_REVERSAL | Estorno da
            taxa de saque                          |
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = Field(examples=["507f1f77bcf86cd799439011"], default=None)
    time: datetime | None = Field(examples=["2023-12-01T10:30:00.000Z"], default=None)
    description: str | None = Field(
        examples=["Payment received from customer"],
        default=None,
    )
    balance: int | None = Field(examples=[1500], default=None)
    value: int | None = Field(examples=[100], default=None)
    type: GetApiV1SubaccountByIdStatementResponseItemType | None = Field(
        examples=["CREDIT"],
        default=None,
    )
    operation_type: GetApiV1SubaccountByIdStatementResponseItemOperationTyp | None = (
        Field(
            validation_alias="operationType",
            serialization_alias="operationType",
            description=(
                "| operationType           | Descrição                                 "
                "        "
                "|\n|-------------------------|----------------------------------------"
                "-----------|\n| CREDIT                  | Valor recebido              "
                "                      |\n| DEBIT                   | Valor enviado    "
                "                                 |\n| TRANSFER_CREDIT         | "
                "Crédito de transferência interna entre subcontas  |\n| TRANSFER_DEBIT "
                "         | Débito de transferência interna entre subcontas   |\n| "
                "WITHDRAWAL              | Saque iniciado a partir da subconta         "
                "      |\n| WITHDRAWAL_REVERSAL     | Estorno de um saque processado "
                "anteriormente      |\n| WITHDRAWAL_FEE          | Taxa cobrada por "
                "uma operação de saque            |\n| WITHDRAWAL_FEE_REVERSAL | "
                "Estorno da taxa de saque                          |"
            ),
            default=None,
        )
    )


class GetApiV1SubaccountResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1SubaccountResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class GetApiV1SubaccountResponseSubaccountsItem(BaseSchema):
    """Schema generated for GetApiV1SubaccountResponseSubaccountsItem.

    Attributes:
        name (str | None): Undocumented in the spec.
        pix_key (str | None): Undocumented in the spec.
        balance (int | None): Undocumented in the spec.
        withdraw_blocked (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = None
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    balance: int | None = None
    withdraw_blocked: bool | None = Field(
        validation_alias="withdrawBlocked",
        serialization_alias="withdrawBlocked",
        default=None,
    )


class GetApiV1TransactionResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1TransactionResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class GetApiV1WebhookEventsResponseEventsItem(BaseSchema):
    """Schema generated for GetApiV1WebhookEventsResponseEventsItem.

    Attributes:
        name (WebhookEventEnum | None): Available events to register a webhook to listen
            to. If no one selected anyone the default event will be
            OPENPIX:TRANSACTION_RECEIVED.  * **OPENPIX:CHARGE_CREATED** - New charge
            created * **OPENPIX:CHARGE_COMPLETED** - Charge completed is when a charge
            is fully paid * **OPENPIX:CHARGE_EXPIRED** - Charge expired is when a charge
            is not fully paid and expired  * **OPENPIX:TRANSACTION_RECEIVED** - New PIX
            transaction received * **OPENPIX:TRANSACTION_REFUND_RECEIVED** - New PIX
            transaction refund received or refunded  *
            **PIX_TRANSACTION_REFUND_RECEIVED_CONFIRMED** - Pix transaction refund
            received confirmed * **PIX_TRANSACTION_REFUND_SENT_CONFIRMED** - Pix
            transaction refund sent confirmed *
            **PIX_TRANSACTION_REFUND_RECEIVED_REJECTED** - Pix transaction refund
            received rejected * **PIX_TRANSACTION_REFUND_SENT_REJECTED** - Pix
            transaction refund sent rejected  * **OPENPIX:MOVEMENT_CONFIRMED** - Payment
            confirmed is when the pix transaction related to the payment gets confirmed
            * **OPENPIX:MOVEMENT_FAILED** - Payment failed is when the payment gets
            approved and a error occurs * **OPENPIX:MOVEMENT_REMOVED** - Payment was
            removed by a user  * **OPENPIX:MOVEMENT_CONFIRMED** - Movement confirmed *
            **OPENPIX:MOVEMENT_FAILED** - Movement failed * **OPENPIX:MOVEMENT_REMOVED**
            - Movement removed  * **OPENPIX:DISPUTE_CREATED** - Dispute created *
            **OPENPIX:DISPUTE_ACCEPTED** - Dispute accepted *
            **OPENPIX:DISPUTE_REJECTED** - Dispute rejected *
            **OPENPIX:DISPUTE_CANCELED** - Dispute canceled  *
            **ACCOUNT_REGISTER_APPROVED** - Account register approved *
            **ACCOUNT_REGISTER_REJECTED** - Account register rejected *
            **ACCOUNT_REGISTER_PENDING** - Account register pending  *
            **PIX_AUTOMATIC_APPROVED** - Pix Automatic approved *
            **PIX_AUTOMATIC_REJECTED** - Pix Automatic rejected *
            **PIX_AUTOMATIC_COBR_CREATED** - Pix Automatic cobr created *
            **PIX_AUTOMATIC_COBR_APPROVED** - Pix Automatic cobr approved *
            **PIX_AUTOMATIC_COBR_REJECTED** - Pix Automatic cobr rejected *
            **PIX_AUTOMATIC_COBR_COMPLETED** - Pix Automatic cobr completed *
            **PIX_AUTOMATIC_COBR_TRY_REJECTED** - Pix Automatic cobr try rejected *
            **PIX_AUTOMATIC_COBR_TRY_REQUESTED** - Pix Automatic cobr try requested
    """

    model_config = ConfigDict(extra="allow")

    name: WebhookEventEnum | None = Field(
        description=(
            "Available events to register a webhook to listen to. If no one selected "
            "anyone the default event will be OPENPIX:TRANSACTION_RECEIVED.\n\n* "
            "**OPENPIX:CHARGE_CREATED** - New charge created\n* "
            "**OPENPIX:CHARGE_COMPLETED** - Charge completed is when a charge is fully "
            "paid\n* **OPENPIX:CHARGE_EXPIRED** - Charge expired is when a charge is "
            "not fully paid and expired\n\n* **OPENPIX:TRANSACTION_RECEIVED** - New "
            "PIX transaction received\n* **OPENPIX:TRANSACTION_REFUND_RECEIVED** - New "
            "PIX transaction refund received or refunded\n\n* "
            "**PIX_TRANSACTION_REFUND_RECEIVED_CONFIRMED** - Pix transaction refund "
            "received confirmed\n* **PIX_TRANSACTION_REFUND_SENT_CONFIRMED** - Pix "
            "transaction refund sent confirmed\n* "
            "**PIX_TRANSACTION_REFUND_RECEIVED_REJECTED** - Pix transaction refund "
            "received rejected\n* **PIX_TRANSACTION_REFUND_SENT_REJECTED** - Pix "
            "transaction refund sent rejected\n\n* **OPENPIX:MOVEMENT_CONFIRMED** - "
            "Payment confirmed is when the pix transaction related to the payment gets "
            "confirmed\n* **OPENPIX:MOVEMENT_FAILED** - Payment failed is when the "
            "payment gets approved and a error occurs\n* **OPENPIX:MOVEMENT_REMOVED** "
            "- Payment was removed by a user\n\n* **OPENPIX:MOVEMENT_CONFIRMED** - "
            "Movement confirmed\n* **OPENPIX:MOVEMENT_FAILED** - Movement failed\n* "
            "**OPENPIX:MOVEMENT_REMOVED** - Movement removed\n\n* "
            "**OPENPIX:DISPUTE_CREATED** - Dispute created\n* "
            "**OPENPIX:DISPUTE_ACCEPTED** - Dispute accepted\n* "
            "**OPENPIX:DISPUTE_REJECTED** - Dispute rejected\n* "
            "**OPENPIX:DISPUTE_CANCELED** - Dispute canceled\n\n* "
            "**ACCOUNT_REGISTER_APPROVED** - Account register approved\n* "
            "**ACCOUNT_REGISTER_REJECTED** - Account register rejected\n* "
            "**ACCOUNT_REGISTER_PENDING** - Account register pending\n\n* "
            "**PIX_AUTOMATIC_APPROVED** - Pix Automatic approved\n* "
            "**PIX_AUTOMATIC_REJECTED** - Pix Automatic rejected\n* "
            "**PIX_AUTOMATIC_COBR_CREATED** - Pix Automatic cobr created\n* "
            "**PIX_AUTOMATIC_COBR_APPROVED** - Pix Automatic cobr approved\n* "
            "**PIX_AUTOMATIC_COBR_REJECTED** - Pix Automatic cobr rejected\n* "
            "**PIX_AUTOMATIC_COBR_COMPLETED** - Pix Automatic cobr completed\n* "
            "**PIX_AUTOMATIC_COBR_TRY_REJECTED** - Pix Automatic cobr try rejected\n* "
            "**PIX_AUTOMATIC_COBR_TRY_REQUESTED** - Pix Automatic cobr try requested"
        ),
        default=None,
    )


class GetApiV1WebhookIpsResponse(BaseSchema):
    """Schema generated for GetApiV1WebhookIpsResponse.

    Attributes:
        ips (list[str]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    ips: list[str] = Field(default_factory=list)


class GetApiV1WebhookResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for GetApiV1WebhookResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class InstallmentCobrTriesItem(BaseSchema):
    """Schema generated for InstallmentCobrTriesItem.

    Attributes:
        try_status (str | None): Undocumented in the spec.
        finality_purpose (str | None): Undocumented in the spec.
        reject_code (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        requested_execution_date (datetime | None): Undocumented in the spec.
        created_at (datetime | None): Undocumented in the spec.
        updated_at (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    try_status: str | None = Field(
        validation_alias="tryStatus",
        serialization_alias="tryStatus",
        default=None,
    )
    finality_purpose: str | None = Field(
        validation_alias="finalityPurpose",
        serialization_alias="finalityPurpose",
        default=None,
    )
    reject_code: str | None = Field(
        validation_alias="rejectCode",
        serialization_alias="rejectCode",
        default=None,
    )
    value: int | None = None
    requested_execution_date: datetime | None = Field(
        validation_alias="requestedExecutionDate",
        serialization_alias="requestedExecutionDate",
        default=None,
    )
    created_at: datetime | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    updated_at: datetime | None = Field(
        validation_alias="updatedAt",
        serialization_alias="updatedAt",
        default=None,
    )


class KycOnboardingAccountRegisterRepresentativesItemTaxId(BaseSchema):
    """Schema generated for KycOnboardingAccountRegisterRepresentativesItemTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        examples=["XXXXXXXXXXX"],
        default=None,
    )
    type: str | None = Field(examples=["BR:CPF"], default=None)


class KycOnboardingAccountRegisterTaxId(BaseSchema):
    """Schema generated for KycOnboardingAccountRegisterTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        examples=["XXXXXXXXXXXXXX"],
        default=None,
    )
    type: str | None = Field(examples=["BR:CNPJ"], default=None)


class KycOnboardingRepresentative(BaseSchema):
    """Schema generated for KycOnboardingRepresentative.

    Attributes:
        tax_id (str): CPF do representante (com ou sem mascara)
        name (str | None): Nome do representante
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="CPF do representante (com ou sem mascara)",
    )
    name: str | None = Field(description="Nome do representante", default=None)


class NumericWindow(BaseSchema):
    """Numeric window with 90 days, 12 months, and 60 months (values are numeric
    strings).

    Attributes:
        d90 (str | None): Undocumented in the spec.
        m12 (str | None): Undocumented in the spec.
        m60 (str | None): Undocumented in the spec.
    """

    d90: str | None = Field(examples=["1"], default=None)
    m12: str | None = Field(examples=["10"], default=None)
    m60: str | None = Field(examples=["21"], default=None)


class PaginationErrorsItemData(BaseSchema):
    """Schema generated for PaginationErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class PartnerApplicationPayload(BaseSchema):
    """Schema generated for PartnerApplicationPayload.

    Attributes:
        name (str | None): The name that identifies your application.
        is_active (bool | None): Current status of your application.
        type (ApplicationEnumTypePayload | None): Type of the application that you want
            to register. Each of this has some kind of permissions.
        client_id (str | None): The ID of this client application.
        client_secret (str | None): The secret of this client application.
        scopes (list[str]): List of scopes assigned to the application for access
            control.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = Field(
        description="The name that identifies your application.",
        default=None,
    )
    is_active: bool | None = Field(
        validation_alias="isActive",
        serialization_alias="isActive",
        description="Current status of your application.",
        default=None,
    )
    type: ApplicationEnumTypePayload | None = Field(
        description=(
            "Type of the application that you want to register. Each of this has some "
            "kind of permissions."
        ),
        default=None,
    )
    client_id: str | None = Field(
        validation_alias="clientId",
        serialization_alias="clientId",
        description="The ID of this client application.",
        default=None,
    )
    client_secret: str | None = Field(
        validation_alias="clientSecret",
        serialization_alias="clientSecret",
        description="The secret of this client application.",
        default=None,
    )
    scopes: list[str] = Field(
        description="List of scopes assigned to the application for access control.",
        default_factory=list,
    )


class PartyAccount(BaseSchema):
    """Schema generated for PartyAccount.

    Attributes:
        branch (str | None): account branch
        account (str | None): account number
        account_type (str | None): account type
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    branch: str | None = Field(description="account branch", default=None)
    account: str | None = Field(description="account number", default=None)
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        description="account type",
        default=None,
    )


class PartyHolder(BaseSchema):
    """Schema generated for PartyHolder.

    Attributes:
        name (str | None): holder name
        name_friendly (str | None): holder name friendly
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = Field(description="holder name", default=None)
    name_friendly: str | None = Field(
        validation_alias="nameFriendly",
        serialization_alias="nameFriendly",
        description="holder name friendly",
        default=None,
    )


class PartyPsp(BaseSchema):
    """Schema generated for PartyPsp.

    Attributes:
        id (str | None): psp id
        name (str | None): psp name
        code (str | None): psp code
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(description="psp id", default=None)
    name: str | None = Field(description="psp name", default=None)
    code: str | None = Field(description="psp code", default=None)


class PartyTaxId(BaseSchema):
    """Schema generated for PartyTaxId.

    Attributes:
        tax_id (str | None): taxID
        type (str | None): taxID type
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="taxID",
        default=None,
    )
    type: str | None = Field(description="taxID type", default=None)


class PatchApiV1ChargeByIdResponse(BaseSchema):
    """Schema generated for PatchApiV1ChargeByIdResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        id (str | None): the id previously informed to be found and deleted
        expires_date (str | None): new date to expire specfic charge
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = None
    id: str | None = Field(
        description="the id previously informed to be found and deleted",
        default=None,
    )
    expires_date: str | None = Field(
        validation_alias="expiresDate",
        serialization_alias="expiresDate",
        description="new date to expire specfic charge",
        default=None,
    )


class PatchApiV1InvoiceIntegrationBody(BaseSchema):
    """Schema generated for PatchApiV1InvoiceIntegrationBody.

    Attributes:
        is_active (bool): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    is_active: bool = Field(validation_alias="isActive", serialization_alias="isActive")


class PatchApiV1InvoiceIntegrationResponseIntegration(BaseSchema):
    """Schema generated for PatchApiV1InvoiceIntegrationResponseIntegration.

    Attributes:
        id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        is_active (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = None
    type: str | None = None
    status: str | None = None
    is_active: bool | None = Field(
        validation_alias="isActive",
        serialization_alias="isActive",
        default=None,
    )


class PayloadAccount(BaseSchema):
    """Schema generated for PayloadAccount.

    Attributes:
        account_id (str | None): ID of the Account
        is_default (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    account_id: str | None = Field(
        validation_alias="accountId",
        serialization_alias="accountId",
        description="ID of the Account",
        default=None,
    )
    is_default: bool | None = Field(
        validation_alias="isDefault",
        serialization_alias="isDefault",
        default=None,
    )


class PaymentApprovePayload(BaseSchema):
    """Schema generated for PaymentApprovePayload.

    Attributes:
        correlation_id (str | None): the correlation ID of the payment to be approved
    """

    model_config = ConfigDict(populate_by_name=True)

    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="the correlation ID of the payment to be approved",
        default=None,
    )


class PaymentBoletoFinalBeneficiary(BaseSchema):
    """Schema generated for PaymentBoletoFinalBeneficiary.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class PaymentBoletoIssuingEntity(BaseSchema):
    """Schema generated for PaymentBoletoIssuingEntity.

    Attributes:
        code (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    code: str | None = None
    name: str | None = None


class PaymentCreatePayloadBoleto(BaseSchema):
    """Boleto.

    Attributes:
        type (PaymentCreatePayloadPixKeyType): type of the payment
        boleto_barcode (str): the boleto barcode to be paid (44, 47 or 48 digits). The
            amount, due date and beneficiary are resolved from the validated boleto, so
            value and destination are not sent in the body
        correlation_id (str): a unique identifier for your payment
        source_account_id (str | None): optional source account ID to use for the
            payment
        comment (str | None): the comment that will be sent alongside your payment
        metadata (dict[str, Any] | None): additional metadata for the payment (max 30
            keys)
    """

    model_config = ConfigDict(populate_by_name=True)

    type: PaymentCreatePayloadPixKeyType = Field(description="type of the payment")
    boleto_barcode: str = Field(
        validation_alias="boletoBarcode",
        serialization_alias="boletoBarcode",
        description=(
            "the boleto barcode to be paid (44, 47 or 48 digits). The amount, due date "
            "and beneficiary are resolved from the validated boleto, so value and "
            "destination are not sent in the body"
        ),
    )
    correlation_id: str = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="a unique identifier for your payment",
    )
    source_account_id: str | None = Field(
        validation_alias="sourceAccountId",
        serialization_alias="sourceAccountId",
        description="optional source account ID to use for the payment",
        default=None,
    )
    comment: str | None = Field(
        description="the comment that will be sent alongside your payment",
        default=None,
    )
    metadata: dict[str, Any] | None = Field(
        description="additional metadata for the payment (max 30 keys)",
        default=None,
    )


class PaymentCreatePayloadManualAccount(BaseSchema):
    """Schema generated for PaymentCreatePayloadManualAccount.

    Attributes:
        account (str): account number
        branch (str): branch number
        account_type (str): type of the account (e.g., TRAN)
    """

    model_config = ConfigDict(populate_by_name=True)

    account: str = Field(description="account number")
    branch: str = Field(description="branch number")
    account_type: str = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        description="type of the account (e.g., TRAN)",
    )


class PaymentCreatePayloadManualHolderTaxId(BaseSchema):
    """Schema generated for PaymentCreatePayloadManualHolderTaxId.

    Attributes:
        type (str): type of the tax ID (e.g., BR:CNPJ)
        tax_id (str): tax ID number
    """

    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(description="type of the tax ID (e.g., BR:CNPJ)")
    tax_id: str = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="tax ID number",
    )


class PaymentCreatePayloadPixKey(BaseSchema):
    """Pix key.

    Attributes:
        type (PaymentCreatePayloadPixKeyType): type of the payment
        value (int): value of the requested payment in cents
        destination_alias (str): the pix key the payment should be sent to
        destination_alias_type (PaymentCreatePayloadPixKeyDestinationAliasType): the
            type of the pix key the payment should be sent to
        correlation_id (str): a unique identifier for your payment
        pix_key_end_to_end_id (str | None): the end to end id of the pix key used for
            track pix key consultations
        comment (str | None): the comment that will be sent alongside your payment
        metadata (dict[str, Any] | None): additional metadata for the payment (max 30
            keys)
    """

    model_config = ConfigDict(populate_by_name=True)

    type: PaymentCreatePayloadPixKeyType = Field(description="type of the payment")
    value: int = Field(description="value of the requested payment in cents")
    destination_alias: str = Field(
        validation_alias="destinationAlias",
        serialization_alias="destinationAlias",
        description="the pix key the payment should be sent to",
    )
    destination_alias_type: PaymentCreatePayloadPixKeyDestinationAliasType = Field(
        validation_alias="destinationAliasType",
        serialization_alias="destinationAliasType",
        description="the type of the pix key the payment should be sent to",
    )
    correlation_id: str = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="a unique identifier for your payment",
    )
    pix_key_end_to_end_id: str | None = Field(
        validation_alias="pixKeyEndToEndId",
        serialization_alias="pixKeyEndToEndId",
        description=(
            "the end to end id of the pix key used for track pix key consultations"
        ),
        default=None,
    )
    comment: str | None = Field(
        description="the comment that will be sent alongside your payment",
        default=None,
    )
    metadata: dict[str, Any] | None = Field(
        description="additional metadata for the payment (max 30 keys)",
        default=None,
    )


class PaymentCreatePayloadQrCode(BaseSchema):
    """QR Code.

    Attributes:
        type (PaymentCreatePayloadPixKeyType): type of the payment
        qr_code (str): the BR Code (Pix QR Code) string to be paid. The system will
            decode it and extract the destination and value automatically
        value (int | None): optional value in cents. Use this to override the value
            extracted from the QR Code, or to set a value for QR Codes without a fixed
            amount
        correlation_id (str): a unique identifier for your payment
        source_account_id (str | None): optional source account ID to use for the
            payment
        comment (str | None): the comment that will be sent alongside your payment
        metadata (dict[str, Any] | None): additional metadata for the payment (max 30
            keys)
    """

    model_config = ConfigDict(populate_by_name=True)

    type: PaymentCreatePayloadPixKeyType = Field(description="type of the payment")
    qr_code: str = Field(
        validation_alias="qrCode",
        serialization_alias="qrCode",
        description=(
            "the BR Code (Pix QR Code) string to be paid. The system will decode it "
            "and extract the destination and value automatically"
        ),
    )
    value: int | None = Field(
        description=(
            "optional value in cents. Use this to override the value extracted from "
            "the QR Code, or to set a value for QR Codes without a fixed amount"
        ),
        default=None,
    )
    correlation_id: str = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="a unique identifier for your payment",
    )
    source_account_id: str | None = Field(
        validation_alias="sourceAccountId",
        serialization_alias="sourceAccountId",
        description="optional source account ID to use for the payment",
        default=None,
    )
    comment: str | None = Field(
        description="the comment that will be sent alongside your payment",
        default=None,
    )
    metadata: dict[str, Any] | None = Field(
        description="additional metadata for the payment (max 30 keys)",
        default=None,
    )


class PaymentDestination(BaseSchema):
    """Schema generated for PaymentDestination.

    Attributes:
        name (str | None): the name of the payment destination
        tax_id (str | None): the tax id of the payment destination
        pix_key (str | None): the pix key of the payment destination
        bank (str | None): the payment destination bank name
        branch (str | None): the payment destination bank branch
        account (str | None): the payment destination bank account
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = Field(
        description="the name of the payment destination",
        default=None,
    )
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="the tax id of the payment destination",
        default=None,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        description="the pix key of the payment destination",
        default=None,
    )
    bank: str | None = Field(
        description="the payment destination bank name",
        default=None,
    )
    branch: str | None = Field(
        description="the payment destination bank branch",
        default=None,
    )
    account: str | None = Field(
        description="the payment destination bank account",
        default=None,
    )


class PixKey(BaseSchema):
    """Schema generated for PixKey.

    Attributes:
        key (str | None): Undocumented in the spec.
        type (PixKeyType | None): Undocumented in the spec.
        is_default (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    key: str | None = None
    type: PixKeyType | None = None
    is_default: bool | None = Field(
        validation_alias="isDefault",
        serialization_alias="isDefault",
        default=None,
    )


class PixKeyCheckOwner(BaseSchema):
    """Schema generated for PixKeyCheckOwner.

    Attributes:
        account (str | None): Undocumented in the spec.
        branch (str | None): Undocumented in the spec.
        psp (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    account: str | None = None
    branch: str | None = None
    psp: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class PixKeyCreate(BaseSchema):
    """Schema generated for PixKeyCreate.

    Attributes:
        key (str): Undocumented in the spec.
        type (PixKeyCreateType): Undocumented in the spec.
    """

    key: str
    type: PixKeyCreateType


class PixKeyTokens(BaseSchema):
    """Schema generated for PixKeyTokens.

    Attributes:
        tokens (float | None): Undocumented in the spec.
        max_tokens (float | None): Undocumented in the spec.
        next_refresh (str | None): Undocumented in the spec.
        tokens_after_refresh (float | None): Undocumented in the spec.
        refresh_rate (float | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    tokens: float | None = None
    max_tokens: float | None = Field(
        validation_alias="maxTokens",
        serialization_alias="maxTokens",
        default=None,
    )
    next_refresh: str | None = Field(
        validation_alias="nextRefresh",
        serialization_alias="nextRefresh",
        default=None,
    )
    tokens_after_refresh: float | None = Field(
        validation_alias="tokensAfterRefresh",
        serialization_alias="tokensAfterRefresh",
        default=None,
    )
    refresh_rate: float | None = Field(
        validation_alias="refreshRate",
        serialization_alias="refreshRate",
        default=None,
    )


class PixQrCode(BaseSchema):
    """Schema generated for PixQrCode.

    Attributes:
        name (str | None): Undocumented in the spec.
        value (str | None): Undocumented in the spec.
        comment (str | None): Undocumented in the spec.
        br_code (str | None): EMV BRCode to be rendered as a Pix QRCode
        correlation_id (str | None): Your correlation ID to keep track of this pix
            qrcode
        payment_link_id (str | None): Payment Link ID, used on payment link and to
            retrieve qrcode image
        payment_link_url (Any | None): Payment Link URL to be shared with customers
        pix_key (str | None): The pix key that this qrcode is associated with
        qr_code_image (Any | None): QRCode image link URL
        created_at (str | None): Undocumented in the spec.
        updated_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = None
    value: str | None = None
    comment: str | None = None
    br_code: str | None = Field(
        validation_alias="brCode",
        serialization_alias="brCode",
        description="EMV BRCode to be rendered as a Pix QRCode",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID to keep track of this pix qrcode",
        default=None,
    )
    payment_link_id: str | None = Field(
        validation_alias="paymentLinkID",
        serialization_alias="paymentLinkID",
        description=(
            "Payment Link ID, used on payment link and to retrieve qrcode image"
        ),
        default=None,
    )
    payment_link_url: Any | None = Field(
        validation_alias="paymentLinkUrl",
        serialization_alias="paymentLinkUrl",
        description="Payment Link URL to be shared with customers",
        default=None,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        description="The pix key that this qrcode is associated with",
        default=None,
    )
    qr_code_image: Any | None = Field(
        validation_alias="qrCodeImage",
        serialization_alias="qrCodeImage",
        description="QRCode image link URL",
        default=None,
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    updated_at: str | None = Field(
        validation_alias="updatedAt",
        serialization_alias="updatedAt",
        default=None,
    )


class PixQrCodePayload(BaseSchema):
    """Schema generated for PixQrCodePayload.

    Attributes:
        name (str): Name of this pix qrcode
        correlation_id (str | None): Your correlation ID to keep track of this qrcode
        value (int | None): Value in cents of this qrcode
        comment (str | None): Comment to be added in infoPagador
        pix_key (str | None): The pix key that this qrcode is associated with
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(description="Name of this pix qrcode")
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID to keep track of this qrcode",
        default=None,
    )
    value: int | None = Field(description="Value in cents of this qrcode", default=None)
    comment: str | None = Field(
        description="Comment to be added in infoPagador",
        default=None,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        description="The pix key that this qrcode is associated with",
        default=None,
    )


class PostApiV1AccountByAccountIdWithdrawBody(BaseSchema):
    """Schema generated for PostApiV1AccountByAccountIdWithdrawBody.

    Attributes:
        value (int | None): Value in cents
    """

    value: int | None = Field(description="Value in cents", default=None)


class PostApiV1CashbackFidelityBody(BaseSchema):
    """Schema generated for PostApiV1CashbackFidelityBody.

    Attributes:
        tax_id (str | None): Customer taxID (CPF or CNPJ)
        value (int | None): Cashback value in centavos
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="Customer taxID (CPF or CNPJ)",
        default=None,
    )
    value: int | None = Field(description="Cashback value in centavos", default=None)


class PostApiV1CashbackFidelityResponseCashback(BaseSchema):
    """Object representing the existing cashback.

    Attributes:
        value (int | None): Cashback value in centavos
    """

    model_config = ConfigDict(extra="allow")

    value: int | None = Field(description="Cashback value in centavos", default=None)


class PostApiV1DecodeEmvBody(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvBody.

    Attributes:
        emv (str): Raw EMV / PIX QR payload (text)
    """

    emv: str = Field(
        description="Raw EMV / PIX QR payload (text)",
        examples=[
            "00020126780014br.gov.bcb.pix0136f4c6089a-bfde-4c00-a2d9-9eaa584b02190216CobrancaEstatica5204000053039865406546.285802BR5903Pix6008BRASILIA6229052584767c56c2ab4e65b6670de2a80950014br.gov.bcb.pix2573qr-h.sandbox.pix.bcb.gov.br/rest/api/rec/4b62d4a088fe4f51bcb4c64cf078869163044486",
        ],
    )


class PostApiV1DecodeEmvResponseCobLocationPayloadAdditionalI(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseCobLocationPayloadAdditionalI.

    Attributes:
        name (str | None): Undocumented in the spec.
        value (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    value: str | None = None


class PostApiV1DecodeEmvResponseCobLocationPayloadCalendar(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseCobLocationPayloadCalendar.

    Attributes:
        presentation (datetime | None): Undocumented in the spec.
        expiration (int | None): Undocumented in the spec.
        creation (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    presentation: datetime | None = None
    expiration: int | None = None
    creation: datetime | None = None


class PostApiV1DecodeEmvResponseCobLocationPayloadDebtor(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseCobLocationPayloadDebtor.

    Attributes:
        cpf (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    cpf: str | None = None
    name: str | None = None


class PostApiV1DecodeEmvResponseCobLocationPayloadValue(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseCobLocationPayloadValue.

    Attributes:
        original (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    original: str | None = None


class PostApiV1DecodeEmvResponseEmvAdditionalDataFieldTemplat(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseEmvAdditionalDataFieldTemplat.

    Attributes:
        reference_label (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    reference_label: str | None = Field(
        validation_alias="referenceLabel",
        serialization_alias="referenceLabel",
        default=None,
    )


class PostApiV1DecodeEmvResponseEmvMerchantAccountInformation(BaseSchema):
    """Parsed "26"/"00"... Pix merchant account info.

    Attributes:
        gui (str | None): Undocumented in the spec.
        pix_key (str | None): UUID or key when Pix key present
        url (str | None): URL when location points to a COB/REC resource
        additional_information (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    gui: str | None = None
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        description="UUID or key when Pix key present",
        default=None,
    )
    url: str | None = Field(
        description="URL when location points to a COB/REC resource",
        default=None,
    )
    additional_information: str | None = Field(
        validation_alias="additionalInformation",
        serialization_alias="additionalInformation",
        default=None,
    )


class PostApiV1DecodeEmvResponseEmvUnreservedTemplates(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseEmvUnreservedTemplates.

    Attributes:
        gui (str | None): Undocumented in the spec.
        url (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    gui: str | None = None
    url: str | None = None


class PostApiV1DecodeEmvResponseRecLocationPayloadCalendar(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseRecLocationPayloadCalendar.

    Attributes:
        start_date (date | None): Undocumented in the spec.
        periodicity (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    start_date: date | None = Field(
        validation_alias="startDate",
        serialization_alias="startDate",
        default=None,
    )
    periodicity: str | None = None


class PostApiV1DecodeEmvResponseRecLocationPayloadLinkDebtor(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseRecLocationPayloadLinkDebtor.

    Attributes:
        cpf (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    cpf: str | None = None
    name: str | None = None


class PostApiV1DecodeEmvResponseRecLocationPayloadReceiver(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseRecLocationPayloadReceiver.

    Attributes:
        cnpj (str | None): Undocumented in the spec.
        participant_ispb (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    cnpj: str | None = None
    participant_ispb: str | None = Field(
        validation_alias="participantIspb",
        serialization_alias="participantIspb",
        default=None,
    )
    name: str | None = None


class PostApiV1DecodeEmvResponseRecLocationPayloadUpdatesItem(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseRecLocationPayloadUpdatesItem.

    Attributes:
        date (datetime | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    date: datetime | None = None
    status: str | None = None


class PostApiV1DecodeEmvResponseRecLocationPayloadValue(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseRecLocationPayloadValue.

    Attributes:
        value_rec (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    value_rec: str | None = Field(
        validation_alias="valueRec",
        serialization_alias="valueRec",
        default=None,
    )


class PostApiV1DisputeIdEvidenceBodyDocumentsItem(BaseSchema):
    """Schema generated for PostApiV1DisputeIdEvidenceBodyDocumentsItem.

    Attributes:
        url (str | None): Document url
        correlation_id (str | None): Id used by the client
        description (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    url: str | None = Field(description="Document url", min_length=1, default=None)
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Id used by the client",
        default=None,
    )
    description: str | None = None


class PostApiV1DisputeIdEvidenceResponseDocumentsItem(BaseSchema):
    """Schema generated for PostApiV1DisputeIdEvidenceResponseDocumentsItem.

    Attributes:
        url (str | None): Document url
        correlation_id (str | None): Id used by the client
        description (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    url: str | None = Field(
        description="Document url",
        examples=["http://www.url.com"],
        min_length=1,
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Id used by the client",
        examples=["id123456789"],
        default=None,
    )
    description: str | None = Field(
        examples=["description for my document"],
        default=None,
    )


class PostApiV1InstallmentsByIdCobrBody(BaseSchema):
    """Schema generated for PostApiV1InstallmentsByIdCobrBody.

    Attributes:
        value (int | None): Valor da cobrança (Opcional)
    """

    value: int | None = Field(description="Valor da cobrança (Opcional)", default=None)


class PostApiV1InstallmentsByIdCobrRetryBody(BaseSchema):
    """Schema generated for PostApiV1InstallmentsByIdCobrRetryBody.

    Attributes:
        value (int | None): Valor da cobrança (Opcional)
    """

    value: int | None = Field(description="Valor da cobrança (Opcional)", default=None)


class PostApiV1InvoiceByCorrelationIdCancelResponse(BaseSchema):
    """Schema generated for PostApiV1InvoiceByCorrelationIdCancelResponse.

    Attributes:
        success (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    success: bool | None = None


class PostApiV1InvoiceIntegrationBody(BaseSchema):
    """Schema generated for PostApiV1InvoiceIntegrationBody.

    Attributes:
        city_service_code (str | None): Undocumented in the spec.
        municipal_subscription (str | None): Undocumented in the spec.
        rps_number (str | None): Undocumented in the spec.
        special_tax (str | None): Undocumented in the spec.
        tax_regime (str | None): Undocumented in the spec.
        federal_tax_determination (str | None): Undocumented in the spec.
        municipal_tax_determination (str | None): Undocumented in the spec.
        is_portal_nacional (bool | None): Undocumented in the spec.
        is_active (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    city_service_code: str | None = Field(
        validation_alias="cityServiceCode",
        serialization_alias="cityServiceCode",
        default=None,
    )
    municipal_subscription: str | None = Field(
        validation_alias="municipalSubscription",
        serialization_alias="municipalSubscription",
        default=None,
    )
    rps_number: str | None = Field(
        validation_alias="rpsNumber",
        serialization_alias="rpsNumber",
        default=None,
    )
    special_tax: str | None = Field(
        validation_alias="specialTax",
        serialization_alias="specialTax",
        default=None,
    )
    tax_regime: str | None = Field(
        validation_alias="taxRegime",
        serialization_alias="taxRegime",
        default=None,
    )
    federal_tax_determination: str | None = Field(
        validation_alias="federalTaxDetermination",
        serialization_alias="federalTaxDetermination",
        default=None,
    )
    municipal_tax_determination: str | None = Field(
        validation_alias="municipalTaxDetermination",
        serialization_alias="municipalTaxDetermination",
        default=None,
    )
    is_portal_nacional: bool | None = Field(
        validation_alias="isPortalNacional",
        serialization_alias="isPortalNacional",
        default=None,
    )
    is_active: bool | None = Field(
        validation_alias="isActive",
        serialization_alias="isActive",
        default=None,
    )


class PostApiV1InvoiceIntegrationCertificateBody(BaseSchema):
    """Schema generated for PostApiV1InvoiceIntegrationCertificateBody.

    Attributes:
        pcks12 (bytes): The A1 certificate (pkcs12) encoded as a base64 string
        passphrase (str): The certificate password
        test (bool | None): If true, the certificate is not uploaded to NFe.io
            (validation and upload are skipped)
    """

    pcks12: bytes = Field(
        description="The A1 certificate (pkcs12) encoded as a base64 string",
    )
    passphrase: str = Field(description="The certificate password")
    test: bool | None = Field(
        description=(
            "If true, the certificate is not uploaded to NFe.io (validation and upload "
            "are skipped)"
        ),
        default=None,
    )


class PostApiV1InvoiceIntegrationCertificateResponseIntegrati(BaseSchema):
    """Schema generated for PostApiV1InvoiceIntegrationCertificateResponseIntegrati.

    Attributes:
        status (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    status: str | None = None


class PostApiV1InvoiceIntegrationResponseIntegrationMetadataN(BaseSchema):
    """Schema generated for PostApiV1InvoiceIntegrationResponseIntegrationMetadataN.

    Attributes:
        nfeio_company_id (str | None): Undocumented in the spec.
        city_service_code (str | None): Undocumented in the spec.
        nbs (str | None): Undocumented in the spec.
        is_portal_nacional (bool | None): Undocumented in the spec.
        municipal_subscription (str | None): Undocumented in the spec.
        rps_number (str | None): Undocumented in the spec.
        special_tax (str | None): Undocumented in the spec.
        tax_regime (str | None): Undocumented in the spec.
        federal_tax_determination (str | None): Undocumented in the spec.
        municipal_tax_determination (str | None): Undocumented in the spec.
        legal_nature (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    nfeio_company_id: str | None = Field(
        validation_alias="nfeioCompanyId",
        serialization_alias="nfeioCompanyId",
        default=None,
    )
    city_service_code: str | None = Field(
        validation_alias="cityServiceCode",
        serialization_alias="cityServiceCode",
        default=None,
    )
    nbs: str | None = None
    is_portal_nacional: bool | None = Field(
        validation_alias="isPortalNacional",
        serialization_alias="isPortalNacional",
        default=None,
    )
    municipal_subscription: str | None = Field(
        validation_alias="municipalSubscription",
        serialization_alias="municipalSubscription",
        default=None,
    )
    rps_number: str | None = Field(
        validation_alias="rpsNumber",
        serialization_alias="rpsNumber",
        default=None,
    )
    special_tax: str | None = Field(
        validation_alias="specialTax",
        serialization_alias="specialTax",
        default=None,
    )
    tax_regime: str | None = Field(
        validation_alias="taxRegime",
        serialization_alias="taxRegime",
        default=None,
    )
    federal_tax_determination: str | None = Field(
        validation_alias="federalTaxDetermination",
        serialization_alias="federalTaxDetermination",
        default=None,
    )
    municipal_tax_determination: str | None = Field(
        validation_alias="municipalTaxDetermination",
        serialization_alias="municipalTaxDetermination",
        default=None,
    )
    legal_nature: str | None = Field(
        validation_alias="legalNature",
        serialization_alias="legalNature",
        default=None,
    )


class PostApiV1InvoiceIntegrationTestResponseIntegration(BaseSchema):
    """Schema generated for PostApiV1InvoiceIntegrationTestResponseIntegration.

    Attributes:
        id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None


class PostApiV1InvoiceIntegrationTestResponseInvoice(BaseSchema):
    """Schema generated for PostApiV1InvoiceIntegrationTestResponseInvoice.

    Attributes:
        id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None


class PostApiV1InvoiceResponseInvoiceCharge(BaseSchema):
    """Schema generated for PostApiV1InvoiceResponseInvoiceCharge.

    Attributes:
        correlation_id (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        paid_at (datetime | None): Undocumented in the spec.
        date (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    value: int | None = None
    status: str | None = None
    paid_at: datetime | None = Field(
        validation_alias="paidAt",
        serialization_alias="paidAt",
        default=None,
    )
    date: datetime | None = None


class PostApiV1InvoiceResponseInvoiceCustomer(BaseSchema):
    """Schema generated for PostApiV1InvoiceResponseInvoiceCustomer.

    Attributes:
        correlation_id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    name: str | None = None


class PostApiV1PartnerApplicationBodyApplication(BaseSchema):
    """Schema generated for PostApiV1PartnerApplicationBodyApplication.

    Attributes:
        name (str): The name you want to give your application
        type (ApplicationEnumTypePayload): Type of the application that you want to
            register. Each of this has some kind of permissions.
        scopes (list[str]): List of scopes to assign to the application. When provided,
            checkScopes will be enabled automatically.
    """

    name: str = Field(description="The name you want to give your application")
    type: ApplicationEnumTypePayload = Field(
        description=(
            "Type of the application that you want to register. Each of this has some "
            "kind of permissions."
        ),
    )
    scopes: list[str] = Field(
        description=(
            "List of scopes to assign to the application. When provided, checkScopes "
            "will be enabled automatically."
        ),
        default_factory=list,
    )


class PostApiV1PaymentBodyBoleto(BaseSchema):
    """Boleto.

    Attributes:
        type (PaymentCreatePayloadPixKeyType): type of the payment
        boleto_barcode (str): the boleto barcode to be paid (44, 47 or 48 digits). The
            amount, due date and beneficiary are resolved from the validated boleto, so
            value and destination are not sent in the body
        correlation_id (str): a unique identifier for your payment
        source_account_id (str | None): optional source account ID to use for the
            payment
        comment (str | None): the comment that will be sent alongside your payment
        metadata (dict[str, Any] | None): additional metadata for the payment (max 30
            keys)
        auto_approve (bool | None): When true, creates and approves the payment in a
            single call returning the enriched response. Defaults to false.
    """

    model_config = ConfigDict(populate_by_name=True)

    type: PaymentCreatePayloadPixKeyType = Field(description="type of the payment")
    boleto_barcode: str = Field(
        validation_alias="boletoBarcode",
        serialization_alias="boletoBarcode",
        description=(
            "the boleto barcode to be paid (44, 47 or 48 digits). The amount, due date "
            "and beneficiary are resolved from the validated boleto, so value and "
            "destination are not sent in the body"
        ),
    )
    correlation_id: str = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="a unique identifier for your payment",
    )
    source_account_id: str | None = Field(
        validation_alias="sourceAccountId",
        serialization_alias="sourceAccountId",
        description="optional source account ID to use for the payment",
        default=None,
    )
    comment: str | None = Field(
        description="the comment that will be sent alongside your payment",
        default=None,
    )
    metadata: dict[str, Any] | None = Field(
        description="additional metadata for the payment (max 30 keys)",
        default=None,
    )
    auto_approve: bool | None = Field(
        validation_alias="autoApprove",
        serialization_alias="autoApprove",
        description=(
            "When true, creates and approves the payment in a single call returning "
            "the enriched response. Defaults to false."
        ),
        default=None,
    )


class PostApiV1PaymentBodyManualAccount(BaseSchema):
    """Schema generated for PostApiV1PaymentBodyManualAccount.

    Attributes:
        account (str): account number
        branch (str): branch number
        account_type (str): type of the account (e.g., TRAN)
    """

    model_config = ConfigDict(populate_by_name=True)

    account: str = Field(description="account number")
    branch: str = Field(description="branch number")
    account_type: str = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        description="type of the account (e.g., TRAN)",
    )


class PostApiV1PaymentBodyManualHolderTaxId(BaseSchema):
    """Schema generated for PostApiV1PaymentBodyManualHolderTaxId.

    Attributes:
        type (str): type of the tax ID (e.g., BR:CNPJ)
        tax_id (str): tax ID number
    """

    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(description="type of the tax ID (e.g., BR:CNPJ)")
    tax_id: str = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="tax ID number",
    )


class PostApiV1PaymentBodyPixKey(BaseSchema):
    """Pix key.

    Attributes:
        type (PaymentCreatePayloadPixKeyType): type of the payment
        value (int): value of the requested payment in cents
        destination_alias (str): the pix key the payment should be sent to
        destination_alias_type (PaymentCreatePayloadPixKeyDestinationAliasType): the
            type of the pix key the payment should be sent to
        correlation_id (str): a unique identifier for your payment
        pix_key_end_to_end_id (str | None): the end to end id of the pix key used for
            track pix key consultations
        comment (str | None): the comment that will be sent alongside your payment
        metadata (dict[str, Any] | None): additional metadata for the payment (max 30
            keys)
        auto_approve (bool | None): When true, creates and approves the payment in a
            single call returning the enriched response. Defaults to false.
    """

    model_config = ConfigDict(populate_by_name=True)

    type: PaymentCreatePayloadPixKeyType = Field(description="type of the payment")
    value: int = Field(description="value of the requested payment in cents")
    destination_alias: str = Field(
        validation_alias="destinationAlias",
        serialization_alias="destinationAlias",
        description="the pix key the payment should be sent to",
    )
    destination_alias_type: PaymentCreatePayloadPixKeyDestinationAliasType = Field(
        validation_alias="destinationAliasType",
        serialization_alias="destinationAliasType",
        description="the type of the pix key the payment should be sent to",
    )
    correlation_id: str = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="a unique identifier for your payment",
    )
    pix_key_end_to_end_id: str | None = Field(
        validation_alias="pixKeyEndToEndId",
        serialization_alias="pixKeyEndToEndId",
        description=(
            "the end to end id of the pix key used for track pix key consultations"
        ),
        default=None,
    )
    comment: str | None = Field(
        description="the comment that will be sent alongside your payment",
        default=None,
    )
    metadata: dict[str, Any] | None = Field(
        description="additional metadata for the payment (max 30 keys)",
        default=None,
    )
    auto_approve: bool | None = Field(
        validation_alias="autoApprove",
        serialization_alias="autoApprove",
        description=(
            "When true, creates and approves the payment in a single call returning "
            "the enriched response. Defaults to false."
        ),
        default=None,
    )


class PostApiV1PaymentBodyQrCode(BaseSchema):
    """QR Code.

    Attributes:
        type (PaymentCreatePayloadPixKeyType): type of the payment
        qr_code (str): the BR Code (Pix QR Code) string to be paid. The system will
            decode it and extract the destination and value automatically
        value (int | None): optional value in cents. Use this to override the value
            extracted from the QR Code, or to set a value for QR Codes without a fixed
            amount
        correlation_id (str): a unique identifier for your payment
        source_account_id (str | None): optional source account ID to use for the
            payment
        comment (str | None): the comment that will be sent alongside your payment
        metadata (dict[str, Any] | None): additional metadata for the payment (max 30
            keys)
        auto_approve (bool | None): When true, creates and approves the payment in a
            single call returning the enriched response. Defaults to false.
    """

    model_config = ConfigDict(populate_by_name=True)

    type: PaymentCreatePayloadPixKeyType = Field(description="type of the payment")
    qr_code: str = Field(
        validation_alias="qrCode",
        serialization_alias="qrCode",
        description=(
            "the BR Code (Pix QR Code) string to be paid. The system will decode it "
            "and extract the destination and value automatically"
        ),
    )
    value: int | None = Field(
        description=(
            "optional value in cents. Use this to override the value extracted from "
            "the QR Code, or to set a value for QR Codes without a fixed amount"
        ),
        default=None,
    )
    correlation_id: str = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="a unique identifier for your payment",
    )
    source_account_id: str | None = Field(
        validation_alias="sourceAccountId",
        serialization_alias="sourceAccountId",
        description="optional source account ID to use for the payment",
        default=None,
    )
    comment: str | None = Field(
        description="the comment that will be sent alongside your payment",
        default=None,
    )
    metadata: dict[str, Any] | None = Field(
        description="additional metadata for the payment (max 30 keys)",
        default=None,
    )
    auto_approve: bool | None = Field(
        validation_alias="autoApprove",
        serialization_alias="autoApprove",
        description=(
            "When true, creates and approves the payment in a single call returning "
            "the enriched response. Defaults to false."
        ),
        default=None,
    )


class PostApiV1PixKeysCheckBody(BaseSchema):
    """Schema generated for PostApiV1PixKeysCheckBody.

    Attributes:
        pix_key (str): The Pix key to check
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: str = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        description="The Pix key to check",
    )


class PostApiV1StablecoinDepositApproveBody(BaseSchema):
    """Schema generated for PostApiV1StablecoinDepositApproveBody.

    Attributes:
        correlation_id (str): The correlationId supplied when the deposit was created.
    """

    model_config = ConfigDict(populate_by_name=True)

    correlation_id: str = Field(
        validation_alias="correlationId",
        serialization_alias="correlationId",
        description="The correlationId supplied when the deposit was created.",
        min_length=1,
    )


class PostApiV1StablecoinDepositApproveResponse(BaseSchema):
    """Schema generated for PostApiV1StablecoinDepositApproveResponse.

    Attributes:
        status (str | None): The deposit status after approval.
        correlation_id (str | None): Undocumented in the spec.
        deposit_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(
        description="The deposit status after approval.",
        examples=["PROCESSING"],
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationId",
        serialization_alias="correlationId",
        examples=["my-unique-id"],
        default=None,
    )
    deposit_id: str | None = Field(
        validation_alias="depositId",
        serialization_alias="depositId",
        examples=["6650abc1234def567890aaaa"],
        default=None,
    )


class PostApiV1SubaccountByIdCreditBody(BaseSchema):
    """Schema generated for PostApiV1SubaccountByIdCreditBody.

    Attributes:
        value (int): Amount to credit to the account
        description (str | None): Optional description for the credit operation
    """

    value: int = Field(description="Amount to credit to the account")
    description: str | None = Field(
        description="Optional description for the credit operation",
        default=None,
    )


class PostApiV1SubaccountByIdCreditResponse(BaseSchema):
    """Schema generated for PostApiV1SubaccountByIdCreditResponse.

    Attributes:
        pix_key (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        success (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        examples=["subaccount@test.com"],
        default=None,
    )
    value: int | None = Field(examples=[100], default=None)
    description: str | None = Field(examples=["Monthly deposit"], default=None)
    success: str | None = Field(
        examples=["Sub-account withdrawal has been successfully credited, 100"],
        default=None,
    )


class PostApiV1SubaccountByIdDebitBody(BaseSchema):
    """Schema generated for PostApiV1SubaccountByIdDebitBody.

    Attributes:
        value (int): Amount to debit from the account
        description (str | None): Optional description for the debit operation
    """

    value: int = Field(description="Amount to debit from the account")
    description: str | None = Field(
        description="Optional description for the debit operation",
        default=None,
    )


class PostApiV1SubaccountByIdDebitResponse(BaseSchema):
    """Schema generated for PostApiV1SubaccountByIdDebitResponse.

    Attributes:
        pix_key (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        success (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        examples=["subaccount@test.com"],
        default=None,
    )
    value: int | None = Field(examples=[50], default=None)
    description: str | None = Field(examples=["Monthly payment"], default=None)
    success: str | None = Field(
        examples=["Sub-account withdrawal has been successfully debited, 50"],
        default=None,
    )


class Psp(BaseSchema):
    """Schema generated for Psp.

    Attributes:
        name (str | None): The name of the PSP
        ispb (str | None): The ISPB code of the PSP (8 digits)
        compe (str | None): The COMPE code of the PSP (3 digits)
    """

    name: str | None = Field(
        description="The name of the PSP",
        examples=["BCO DO BRASIL S.A."],
        default=None,
    )
    ispb: str | None = Field(
        description="The ISPB code of the PSP (8 digits)",
        examples=["00000000"],
        default=None,
    )
    compe: str | None = Field(
        description="The COMPE code of the PSP (3 digits)",
        examples=["001"],
        default=None,
    )


class PutApiV1InvoiceIntegrationBody(BaseSchema):
    """Schema generated for PutApiV1InvoiceIntegrationBody.

    Attributes:
        city_service_code (str | None): Undocumented in the spec.
        municipal_subscription (str | None): Undocumented in the spec.
        rps_number (str | None): Undocumented in the spec.
        special_tax (str | None): Undocumented in the spec.
        tax_regime (str | None): Undocumented in the spec.
        legal_nature (str | None): Undocumented in the spec.
        federal_tax_determination (str | None): Undocumented in the spec.
        municipal_tax_determination (str | None): Undocumented in the spec.
        is_portal_nacional (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    city_service_code: str | None = Field(
        validation_alias="cityServiceCode",
        serialization_alias="cityServiceCode",
        default=None,
    )
    municipal_subscription: str | None = Field(
        validation_alias="municipalSubscription",
        serialization_alias="municipalSubscription",
        default=None,
    )
    rps_number: str | None = Field(
        validation_alias="rpsNumber",
        serialization_alias="rpsNumber",
        default=None,
    )
    special_tax: str | None = Field(
        validation_alias="specialTax",
        serialization_alias="specialTax",
        default=None,
    )
    tax_regime: str | None = Field(
        validation_alias="taxRegime",
        serialization_alias="taxRegime",
        default=None,
    )
    legal_nature: str | None = Field(
        validation_alias="legalNature",
        serialization_alias="legalNature",
        default=None,
    )
    federal_tax_determination: str | None = Field(
        validation_alias="federalTaxDetermination",
        serialization_alias="federalTaxDetermination",
        default=None,
    )
    municipal_tax_determination: str | None = Field(
        validation_alias="municipalTaxDetermination",
        serialization_alias="municipalTaxDetermination",
        default=None,
    )
    is_portal_nacional: bool | None = Field(
        validation_alias="isPortalNacional",
        serialization_alias="isPortalNacional",
        default=None,
    )


class PutApiV1InvoiceIntegrationResponse(BaseSchema):
    """Schema generated for PutApiV1InvoiceIntegrationResponse.

    Attributes:
        integration (dict[str, Any] | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    integration: dict[str, Any] | None = None


class Refund(BaseSchema):
    """Schema generated for Refund.

    Attributes:
        value (int | None): Undocumented in the spec.
        status (RefundStatus | None): Undocumented in the spec.
        correlation_id (str | None): Your correlation ID to keep track of this refund
        refund_id (str | None): Unique refund ID for this pix refund
        time (str | None): Time of this refund
        comment (str | None): Comment of this refund
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    value: int | None = None
    status: RefundStatus | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID to keep track of this refund",
        default=None,
    )
    refund_id: str | None = Field(
        validation_alias="refundId",
        serialization_alias="refundId",
        description="Unique refund ID for this pix refund",
        default=None,
    )
    time: str | None = Field(description="Time of this refund", default=None)
    comment: str | None = Field(description="Comment of this refund", default=None)


class RefundPayload(BaseSchema):
    """Schema generated for RefundPayload.

    Attributes:
        value (int | None): Undocumented in the spec.
        transaction_end_to_end_id (str | None): Your transaction ID, or endToEnd ID, to
            keep track of this refund
        correlation_id (str | None): Your correlation ID, unique identifier refund
        comment (str | None): Comment of this refund. Maximum length of 140 characters.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int | None = None
    transaction_end_to_end_id: str | None = Field(
        validation_alias="transactionEndToEndId",
        serialization_alias="transactionEndToEndId",
        description="Your transaction ID, or endToEnd ID, to keep track of this refund",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID, unique identifier refund",
        default=None,
    )
    comment: str | None = Field(
        description="Comment of this refund. Maximum length of 140 characters.",
        max_length=140,
        default=None,
    )


class StablecoinDepositError(BaseSchema):
    """Schema generated for StablecoinDepositError.

    Attributes:
        step (str | None): Present when the failure happened during deposit creation.
        error (str | None): Undocumented in the spec.
    """

    step: str | None = Field(
        description="Present when the failure happened during deposit creation.",
        examples=["create"],
        default=None,
    )
    error: str | None = Field(
        examples=["No active stable subaccount. A KYB is required, contact support."],
        default=None,
    )


class StablecoinDepositListItem(BaseSchema):
    """Schema generated for StablecoinDepositListItem.

    Attributes:
        id (str | None): The StableDeposit document id.
        correlation_id (str | None): Idempotency identifier supplied at creation. May be
            absent.
        status (str | None): The deposit status.
        input_amount (int | None): Amount deposited, in cents (BRL).
        input_currency (str | None): Undocumented in the spec.
        output_amount (float | None): Amount of stablecoin received. May be absent until
            quoted.
        output_currency (str | None): Undocumented in the spec.
        fee (int | None): Total applied fee. May be absent.
        created_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(
        description="The StableDeposit document id.",
        examples=["6650abc1234def567890aaaa"],
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationId",
        serialization_alias="correlationId",
        description="Idempotency identifier supplied at creation. May be absent.",
        examples=["my-unique-id"],
        default=None,
    )
    status: str | None = Field(
        description="The deposit status.",
        examples=["PENDING"],
        default=None,
    )
    input_amount: int | None = Field(
        validation_alias="inputAmount",
        serialization_alias="inputAmount",
        description="Amount deposited, in cents (BRL).",
        examples=[10000],
        default=None,
    )
    input_currency: str | None = Field(
        validation_alias="inputCurrency",
        serialization_alias="inputCurrency",
        examples=["BRL"],
        default=None,
    )
    output_amount: float | None = Field(
        validation_alias="outputAmount",
        serialization_alias="outputAmount",
        description="Amount of stablecoin received. May be absent until quoted.",
        examples=[18.45],
        default=None,
    )
    output_currency: str | None = Field(
        validation_alias="outputCurrency",
        serialization_alias="outputCurrency",
        examples=["USDT"],
        default=None,
    )
    fee: int | None = Field(
        description="Total applied fee. May be absent.",
        examples=[50],
        default=None,
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        examples=["2026-06-05T12:00:00.000Z"],
        default=None,
    )


class StablecoinDepositQuote(BaseSchema):
    """Schema generated for StablecoinDepositQuote.

    Attributes:
        input_amount (float | None): Undocumented in the spec.
        input_currency (str | None): Undocumented in the spec.
        output_amount (float | None): Undocumented in the spec.
        output_currency (str | None): Undocumented in the spec.
        rate (float | None): Undocumented in the spec.
        fee (int | None): Total applied fee (Woovi fee + provider applied fees).
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    input_amount: float | None = Field(
        validation_alias="inputAmount",
        serialization_alias="inputAmount",
        examples=[10000],
        default=None,
    )
    input_currency: str | None = Field(
        validation_alias="inputCurrency",
        serialization_alias="inputCurrency",
        examples=["BRL"],
        default=None,
    )
    output_amount: float | None = Field(
        validation_alias="outputAmount",
        serialization_alias="outputAmount",
        examples=[18.45],
        default=None,
    )
    output_currency: str | None = Field(
        validation_alias="outputCurrency",
        serialization_alias="outputCurrency",
        examples=["USDT"],
        default=None,
    )
    rate: float | None = Field(examples=[5.42], default=None)
    fee: int | None = Field(
        description="Total applied fee (Woovi fee + provider applied fees).",
        examples=[50],
        default=None,
    )


class StablecoinDepositRequest(BaseSchema):
    """Schema generated for StablecoinDepositRequest.

    Attributes:
        value (int): Amount to deposit, in cents (BRL). Must be positive.
        currency (StablecoinDepositRequestCurrency): Stablecoin to receive.
        network (StablecoinDepositRequestNetwork | None): Network to receive the
            stablecoin on. Defaults to POLYGON. Must be supported for the chosen
            currency.
        sub_account_id (str | None): Stable subaccount id to use. Optional; resolved
            from the company when omitted.
        correlation_id (str | None): Unique identifier for idempotency. Optional.
        destination_wallet_address (str | None): Explicit destination wallet address for
            the stablecoin. Optional.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int = Field(
        description="Amount to deposit, in cents (BRL). Must be positive.",
        examples=[10000],
    )
    currency: StablecoinDepositRequestCurrency = Field(
        description="Stablecoin to receive.",
        examples=["USDT"],
    )
    network: StablecoinDepositRequestNetwork | None = Field(
        description=(
            "Network to receive the stablecoin on. Defaults to POLYGON. Must be "
            "supported\nfor the chosen currency."
        ),
        examples=["POLYGON"],
        default=None,
    )
    sub_account_id: str | None = Field(
        validation_alias="subAccountId",
        serialization_alias="subAccountId",
        description=(
            "Stable subaccount id to use. Optional; resolved from the company when "
            "omitted."
        ),
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationId",
        serialization_alias="correlationId",
        description="Unique identifier for idempotency. Optional.",
        default=None,
    )
    destination_wallet_address: str | None = Field(
        validation_alias="destinationWalletAddress",
        serialization_alias="destinationWalletAddress",
        description="Explicit destination wallet address for the stablecoin. Optional.",
        default=None,
    )


class StablecoinSubAccountCreateError(BaseSchema):
    """Schema generated for StablecoinSubAccountCreateError.

    Attributes:
        error (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    error: str | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationId",
        serialization_alias="correlationId",
        default=None,
    )


class StablecoinSubAccountCreateRequest(BaseSchema):
    """Schema generated for StablecoinSubAccountCreateRequest.

    Attributes:
        account_register_id (str): The account register id whose KYC data backs the KYB.
        company_bank_account_id (str | None): Company bank account to associate with the
            subaccount. Defaults to the company's default bank account when omitted.
    """

    model_config = ConfigDict(populate_by_name=True)

    account_register_id: str = Field(
        validation_alias="accountRegisterId",
        serialization_alias="accountRegisterId",
        description="The account register id whose KYC data backs the KYB.",
        examples=["6650abc1234def567890aaaa"],
    )
    company_bank_account_id: str | None = Field(
        validation_alias="companyBankAccountId",
        serialization_alias="companyBankAccountId",
        description=(
            "Company bank account to associate with the subaccount. Defaults to the "
            "company's default bank account when omitted."
        ),
        examples=["6650def1234abc567890bbbb"],
        default=None,
    )


class StablecoinSubAccountCreateResponse(BaseSchema):
    """Schema generated for StablecoinSubAccountCreateResponse.

    Attributes:
        sub_account_id (str | None): The provider subaccount id.
        status (str | None): The StableSubAccount status (IN_REVIEW on creation).
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    sub_account_id: str | None = Field(
        validation_alias="subAccountId",
        serialization_alias="subAccountId",
        description="The provider subaccount id.",
        examples=["sub_01HZ..."],
        default=None,
    )
    status: str | None = Field(
        description="The StableSubAccount status (IN_REVIEW on creation).",
        examples=["IN_REVIEW"],
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationId",
        serialization_alias="correlationId",
        examples=["3f1a2b3c-4d5e-6f70-8a9b-0c1d2e3f4a5b"],
        default=None,
    )


class StablecoinSubAccountItem(BaseSchema):
    """Schema generated for StablecoinSubAccountItem.

    Attributes:
        id (str | None): The StableSubAccount document id.
        sub_account_id (str | None): The provider subaccount id. May be absent until
            provisioned.
        account (str | None): The associated company bank account id. May be absent.
        created_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = Field(
        description="The StableSubAccount document id.",
        examples=["6650abc1234def567890aaaa"],
        default=None,
    )
    sub_account_id: str | None = Field(
        validation_alias="subAccountId",
        serialization_alias="subAccountId",
        description="The provider subaccount id. May be absent until provisioned.",
        examples=["sub_01HZ..."],
        default=None,
    )
    account: str | None = Field(
        description="The associated company bank account id. May be absent.",
        examples=["6650def1234abc567890bbbb"],
        default=None,
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        examples=["2026-06-05T12:00:00.000Z"],
        default=None,
    )


class SubAccount(BaseSchema):
    """Schema generated for SubAccount.

    Attributes:
        name (str | None): Name of the sub account
        pix_key (str | None): The pix key for the sub account
        balance (int | None): Number in cents that represent the balance of the sub
            account
        withdraw_blocked (bool | None): Whether withdrawals are blocked for this sub
            account due to an invalid or restricted pix key
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = Field(description="Name of the sub account", default=None)
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        description="The pix key for the sub account",
        default=None,
    )
    balance: int | None = Field(
        description="Number in cents that represent the balance of the sub account",
        default=None,
    )
    withdraw_blocked: bool | None = Field(
        validation_alias="withdrawBlocked",
        serialization_alias="withdrawBlocked",
        description=(
            "Whether withdrawals are blocked for this sub account due to an invalid or "
            "restricted pix key"
        ),
        default=None,
    )


class SubAccountPayload(BaseSchema):
    """Schema generated for SubAccountPayload.

    Attributes:
        pix_key (str | None): The pix key for the sub account
        name (str | None): Name of the sub account
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        description="The pix key for the sub account",
        default=None,
    )
    name: str | None = Field(description="Name of the sub account", default=None)


class SubAccountTransferPayload(BaseSchema):
    """Schema generated for SubAccountTransferPayload.

    Attributes:
        value (int): The value of the transfer in cents
        from_pix_key (str): The transfer origin pix key
        from_pix_key_type (PaymentCreatePayloadPixKeyDestinationAliasType): The transfer
            origin pix key type
        to_pix_key (str): The transfer destination pix key
        to_pix_key_type (PaymentCreatePayloadPixKeyDestinationAliasType): The transfer
            destination pix key type
        correlation_id (str | None): Your correlation ID to keep track of this transfer
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int = Field(description="The value of the transfer in cents")
    from_pix_key: str = Field(
        validation_alias="fromPixKey",
        serialization_alias="fromPixKey",
        description="The transfer origin pix key",
    )
    from_pix_key_type: PaymentCreatePayloadPixKeyDestinationAliasType = Field(
        validation_alias="fromPixKeyType",
        serialization_alias="fromPixKeyType",
        description="The transfer origin pix key type",
    )
    to_pix_key: str = Field(
        validation_alias="toPixKey",
        serialization_alias="toPixKey",
        description="The transfer destination pix key",
    )
    to_pix_key_type: PaymentCreatePayloadPixKeyDestinationAliasType = Field(
        validation_alias="toPixKeyType",
        serialization_alias="toPixKeyType",
        description="The transfer destination pix key type",
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID to keep track of this transfer",
        default=None,
    )


class SubAccountTransferResponsePayloadDestinationSubaccount(BaseSchema):
    """The destination subaccount.

    Attributes:
        name (str | None): Name of the subaccount
        pix_key (str | None): The pix key for the subaccount
        balance (int | None): Number in cents that represent the balance of the
            subaccount
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = Field(description="Name of the subaccount", default=None)
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        description="The pix key for the subaccount",
        default=None,
    )
    balance: int | None = Field(
        description="Number in cents that represent the balance of the subaccount",
        default=None,
    )


class SubAccountTransferResponsePayloadOriginSubaccount(BaseSchema):
    """The destination subaccount.

    Attributes:
        name (str | None): Name of the subaccount
        pix_key (str | None): The pix key for the subaccount
        balance (int | None): Number in cents that represent the balance of the
            subaccount
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = Field(description="Name of the subaccount", default=None)
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        description="The pix key for the subaccount",
        default=None,
    )
    balance: int | None = Field(
        description="Number in cents that represent the balance of the subaccount",
        default=None,
    )


class SubAccountWithdrawPayload(BaseSchema):
    """Schema generated for SubAccountWithdrawPayload.

    Attributes:
        value (int | None): Value of the withdrawal in cents if want to make a partial
            withdrawal
    """

    value: int | None = Field(
        description=(
            "Value of the withdrawal in cents if want to make a partial withdrawal"
        ),
        default=None,
    )


class SubscriptionAddtionalInfoItem(BaseSchema):
    """Schema generated for SubscriptionAddtionalInfoItem.

    Attributes:
        key (str | None): Undocumented in the spec.
        value (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    key: str | None = None
    value: str | None = None


class SubscriptionPayloadAdditionalInfoItem(BaseSchema):
    """Schema generated for SubscriptionPayloadAdditionalInfoItem.

    Attributes:
        key (str | None): Undocumented in the spec.
        value (str | None): Undocumented in the spec.
    """

    key: str | None = None
    value: str | None = None


class SubscriptionPayloadCustomerAddress(BaseSchema):
    """Schema generated for SubscriptionPayloadCustomerAddress.

    Attributes:
        zipcode (str | None): Undocumented in the spec.
        street (str | None): Undocumented in the spec.
        number (str | None): Undocumented in the spec.
        neighborhood (str | None): Undocumented in the spec.
        city (str | None): Undocumented in the spec.
        state (str | None): Undocumented in the spec.
        complement (str | None): Undocumented in the spec.
        country (str | None): Undocumented in the spec.
    """

    zipcode: str | None = None
    street: str | None = None
    number: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    complement: str | None = None
    country: str | None = None


class SubscriptionPayloadPixRecurringOptions(BaseSchema):
    """Pix automatic options.

    Attributes:
        retry_policy (SubscriptionPixRecurringOptionsRetryPolicy | None): Undocumented
            in the spec.
        journey (SubscriptionPixRecurringOptionsJourney | None): Journey type of the pix
            automatic
        minimum_value (int | None): Minimum value for each cobr
    """

    model_config = ConfigDict(populate_by_name=True)

    retry_policy: SubscriptionPixRecurringOptionsRetryPolicy | None = Field(
        validation_alias="retryPolicy",
        serialization_alias="retryPolicy",
        default=None,
    )
    journey: SubscriptionPixRecurringOptionsJourney | None = Field(
        description="Journey type of the pix automatic",
        default=None,
    )
    minimum_value: int | None = Field(
        validation_alias="minimumValue",
        serialization_alias="minimumValue",
        description="Minimum value for each cobr",
        default=None,
    )


class SubscriptionPixRecurringOptions(BaseSchema):
    """Pix automatic options.

    Attributes:
        emv (str | None): QR Code
        status (SubscriptionPixRecurringOptionsStatus | None): Pix automatic status
        retry_policy (SubscriptionPixRecurringOptionsRetryPolicy | None): Undocumented
            in the spec.
        journey (SubscriptionPixRecurringOptionsJourney | None): Journey type of the pix
            automatic
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    emv: str | None = Field(description="QR Code", default=None)
    status: SubscriptionPixRecurringOptionsStatus | None = Field(
        description="Pix automatic status",
        default=None,
    )
    retry_policy: SubscriptionPixRecurringOptionsRetryPolicy | None = Field(
        validation_alias="retryPolicy",
        serialization_alias="retryPolicy",
        default=None,
    )
    journey: SubscriptionPixRecurringOptionsJourney | None = Field(
        description="Journey type of the pix automatic",
        default=None,
    )


class TaxIdObjectPayload(BaseSchema):
    """Schema generated for TaxIdObjectPayload.

    Attributes:
        tax_id (str | None): The tax identifier of your account holder. This should be a
            raw string with only digits.
        type (TaxIdObjectPayloadType | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description=(
            "The tax identifier of your account holder. This should be a raw string "
            "with only digits."
        ),
        default=None,
    )
    type: TaxIdObjectPayloadType | None = None


class TokenBucketLog(BaseSchema):
    """Schema generated for TokenBucketLog.

    Attributes:
        operation (TokenBucketLogOperation | None): Undocumented in the spec.
        reason (str | None): Undocumented in the spec.
        tokens (float | None): Undocumented in the spec.
        tokens_before (float | None): Undocumented in the spec.
        tokens_after (float | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        pix_key (str | None): Undocumented in the spec.
        created_at (datetime | None): Undocumented in the spec.
        updated_at (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    operation: TokenBucketLogOperation | None = None
    reason: str | None = None
    tokens: float | None = None
    tokens_before: float | None = Field(
        validation_alias="tokensBefore",
        serialization_alias="tokensBefore",
        default=None,
    )
    tokens_after: float | None = Field(
        validation_alias="tokensAfter",
        serialization_alias="tokensAfter",
        default=None,
    )
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    created_at: datetime | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    updated_at: datetime | None = Field(
        validation_alias="updatedAt",
        serialization_alias="updatedAt",
        default=None,
    )


class Transaction2(BaseSchema):
    """Schema generated for Transaction2.

    Attributes:
        status (str | None): The status of the transaction
        value (int | None): The value of the transaction in cents
        correlation_id (str | None): The correlation ID of the transaction
        destination_alias (str | None): The pix key of the transaction
        comment (str | None): The comment of the transaction
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(
        description="The status of the transaction",
        default=None,
    )
    value: int | None = Field(
        description="The value of the transaction in cents",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="The correlation ID of the transaction",
        default=None,
    )
    destination_alias: str | None = Field(
        validation_alias="destinationAlias",
        serialization_alias="destinationAlias",
        description="The pix key of the transaction",
        default=None,
    )
    comment: str | None = Field(
        description="The comment of the transaction",
        default=None,
    )


class TransactionWebhookSentItem(BaseSchema):
    """Schema generated for TransactionWebhookSentItem.

    Attributes:
        is_retry (bool | None): Whether this webhook delivery was a retry
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    is_retry: bool | None = Field(
        validation_alias="isRetry",
        serialization_alias="isRetry",
        description="Whether this webhook delivery was a retry",
        default=None,
    )


class TransferCreatePayload(BaseSchema):
    """Schema generated for TransferCreatePayload.

    Attributes:
        value (int | None): value of the transfer in cents
        from_pix_key (str | None): the pix key of the account the value of the transfer
            will come out from
        to_pix_key (str | None): the pix key of the account the value of the transfer
            will go to
        correlation_id (str | None): your correlation ID to keep track of this transfer
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int | None = Field(
        description="value of the transfer in cents",
        default=None,
    )
    from_pix_key: str | None = Field(
        validation_alias="fromPixKey",
        serialization_alias="fromPixKey",
        description=(
            "the pix key of the account the value of the transfer will come out from"
        ),
        default=None,
    )
    to_pix_key: str | None = Field(
        validation_alias="toPixKey",
        serialization_alias="toPixKey",
        description="the pix key of the account the value of the transfer will go to",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="your correlation ID to keep track of this transfer",
        default=None,
    )


class TransferTransaction(BaseSchema):
    """Schema generated for TransferTransaction.

    Attributes:
        value (int | None): value of the transaction generated by the transfer
        time (str | None): the time the transfer happened
        correlation_id (str | None): your correlation ID to keep track of this transfer
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    value: int | None = Field(
        description="value of the transaction generated by the transfer",
        default=None,
    )
    time: str | None = Field(description="the time the transfer happened", default=None)
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="your correlation ID to keep track of this transfer",
        default=None,
    )


class Webhook(BaseSchema):
    """Schema generated for Webhook.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        event (WebhookEventEnum | None): Available events to register a webhook to
            listen to. If no one selected anyone the default event will be
            OPENPIX:TRANSACTION_RECEIVED.  * **OPENPIX:CHARGE_CREATED** - New charge
            created * **OPENPIX:CHARGE_COMPLETED** - Charge completed is when a charge
            is fully paid * **OPENPIX:CHARGE_EXPIRED** - Charge expired is when a charge
            is not fully paid and expired  * **OPENPIX:TRANSACTION_RECEIVED** - New PIX
            transaction received * **OPENPIX:TRANSACTION_REFUND_RECEIVED** - New PIX
            transaction refund received or refunded  *
            **PIX_TRANSACTION_REFUND_RECEIVED_CONFIRMED** - Pix transaction refund
            received confirmed * **PIX_TRANSACTION_REFUND_SENT_CONFIRMED** - Pix
            transaction refund sent confirmed *
            **PIX_TRANSACTION_REFUND_RECEIVED_REJECTED** - Pix transaction refund
            received rejected * **PIX_TRANSACTION_REFUND_SENT_REJECTED** - Pix
            transaction refund sent rejected  * **OPENPIX:MOVEMENT_CONFIRMED** - Payment
            confirmed is when the pix transaction related to the payment gets confirmed
            * **OPENPIX:MOVEMENT_FAILED** - Payment failed is when the payment gets
            approved and a error occurs * **OPENPIX:MOVEMENT_REMOVED** - Payment was
            removed by a user  * **OPENPIX:MOVEMENT_CONFIRMED** - Movement confirmed *
            **OPENPIX:MOVEMENT_FAILED** - Movement failed * **OPENPIX:MOVEMENT_REMOVED**
            - Movement removed  * **OPENPIX:DISPUTE_CREATED** - Dispute created *
            **OPENPIX:DISPUTE_ACCEPTED** - Dispute accepted *
            **OPENPIX:DISPUTE_REJECTED** - Dispute rejected *
            **OPENPIX:DISPUTE_CANCELED** - Dispute canceled  *
            **ACCOUNT_REGISTER_APPROVED** - Account register approved *
            **ACCOUNT_REGISTER_REJECTED** - Account register rejected *
            **ACCOUNT_REGISTER_PENDING** - Account register pending  *
            **PIX_AUTOMATIC_APPROVED** - Pix Automatic approved *
            **PIX_AUTOMATIC_REJECTED** - Pix Automatic rejected *
            **PIX_AUTOMATIC_COBR_CREATED** - Pix Automatic cobr created *
            **PIX_AUTOMATIC_COBR_APPROVED** - Pix Automatic cobr approved *
            **PIX_AUTOMATIC_COBR_REJECTED** - Pix Automatic cobr rejected *
            **PIX_AUTOMATIC_COBR_COMPLETED** - Pix Automatic cobr completed *
            **PIX_AUTOMATIC_COBR_TRY_REJECTED** - Pix Automatic cobr try rejected *
            **PIX_AUTOMATIC_COBR_TRY_REQUESTED** - Pix Automatic cobr try requested
        url (str | None): Undocumented in the spec.
        authorization (str | None): Undocumented in the spec.
        is_active (bool | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        updated_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = None
    name: str | None = None
    event: WebhookEventEnum | None = Field(
        description=(
            "Available events to register a webhook to listen to. If no one selected "
            "anyone the default event will be OPENPIX:TRANSACTION_RECEIVED.\n\n* "
            "**OPENPIX:CHARGE_CREATED** - New charge created\n* "
            "**OPENPIX:CHARGE_COMPLETED** - Charge completed is when a charge is fully "
            "paid\n* **OPENPIX:CHARGE_EXPIRED** - Charge expired is when a charge is "
            "not fully paid and expired\n\n* **OPENPIX:TRANSACTION_RECEIVED** - New "
            "PIX transaction received\n* **OPENPIX:TRANSACTION_REFUND_RECEIVED** - New "
            "PIX transaction refund received or refunded\n\n* "
            "**PIX_TRANSACTION_REFUND_RECEIVED_CONFIRMED** - Pix transaction refund "
            "received confirmed\n* **PIX_TRANSACTION_REFUND_SENT_CONFIRMED** - Pix "
            "transaction refund sent confirmed\n* "
            "**PIX_TRANSACTION_REFUND_RECEIVED_REJECTED** - Pix transaction refund "
            "received rejected\n* **PIX_TRANSACTION_REFUND_SENT_REJECTED** - Pix "
            "transaction refund sent rejected\n\n* **OPENPIX:MOVEMENT_CONFIRMED** - "
            "Payment confirmed is when the pix transaction related to the payment gets "
            "confirmed\n* **OPENPIX:MOVEMENT_FAILED** - Payment failed is when the "
            "payment gets approved and a error occurs\n* **OPENPIX:MOVEMENT_REMOVED** "
            "- Payment was removed by a user\n\n* **OPENPIX:MOVEMENT_CONFIRMED** - "
            "Movement confirmed\n* **OPENPIX:MOVEMENT_FAILED** - Movement failed\n* "
            "**OPENPIX:MOVEMENT_REMOVED** - Movement removed\n\n* "
            "**OPENPIX:DISPUTE_CREATED** - Dispute created\n* "
            "**OPENPIX:DISPUTE_ACCEPTED** - Dispute accepted\n* "
            "**OPENPIX:DISPUTE_REJECTED** - Dispute rejected\n* "
            "**OPENPIX:DISPUTE_CANCELED** - Dispute canceled\n\n* "
            "**ACCOUNT_REGISTER_APPROVED** - Account register approved\n* "
            "**ACCOUNT_REGISTER_REJECTED** - Account register rejected\n* "
            "**ACCOUNT_REGISTER_PENDING** - Account register pending\n\n* "
            "**PIX_AUTOMATIC_APPROVED** - Pix Automatic approved\n* "
            "**PIX_AUTOMATIC_REJECTED** - Pix Automatic rejected\n* "
            "**PIX_AUTOMATIC_COBR_CREATED** - Pix Automatic cobr created\n* "
            "**PIX_AUTOMATIC_COBR_APPROVED** - Pix Automatic cobr approved\n* "
            "**PIX_AUTOMATIC_COBR_REJECTED** - Pix Automatic cobr rejected\n* "
            "**PIX_AUTOMATIC_COBR_COMPLETED** - Pix Automatic cobr completed\n* "
            "**PIX_AUTOMATIC_COBR_TRY_REJECTED** - Pix Automatic cobr try rejected\n* "
            "**PIX_AUTOMATIC_COBR_TRY_REQUESTED** - Pix Automatic cobr try requested"
        ),
        default=None,
    )
    url: str | None = None
    authorization: str | None = None
    is_active: bool | None = Field(
        validation_alias="isActive",
        serialization_alias="isActive",
        default=None,
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    updated_at: str | None = Field(
        validation_alias="updatedAt",
        serialization_alias="updatedAt",
        default=None,
    )


class WebhookPayload(BaseSchema):
    """Schema generated for WebhookPayload.

    Attributes:
        name (str | None): Undocumented in the spec.
        event (WebhookEventEnum | None): Available events to register a webhook to
            listen to. If no one selected anyone the default event will be
            OPENPIX:TRANSACTION_RECEIVED.  * **OPENPIX:CHARGE_CREATED** - New charge
            created * **OPENPIX:CHARGE_COMPLETED** - Charge completed is when a charge
            is fully paid * **OPENPIX:CHARGE_EXPIRED** - Charge expired is when a charge
            is not fully paid and expired  * **OPENPIX:TRANSACTION_RECEIVED** - New PIX
            transaction received * **OPENPIX:TRANSACTION_REFUND_RECEIVED** - New PIX
            transaction refund received or refunded  *
            **PIX_TRANSACTION_REFUND_RECEIVED_CONFIRMED** - Pix transaction refund
            received confirmed * **PIX_TRANSACTION_REFUND_SENT_CONFIRMED** - Pix
            transaction refund sent confirmed *
            **PIX_TRANSACTION_REFUND_RECEIVED_REJECTED** - Pix transaction refund
            received rejected * **PIX_TRANSACTION_REFUND_SENT_REJECTED** - Pix
            transaction refund sent rejected  * **OPENPIX:MOVEMENT_CONFIRMED** - Payment
            confirmed is when the pix transaction related to the payment gets confirmed
            * **OPENPIX:MOVEMENT_FAILED** - Payment failed is when the payment gets
            approved and a error occurs * **OPENPIX:MOVEMENT_REMOVED** - Payment was
            removed by a user  * **OPENPIX:MOVEMENT_CONFIRMED** - Movement confirmed *
            **OPENPIX:MOVEMENT_FAILED** - Movement failed * **OPENPIX:MOVEMENT_REMOVED**
            - Movement removed  * **OPENPIX:DISPUTE_CREATED** - Dispute created *
            **OPENPIX:DISPUTE_ACCEPTED** - Dispute accepted *
            **OPENPIX:DISPUTE_REJECTED** - Dispute rejected *
            **OPENPIX:DISPUTE_CANCELED** - Dispute canceled  *
            **ACCOUNT_REGISTER_APPROVED** - Account register approved *
            **ACCOUNT_REGISTER_REJECTED** - Account register rejected *
            **ACCOUNT_REGISTER_PENDING** - Account register pending  *
            **PIX_AUTOMATIC_APPROVED** - Pix Automatic approved *
            **PIX_AUTOMATIC_REJECTED** - Pix Automatic rejected *
            **PIX_AUTOMATIC_COBR_CREATED** - Pix Automatic cobr created *
            **PIX_AUTOMATIC_COBR_APPROVED** - Pix Automatic cobr approved *
            **PIX_AUTOMATIC_COBR_REJECTED** - Pix Automatic cobr rejected *
            **PIX_AUTOMATIC_COBR_COMPLETED** - Pix Automatic cobr completed *
            **PIX_AUTOMATIC_COBR_TRY_REJECTED** - Pix Automatic cobr try rejected *
            **PIX_AUTOMATIC_COBR_TRY_REQUESTED** - Pix Automatic cobr try requested
        url (str | None): Undocumented in the spec.
        authorization (str | None): Undocumented in the spec.
        is_active (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    event: WebhookEventEnum | None = Field(
        description=(
            "Available events to register a webhook to listen to. If no one selected "
            "anyone the default event will be OPENPIX:TRANSACTION_RECEIVED.\n\n* "
            "**OPENPIX:CHARGE_CREATED** - New charge created\n* "
            "**OPENPIX:CHARGE_COMPLETED** - Charge completed is when a charge is fully "
            "paid\n* **OPENPIX:CHARGE_EXPIRED** - Charge expired is when a charge is "
            "not fully paid and expired\n\n* **OPENPIX:TRANSACTION_RECEIVED** - New "
            "PIX transaction received\n* **OPENPIX:TRANSACTION_REFUND_RECEIVED** - New "
            "PIX transaction refund received or refunded\n\n* "
            "**PIX_TRANSACTION_REFUND_RECEIVED_CONFIRMED** - Pix transaction refund "
            "received confirmed\n* **PIX_TRANSACTION_REFUND_SENT_CONFIRMED** - Pix "
            "transaction refund sent confirmed\n* "
            "**PIX_TRANSACTION_REFUND_RECEIVED_REJECTED** - Pix transaction refund "
            "received rejected\n* **PIX_TRANSACTION_REFUND_SENT_REJECTED** - Pix "
            "transaction refund sent rejected\n\n* **OPENPIX:MOVEMENT_CONFIRMED** - "
            "Payment confirmed is when the pix transaction related to the payment gets "
            "confirmed\n* **OPENPIX:MOVEMENT_FAILED** - Payment failed is when the "
            "payment gets approved and a error occurs\n* **OPENPIX:MOVEMENT_REMOVED** "
            "- Payment was removed by a user\n\n* **OPENPIX:MOVEMENT_CONFIRMED** - "
            "Movement confirmed\n* **OPENPIX:MOVEMENT_FAILED** - Movement failed\n* "
            "**OPENPIX:MOVEMENT_REMOVED** - Movement removed\n\n* "
            "**OPENPIX:DISPUTE_CREATED** - Dispute created\n* "
            "**OPENPIX:DISPUTE_ACCEPTED** - Dispute accepted\n* "
            "**OPENPIX:DISPUTE_REJECTED** - Dispute rejected\n* "
            "**OPENPIX:DISPUTE_CANCELED** - Dispute canceled\n\n* "
            "**ACCOUNT_REGISTER_APPROVED** - Account register approved\n* "
            "**ACCOUNT_REGISTER_REJECTED** - Account register rejected\n* "
            "**ACCOUNT_REGISTER_PENDING** - Account register pending\n\n* "
            "**PIX_AUTOMATIC_APPROVED** - Pix Automatic approved\n* "
            "**PIX_AUTOMATIC_REJECTED** - Pix Automatic rejected\n* "
            "**PIX_AUTOMATIC_COBR_CREATED** - Pix Automatic cobr created\n* "
            "**PIX_AUTOMATIC_COBR_APPROVED** - Pix Automatic cobr approved\n* "
            "**PIX_AUTOMATIC_COBR_REJECTED** - Pix Automatic cobr rejected\n* "
            "**PIX_AUTOMATIC_COBR_COMPLETED** - Pix Automatic cobr completed\n* "
            "**PIX_AUTOMATIC_COBR_TRY_REJECTED** - Pix Automatic cobr try rejected\n* "
            "**PIX_AUTOMATIC_COBR_TRY_REQUESTED** - Pix Automatic cobr try requested"
        ),
        default=None,
    )
    url: str | None = None
    authorization: str | None = None
    is_active: bool | None = Field(
        validation_alias="isActive",
        serialization_alias="isActive",
        default=None,
    )


class WithdrawTransaction(BaseSchema):
    """Schema generated for WithdrawTransaction.

    Attributes:
        end_to_end_id (str | None): ID of the Withdraw Transaction
        value (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        description="ID of the Withdraw Transaction",
        default=None,
    )
    value: str | None = None


class AccountRegister(BaseSchema):
    """Schema generated for AccountRegister.

    Attributes:
        official_name (str | None): Official name of the company
        trade_name (str | None): Trade name of the company
        tax_id (AccountRegisterTaxId | None): Undocumented in the spec.
        status (str | None): Status of the account registration
        annual_revenue (float | None): Annual revenue of the company
    """

    model_config = ConfigDict(populate_by_name=True)

    official_name: str | None = Field(
        validation_alias="officialName",
        serialization_alias="officialName",
        description="Official name of the company",
        default=None,
    )
    trade_name: str | None = Field(
        validation_alias="tradeName",
        serialization_alias="tradeName",
        description="Trade name of the company",
        default=None,
    )
    tax_id: AccountRegisterTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    status: str | None = Field(
        description="Status of the account registration",
        default=None,
    )
    annual_revenue: float | None = Field(
        validation_alias="annualRevenue",
        serialization_alias="annualRevenue",
        description="Annual revenue of the company",
        default=None,
    )


class AccountRegisterResponse(BaseSchema):
    """Schema generated for AccountRegisterResponse.

    Attributes:
        official_name (str | None): Official name of the company
        trade_name (str | None): Trade name of the company
        tax_id (AccountRegisterResponseTaxId | None): Undocumented in the spec.
        status (str | None): Status of the account registration
    """

    model_config = ConfigDict(populate_by_name=True)

    official_name: str | None = Field(
        validation_alias="officialName",
        serialization_alias="officialName",
        description="Official name of the company",
        default=None,
    )
    trade_name: str | None = Field(
        validation_alias="tradeName",
        serialization_alias="tradeName",
        description="Trade name of the company",
        default=None,
    )
    tax_id: AccountRegisterResponseTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    status: str | None = Field(
        description="Status of the account registration",
        default=None,
    )


class ApplicationPayload(BaseSchema):
    """Schema generated for ApplicationPayload.

    Attributes:
        account_id (str | None): The ID of the company bank account
        application (ApplicationPayloadApplication | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    account_id: str | None = Field(
        validation_alias="accountId",
        serialization_alias="accountId",
        description="The ID of the company bank account",
        default=None,
    )
    application: ApplicationPayloadApplication | None = None


class BoletoValidatedInfo(BaseSchema):
    """Schema generated for BoletoValidatedInfo.

    Attributes:
        barcode (str): Normalized boleto barcode.
        digitable (str | None): Digitable line, when the provider returns it.
        expires_date (str | None): Due date (ISO 8601), when available.
        total_value (int): Total amount to pay, in cents.
        issuing_entity (BoletoValidatedInfoIssuingEntity | None): Issuing institution,
            when available.
        final_beneficiary (BoletoValidatedInfoFinalBeneficiary | None): Final
            beneficiary, when available.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    barcode: str = Field(
        description="Normalized boleto barcode.",
        examples=["34195148200000003001095517077320772982609000"],
    )
    digitable: str | None = Field(
        description="Digitable line, when the provider returns it.",
        default=None,
    )
    expires_date: str | None = Field(
        validation_alias="expiresDate",
        serialization_alias="expiresDate",
        description="Due date (ISO 8601), when available.",
        examples=["2026-06-27T02:59:59.999Z"],
        default=None,
    )
    total_value: int = Field(
        validation_alias="totalValue",
        serialization_alias="totalValue",
        description="Total amount to pay, in cents.",
        examples=[300],
    )
    issuing_entity: BoletoValidatedInfoIssuingEntity | None = Field(
        validation_alias="issuingEntity",
        serialization_alias="issuingEntity",
        description="Issuing institution, when available.",
        default=None,
    )
    final_beneficiary: BoletoValidatedInfoFinalBeneficiary | None = Field(
        validation_alias="finalBeneficiary",
        serialization_alias="finalBeneficiary",
        description="Final beneficiary, when available.",
        default=None,
    )


class ChargePayloadDiscountSettings(BaseSchema):
    """Discount settings for the charge. This property is only considered for charges of
    type OVERDUE.

    Attributes:
        modality (ChargePayloadDiscountSettingsModality | None): Modality of discount to
            be applied
        discount_fixed_date (list[ChargePayloadDiscountSettingsDiscountFixedDateItem]):
            Absolute discounts applied to charge. Required when `modality` is
            `FIXED_VALUE_UNTIL_SPECIFIED_DATE` or `PERCENTAGE_UNTIL_SPECIFIED_DATE`.
            Must contain at least one entry.
        value (int | None): Discount value. Required when `modality` is one of the
            advance-day modalities. Must be `>= 1`. Units depend on modality:   -
            `VALUE_PER_RUNNING_DAY_ADVANCE`: cents per running day.   -
            `VALUE_PER_BUSINESS_DAY_ADVANCE`: cents per business day.   -
            `PERCENTAGE_PER_RUNNING_DAY_ADVANCE`, `PERCENTAGE_PER_BUSINESS_DAY_ADVANCE`:
            basis points (e.g. 100 = 1.00%).
    """

    model_config = ConfigDict(populate_by_name=True)

    modality: ChargePayloadDiscountSettingsModality | None = Field(
        description="Modality of discount to be applied",
        default=None,
    )
    discount_fixed_date: list[ChargePayloadDiscountSettingsDiscountFixedDateItem] = (
        Field(
            validation_alias="discountFixedDate",
            serialization_alias="discountFixedDate",
            description=(
                "Absolute discounts applied to charge. Required when `modality` is "
                "`FIXED_VALUE_UNTIL_SPECIFIED_DATE` or "
                "`PERCENTAGE_UNTIL_SPECIFIED_DATE`. Must contain at least one entry."
            ),
            min_length=1,
            default_factory=list,
        )
    )
    value: int | None = Field(
        description=(
            "Discount value. Required when `modality` is one of the advance-day "
            "modalities. Must be `>= 1`.\nUnits depend on modality:\n  - "
            "`VALUE_PER_RUNNING_DAY_ADVANCE`: cents per running day.\n  - "
            "`VALUE_PER_BUSINESS_DAY_ADVANCE`: cents per business day.\n  - "
            "`PERCENTAGE_PER_RUNNING_DAY_ADVANCE`, "
            "`PERCENTAGE_PER_BUSINESS_DAY_ADVANCE`: basis points (e.g. 100 = 1.00%)."
        ),
        ge=1,
        default=None,
    )


class ChargePaymentMethodsPix(BaseSchema):
    """Schema generated for ChargePaymentMethodsPix.

    Attributes:
        method (str | None): Undocumented in the spec.
        transaction_id (str | None): Undocumented in the spec.
        identifier (str | None): Undocumented in the spec.
        additional_info (list[ChargePaymentMethodsPixAdditionalInfoItem]): Undocumented
            in the spec.
        fee (int | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        tx_id (str | None): Undocumented in the spec.
        br_code (str | None): Undocumented in the spec.
        qr_code_image (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    method: str | None = None
    transaction_id: str | None = Field(
        validation_alias="transactionID",
        serialization_alias="transactionID",
        default=None,
    )
    identifier: str | None = None
    additional_info: list[ChargePaymentMethodsPixAdditionalInfoItem] = Field(
        validation_alias="additionalInfo",
        serialization_alias="additionalInfo",
        default_factory=list,
    )
    fee: int | None = None
    value: int | None = None
    status: str | None = None
    tx_id: str | None = Field(
        validation_alias="txId",
        serialization_alias="txId",
        default=None,
    )
    br_code: str | None = Field(
        validation_alias="brCode",
        serialization_alias="brCode",
        default=None,
    )
    qr_code_image: str | None = Field(
        validation_alias="qrCodeImage",
        serialization_alias="qrCodeImage",
        default=None,
    )


class CompanyBankAccount(BaseSchema):
    """Schema generated for CompanyBankAccount.

    Attributes:
        account_id (str | None): ID of the Account
        is_default (bool | None): Undocumented in the spec.
        balance (CompanyBankAccountBalance | None): Undocumented in the spec.
        tax_id (str | None): Tax ID associated with the account
        official_name (str | None): Official name of the account holder
        trade_name (str | None): Trade name of the account holder
        branch (str | None): Bank branch number
        account (str | None): Bank account number
        account_name (str | None): Name of the account
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    account_id: str | None = Field(
        validation_alias="accountId",
        serialization_alias="accountId",
        description="ID of the Account",
        default=None,
    )
    is_default: bool | None = Field(
        validation_alias="isDefault",
        serialization_alias="isDefault",
        default=None,
    )
    balance: CompanyBankAccountBalance | None = None
    tax_id: str | None = Field(
        validation_alias="taxId",
        serialization_alias="taxId",
        description="Tax ID associated with the account",
        default=None,
    )
    official_name: str | None = Field(
        validation_alias="officialName",
        serialization_alias="officialName",
        description="Official name of the account holder",
        default=None,
    )
    trade_name: str | None = Field(
        validation_alias="tradeName",
        serialization_alias="tradeName",
        description="Trade name of the account holder",
        default=None,
    )
    branch: str | None = Field(description="Bank branch number", default=None)
    account: str | None = Field(description="Bank account number", default=None)
    account_name: str | None = Field(
        validation_alias="accountName",
        serialization_alias="accountName",
        description="Name of the account",
        default=None,
    )


class CompanyObjectPayload(BaseSchema):
    """Schema generated for CompanyObjectPayload.

    Attributes:
        id (str | None): The ID of the company that is related to this preregistration.
        name (str | None): The name of the company that is related to this
            preregistration.
        tax_id (TaxIdObjectPayload | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = Field(
        description="The ID of the company that is related to this preregistration.",
        default=None,
    )
    name: str | None = Field(
        description="The name of the company that is related to this preregistration.",
        default=None,
    )
    tax_id: TaxIdObjectPayload | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class CompanyResponse(BaseSchema):
    """Schema generated for CompanyResponse.

    Attributes:
        company (Company | None): Undocumented in the spec.
    """

    company: Company | None = None


class Customer(BaseSchema):
    """Schema generated for Customer.

    Attributes:
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone (str | None): Undocumented in the spec.
        tax_id (CustomerTaxId | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        address (CustomerAddress | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    tax_id: CustomerTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    address: CustomerAddress | None = None


class CustomerPatchPayload(BaseSchema):
    """Schema generated for CustomerPatchPayload.

    Attributes:
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
        address (CustomerPatchPayloadAddress | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    address: CustomerPatchPayloadAddress | None = None


class CustomerPayload(BaseSchema):
    """Customer field is not required. However, if you decide to send it, you must send
    at least one of the following combinations, name + taxID or name + email or name +
    phone.

    Attributes:
        name (str): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        address (CustomerPayloadAddress | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    email: str | None = None
    phone: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    address: CustomerPayloadAddress | None = None


class FraudMarkers(BaseSchema):
    """Schema generated for FraudMarkers.

    Attributes:
        watermark (datetime | None): Undocumented in the spec.
        application_frauds (NumericWindow | None): Numeric window with 90 days, 12
            months, and 60 months (values are numeric strings).
        mule_accounts (NumericWindow | None): Numeric window with 90 days, 12 months,
            and 60 months (values are numeric strings).
        scammer_accounts (NumericWindow | None): Numeric window with 90 days, 12 months,
            and 60 months (values are numeric strings).
        other_frauds (NumericWindow | None): Numeric window with 90 days, 12 months, and
            60 months (values are numeric strings).
        unknown_frauds (NumericWindow | None): Numeric window with 90 days, 12 months,
            and 60 months (values are numeric strings).
        total_fraud_transaction_amount (NumericWindow | None): Numeric window with 90
            days, 12 months, and 60 months (values are numeric strings).
        distinct_fraud_reporters (NumericWindow | None): Numeric window with 90 days, 12
            months, and 60 months (values are numeric strings).
    """

    model_config = ConfigDict(populate_by_name=True)

    watermark: datetime | None = Field(
        examples=["2025-08-04T17:18:21.716Z"],
        default=None,
    )
    application_frauds: NumericWindow | None = Field(
        validation_alias="applicationFrauds",
        serialization_alias="applicationFrauds",
        description=(
            "Numeric window with 90 days, 12 months, and 60 months (values are numeric "
            "strings)."
        ),
        default=None,
    )
    mule_accounts: NumericWindow | None = Field(
        validation_alias="muleAccounts",
        serialization_alias="muleAccounts",
        description=(
            "Numeric window with 90 days, 12 months, and 60 months (values are numeric "
            "strings)."
        ),
        default=None,
    )
    scammer_accounts: NumericWindow | None = Field(
        validation_alias="scammerAccounts",
        serialization_alias="scammerAccounts",
        description=(
            "Numeric window with 90 days, 12 months, and 60 months (values are numeric "
            "strings)."
        ),
        default=None,
    )
    other_frauds: NumericWindow | None = Field(
        validation_alias="otherFrauds",
        serialization_alias="otherFrauds",
        description=(
            "Numeric window with 90 days, 12 months, and 60 months (values are numeric "
            "strings)."
        ),
        default=None,
    )
    unknown_frauds: NumericWindow | None = Field(
        validation_alias="unknownFrauds",
        serialization_alias="unknownFrauds",
        description=(
            "Numeric window with 90 days, 12 months, and 60 months (values are numeric "
            "strings)."
        ),
        default=None,
    )
    total_fraud_transaction_amount: NumericWindow | None = Field(
        validation_alias="totalFraudTransactionAmount",
        serialization_alias="totalFraudTransactionAmount",
        description=(
            "Numeric window with 90 days, 12 months, and 60 months (values are numeric "
            "strings)."
        ),
        default=None,
    )
    distinct_fraud_reporters: NumericWindow | None = Field(
        validation_alias="distinctFraudReporters",
        serialization_alias="distinctFraudReporters",
        description=(
            "Numeric window with 90 days, 12 months, and 60 months (values are numeric "
            "strings)."
        ),
        default=None,
    )


class FundsRecovery(BaseSchema):
    """Schema generated for FundsRecovery.

    Attributes:
        dict_id (UUID | None): Unique identifier of the funds recovery. Use it as the
            `{id}` on the get and cancel endpoints.
        root_transaction_id (str | None): The endToEndId of the reported Pix transaction
        situation_type (FundsRecoverySituationType | None): The situation that motivated
            the funds recovery
        report_details (str | None): Detailed description of what happened
        status (FundsRecoveryStatus | None): Current status of the funds recovery.
            COMPLETED and CANCELLED are terminal.
        direction (FundsRecoveryDirection | None): SENT when the funds recovery was
            opened by your institution
        reporter_participant (str | None): ISPB code of the institution that opened the
            funds recovery
        creation_time (str | None): When the funds recovery was created on the Central
            Bank, in ISO 8601 format
        last_modified (str | None): Last time the funds recovery was modified on the
            Central Bank, in ISO 8601 format
        events (list[FundsRecoveryEventsItem]): Event history of the funds recovery, in
            chronological order
        created_at (str | None): Undocumented in the spec.
        updated_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    dict_id: UUID | None = Field(
        validation_alias="dictId",
        serialization_alias="dictId",
        description=(
            "Unique identifier of the funds recovery. Use it as the `{id}` on the get "
            "and cancel endpoints."
        ),
        default=None,
    )
    root_transaction_id: str | None = Field(
        validation_alias="rootTransactionId",
        serialization_alias="rootTransactionId",
        description="The endToEndId of the reported Pix transaction",
        default=None,
    )
    situation_type: FundsRecoverySituationType | None = Field(
        validation_alias="situationType",
        serialization_alias="situationType",
        description="The situation that motivated the funds recovery",
        default=None,
    )
    report_details: str | None = Field(
        validation_alias="reportDetails",
        serialization_alias="reportDetails",
        description="Detailed description of what happened",
        default=None,
    )
    status: FundsRecoveryStatus | None = Field(
        description=(
            "Current status of the funds recovery. COMPLETED and CANCELLED are "
            "terminal."
        ),
        default=None,
    )
    direction: FundsRecoveryDirection | None = Field(
        description="SENT when the funds recovery was opened by your institution",
        default=None,
    )
    reporter_participant: str | None = Field(
        validation_alias="reporterParticipant",
        serialization_alias="reporterParticipant",
        description="ISPB code of the institution that opened the funds recovery",
        default=None,
    )
    creation_time: str | None = Field(
        validation_alias="creationTime",
        serialization_alias="creationTime",
        description=(
            "When the funds recovery was created on the Central Bank, in ISO 8601 "
            "format"
        ),
        default=None,
    )
    last_modified: str | None = Field(
        validation_alias="lastModified",
        serialization_alias="lastModified",
        description=(
            "Last time the funds recovery was modified on the Central Bank, in ISO "
            "8601 format"
        ),
        default=None,
    )
    events: list[FundsRecoveryEventsItem] = Field(
        description="Event history of the funds recovery, in chronological order",
        default_factory=list,
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    updated_at: str | None = Field(
        validation_alias="updatedAt",
        serialization_alias="updatedAt",
        default=None,
    )


class GetApiV1AccountRegisterResponse(BaseSchema):
    """Schema generated for GetApiV1AccountRegisterResponse.

    Attributes:
        official_name (str | None): Undocumented in the spec.
        trade_name (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        tax_id (GetApiV1AccountRegisterResponseTaxId | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    official_name: str | None = Field(
        validation_alias="officialName",
        serialization_alias="officialName",
        examples=["Company Official Name"],
        default=None,
    )
    trade_name: str | None = Field(
        validation_alias="tradeName",
        serialization_alias="tradeName",
        examples=["Company Trade Name"],
        default=None,
    )
    type: str | None = Field(examples=["BAAS"], default=None)
    tax_id: GetApiV1AccountRegisterResponseTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    status: str | None = Field(examples=["PENDING"], default=None)
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        examples=["6fe18d8e-5009-4f57-8f1d-5b084b6b83ac"],
        default=None,
    )


class GetApiV1AccountResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1AccountResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1AccountResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1AccountResponsePageInfoErrorsItemData | None = None


class GetApiV1ChargeByIdRefundResponse(BaseSchema):
    """Schema generated for GetApiV1ChargeByIdRefundResponse.

    Attributes:
        refunds (list[ChargeRefund]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    refunds: list[ChargeRefund] = Field(default_factory=list)


class GetApiV1ChargeResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1ChargeResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1ChargeResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1ChargeResponsePageInfoErrorsItemData | None = None


class GetApiV1CompanyResponse(BaseSchema):
    """Schema generated for GetApiV1CompanyResponse.

    Attributes:
        company (GetApiV1CompanyResponseCompany | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    company: GetApiV1CompanyResponseCompany | None = None


class GetApiV1CustomerResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1CustomerResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1CustomerResponsePageInfoErrorsItemData | None): Undocumented in
            the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1CustomerResponsePageInfoErrorsItemData | None = None


class GetApiV1DisputeByIdResponse(BaseSchema):
    """Schema generated for GetApiV1DisputeByIdResponse.

    Attributes:
        dispute (GetApiV1DisputeByIdResponseDispute | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    dispute: GetApiV1DisputeByIdResponseDispute | None = None


class GetApiV1DisputeResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1DisputeResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1DisputeResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1DisputeResponsePageInfoErrorsItemData | None = None


class GetApiV1LimitsByAccountIdResponse(BaseSchema):
    """Schema generated for GetApiV1LimitsByAccountIdResponse.

    Attributes:
        limits (AccountLimit | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    limits: AccountLimit | None = None


class GetApiV1PartnerAffiliateResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1PartnerAffiliateResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1PartnerAffiliateResponsePageInfoErrorsItemData | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1PartnerAffiliateResponsePageInfoErrorsItemData | None = None


class GetApiV1PartnerCompanyResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1PartnerCompanyResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1PartnerCompanyResponsePageInfoErrorsItemData | None): Undocumented
            in the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1PartnerCompanyResponsePageInfoErrorsItemData | None = None


class GetApiV1PaymentResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1PaymentResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1PaymentResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1PaymentResponsePageInfoErrorsItemData | None = None


class GetApiV1PixKeysTokensLogsResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1PixKeysTokensLogsResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1PixKeysTokensLogsResponsePageInfoErrorsItemData | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1PixKeysTokensLogsResponsePageInfoErrorsItemData | None = None


class GetApiV1PspResponse(BaseSchema):
    """Schema generated for GetApiV1PspResponse.

    Attributes:
        success (bool | None): Undocumented in the spec.
        psps (list[GetApiV1PspResponsePspsItem]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    success: bool | None = Field(examples=[True], default=None)
    psps: list[GetApiV1PspResponsePspsItem] = Field(default_factory=list)


class GetApiV1QrcodeStaticByIdResponse(BaseSchema):
    """Schema generated for GetApiV1QrcodeStaticByIdResponse.

    Attributes:
        pix_qr_code (PixQrCode | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pix_qr_code: PixQrCode | None = Field(
        validation_alias="pixQrCode",
        serialization_alias="pixQrCode",
        default=None,
    )


class GetApiV1QrcodeStaticResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1QrcodeStaticResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1QrcodeStaticResponsePageInfoErrorsItemData | None): Undocumented
            in the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1QrcodeStaticResponsePageInfoErrorsItemData | None = None


class GetApiV1RefundByIdResponse(BaseSchema):
    """Schema generated for GetApiV1RefundByIdResponse.

    Attributes:
        pix_transaction_refund (Refund | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pix_transaction_refund: Refund | None = Field(
        validation_alias="pixTransactionRefund",
        serialization_alias="pixTransactionRefund",
        default=None,
    )


class GetApiV1RefundResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1RefundResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1RefundResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1RefundResponsePageInfoErrorsItemData | None = None


class GetApiV1StablecoinQuoteResponseQuote(BaseSchema):
    """Schema generated for GetApiV1StablecoinQuoteResponseQuote.

    Attributes:
        base_price (float | None): Exchange rate applied (BRL per stablecoin unit).
        input_amount (float | None): Input amount in BRL (currency unit, not cents).
        input_currency (str | None): Undocumented in the spec.
        output_amount (float | None): Exact stablecoin amount the customer would
            receive.
        output_currency (str | None): Undocumented in the spec.
        applied_fees (list[GetApiV1StablecoinQuoteResponseQuoteAppliedFeesItem]):
            Undocumented in the spec.
        pair_name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    base_price: float | None = Field(
        validation_alias="basePrice",
        serialization_alias="basePrice",
        description="Exchange rate applied (BRL per stablecoin unit).",
        examples=[5.25],
        default=None,
    )
    input_amount: float | None = Field(
        validation_alias="inputAmount",
        serialization_alias="inputAmount",
        description="Input amount in BRL (currency unit, not cents).",
        examples=[100],
        default=None,
    )
    input_currency: str | None = Field(
        validation_alias="inputCurrency",
        serialization_alias="inputCurrency",
        examples=["BRL"],
        default=None,
    )
    output_amount: float | None = Field(
        validation_alias="outputAmount",
        serialization_alias="outputAmount",
        description="Exact stablecoin amount the customer would receive.",
        examples=[19.04],
        default=None,
    )
    output_currency: str | None = Field(
        validation_alias="outputCurrency",
        serialization_alias="outputCurrency",
        examples=["USDT"],
        default=None,
    )
    applied_fees: list[GetApiV1StablecoinQuoteResponseQuoteAppliedFeesItem] = Field(
        validation_alias="appliedFees",
        serialization_alias="appliedFees",
        default_factory=list,
    )
    pair_name: str | None = Field(
        validation_alias="pairName",
        serialization_alias="pairName",
        examples=["BRL/USDT"],
        default=None,
    )


class GetApiV1SubaccountByIdResponse(BaseSchema):
    """Schema generated for GetApiV1SubaccountByIdResponse.

    Attributes:
        sub_account (SubAccount | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    sub_account: SubAccount | None = Field(
        validation_alias="SubAccount",
        serialization_alias="SubAccount",
        default=None,
    )


class GetApiV1SubaccountResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1SubaccountResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1SubaccountResponsePageInfoErrorsItemData | None): Undocumented in
            the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1SubaccountResponsePageInfoErrorsItemData | None = None


class GetApiV1TransactionResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1TransactionResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1TransactionResponsePageInfoErrorsItemData | None): Undocumented in
            the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1TransactionResponsePageInfoErrorsItemData | None = None


class GetApiV1WebhookEventsResponse(BaseSchema):
    """Schema generated for GetApiV1WebhookEventsResponse.

    Attributes:
        events (list[GetApiV1WebhookEventsResponseEventsItem]): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    events: list[GetApiV1WebhookEventsResponseEventsItem] = Field(default_factory=list)


class GetApiV1WebhookResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for GetApiV1WebhookResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (GetApiV1WebhookResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: GetApiV1WebhookResponsePageInfoErrorsItemData | None = None


class InfractionReports(BaseSchema):
    """Schema generated for InfractionReports.

    Attributes:
        watermark (datetime | None): Undocumented in the spec.
        open_reports (str | None): Undocumented in the spec.
        open_reports_distinct_reporters (str | None): Undocumented in the spec.
        rejected_reports (NumericWindow | None): Numeric window with 90 days, 12 months,
            and 60 months (values are numeric strings).
    """

    model_config = ConfigDict(populate_by_name=True)

    watermark: datetime | None = Field(
        examples=["2025-08-04T17:18:21.756Z"],
        default=None,
    )
    open_reports: str | None = Field(
        validation_alias="openReports",
        serialization_alias="openReports",
        examples=["2"],
        default=None,
    )
    open_reports_distinct_reporters: str | None = Field(
        validation_alias="openReportsDistinctReporters",
        serialization_alias="openReportsDistinctReporters",
        examples=["1"],
        default=None,
    )
    rejected_reports: NumericWindow | None = Field(
        validation_alias="rejectedReports",
        serialization_alias="rejectedReports",
        description=(
            "Numeric window with 90 days, 12 months, and 60 months (values are numeric "
            "strings)."
        ),
        default=None,
    )


class InstallmentCobr(BaseSchema):
    """Schema generated for InstallmentCobr.

    Attributes:
        identifier_id (str | None): Undocumented in the spec.
        recurrency_id (str | None): Undocumented in the spec.
        installment_id (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        reject_code (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        tries (list[InstallmentCobrTriesItem]): Undocumented in the spec.
        payment_date (str | None): Undocumented in the spec.
        charge_date (str | None): Undocumented in the spec.
        expiry_date (str | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    identifier_id: str | None = Field(
        validation_alias="identifierId",
        serialization_alias="identifierId",
        default=None,
    )
    recurrency_id: str | None = Field(
        validation_alias="recurrencyId",
        serialization_alias="recurrencyId",
        default=None,
    )
    installment_id: str | None = Field(
        validation_alias="installmentId",
        serialization_alias="installmentId",
        default=None,
    )
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    reject_code: str | None = Field(
        validation_alias="rejectCode",
        serialization_alias="rejectCode",
        default=None,
    )
    status: str | None = None
    value: int | None = None
    tries: list[InstallmentCobrTriesItem] = Field(default_factory=list)
    payment_date: str | None = Field(
        validation_alias="paymentDate",
        serialization_alias="paymentDate",
        default=None,
    )
    charge_date: str | None = Field(
        validation_alias="chargeDate",
        serialization_alias="chargeDate",
        default=None,
    )
    expiry_date: str | None = Field(
        validation_alias="expiryDate",
        serialization_alias="expiryDate",
        default=None,
    )
    description: str | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )


class KycOnboardingAccountRegisterRepresentativesItem(BaseSchema):
    """Schema generated for KycOnboardingAccountRegisterRepresentativesItem.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (KycOnboardingAccountRegisterRepresentativesItemTaxId | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = Field(examples=["NOME_DO_SOCIO"], default=None)
    tax_id: KycOnboardingAccountRegisterRepresentativesItemTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class KycOnboardingRequest(BaseSchema):
    """Schema generated for KycOnboardingRequest.

    Attributes:
        tax_id (str): CNPJ da empresa do merchant (com ou sem mascara)
        correlation_id (str | None): Identificador unico para idempotencia. Se nao
            informado, o CNPJ sera usado.
        redirect_url (str | None): URL para onde o merchant sera redirecionado apos
            concluir o onboarding. Quando informado, a pagina final do fluxo KYC
            redireciona automaticamente apos 5 segundos.
        representatives (list[KycOnboardingRepresentative]): Socios/representantes da
            empresa
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="CNPJ da empresa do merchant (com ou sem mascara)",
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description=(
            "Identificador unico para idempotencia. Se nao informado, o CNPJ sera "
            "usado."
        ),
        default=None,
    )
    redirect_url: str | None = Field(
        validation_alias="redirectUrl",
        serialization_alias="redirectUrl",
        description=(
            "URL para onde o merchant sera redirecionado apos concluir o "
            "onboarding.\nQuando informado, a pagina final do fluxo KYC redireciona "
            "automaticamente apos 5 segundos."
        ),
        examples=["https://partner.example.com/kyc-done"],
        default=None,
    )
    representatives: list[KycOnboardingRepresentative] = Field(
        description="Socios/representantes da empresa",
        default_factory=list,
    )


class PaginationErrorsItem(BaseSchema):
    """Schema generated for PaginationErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (PaginationErrorsItemData | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: PaginationErrorsItemData | None = None


class Party(BaseSchema):
    """Schema generated for Party.

    Attributes:
        account (PartyAccount | None): Undocumented in the spec.
        psp (PartyPsp | None): Undocumented in the spec.
        holder (PartyHolder | None): Undocumented in the spec.
        tax_id (PartyTaxId | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    account: PartyAccount | None = None
    psp: PartyPsp | None = None
    holder: PartyHolder | None = None
    tax_id: PartyTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class PatchApiV1InvoiceIntegrationResponse(BaseSchema):
    """Schema generated for PatchApiV1InvoiceIntegrationResponse.

    Attributes:
        integration (PatchApiV1InvoiceIntegrationResponseIntegration | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    integration: PatchApiV1InvoiceIntegrationResponseIntegration | None = None


class PaymentBoleto(BaseSchema):
    """present for boleto payments (type BOLETO), resolved from the validated boleto.

    Attributes:
        barcode (str | None): the boleto barcode
        expires_date (str | None): due date (ISO 8601)
        total_value (int | None): total amount to pay, in cents
        issuing_entity (PaymentBoletoIssuingEntity | None): Undocumented in the spec.
        final_beneficiary (PaymentBoletoFinalBeneficiary | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    barcode: str | None = Field(description="the boleto barcode", default=None)
    expires_date: str | None = Field(
        validation_alias="expiresDate",
        serialization_alias="expiresDate",
        description="due date (ISO 8601)",
        default=None,
    )
    total_value: int | None = Field(
        validation_alias="totalValue",
        serialization_alias="totalValue",
        description="total amount to pay, in cents",
        default=None,
    )
    issuing_entity: PaymentBoletoIssuingEntity | None = Field(
        validation_alias="issuingEntity",
        serialization_alias="issuingEntity",
        default=None,
    )
    final_beneficiary: PaymentBoletoFinalBeneficiary | None = Field(
        validation_alias="finalBeneficiary",
        serialization_alias="finalBeneficiary",
        default=None,
    )


class PaymentCreatePayloadManualHolder(BaseSchema):
    """Schema generated for PaymentCreatePayloadManualHolder.

    Attributes:
        name (str): name of the account holder
        tax_id (PaymentCreatePayloadManualHolderTaxId): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(description="name of the account holder")
    tax_id: PaymentCreatePayloadManualHolderTaxId = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
    )


class PixKeyCheck(BaseSchema):
    """Schema generated for PixKeyCheck.

    Attributes:
        pix_key (str | None): Undocumented in the spec.
        type (PixKeyType | None): Undocumented in the spec.
        pix_key_end_to_end_id (str | None): Undocumented in the spec.
        owner (PixKeyCheckOwner | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    type: PixKeyType | None = None
    pix_key_end_to_end_id: str | None = Field(
        validation_alias="pixKeyEndToEndId",
        serialization_alias="pixKeyEndToEndId",
        default=None,
    )
    owner: PixKeyCheckOwner | None = None


class PostApiV1ApplicationResponse(BaseSchema):
    """Schema generated for PostApiV1ApplicationResponse.

    Attributes:
        application (Application | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    application: Application | None = None


class PostApiV1CashbackFidelityResponse(BaseSchema):
    """Schema generated for PostApiV1CashbackFidelityResponse.

    Attributes:
        cashback (PostApiV1CashbackFidelityResponseCashback | None): Object representing
            the existing cashback
        message (str | None): String explaining what happened
    """

    model_config = ConfigDict(extra="allow")

    cashback: PostApiV1CashbackFidelityResponseCashback | None = Field(
        description="Object representing the existing cashback",
        default=None,
    )
    message: str | None = Field(
        description="String explaining what happened",
        default=None,
    )


class PostApiV1ChargeByIdRefundResponse(BaseSchema):
    """Schema generated for PostApiV1ChargeByIdRefundResponse.

    Attributes:
        refund (ChargeRefund | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    refund: ChargeRefund | None = None


class PostApiV1DecodeEmvResponseCobLocationPayload(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseCobLocationPayload.

    Attributes:
        calendar (PostApiV1DecodeEmvResponseCobLocationPayloadCalendar | None):
            Undocumented in the spec.
        key (str | None): Undocumented in the spec.
        debtor (PostApiV1DecodeEmvResponseCobLocationPayloadDebtor | None): Undocumented
            in the spec.
        additional_info (list[PostApiV1DecodeEmvResponseCobLocationPayloadAdditionalI]):
            Undocumented in the spec.
        revision (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        txid (str | None): Undocumented in the spec.
        value (PostApiV1DecodeEmvResponseCobLocationPayloadValue | None): Undocumented
            in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    calendar: PostApiV1DecodeEmvResponseCobLocationPayloadCalendar | None = None
    key: str | None = None
    debtor: PostApiV1DecodeEmvResponseCobLocationPayloadDebtor | None = None
    additional_info: list[PostApiV1DecodeEmvResponseCobLocationPayloadAdditionalI] = (
        Field(
            validation_alias="additionalInfo",
            serialization_alias="additionalInfo",
            default_factory=list,
        )
    )
    revision: int | None = None
    status: str | None = None
    txid: str | None = None
    value: PostApiV1DecodeEmvResponseCobLocationPayloadValue | None = None


class PostApiV1DecodeEmvResponseEmv(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseEmv.

    Attributes:
        payload_format_indicator (str | None): Undocumented in the spec.
        point_of_initiation_method (str | None): Present when EMV indicates a dynamic QR
            (e.g. "12")
        merchant_account_information_pix
            (PostApiV1DecodeEmvResponseEmvMerchantAccountInformation | None): Parsed
            "26"/"00"... Pix merchant account info
        merchant_category_code (str | None): Undocumented in the spec.
        transaction_currency (str | None): Undocumented in the spec.
        transaction_amount (str | None): Undocumented in the spec.
        country_code (str | None): Undocumented in the spec.
        merchant_name (str | None): Undocumented in the spec.
        merchant_city (str | None): Undocumented in the spec.
        additional_data_field_template
            (PostApiV1DecodeEmvResponseEmvAdditionalDataFieldTemplat | None):
            Undocumented in the spec.
        unreserved_templates (PostApiV1DecodeEmvResponseEmvUnreservedTemplates | None):
            Undocumented in the spec.
        crc (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    payload_format_indicator: str | None = Field(
        validation_alias="payloadFormatIndicator",
        serialization_alias="payloadFormatIndicator",
        default=None,
    )
    point_of_initiation_method: str | None = Field(
        validation_alias="pointOfInitiationMethod",
        serialization_alias="pointOfInitiationMethod",
        description='Present when EMV indicates a dynamic QR (e.g. "12")',
        default=None,
    )
    merchant_account_information_pix: (
        PostApiV1DecodeEmvResponseEmvMerchantAccountInformation | None
    ) = Field(
        validation_alias="merchantAccountInformationPix",
        serialization_alias="merchantAccountInformationPix",
        description='Parsed "26"/"00"... Pix merchant account info',
        default=None,
    )
    merchant_category_code: str | None = Field(
        validation_alias="merchantCategoryCode",
        serialization_alias="merchantCategoryCode",
        default=None,
    )
    transaction_currency: str | None = Field(
        validation_alias="transactionCurrency",
        serialization_alias="transactionCurrency",
        default=None,
    )
    transaction_amount: str | None = Field(
        validation_alias="transactionAmount",
        serialization_alias="transactionAmount",
        default=None,
    )
    country_code: str | None = Field(
        validation_alias="countryCode",
        serialization_alias="countryCode",
        default=None,
    )
    merchant_name: str | None = Field(
        validation_alias="merchantName",
        serialization_alias="merchantName",
        default=None,
    )
    merchant_city: str | None = Field(
        validation_alias="merchantCity",
        serialization_alias="merchantCity",
        default=None,
    )
    additional_data_field_template: (
        PostApiV1DecodeEmvResponseEmvAdditionalDataFieldTemplat | None
    ) = Field(
        validation_alias="additionalDataFieldTemplate",
        serialization_alias="additionalDataFieldTemplate",
        default=None,
    )
    unreserved_templates: PostApiV1DecodeEmvResponseEmvUnreservedTemplates | None = (
        Field(
            validation_alias="unreservedTemplates",
            serialization_alias="unreservedTemplates",
            default=None,
        )
    )
    crc: str | None = None


class PostApiV1DecodeEmvResponseRecLocationPayloadLink(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseRecLocationPayloadLink.

    Attributes:
        contract (str | None): Undocumented in the spec.
        debtor (PostApiV1DecodeEmvResponseRecLocationPayloadLinkDebtor | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    contract: str | None = None
    debtor: PostApiV1DecodeEmvResponseRecLocationPayloadLinkDebtor | None = None


class PostApiV1DisputeIdEvidenceBody(BaseSchema):
    """Schema generated for PostApiV1DisputeIdEvidenceBody.

    Attributes:
        documents (list[PostApiV1DisputeIdEvidenceBodyDocumentsItem]): documents for
            upload
    """

    documents: list[PostApiV1DisputeIdEvidenceBodyDocumentsItem] = Field(
        description="documents for upload",
        default_factory=list,
    )


class PostApiV1DisputeIdEvidenceResponse(BaseSchema):
    """Schema generated for PostApiV1DisputeIdEvidenceResponse.

    Attributes:
        documents (list[PostApiV1DisputeIdEvidenceResponseDocumentsItem]): documents for
            upload
    """

    model_config = ConfigDict(extra="allow")

    documents: list[PostApiV1DisputeIdEvidenceResponseDocumentsItem] = Field(
        description="documents for upload",
        default_factory=list,
    )


class PostApiV1InvoiceIntegrationCertificateResponse(BaseSchema):
    """Schema generated for PostApiV1InvoiceIntegrationCertificateResponse.

    Attributes:
        integration (PostApiV1InvoiceIntegrationCertificateResponseIntegrati | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    integration: PostApiV1InvoiceIntegrationCertificateResponseIntegrati | None = None


class PostApiV1InvoiceIntegrationResponseIntegrationMetadata(BaseSchema):
    """Schema generated for PostApiV1InvoiceIntegrationResponseIntegrationMetadata.

    Attributes:
        nfeio (PostApiV1InvoiceIntegrationResponseIntegrationMetadataN | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    nfeio: PostApiV1InvoiceIntegrationResponseIntegrationMetadataN | None = None


class PostApiV1InvoiceIntegrationTestResponse(BaseSchema):
    """Schema generated for PostApiV1InvoiceIntegrationTestResponse.

    Attributes:
        invoice (PostApiV1InvoiceIntegrationTestResponseInvoice | None): Undocumented in
            the spec.
        integration (PostApiV1InvoiceIntegrationTestResponseIntegration | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    invoice: PostApiV1InvoiceIntegrationTestResponseInvoice | None = None
    integration: PostApiV1InvoiceIntegrationTestResponseIntegration | None = None


class PostApiV1InvoiceResponseInvoice(BaseSchema):
    """Schema generated for PostApiV1InvoiceResponseInvoice.

    Attributes:
        id (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        date (datetime | None): Undocumented in the spec.
        billing_date (datetime | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        status_raw (str | None): Undocumented in the spec.
        customer (PostApiV1InvoiceResponseInvoiceCustomer | None): Undocumented in the
            spec.
        charge (PostApiV1InvoiceResponseInvoiceCharge | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = None
    value: int | None = None
    date: datetime | None = None
    billing_date: datetime | None = Field(
        validation_alias="billingDate",
        serialization_alias="billingDate",
        default=None,
    )
    status: str | None = None
    status_raw: str | None = Field(
        validation_alias="statusRaw",
        serialization_alias="statusRaw",
        default=None,
    )
    customer: PostApiV1InvoiceResponseInvoiceCustomer | None = None
    charge: PostApiV1InvoiceResponseInvoiceCharge | None = None


class PostApiV1PartnerApplicationBody(BaseSchema):
    """Schema generated for PostApiV1PartnerApplicationBody.

    Attributes:
        application (PostApiV1PartnerApplicationBodyApplication | None): Undocumented in
            the spec.
        tax_id (TaxIdObjectPayload | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    application: PostApiV1PartnerApplicationBodyApplication | None = None
    tax_id: TaxIdObjectPayload | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class PostApiV1PartnerApplicationResponse(BaseSchema):
    """Schema generated for PostApiV1PartnerApplicationResponse.

    Attributes:
        application (PartnerApplicationPayload | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    application: PartnerApplicationPayload | None = None


class PostApiV1PaymentBodyManualHolder(BaseSchema):
    """Schema generated for PostApiV1PaymentBodyManualHolder.

    Attributes:
        name (str): name of the account holder
        tax_id (PostApiV1PaymentBodyManualHolderTaxId): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(description="name of the account holder")
    tax_id: PostApiV1PaymentBodyManualHolderTaxId = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
    )


class PostApiV1QrcodeStaticResponse(BaseSchema):
    """Schema generated for PostApiV1QrcodeStaticResponse.

    Attributes:
        pix_qr_code (PixQrCode | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        br_code (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pix_qr_code: PixQrCode | None = Field(
        validation_alias="pixQrCode",
        serialization_alias="pixQrCode",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    br_code: str | None = Field(
        validation_alias="brCode",
        serialization_alias="brCode",
        default=None,
    )


class PostApiV1RefundResponse(BaseSchema):
    """Schema generated for PostApiV1RefundResponse.

    Attributes:
        refund (Refund | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    refund: Refund | None = None


class PostApiV1SubaccountByIdWithdrawResponseWithdraw(BaseSchema):
    """Schema generated for PostApiV1SubaccountByIdWithdrawResponseWithdraw.

    Attributes:
        account (Transaction2 | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    account: Transaction2 | None = None


class PostApiV1SubaccountResponse(BaseSchema):
    """Schema generated for PostApiV1SubaccountResponse.

    Attributes:
        sub_account (SubAccount | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    sub_account: SubAccount | None = Field(
        validation_alias="SubAccount",
        serialization_alias="SubAccount",
        default=None,
    )


class PostApiV1TransferResponse(BaseSchema):
    """Schema generated for PostApiV1TransferResponse.

    Attributes:
        transaction (TransferTransaction | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    transaction: TransferTransaction | None = None


class PostApiV1WebhookBody(BaseSchema):
    """Schema generated for PostApiV1WebhookBody.

    Attributes:
        webhook (WebhookPayload | None): Undocumented in the spec.
    """

    webhook: WebhookPayload | None = None


class PostApiV1WebhookResponse(BaseSchema):
    """Schema generated for PostApiV1WebhookResponse.

    Attributes:
        webhook (Webhook | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    webhook: Webhook | None = None


class PreRegistrationObject(BaseSchema):
    """Schema generated for PreRegistrationObject.

    Attributes:
        name (str): The name of this preregistration. It'll be related as your company
            name too.
        website (str | None): A website that is related to this preregistration.
        tax_id (TaxIdObjectPayload): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str = Field(
        description=(
            "The name of this preregistration. It'll be related as your company name "
            "too."
        ),
    )
    website: str | None = Field(
        description="A website that is related to this preregistration.",
        default=None,
    )
    tax_id: TaxIdObjectPayload = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
    )


class PreRegistrationObjectPayload(BaseSchema):
    """Schema generated for PreRegistrationObjectPayload.

    Attributes:
        name (str | None): When the preregistration will turn a company, this will be
            the name of the company that this preregistration is related.
        tax_id (TaxIdObjectPayload | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = Field(
        description=(
            "When the preregistration will turn a company, this will be the name of "
            "the company that this preregistration is related."
        ),
        default=None,
    )
    tax_id: TaxIdObjectPayload | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class PreRegistrationUserObject(BaseSchema):
    """Schema generated for PreRegistrationUserObject.

    Attributes:
        first_name (str): The user's first name. If the pre registration has been
            approved, this will be turn the company's first user first name.
        last_name (str): The user's last name. If the pre registration has been
            approved, this will be turn the company's first user last name.
        email (str): The user's email. It'll be the email that will entered in contact
            to validate that it's a real person (it's a step to approve the
            preregistration). After approving the preregistration, it'll be the
            company's user email.
        phone (str): The user's phone number, need to be a validated phone number
            because it'll receive a SMS confirming that is a real person. We're accept
            only values that matches the E.164 standard, that follows this pattern:
            [+][country code][local phone number].
        tax_id (TaxIdObjectPayload): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    first_name: str = Field(
        validation_alias="firstName",
        serialization_alias="firstName",
        description=(
            "The user's first name.\nIf the pre registration has been approved, this "
            "will be turn the company's first user first name."
        ),
    )
    last_name: str = Field(
        validation_alias="lastName",
        serialization_alias="lastName",
        description=(
            "The user's last name.\nIf the pre registration has been approved, this "
            "will be turn the company's first user last name."
        ),
    )
    email: str = Field(
        description=(
            "The user's email.\nIt'll be the email that will entered in contact to "
            "validate that it's a real person (it's a step to approve the "
            "preregistration).\nAfter approving the preregistration, it'll be the "
            "company's user email."
        ),
    )
    phone: str = Field(
        description=(
            "The user's phone number, need to be a validated phone number because "
            "it'll receive a SMS confirming that is a real person.\nWe're accept only "
            "values that matches the E.164 standard, that follows this pattern: "
            "[+][country code][local phone number]."
        ),
    )
    tax_id: TaxIdObjectPayload = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
    )


class StablecoinDepositGetResponse(BaseSchema):
    """Schema generated for StablecoinDepositGetResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        deposit (StablecoinDepositListItem | None): Undocumented in the spec.
    """

    status: str | None = Field(examples=["ok"], default=None)
    deposit: StablecoinDepositListItem | None = None


class StablecoinDepositListResponse(BaseSchema):
    """Schema generated for StablecoinDepositListResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        deposits (list[StablecoinDepositListItem]): Undocumented in the spec.
        count (int | None): Total number of deposits for the company (ignores
            pagination).
        limit (int | None): Undocumented in the spec.
        skip (int | None): Undocumented in the spec.
    """

    status: str | None = Field(examples=["ok"], default=None)
    deposits: list[StablecoinDepositListItem] = Field(default_factory=list)
    count: int | None = Field(
        description="Total number of deposits for the company (ignores pagination).",
        examples=[42],
        default=None,
    )
    limit: int | None = Field(examples=[20], default=None)
    skip: int | None = Field(examples=[0], default=None)


class StablecoinDepositResponse(BaseSchema):
    """Schema generated for StablecoinDepositResponse.

    Attributes:
        status (str | None): The deposit status.
        deposit_id (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        expiration (str | None): Undocumented in the spec.
        quote (StablecoinDepositQuote | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(
        description="The deposit status.",
        examples=["PENDING"],
        default=None,
    )
    deposit_id: str | None = Field(
        validation_alias="depositId",
        serialization_alias="depositId",
        examples=["6650..."],
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationId",
        serialization_alias="correlationId",
        examples=["my-unique-id"],
        default=None,
    )
    expiration: str | None = Field(examples=["2026-06-05T12:00:00.000Z"], default=None)
    quote: StablecoinDepositQuote | None = None


class StablecoinSubAccountGetResponse(BaseSchema):
    """Schema generated for StablecoinSubAccountGetResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        sub_account (StablecoinSubAccountItem | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(examples=["ok"], default=None)
    sub_account: StablecoinSubAccountItem | None = Field(
        validation_alias="subAccount",
        serialization_alias="subAccount",
        default=None,
    )


class StablecoinSubAccountListResponse(BaseSchema):
    """Schema generated for StablecoinSubAccountListResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        sub_accounts (list[StablecoinSubAccountItem]): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(examples=["ok"], default=None)
    sub_accounts: list[StablecoinSubAccountItem] = Field(
        validation_alias="subAccounts",
        serialization_alias="subAccounts",
        default_factory=list,
    )


class SubAccountTransferResponsePayload(BaseSchema):
    """Schema generated for SubAccountTransferResponsePayload.

    Attributes:
        value (int | None): The value of the transfer in cents
        destination_subaccount (SubAccountTransferResponsePayloadDestinationSubaccount |
            None): The destination subaccount
        origin_subaccount (SubAccountTransferResponsePayloadOriginSubaccount | None):
            The destination subaccount
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    value: int | None = Field(
        description="The value of the transfer in cents",
        default=None,
    )
    destination_subaccount: (
        SubAccountTransferResponsePayloadDestinationSubaccount | None
    ) = Field(
        validation_alias="destinationSubaccount",
        serialization_alias="destinationSubaccount",
        description="The destination subaccount",
        default=None,
    )
    origin_subaccount: SubAccountTransferResponsePayloadOriginSubaccount | None = Field(
        validation_alias="originSubaccount",
        serialization_alias="originSubaccount",
        description="The destination subaccount",
        default=None,
    )


class SubscriptionPayloadCustomer(BaseSchema):
    """Customer of this subscription.

    Attributes:
        name (str | None): Customer name
        email (str | None): Customer email
        phone (str | None): Customer phone
        tax_id (str | None): Customer taxID (CPF or CNPJ)
        address (SubscriptionPayloadCustomerAddress | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(description="Customer name", default=None)
    email: str | None = Field(description="Customer email", default=None)
    phone: str | None = Field(description="Customer phone", default=None)
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="Customer taxID (CPF or CNPJ)",
        default=None,
    )
    address: SubscriptionPayloadCustomerAddress | None = None


class BoletoValidateResponse(BaseSchema):
    """Schema generated for BoletoValidateResponse.

    Attributes:
        boleto (BoletoValidatedInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    boleto: BoletoValidatedInfo | None = None


class ChargePayload(BaseSchema):
    """Schema generated for ChargePayload.

    Attributes:
        correlation_id (str): Your correlation ID to keep track of this charge
        value (int): Value in cents of this charge
        type (ChargeType | None): Charge type is used to determine whether a charge will
            have a deadline, fines and interests
        comment (str | None): Comment to be added in infoPagador
        expires_in (int | None): Expires the charge in seconds (minimum is 5 minutes)
        expires_date (str | None): Expiration date of the charge. Only in ISO 8601
            format.
        due_date (str | None): Due date for OVERDUE, BOLETO, or subscription charges in
            ISO 8601 format.
        customer (CustomerPayload | None): Customer field is not required. However, if
            you decide to send it, you must send at least one of the following
            combinations, name + taxID or name + email or name + phone.
        ensure_same_tax_id (bool | None): true to ensure that the payer taxID must be
            the same as the customer taxID.
        fixed_location (bool | None): true to fix the qrcode of the charge, same qrcode
            to all future charges.
        payment_link_id (str | None): Payment Link ID, used to link charges to the same
            qrCode.
        days_for_due_date (int | None): Time in days until the charge hits the deadline
            so fines and interests start applying. This property is only considered for
            charges of type OVERDUE
        days_after_due_date (int | None): Time in days that a charge is still payable
            after the deadline. This property is only considered for charges of type
            OVERDUE
        interests (ChargePayloadInterests | None): Interests configuration. This
            property is only considered for charges of type OVERDUE
        fines (ChargePayloadFines | None): Fines configuration. This property is only
            considered for charges of type OVERDUE
        discount_settings (ChargePayloadDiscountSettings | None): Discount settings for
            the charge. This property is only considered for charges of type OVERDUE.
            **How it interacts with `fines` and `interests`.** Discount only applies to
            payments **before** the due date (controlled by `daysForDueDate`). On or
            after the due date the discount is gone, and `fines` (applied once) and
            `interests` (accruing per day) start adding **on top of** `value`. Use the
            [day-by-day
            simulator](https://github.com/entria/woovi/blob/main/packages/openpix/scripts/api/charge/simulateChargeDiscount.ts)
            to preview the totals a payer sees on each day of the charge lifecycle.
            **Modality enum** follows the BACEN COBV (Cobrança com Vencimento) spec —
            see [bacen.github.io/pix-api](https://bacen.github.io/pix-api/) for the
            upstream reference.  **Shape of the object depends on `modality`:**   - For
            `FIXED_VALUE_UNTIL_SPECIFIED_DATE` and `PERCENTAGE_UNTIL_SPECIFIED_DATE`,
            provide `discountFixedDate` (array of items with `daysActive` and `value`).
            When multiple entries match the current day (i.e. their `daysActive` window
            has not yet expired), the entry with the **largest** discount wins.   - For
            the four advance-day modalities (`VALUE_PER_RUNNING_DAY_ADVANCE`,
            `VALUE_PER_BUSINESS_DAY_ADVANCE`, `PERCENTAGE_PER_RUNNING_DAY_ADVANCE`,
            `PERCENTAGE_PER_BUSINESS_DAY_ADVANCE`), provide a single `value`.
            **Rounding.** Computed discount and interest amounts are rounded to the
            nearest cent.
        additional_info (list[ChargePayloadAdditionalInfoItem]): Additional info of the
            charge
        enable_cashback_percentage (bool | None): true to enable cashback and false to
            disable.
        enable_cashback_exclusive_percentage (bool | None): true to enable fidelity
            cashback and false to disable.
        subaccount (str | None): Pix key of the subaccount to receive the charge
        splits (list[ChargePayloadSplitsItem]): This is the array that will configure
            how will be splitted the value of the charge
    """

    model_config = ConfigDict(populate_by_name=True)

    correlation_id: str = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID to keep track of this charge",
    )
    value: int = Field(description="Value in cents of this charge")
    type: ChargeType | None = Field(
        description=(
            "Charge type is used to determine whether a charge will have a deadline, "
            "fines and interests"
        ),
        default=None,
    )
    comment: str | None = Field(
        description="Comment to be added in infoPagador",
        default=None,
    )
    expires_in: int | None = Field(
        validation_alias="expiresIn",
        serialization_alias="expiresIn",
        description="Expires the charge in seconds (minimum is 5 minutes)",
        default=None,
    )
    expires_date: str | None = Field(
        validation_alias="expiresDate",
        serialization_alias="expiresDate",
        description="Expiration date of the charge. Only in ISO 8601 format.",
        default=None,
    )
    due_date: str | None = Field(
        validation_alias="dueDate",
        serialization_alias="dueDate",
        description=(
            "Due date for OVERDUE, BOLETO, or subscription charges in ISO 8601 format."
        ),
        default=None,
    )
    # openapi: unsupported — oneOf in 'CustomerPayload' merged into one model — every
    #   variant's properties are accepted together, so 'exactly one variant' is not
    #   enforced
    customer: CustomerPayload | None = Field(
        description=(
            "Customer field is not required. However, if you decide to send it, you "
            "must send at least one of the following combinations, name + taxID or "
            "name + email or name + phone."
        ),
        default=None,
    )
    ensure_same_tax_id: bool | None = Field(
        validation_alias="ensureSameTaxID",
        serialization_alias="ensureSameTaxID",
        description=(
            "true to ensure that the payer taxID must be the same as the customer "
            "taxID."
        ),
        default=None,
    )
    fixed_location: bool | None = Field(
        validation_alias="fixedLocation",
        serialization_alias="fixedLocation",
        description=(
            "true to fix the qrcode of the charge, same qrcode to all future charges."
        ),
        default=None,
    )
    payment_link_id: str | None = Field(
        validation_alias="paymentLinkID",
        serialization_alias="paymentLinkID",
        description="Payment Link ID, used to link charges to the same qrCode.",
        default=None,
    )
    days_for_due_date: int | None = Field(
        validation_alias="daysForDueDate",
        serialization_alias="daysForDueDate",
        description=(
            "Time in days until the charge hits the deadline so fines and interests "
            "start applying. This property is only considered for charges of type "
            "OVERDUE"
        ),
        default=None,
    )
    days_after_due_date: int | None = Field(
        validation_alias="daysAfterDueDate",
        serialization_alias="daysAfterDueDate",
        description=(
            "Time in days that a charge is still payable after the deadline. This "
            "property is only considered for charges of type OVERDUE"
        ),
        default=None,
    )
    interests: ChargePayloadInterests | None = Field(
        description=(
            "Interests configuration. This property is only considered for charges of "
            "type OVERDUE"
        ),
        default=None,
    )
    fines: ChargePayloadFines | None = Field(
        description=(
            "Fines configuration. This property is only considered for charges of type "
            "OVERDUE"
        ),
        default=None,
    )
    discount_settings: ChargePayloadDiscountSettings | None = Field(
        validation_alias="discountSettings",
        serialization_alias="discountSettings",
        description=(
            "Discount settings for the charge. This property is only considered for "
            "charges of type OVERDUE.\n\n**How it interacts with `fines` and "
            "`interests`.** Discount only applies to payments **before** the due date "
            "(controlled by `daysForDueDate`). On or after the due date the discount "
            "is gone, and `fines` (applied once) and `interests` (accruing per day) "
            "start adding **on top of** `value`. Use the [day-by-day "
            "simulator](https://github.com/entria/woovi/blob/main/packages/openpix/scri"
            "pts/api/charge/simulateChargeDiscount.ts) to preview the totals a payer "
            "sees on each day of the charge lifecycle.\n\n**Modality enum** follows "
            "the BACEN COBV (Cobrança com Vencimento) spec — see "
            "[bacen.github.io/pix-api](https://bacen.github.io/pix-api/) for the "
            "upstream reference.\n\n**Shape of the object depends on `modality`:**\n  "
            "- For `FIXED_VALUE_UNTIL_SPECIFIED_DATE` and "
            "`PERCENTAGE_UNTIL_SPECIFIED_DATE`, provide `discountFixedDate` (array of "
            "items with `daysActive` and `value`). When multiple entries match the "
            "current day (i.e. their `daysActive` window has not yet expired), the "
            "entry with the **largest** discount wins.\n  - For the four advance-day "
            "modalities (`VALUE_PER_RUNNING_DAY_ADVANCE`, "
            "`VALUE_PER_BUSINESS_DAY_ADVANCE`, `PERCENTAGE_PER_RUNNING_DAY_ADVANCE`, "
            "`PERCENTAGE_PER_BUSINESS_DAY_ADVANCE`), provide a single "
            "`value`.\n\n**Rounding.** Computed discount and interest amounts are "
            "rounded to the nearest cent."
        ),
        default=None,
    )
    additional_info: list[ChargePayloadAdditionalInfoItem] = Field(
        validation_alias="additionalInfo",
        serialization_alias="additionalInfo",
        description="Additional info of the charge",
        default_factory=list,
    )
    enable_cashback_percentage: bool | None = Field(
        validation_alias="enableCashbackPercentage",
        serialization_alias="enableCashbackPercentage",
        description="true to enable cashback and false to disable.",
        default=None,
    )
    enable_cashback_exclusive_percentage: bool | None = Field(
        validation_alias="enableCashbackExclusivePercentage",
        serialization_alias="enableCashbackExclusivePercentage",
        description="true to enable fidelity cashback and false to disable.",
        default=None,
    )
    subaccount: str | None = Field(
        description="Pix key of the subaccount to receive the charge",
        default=None,
    )
    splits: list[ChargePayloadSplitsItem] = Field(
        description=(
            "This is the array that will configure how will be splitted the value of "
            "the charge"
        ),
        default_factory=list,
    )


class ChargePaymentMethods(BaseSchema):
    """Schema generated for ChargePaymentMethods.

    Attributes:
        pix (ChargePaymentMethodsPix | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    pix: ChargePaymentMethodsPix | None = None


class GetApiV1AccountByAccountIdResponse(BaseSchema):
    """Schema generated for GetApiV1AccountByAccountIdResponse.

    Attributes:
        account (CompanyBankAccount | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    account: CompanyBankAccount | None = None


class GetApiV1AccountResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1AccountResponsePageInfo.

    Attributes:
        errors (list[GetApiV1AccountResponsePageInfoErrorsItem]): Undocumented in the
            spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[GetApiV1AccountResponsePageInfoErrorsItem] = Field(
        default_factory=list,
    )
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class GetApiV1ChargeResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1ChargeResponsePageInfo.

    Attributes:
        errors (list[GetApiV1ChargeResponsePageInfoErrorsItem]): Undocumented in the
            spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[GetApiV1ChargeResponsePageInfoErrorsItem] = Field(default_factory=list)
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class GetApiV1CustomerByIdResponse(BaseSchema):
    """Schema generated for GetApiV1CustomerByIdResponse.

    Attributes:
        customer (Customer | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    customer: Customer | None = None


class GetApiV1CustomerResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1CustomerResponsePageInfo.

    Attributes:
        errors (list[GetApiV1CustomerResponsePageInfoErrorsItem]): Undocumented in the
            spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[GetApiV1CustomerResponsePageInfoErrorsItem] = Field(
        default_factory=list,
    )
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class GetApiV1DisputeResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1DisputeResponsePageInfo.

    Attributes:
        errors (list[GetApiV1DisputeResponsePageInfoErrorsItem]): Undocumented in the
            spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[GetApiV1DisputeResponsePageInfoErrorsItem] = Field(
        default_factory=list,
    )
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class GetApiV1PartnerAffiliateResponseAffiliatesItem(BaseSchema):
    """Schema generated for GetApiV1PartnerAffiliateResponseAffiliatesItem.

    Attributes:
        company (CompanyObjectPayload): Undocumented in the spec.
        account (AccountObjectPayload | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    company: CompanyObjectPayload
    account: AccountObjectPayload | None = None


class GetApiV1PartnerAffiliateResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1PartnerAffiliateResponsePageInfo.

    Attributes:
        errors (list[GetApiV1PartnerAffiliateResponsePageInfoErrorsItem]): Undocumented
            in the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[GetApiV1PartnerAffiliateResponsePageInfoErrorsItem] = Field(
        default_factory=list,
    )
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class GetApiV1PartnerCompanyByTaxIdResponsePreRegistration(BaseSchema):
    """Schema generated for GetApiV1PartnerCompanyByTaxIdResponsePreRegistration.

    Attributes:
        pre_registration (PreRegistrationObjectPayload): Undocumented in the spec.
        user (PreRegistrationUserObject): Undocumented in the spec.
        company (CompanyObjectPayload | None): Undocumented in the spec.
        account (AccountObjectPayload | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pre_registration: PreRegistrationObjectPayload = Field(
        validation_alias="preRegistration",
        serialization_alias="preRegistration",
    )
    user: PreRegistrationUserObject
    company: CompanyObjectPayload | None = None
    account: AccountObjectPayload | None = None


class GetApiV1PartnerCompanyResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1PartnerCompanyResponsePageInfo.

    Attributes:
        errors (list[GetApiV1PartnerCompanyResponsePageInfoErrorsItem]): Undocumented in
            the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[GetApiV1PartnerCompanyResponsePageInfoErrorsItem] = Field(
        default_factory=list,
    )
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class GetApiV1PartnerCompanyResponsePreRegistrationsItem(BaseSchema):
    """Schema generated for GetApiV1PartnerCompanyResponsePreRegistrationsItem.

    Attributes:
        pre_registration (PreRegistrationObjectPayload): Undocumented in the spec.
        user (PreRegistrationUserObject): Undocumented in the spec.
        company (CompanyObjectPayload | None): Undocumented in the spec.
        account (AccountObjectPayload | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pre_registration: PreRegistrationObjectPayload = Field(
        validation_alias="preRegistration",
        serialization_alias="preRegistration",
    )
    user: PreRegistrationUserObject
    company: CompanyObjectPayload | None = None
    account: AccountObjectPayload | None = None


class GetApiV1PaymentResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1PaymentResponsePageInfo.

    Attributes:
        errors (list[GetApiV1PaymentResponsePageInfoErrorsItem]): Undocumented in the
            spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[GetApiV1PaymentResponsePageInfoErrorsItem] = Field(
        default_factory=list,
    )
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class GetApiV1PixKeysResponse(BaseSchema):
    """Schema generated for GetApiV1PixKeysResponse.

    Attributes:
        pix_keys (list[PixKey]): Undocumented in the spec.
        account (CompanyBankAccount | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pix_keys: list[PixKey] = Field(
        validation_alias="pixKeys",
        serialization_alias="pixKeys",
        default_factory=list,
    )
    account: CompanyBankAccount | None = None


class GetApiV1PixKeysTokensLogsResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1PixKeysTokensLogsResponsePageInfo.

    Attributes:
        errors (list[GetApiV1PixKeysTokensLogsResponsePageInfoErrorsItem]): Undocumented
            in the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[GetApiV1PixKeysTokensLogsResponsePageInfoErrorsItem] = Field(
        default_factory=list,
    )
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class GetApiV1QrcodeStaticResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1QrcodeStaticResponsePageInfo.

    Attributes:
        errors (list[GetApiV1QrcodeStaticResponsePageInfoErrorsItem]): Undocumented in
            the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[GetApiV1QrcodeStaticResponsePageInfoErrorsItem] = Field(
        default_factory=list,
    )
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class GetApiV1RefundResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1RefundResponsePageInfo.

    Attributes:
        errors (list[GetApiV1RefundResponsePageInfoErrorsItem]): Undocumented in the
            spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[GetApiV1RefundResponsePageInfoErrorsItem] = Field(default_factory=list)
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class GetApiV1StablecoinQuoteResponse(BaseSchema):
    """Schema generated for GetApiV1StablecoinQuoteResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        quote (GetApiV1StablecoinQuoteResponseQuote | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    status: str | None = Field(examples=["ok"], default=None)
    quote: GetApiV1StablecoinQuoteResponseQuote | None = None


class GetApiV1SubaccountResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1SubaccountResponsePageInfo.

    Attributes:
        errors (list[GetApiV1SubaccountResponsePageInfoErrorsItem]): Undocumented in the
            spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[GetApiV1SubaccountResponsePageInfoErrorsItem] = Field(
        default_factory=list,
    )
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class GetApiV1TransactionResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1TransactionResponsePageInfo.

    Attributes:
        errors (list[GetApiV1TransactionResponsePageInfoErrorsItem]): Undocumented in
            the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[GetApiV1TransactionResponsePageInfoErrorsItem] = Field(
        default_factory=list,
    )
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class GetApiV1WebhookResponsePageInfo(BaseSchema):
    """Schema generated for GetApiV1WebhookResponsePageInfo.

    Attributes:
        errors (list[GetApiV1WebhookResponsePageInfoErrorsItem]): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    errors: list[GetApiV1WebhookResponsePageInfoErrorsItem] = Field(
        default_factory=list,
    )


class Installment(BaseSchema):
    """Schema generated for Installment.

    Attributes:
        date_generate_charge (datetime | None): Undocumented in the spec.
        expiration (float | None): Undocumented in the spec.
        installment_number (int | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        created_at (datetime | None): Undocumented in the spec.
        cobr (InstallmentCobr | None): Undocumented in the spec.
        payment_subscription_global_id (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    date_generate_charge: datetime | None = Field(
        validation_alias="dateGenerateCharge",
        serialization_alias="dateGenerateCharge",
        default=None,
    )
    expiration: float | None = None
    installment_number: int | None = Field(
        validation_alias="installmentNumber",
        serialization_alias="installmentNumber",
        default=None,
    )
    value: int | None = None
    status: str | None = None
    created_at: datetime | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    cobr: InstallmentCobr | None = None
    payment_subscription_global_id: str | None = Field(
        validation_alias="paymentSubscriptionGlobalID",
        serialization_alias="paymentSubscriptionGlobalID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class KeyOrOwnerStatistics(BaseSchema):
    """Schema generated for KeyOrOwnerStatistics.

    Attributes:
        fraud_markers (FraudMarkers | None): Undocumented in the spec.
        infraction_reports (InfractionReports | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    fraud_markers: FraudMarkers | None = Field(
        validation_alias="fraudMarkers",
        serialization_alias="fraudMarkers",
        default=None,
    )
    infraction_reports: InfractionReports | None = Field(
        validation_alias="infractionReports",
        serialization_alias="infractionReports",
        default=None,
    )


class KycOnboardingAccountRegister(BaseSchema):
    """Schema generated for KycOnboardingAccountRegister.

    Attributes:
        status (str | None): Undocumented in the spec.
        official_name (str | None): Undocumented in the spec.
        trade_name (str | None): Undocumented in the spec.
        tax_id (KycOnboardingAccountRegisterTaxId | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        representatives (list[KycOnboardingAccountRegisterRepresentativesItem]):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(examples=["PENDING"], default=None)
    official_name: str | None = Field(
        validation_alias="officialName",
        serialization_alias="officialName",
        examples=["RAZAO_SOCIAL_DA_EMPRESA"],
        default=None,
    )
    trade_name: str | None = Field(
        validation_alias="tradeName",
        serialization_alias="tradeName",
        examples=["NOME_FANTASIA_DA_EMPRESA"],
        default=None,
    )
    tax_id: KycOnboardingAccountRegisterTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        examples=["my-unique-id"],
        default=None,
    )
    representatives: list[KycOnboardingAccountRegisterRepresentativesItem] = Field(
        default_factory=list,
    )


class Pagination(BaseSchema):
    """Schema generated for Pagination.

    Attributes:
        errors (list[PaginationErrorsItem]): Undocumented in the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[PaginationErrorsItem] = Field(default_factory=list)
    skip: int | None = None
    limit: int | None = None
    has_previous_page: bool | None = Field(
        validation_alias="hasPreviousPage",
        serialization_alias="hasPreviousPage",
        default=None,
    )
    has_next_page: bool | None = Field(
        validation_alias="hasNextPage",
        serialization_alias="hasNextPage",
        default=None,
    )


class PatchApiV1CustomerByCorrelationIdResponse(BaseSchema):
    """Schema generated for PatchApiV1CustomerByCorrelationIdResponse.

    Attributes:
        customer (Customer | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    customer: Customer | None = None


class Payment(BaseSchema):
    """Schema generated for Payment.

    Attributes:
        type (PaymentCreatePayloadPixKeyType | None): type of the payment
        value (int | None): value of the requested payment in cents
        destination_alias (str | None): the pix key the payment should be sent to
        destination_alias_type (PaymentCreatePayloadPixKeyDestinationAliasType | None):
            the type of the pix key the payment should be sent to
        qr_code (str | None): the QR Code to be paid
        correlation_id (str | None): Your correlation ID to keep track of this payment
        comment (str | None): the comment that will be sent alongside your payment
        source_account_id (str | None): the source account the payment was created from
        status (PaymentStatus | None): payment status
        boleto (PaymentBoleto | None): present for boleto payments (type BOLETO),
            resolved from the validated boleto
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    type: PaymentCreatePayloadPixKeyType | None = Field(
        description="type of the payment",
        default=None,
    )
    value: int | None = Field(
        description="value of the requested payment in cents",
        default=None,
    )
    destination_alias: str | None = Field(
        validation_alias="destinationAlias",
        serialization_alias="destinationAlias",
        description="the pix key the payment should be sent to",
        default=None,
    )
    destination_alias_type: PaymentCreatePayloadPixKeyDestinationAliasType | None = (
        Field(
            validation_alias="destinationAliasType",
            serialization_alias="destinationAliasType",
            description="the type of the pix key the payment should be sent to",
            default=None,
        )
    )
    qr_code: str | None = Field(
        validation_alias="qrCode",
        serialization_alias="qrCode",
        description="the QR Code to be paid",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID to keep track of this payment",
        default=None,
    )
    comment: str | None = Field(
        description="the comment that will be sent alongside your payment",
        default=None,
    )
    source_account_id: str | None = Field(
        validation_alias="sourceAccountId",
        serialization_alias="sourceAccountId",
        description="the source account the payment was created from",
        default=None,
    )
    status: PaymentStatus | None = Field(description="payment status", default=None)
    boleto: PaymentBoleto | None = Field(
        description=(
            "present for boleto payments (type BOLETO), resolved from the validated "
            "boleto"
        ),
        default=None,
    )


class PaymentCreatePayloadManual(BaseSchema):
    """Manual.

    Attributes:
        type (PaymentCreatePayloadPixKeyType): type of the payment
        value (int): value of the requested payment in cents
        correlation_id (str): a unique identifier for your payment
        pix_key_end_to_end_id (str | None): the end to end id of the pix key used for
            track pix key consultations
        psp (str): the PSP (Payment Service Provider) identifier
        holder (PaymentCreatePayloadManualHolder): Undocumented in the spec.
        account (PaymentCreatePayloadManualAccount): Undocumented in the spec.
        metadata (dict[str, Any] | None): additional metadata for the payment (max 30
            keys)
    """

    model_config = ConfigDict(populate_by_name=True)

    type: PaymentCreatePayloadPixKeyType = Field(description="type of the payment")
    value: int = Field(description="value of the requested payment in cents")
    correlation_id: str = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="a unique identifier for your payment",
    )
    pix_key_end_to_end_id: str | None = Field(
        validation_alias="pixKeyEndToEndId",
        serialization_alias="pixKeyEndToEndId",
        description=(
            "the end to end id of the pix key used for track pix key consultations"
        ),
        default=None,
    )
    psp: str = Field(description="the PSP (Payment Service Provider) identifier")
    holder: PaymentCreatePayloadManualHolder
    account: PaymentCreatePayloadManualAccount
    metadata: dict[str, Any] | None = Field(
        description="additional metadata for the payment (max 30 keys)",
        default=None,
    )


class PaymentTransaction(BaseSchema):
    """Schema generated for PaymentTransaction.

    Attributes:
        value (int | None): value of the transaction generated by the payment in cents
        end_to_end_id (str | None): endToEndId of the transaction generated by the
            payment
        time (str | None): time the transaction generated by the payment happened
        provider_rejected_reason (str | None): providerRejectedReason
        debit_party (Party | None): Undocumented in the spec.
        credit_party (Party | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    value: int | None = Field(
        description="value of the transaction generated by the payment in cents",
        default=None,
    )
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        description="endToEndId of the transaction generated by the payment",
        default=None,
    )
    time: str | None = Field(
        description="time the transaction generated by the payment happened",
        default=None,
    )
    provider_rejected_reason: str | None = Field(
        validation_alias="providerRejectedReason",
        serialization_alias="providerRejectedReason",
        description="providerRejectedReason",
        default=None,
    )
    debit_party: Party | None = Field(
        validation_alias="debitParty",
        serialization_alias="debitParty",
        default=None,
    )
    credit_party: Party | None = Field(
        validation_alias="creditParty",
        serialization_alias="creditParty",
        default=None,
    )


class PixWithdrawTransaction(BaseSchema):
    """Schema generated for PixWithdrawTransaction.

    Attributes:
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        transaction_id (str | None): Undocumented in the spec.
        info_pagador (str | None): Undocumented in the spec.
        end_to_end_id_2 (str | None): Undocumented in the spec.
        payer (Customer | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndID",
        serialization_alias="endToEndID",
        default=None,
    )
    transaction_id: str | None = Field(
        validation_alias="transactionID",
        serialization_alias="transactionID",
        default=None,
    )
    info_pagador: str | None = Field(
        validation_alias="infoPagador",
        serialization_alias="infoPagador",
        default=None,
    )
    end_to_end_id_2: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    payer: Customer | None = None
    type: str | None = None


class PostApiV1AccountByAccountIdWithdrawResponseWithdraw(BaseSchema):
    """Schema generated for PostApiV1AccountByAccountIdWithdrawResponseWithdraw.

    Attributes:
        account (CompanyBankAccount | None): Undocumented in the spec.
        transaction (WithdrawTransaction | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    account: CompanyBankAccount | None = None
    transaction: WithdrawTransaction | None = None


class PostApiV1AccountResponse(BaseSchema):
    """Schema generated for PostApiV1AccountResponse.

    Attributes:
        account (CompanyBankAccount | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    account: CompanyBankAccount | None = None


class PostApiV1CustomerResponse(BaseSchema):
    """Schema generated for PostApiV1CustomerResponse.

    Attributes:
        customer (Customer | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    customer: Customer | None = None


class PostApiV1DecodeEmvResponseCobLocation(BaseSchema):
    """Resolved COB (charge) location details when the EMV points to a COB endpoint.

    Attributes:
        is_valid (bool | None): Undocumented in the spec.
        location_errors (list[str]): Undocumented in the spec.
        payload (PostApiV1DecodeEmvResponseCobLocationPayload | None): Undocumented in
            the spec.
        url (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    is_valid: bool | None = Field(
        validation_alias="isValid",
        serialization_alias="isValid",
        default=None,
    )
    location_errors: list[str] = Field(
        validation_alias="locationErrors",
        serialization_alias="locationErrors",
        default_factory=list,
    )
    payload: PostApiV1DecodeEmvResponseCobLocationPayload | None = None
    url: str | None = None


class PostApiV1DecodeEmvResponseRecLocationPayload(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponseRecLocationPayload.

    Attributes:
        updates (list[PostApiV1DecodeEmvResponseRecLocationPayloadUpdatesItem]):
            Undocumented in the spec.
        calendar (PostApiV1DecodeEmvResponseRecLocationPayloadCalendar | None):
            Undocumented in the spec.
        id_rec (str | None): Undocumented in the spec.
        retry_policy (str | None): Undocumented in the spec.
        receiver (PostApiV1DecodeEmvResponseRecLocationPayloadReceiver | None):
            Undocumented in the spec.
        value (PostApiV1DecodeEmvResponseRecLocationPayloadValue | None): Undocumented
            in the spec.
        link (PostApiV1DecodeEmvResponseRecLocationPayloadLink | None): Undocumented in
            the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    updates: list[PostApiV1DecodeEmvResponseRecLocationPayloadUpdatesItem] = Field(
        default_factory=list,
    )
    calendar: PostApiV1DecodeEmvResponseRecLocationPayloadCalendar | None = None
    id_rec: str | None = Field(
        validation_alias="idRec",
        serialization_alias="idRec",
        default=None,
    )
    retry_policy: str | None = Field(
        validation_alias="retryPolicy",
        serialization_alias="retryPolicy",
        default=None,
    )
    receiver: PostApiV1DecodeEmvResponseRecLocationPayloadReceiver | None = None
    value: PostApiV1DecodeEmvResponseRecLocationPayloadValue | None = None
    link: PostApiV1DecodeEmvResponseRecLocationPayloadLink | None = None


class PostApiV1InvoiceIntegrationResponseIntegration(BaseSchema):
    """Schema generated for PostApiV1InvoiceIntegrationResponseIntegration.

    Attributes:
        id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        is_active (bool | None): Undocumented in the spec.
        metadata (PostApiV1InvoiceIntegrationResponseIntegrationMetadata | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = None
    type: str | None = None
    status: str | None = None
    is_active: bool | None = Field(
        validation_alias="isActive",
        serialization_alias="isActive",
        default=None,
    )
    metadata: PostApiV1InvoiceIntegrationResponseIntegrationMetadata | None = None


class PostApiV1InvoiceResponse(BaseSchema):
    """Schema generated for PostApiV1InvoiceResponse.

    Attributes:
        invoice (PostApiV1InvoiceResponseInvoice | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    invoice: PostApiV1InvoiceResponseInvoice | None = None


class PostApiV1PaymentBodyManual(BaseSchema):
    """Manual.

    Attributes:
        type (PaymentCreatePayloadPixKeyType): type of the payment
        value (int): value of the requested payment in cents
        correlation_id (str): a unique identifier for your payment
        pix_key_end_to_end_id (str | None): the end to end id of the pix key used for
            track pix key consultations
        psp (str): the PSP (Payment Service Provider) identifier
        holder (PostApiV1PaymentBodyManualHolder): Undocumented in the spec.
        account (PostApiV1PaymentBodyManualAccount): Undocumented in the spec.
        metadata (dict[str, Any] | None): additional metadata for the payment (max 30
            keys)
        auto_approve (bool | None): When true, creates and approves the payment in a
            single call returning the enriched response. Defaults to false.
    """

    model_config = ConfigDict(populate_by_name=True)

    type: PaymentCreatePayloadPixKeyType = Field(description="type of the payment")
    value: int = Field(description="value of the requested payment in cents")
    correlation_id: str = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="a unique identifier for your payment",
    )
    pix_key_end_to_end_id: str | None = Field(
        validation_alias="pixKeyEndToEndId",
        serialization_alias="pixKeyEndToEndId",
        description=(
            "the end to end id of the pix key used for track pix key consultations"
        ),
        default=None,
    )
    psp: str = Field(description="the PSP (Payment Service Provider) identifier")
    holder: PostApiV1PaymentBodyManualHolder
    account: PostApiV1PaymentBodyManualAccount
    metadata: dict[str, Any] | None = Field(
        description="additional metadata for the payment (max 30 keys)",
        default=None,
    )
    auto_approve: bool | None = Field(
        validation_alias="autoApprove",
        serialization_alias="autoApprove",
        description=(
            "When true, creates and approves the payment in a single call returning "
            "the enriched response. Defaults to false."
        ),
        default=None,
    )


class PostApiV1SubaccountByIdWithdrawResponse(BaseSchema):
    """Schema generated for PostApiV1SubaccountByIdWithdrawResponse.

    Attributes:
        withdraw (PostApiV1SubaccountByIdWithdrawResponseWithdraw | None): Undocumented
            in the spec.
    """

    model_config = ConfigDict(extra="allow")

    withdraw: PostApiV1SubaccountByIdWithdrawResponseWithdraw | None = None


class PreRegistrationPayloadObject(BaseSchema):
    """Schema generated for PreRegistrationPayloadObject.

    Attributes:
        pre_registration (PreRegistrationObject | None): Undocumented in the spec.
        user (PreRegistrationUserObject | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pre_registration: PreRegistrationObject | None = Field(
        validation_alias="preRegistration",
        serialization_alias="preRegistration",
        default=None,
    )
    user: PreRegistrationUserObject | None = None


class Subscription(BaseSchema):
    """Schema generated for Subscription.

    Attributes:
        global_id (str | None): The globalID of the subscription.
        value (int | None): Value in cents of the subscription
        name (str | None): Name of the subscription
        customer (Customer | None): Undocumented in the spec.
        day_generate_charge (int | None): Day of the month that the charges will be
            generated
        type (SubscriptionType | None): Type of the subscription
        frequency (SubscriptionFrequency | None): Frequency of the subscription — the
            interval between charges:   - `WEEKLY`: every week   - `MONTHLY`: every
            month   - `BIMONTHLY`: every 2 months   - `QUARTERLY`: every 3 months
            (trimestral)   - `SEMIANNUALLY`: every 6 months   - `ANNUALLY`: every 12
            months For Pix Automático (`type: PIX_RECURRING`), only the frequencies
            allowed by the Central Bank apply: `WEEKLY`, `MONTHLY`, `QUARTERLY`,
            `SEMIANNUALLY` and `ANNUALLY` (`BIMONTHLY` is not supported).
        installments_count (int | None): Total number of installments currently linked
            to the subscription. `null` when the subscription has no `dateEnd`
            (open-ended). Mirrors the GraphQL `installmentsCount` field.
        is_active (bool | None): Undocumented in the spec.
        status (ChargeStatus | None): Undocumented in the spec.
        correlation_id (str | None): Your correlation ID to keep track of this
            subscription
        payment_link_url (str | None): Payment link to this subscription
        addtional_info (list[SubscriptionAddtionalInfoItem]): Undocumented in the spec.
        pix_recurring_options (SubscriptionPixRecurringOptions | None): Pix automatic
            options
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        description="The globalID of the subscription.",
        default=None,
    )
    value: int | None = Field(
        description="Value in cents of the subscription",
        default=None,
    )
    name: str | None = Field(description="Name of the subscription", default=None)
    customer: Customer | None = None
    day_generate_charge: int | None = Field(
        validation_alias="dayGenerateCharge",
        serialization_alias="dayGenerateCharge",
        description="Day of the month that the charges will be generated",
        default=None,
    )
    type: SubscriptionType | None = Field(
        description="Type of the subscription",
        default=None,
    )
    frequency: SubscriptionFrequency | None = Field(
        description=(
            "Frequency of the subscription — the interval between charges:\n  - "
            "`WEEKLY`: every week\n  - `MONTHLY`: every month\n  - `BIMONTHLY`: every "
            "2 months\n  - `QUARTERLY`: every 3 months (trimestral)\n  - "
            "`SEMIANNUALLY`: every 6 months\n  - `ANNUALLY`: every 12 months\nFor Pix "
            "Automático (`type: PIX_RECURRING`), only the frequencies allowed by the "
            "Central Bank apply: `WEEKLY`, `MONTHLY`, `QUARTERLY`, `SEMIANNUALLY` and "
            "`ANNUALLY` (`BIMONTHLY` is not supported)."
        ),
        default=None,
    )
    installments_count: int | None = Field(
        validation_alias="installmentsCount",
        serialization_alias="installmentsCount",
        description=(
            "Total number of installments currently linked to the subscription. `null` "
            "when the subscription has no `dateEnd` (open-ended). Mirrors the GraphQL "
            "`installmentsCount` field."
        ),
        default=None,
    )
    is_active: bool | None = Field(
        validation_alias="isActive",
        serialization_alias="isActive",
        default=None,
    )
    status: ChargeStatus | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID to keep track of this subscription",
        default=None,
    )
    payment_link_url: str | None = Field(
        validation_alias="paymentLinkUrl",
        serialization_alias="paymentLinkUrl",
        description="Payment link to this subscription",
        default=None,
    )
    addtional_info: list[SubscriptionAddtionalInfoItem] = Field(
        validation_alias="addtionalInfo",
        serialization_alias="addtionalInfo",
        default_factory=list,
    )
    pix_recurring_options: SubscriptionPixRecurringOptions | None = Field(
        validation_alias="pixRecurringOptions",
        serialization_alias="pixRecurringOptions",
        description="Pix automatic options",
        default=None,
    )


class SubscriptionPayload(BaseSchema):
    """Schema generated for SubscriptionPayload.

    Attributes:
        customer (SubscriptionPayloadCustomer): Customer of this subscription
        value (int): Value in cents of this subscription
        name (str | None): Name of the subscription
        comment (str | None): Comment to be show in QR Code
        day_generate_charge (int | datetime | None): Undocumented in the spec.
        frequency (SubscriptionFrequency | None): Frequency of the subscription — the
            interval between charges (defaults to `MONTHLY` when omitted):   - `WEEKLY`:
            every week   - `MONTHLY`: every month   - `BIMONTHLY`: every 2 months   -
            `QUARTERLY`: every 3 months (trimestral)   - `SEMIANNUALLY`: every 6 months
            - `ANNUALLY`: every 12 months For Pix Automático (`type: PIX_RECURRING`),
            only the frequencies allowed by the Central Bank apply: `WEEKLY`, `MONTHLY`,
            `QUARTERLY`, `SEMIANNUALLY` and `ANNUALLY` (`BIMONTHLY` is not supported).
        type (SubscriptionPayloadType): Type of the subscription
        day_due (int | None): Days that the charge will take to expire from the
            generation day.
        installment_count (int | None): number of installments (optional)
        correlation_id (str): Your correlation ID to keep track of this subscription
        additional_info (list[SubscriptionPayloadAdditionalInfoItem]): Undocumented in
            the spec.
        pix_recurring_options (SubscriptionPayloadPixRecurringOptions | None): Pix
            automatic options
        charge_type (ChargeType | None): Charge method used for each charge generated by
            the subscription (defaults to `DYNAMIC` when omitted):   - `DYNAMIC`: a
            standard Pix charge.   - `OVERDUE`: a Pix charge with a due date (supports
            interest and fine).   - `BOLETO`: each charge is issued as a boleto, which
            can also be paid through its Pix QR Code. Requires the boleto feature
            enabled for your account — talk to our support. Only applies to `type:
            RECURRENT` subscriptions; it is ignored for Pix Automático (`type:
            PIX_RECURRING`).
    """

    model_config = ConfigDict(populate_by_name=True)

    customer: SubscriptionPayloadCustomer = Field(
        description="Customer of this subscription",
    )
    value: int = Field(description="Value in cents of this subscription")
    name: str | None = Field(description="Name of the subscription", default=None)
    comment: str | None = Field(
        description="Comment to be show in QR Code",
        default=None,
    )
    day_generate_charge: int | datetime | None = Field(
        validation_alias="dayGenerateCharge",
        serialization_alias="dayGenerateCharge",
        default=None,
    )
    frequency: SubscriptionFrequency | None = Field(
        description=(
            "Frequency of the subscription — the interval between charges (defaults to "
            "`MONTHLY` when omitted):\n  - `WEEKLY`: every week\n  - `MONTHLY`: every "
            "month\n  - `BIMONTHLY`: every 2 months\n  - `QUARTERLY`: every 3 months "
            "(trimestral)\n  - `SEMIANNUALLY`: every 6 months\n  - `ANNUALLY`: every "
            "12 months\nFor Pix Automático (`type: PIX_RECURRING`), only the "
            "frequencies allowed by the Central Bank apply: `WEEKLY`, `MONTHLY`, "
            "`QUARTERLY`, `SEMIANNUALLY` and `ANNUALLY` (`BIMONTHLY` is not supported)."
        ),
        default=None,
    )
    type: SubscriptionPayloadType = Field(description="Type of the subscription")
    day_due: int | None = Field(
        validation_alias="dayDue",
        serialization_alias="dayDue",
        description="Days that the charge will take to expire from the generation day.",
        ge=3,
        default=None,
    )
    installment_count: int | None = Field(
        validation_alias="installmentCount",
        serialization_alias="installmentCount",
        description="number of installments (optional)",
        default=None,
    )
    correlation_id: str = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID to keep track of this subscription",
    )
    additional_info: list[SubscriptionPayloadAdditionalInfoItem] = Field(
        validation_alias="additionalInfo",
        serialization_alias="additionalInfo",
        default_factory=list,
    )
    pix_recurring_options: SubscriptionPayloadPixRecurringOptions | None = Field(
        validation_alias="pixRecurringOptions",
        serialization_alias="pixRecurringOptions",
        description="Pix automatic options",
        default=None,
    )
    charge_type: ChargeType | None = Field(
        validation_alias="chargeType",
        serialization_alias="chargeType",
        description=(
            "Charge method used for each charge generated by the subscription "
            "(defaults to `DYNAMIC` when omitted):\n  - `DYNAMIC`: a standard Pix "
            "charge.\n  - `OVERDUE`: a Pix charge with a due date (supports interest "
            "and fine).\n  - `BOLETO`: each charge is issued as a boleto, which can "
            "also be paid through its Pix QR Code. Requires the boleto feature enabled "
            "for your account — talk to our support.\nOnly applies to `type: "
            "RECURRENT` subscriptions; it is ignored for Pix Automático (`type: "
            "PIX_RECURRING`)."
        ),
        default=None,
    )


class Charge(BaseSchema):
    """Schema generated for Charge.

    Attributes:
        value (int | None): Undocumented in the spec.
        customer (Customer | None): Undocumented in the spec.
        type (ChargeType | None): Charge type is used to determine whether a charge will
            have a deadline, fines and interests
        comment (str | None): Undocumented in the spec.
        br_code (str | None): EMV BRCode to be rendered as a QRCode
        status (ChargeStatus | None): Undocumented in the spec.
        correlation_id (str | None): Your correlation ID to keep track of this charge
        payment_link_id (str | None): Payment Link ID, used on payment link and to
            retrieve qrcode image
        payment_link_url (Any | None): Payment Link URL to be shared with customers
        global_id (Any | None): External ID of this charge
        transaction_id (Any | None): unique uuid used as the txid from Pix into the
            provider from your openpix account. This field link the charge with the
            transaction when paid.
        identifier (str | None): Custom identifier for EMV
        qr_code_image (Any | None): QRCode image link URL
        additional_info (list[ChargeAdditionalInfoItem]): Additional info of the charge
        pix_key (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        updated_at (str | None): Undocumented in the spec.
        expires_in (str | None): Undocumented in the spec.
        expires_date (str | None): Expiration date of the charge in ISO 8601 format.
        due_date (str | None): Due date for OVERDUE, BOLETO, or subscription charges in
            ISO 8601 format.
        subscription (Subscription | None): Undocumented in the spec.
        payment_methods (ChargePaymentMethods | None): Undocumented in the spec.
        fee (int | None): Fee charged on this charge, in cents. Returned by the API at
            the top level of the charge object; absent from the specification, which
            models a fee only under `paymentMethods.pix`.
        discount (int | None): Discount applied to this charge, in cents. Returned by
            the API and absent from the specification.
        value_with_discount (int | None): Charge value after the discount, in cents.
            Returned by the API and absent from the specification.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    value: int | None = None
    customer: Customer | None = None
    type: ChargeType | None = Field(
        description=(
            "Charge type is used to determine whether a charge will have a deadline, "
            "fines and interests"
        ),
        default=None,
    )
    comment: str | None = None
    br_code: str | None = Field(
        validation_alias="brCode",
        serialization_alias="brCode",
        description="EMV BRCode to be rendered as a QRCode",
        default=None,
    )
    status: ChargeStatus | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID to keep track of this charge",
        default=None,
    )
    payment_link_id: str | None = Field(
        validation_alias="paymentLinkID",
        serialization_alias="paymentLinkID",
        description=(
            "Payment Link ID, used on payment link and to retrieve qrcode image"
        ),
        default=None,
    )
    payment_link_url: Any | None = Field(
        validation_alias="paymentLinkUrl",
        serialization_alias="paymentLinkUrl",
        description="Payment Link URL to be shared with customers",
        default=None,
    )
    global_id: Any | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        description="External ID of this charge",
        default=None,
    )
    transaction_id: Any | None = Field(
        validation_alias="transactionID",
        serialization_alias="transactionID",
        description=(
            "unique uuid used as the txid from Pix into the provider from your openpix "
            "account. This field link the charge with the transaction when paid."
        ),
        default=None,
    )
    identifier: str | None = Field(
        description="Custom identifier for EMV",
        default=None,
    )
    qr_code_image: Any | None = Field(
        validation_alias="qrCodeImage",
        serialization_alias="qrCodeImage",
        description="QRCode image link URL",
        default=None,
    )
    additional_info: list[ChargeAdditionalInfoItem] = Field(
        validation_alias="additionalInfo",
        serialization_alias="additionalInfo",
        description="Additional info of the charge",
        default_factory=list,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    updated_at: str | None = Field(
        validation_alias="updatedAt",
        serialization_alias="updatedAt",
        default=None,
    )
    expires_in: str | None = Field(
        validation_alias="expiresIn",
        serialization_alias="expiresIn",
        default=None,
    )
    expires_date: str | None = Field(
        validation_alias="expiresDate",
        serialization_alias="expiresDate",
        description="Expiration date of the charge in ISO 8601 format.",
        default=None,
    )
    due_date: str | None = Field(
        validation_alias="dueDate",
        serialization_alias="dueDate",
        description=(
            "Due date for OVERDUE, BOLETO, or subscription charges in ISO 8601 format."
        ),
        default=None,
    )
    subscription: Subscription | None = None
    payment_methods: ChargePaymentMethods | None = Field(
        validation_alias="paymentMethods",
        serialization_alias="paymentMethods",
        default=None,
    )
    fee: int | None = Field(
        description=(
            "Fee charged on this charge, in cents. Returned by the API at the top "
            "level of the charge object; absent from the specification, which models a "
            "fee only under `paymentMethods.pix`."
        ),
        default=None,
    )
    discount: int | None = Field(
        description=(
            "Discount applied to this charge, in cents. Returned by the API and absent "
            "from the specification."
        ),
        default=None,
    )
    value_with_discount: int | None = Field(
        validation_alias="valueWithDiscount",
        serialization_alias="valueWithDiscount",
        description=(
            "Charge value after the discount, in cents. Returned by the API and absent "
            "from the specification."
        ),
        default=None,
    )


class GetApiV1AccountResponse(BaseSchema):
    """Schema generated for GetApiV1AccountResponse.

    Attributes:
        accounts (list[CompanyBankAccount]): Undocumented in the spec.
        page_info (GetApiV1AccountResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    accounts: list[CompanyBankAccount] = Field(default_factory=list)
    page_info: GetApiV1AccountResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class GetApiV1CustomerResponse(BaseSchema):
    """Schema generated for GetApiV1CustomerResponse.

    Attributes:
        customers (list[Customer]): Undocumented in the spec.
        page_info (GetApiV1CustomerResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    customers: list[Customer] = Field(default_factory=list)
    page_info: GetApiV1CustomerResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class GetApiV1DisputeResponse(BaseSchema):
    """Schema generated for GetApiV1DisputeResponse.

    Attributes:
        disputes (list[GetApiV1DisputeResponseDisputesItem]): Undocumented in the spec.
        page_info (GetApiV1DisputeResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    disputes: list[GetApiV1DisputeResponseDisputesItem] = Field(default_factory=list)
    page_info: GetApiV1DisputeResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class GetApiV1InstallmentsByIdResponse(BaseSchema):
    """Schema generated for GetApiV1InstallmentsByIdResponse.

    Attributes:
        installment (Installment | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    installment: Installment | None = None


class GetApiV1PartnerAffiliateResponse(BaseSchema):
    """Schema generated for GetApiV1PartnerAffiliateResponse.

    Attributes:
        affiliates (list[GetApiV1PartnerAffiliateResponseAffiliatesItem]): Undocumented
            in the spec.
        page_info (GetApiV1PartnerAffiliateResponsePageInfo | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    affiliates: list[GetApiV1PartnerAffiliateResponseAffiliatesItem] = Field(
        default_factory=list,
    )
    page_info: GetApiV1PartnerAffiliateResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class GetApiV1PartnerCompanyByTaxIdResponse(BaseSchema):
    """Schema generated for GetApiV1PartnerCompanyByTaxIdResponse.

    Attributes:
        pre_registration (GetApiV1PartnerCompanyByTaxIdResponsePreRegistration | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pre_registration: GetApiV1PartnerCompanyByTaxIdResponsePreRegistration | None = (
        Field(
            validation_alias="preRegistration",
            serialization_alias="preRegistration",
            default=None,
        )
    )


class GetApiV1PartnerCompanyResponse(BaseSchema):
    """Schema generated for GetApiV1PartnerCompanyResponse.

    Attributes:
        pre_registrations (list[GetApiV1PartnerCompanyResponsePreRegistrationsItem]):
            Undocumented in the spec.
        page_info (GetApiV1PartnerCompanyResponsePageInfo | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pre_registrations: list[GetApiV1PartnerCompanyResponsePreRegistrationsItem] = Field(
        validation_alias="preRegistrations",
        serialization_alias="preRegistrations",
        default_factory=list,
    )
    page_info: GetApiV1PartnerCompanyResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class GetApiV1PaymentByIdResponse(BaseSchema):
    """Schema generated for GetApiV1PaymentByIdResponse.

    Attributes:
        payment (Payment | None): Undocumented in the spec.
        transaction (PaymentTransaction | None): Undocumented in the spec.
        destination (PaymentDestination | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    payment: Payment | None = None
    transaction: PaymentTransaction | None = None
    destination: PaymentDestination | None = None


class GetApiV1PaymentResponsePaymentsItem(BaseSchema):
    """Schema generated for GetApiV1PaymentResponsePaymentsItem.

    Attributes:
        payment (Payment | None): Undocumented in the spec.
        transaction (PaymentTransaction | None): Undocumented in the spec.
        destination (PaymentDestination | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    payment: Payment | None = None
    transaction: PaymentTransaction | None = None
    destination: PaymentDestination | None = None


class GetApiV1PixKeysTokensLogsResponse(BaseSchema):
    """Schema generated for GetApiV1PixKeysTokensLogsResponse.

    Attributes:
        logs (list[TokenBucketLog]): Undocumented in the spec.
        page_info (GetApiV1PixKeysTokensLogsResponsePageInfo | None): Undocumented in
            the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    logs: list[TokenBucketLog] = Field(default_factory=list)
    page_info: GetApiV1PixKeysTokensLogsResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class GetApiV1QrcodeStaticResponse(BaseSchema):
    """Schema generated for GetApiV1QrcodeStaticResponse.

    Attributes:
        pix_qr_codes (list[PixQrCode]): Undocumented in the spec.
        page_info (GetApiV1QrcodeStaticResponsePageInfo | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pix_qr_codes: list[PixQrCode] = Field(
        validation_alias="pixQrCodes",
        serialization_alias="pixQrCodes",
        default_factory=list,
    )
    page_info: GetApiV1QrcodeStaticResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class GetApiV1RefundResponse(BaseSchema):
    """Schema generated for GetApiV1RefundResponse.

    Attributes:
        refunds (list[Refund]): Undocumented in the spec.
        page_info (GetApiV1RefundResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    refunds: list[Refund] = Field(default_factory=list)
    page_info: GetApiV1RefundResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class GetApiV1SubaccountResponse(BaseSchema):
    """Schema generated for GetApiV1SubaccountResponse.

    Attributes:
        subaccounts (list[GetApiV1SubaccountResponseSubaccountsItem]): Undocumented in
            the spec.
        page_info (GetApiV1SubaccountResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    subaccounts: list[GetApiV1SubaccountResponseSubaccountsItem] = Field(
        default_factory=list,
    )
    page_info: GetApiV1SubaccountResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class GetApiV1SubscriptionsByIdInstallmentsResponse(BaseSchema):
    """Schema generated for GetApiV1SubscriptionsByIdInstallmentsResponse.

    Attributes:
        installments (list[Installment]): Undocumented in the spec.
        page_info (Pagination | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    installments: list[Installment] = Field(default_factory=list)
    page_info: Pagination | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class GetApiV1SubscriptionsByIdResponse(BaseSchema):
    """Schema generated for GetApiV1SubscriptionsByIdResponse.

    Attributes:
        subscription (Subscription | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    subscription: Subscription | None = None


class GetApiV1SubscriptionsResponse(BaseSchema):
    """Schema generated for GetApiV1SubscriptionsResponse.

    Attributes:
        subscriptions (list[Subscription]): Undocumented in the spec.
        page_info (Pagination | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    subscriptions: list[Subscription] = Field(default_factory=list)
    page_info: Pagination | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class GetApiV1WebhookResponse(BaseSchema):
    """Schema generated for GetApiV1WebhookResponse.

    Attributes:
        webhooks (list[Webhook]): Undocumented in the spec.
        page_info (GetApiV1WebhookResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    webhooks: list[Webhook] = Field(default_factory=list)
    page_info: GetApiV1WebhookResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


PaymentCreatePayload = (
    PaymentCreatePayloadPixKey
    | PaymentCreatePayloadQrCode
    | PaymentCreatePayloadManual
    | PaymentCreatePayloadBoleto
)
"""Schema generated for PaymentCreatePayload."""


class PixKeyFraudValidationData(BaseSchema):
    """Schema generated for PixKeyFraudValidationData.

    Attributes:
        key_statistics (KeyOrOwnerStatistics | None): Undocumented in the spec.
        owner_statistics (KeyOrOwnerStatistics | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    key_statistics: KeyOrOwnerStatistics | None = Field(
        validation_alias="keyStatistics",
        serialization_alias="keyStatistics",
        default=None,
    )
    owner_statistics: KeyOrOwnerStatistics | None = Field(
        validation_alias="ownerStatistics",
        serialization_alias="ownerStatistics",
        default=None,
    )


class PostApiV1AccountByAccountIdWithdrawResponse(BaseSchema):
    """Schema generated for PostApiV1AccountByAccountIdWithdrawResponse.

    Attributes:
        withdraw (PostApiV1AccountByAccountIdWithdrawResponseWithdraw | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    withdraw: PostApiV1AccountByAccountIdWithdrawResponseWithdraw | None = None


class PostApiV1DecodeEmvResponseRecLocation(BaseSchema):
    """Resolved REC (request for payment) location details when EMV points to a REC
    endpoint.

    Attributes:
        is_valid (bool | None): Undocumented in the spec.
        location_errors (list[str]): Undocumented in the spec.
        payload (PostApiV1DecodeEmvResponseRecLocationPayload | None): Undocumented in
            the spec.
        url (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    is_valid: bool | None = Field(
        validation_alias="isValid",
        serialization_alias="isValid",
        default=None,
    )
    location_errors: list[str] = Field(
        validation_alias="locationErrors",
        serialization_alias="locationErrors",
        default_factory=list,
    )
    payload: PostApiV1DecodeEmvResponseRecLocationPayload | None = None
    url: str | None = None


class PostApiV1InvoiceIntegrationResponse(BaseSchema):
    """Schema generated for PostApiV1InvoiceIntegrationResponse.

    Attributes:
        integration (PostApiV1InvoiceIntegrationResponseIntegration | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    integration: PostApiV1InvoiceIntegrationResponseIntegration | None = None


class PostApiV1KycOnboardingResponse(BaseSchema):
    """Schema generated for PostApiV1KycOnboardingResponse.

    Attributes:
        link_onboarding (str | None): Undocumented in the spec.
        redirect_url (str | None): URL para redirecionamento pos-onboarding (echo do
            valor enviado na criacao do link).
        account_register (KycOnboardingAccountRegister | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    link_onboarding: str | None = Field(
        validation_alias="linkOnboarding",
        serialization_alias="linkOnboarding",
        examples=["https://kyc.woovi.com/onboarding/QWNjb3VudFJlZ2lzdGVyOjY5..."],
        default=None,
    )
    redirect_url: str | None = Field(
        validation_alias="redirectUrl",
        serialization_alias="redirectUrl",
        description=(
            "URL para redirecionamento pos-onboarding (echo do valor enviado na "
            "criacao do link)."
        ),
        examples=["https://partner.example.com/kyc-done"],
        default=None,
    )
    account_register: KycOnboardingAccountRegister | None = Field(
        validation_alias="accountRegister",
        serialization_alias="accountRegister",
        default=None,
    )


class PostApiV1PaymentApproveResponse(BaseSchema):
    """Schema generated for PostApiV1PaymentApproveResponse.

    Attributes:
        payment (Payment | None): Undocumented in the spec.
        transaction (PaymentTransaction | None): Undocumented in the spec.
        destination (PaymentDestination | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    payment: Payment | None = None
    transaction: PaymentTransaction | None = None
    destination: PaymentDestination | None = None


PostApiV1PaymentBody = (
    PostApiV1PaymentBodyPixKey
    | PostApiV1PaymentBodyQrCode
    | PostApiV1PaymentBodyManual
    | PostApiV1PaymentBodyBoleto
)
"""Request body of PostApiV1PaymentBody, one variant per shape."""


class PostApiV1PaymentResponse(BaseSchema):
    """Schema generated for PostApiV1PaymentResponse.

    Attributes:
        payment (Payment | None): Undocumented in the spec.
        transaction (PaymentTransaction | None): Undocumented in the spec.
        destination (PaymentDestination | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    payment: Payment | None = None
    transaction: PaymentTransaction | None = None
    destination: PaymentDestination | None = None


class PostApiV1SubscriptionsResponse(BaseSchema):
    """Schema generated for PostApiV1SubscriptionsResponse.

    Attributes:
        subscription (Subscription | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    subscription: Subscription | None = None


class GetApiV1ChargeByIdResponse(BaseSchema):
    """Schema generated for GetApiV1ChargeByIdResponse.

    Attributes:
        charge (Charge | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    charge: Charge | None = None


class GetApiV1ChargeResponse(BaseSchema):
    """Schema generated for GetApiV1ChargeResponse.

    Attributes:
        charges (list[Charge]): Undocumented in the spec.
        page_info (GetApiV1ChargeResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    charges: list[Charge] = Field(default_factory=list)
    page_info: GetApiV1ChargeResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class GetApiV1PaymentResponse(BaseSchema):
    """Schema generated for GetApiV1PaymentResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        payments (list[GetApiV1PaymentResponsePaymentsItem]): Undocumented in the spec.
        page_info (GetApiV1PaymentResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = None
    payments: list[GetApiV1PaymentResponsePaymentsItem] = Field(default_factory=list)
    page_info: GetApiV1PaymentResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class PixKeyFraudValidationResponse(BaseSchema):
    """Schema generated for PixKeyFraudValidationResponse.

    Attributes:
        success (bool | None): Undocumented in the spec.
        data (PixKeyFraudValidationData | None): Undocumented in the spec.
    """

    success: bool | None = None
    data: PixKeyFraudValidationData | None = None


class PostApiV1ChargeResponse(BaseSchema):
    """Schema generated for PostApiV1ChargeResponse.

    Attributes:
        charge (Charge | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        br_code (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    charge: Charge | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    br_code: str | None = Field(
        validation_alias="brCode",
        serialization_alias="brCode",
        default=None,
    )


class PostApiV1DecodeEmvResponse(BaseSchema):
    """Schema generated for PostApiV1DecodeEmvResponse.

    Attributes:
        emv (PostApiV1DecodeEmvResponseEmv | None): Undocumented in the spec.
        cob_location (PostApiV1DecodeEmvResponseCobLocation | None): Resolved COB
            (charge) location details when the EMV points to a COB endpoint
        rec_location (PostApiV1DecodeEmvResponseRecLocation | None): Resolved REC
            (request for payment) location details when EMV points to a REC endpoint
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    emv: PostApiV1DecodeEmvResponseEmv | None = None
    cob_location: PostApiV1DecodeEmvResponseCobLocation | None = Field(
        validation_alias="cobLocation",
        serialization_alias="cobLocation",
        description=(
            "Resolved COB (charge) location details when the EMV points to a COB "
            "endpoint"
        ),
        default=None,
    )
    rec_location: PostApiV1DecodeEmvResponseRecLocation | None = Field(
        validation_alias="recLocation",
        serialization_alias="recLocation",
        description=(
            "Resolved REC (request for payment) location details when EMV points to a "
            "REC endpoint"
        ),
        default=None,
    )


class Transaction(BaseSchema):
    """Schema generated for Transaction.

    Attributes:
        charge (Charge | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        transaction_id (str | None): Undocumented in the spec.
        info_pagador (str | None): Undocumented in the spec.
        end_to_end_id_2 (str | None): Undocumented in the spec.
        customer (Customer | None): Undocumented in the spec.
        withdraw (PixWithdrawTransaction | None): Undocumented in the spec.
        payer (Customer | None): Undocumented in the spec.
        type (TransactionType | None): Pix Transaction type
        status (TransactionStatus | None): Pix Transaction type
        global_id (Any | None): External ID of this transaction
        pix_qr_code (PixQrCode | None): Undocumented in the spec.
        webhook_sent (list[TransactionWebhookSentItem]): List of webhook delivery
            attempts for this transaction, sorted by most recent first. Each item
            contains the event name as key with status and time, plus an isRetry flag.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    charge: Charge | None = None
    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndID",
        serialization_alias="endToEndID",
        default=None,
    )
    transaction_id: str | None = Field(
        validation_alias="transactionID",
        serialization_alias="transactionID",
        default=None,
    )
    info_pagador: str | None = Field(
        validation_alias="infoPagador",
        serialization_alias="infoPagador",
        default=None,
    )
    end_to_end_id_2: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    customer: Customer | None = None
    withdraw: PixWithdrawTransaction | None = None
    payer: Customer | None = None
    type: TransactionType | None = Field(
        description="Pix Transaction type",
        default=None,
    )
    status: TransactionStatus | None = Field(
        description="Pix Transaction type",
        default=None,
    )
    global_id: Any | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        description="External ID of this transaction",
        default=None,
    )
    pix_qr_code: PixQrCode | None = Field(
        validation_alias="pixQrCode",
        serialization_alias="pixQrCode",
        default=None,
    )
    webhook_sent: list[TransactionWebhookSentItem] = Field(
        validation_alias="webhookSent",
        serialization_alias="webhookSent",
        description=(
            "List of webhook delivery attempts for this transaction, sorted by most "
            "recent first. Each item contains the event name as key with status and "
            "time, plus an isRetry flag."
        ),
        examples=[
            [
                {
                    "OPENPIX:TRANSACTION_RECEIVED": {
                        "status": 200,
                        "time": "2025-01-01T00:00:00.000Z",
                    },
                    "isRetry": False,
                },
                {
                    "OPENPIX:TRANSACTION_RECEIVED": {
                        "status": 404,
                        "time": "2025-01-02T00:00:00.000Z",
                    },
                    "isRetry": True,
                },
            ],
        ],
        default_factory=list,
    )


class GetApiV1TransactionByIdResponse(BaseSchema):
    """Schema generated for GetApiV1TransactionByIdResponse.

    Attributes:
        transaction (Transaction | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    transaction: Transaction | None = None


class GetApiV1TransactionResponse(BaseSchema):
    """Schema generated for GetApiV1TransactionResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        transactions (list[Transaction]): Undocumented in the spec.
        page_info (GetApiV1TransactionResponsePageInfo | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = None
    transactions: list[Transaction] = Field(default_factory=list)
    page_info: GetApiV1TransactionResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


__all__: list[str] = [
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
]
