"""Pydantic schemas generated from the Woovi OpenAPI specification.

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

from pydantic import ConfigDict, EmailStr, Field

from tempest_fastapi_sdk import BaseSchema, BaseStrEnum


class AnticipationBeneficiaryTaxIdType(BaseStrEnum):
    """Allowed values for AnticipationBeneficiaryTaxIdType."""

    BR_CPF = "BR:CPF"
    BR_CNPJ = "BR:CNPJ"


class AnticipationRequestStatus(BaseStrEnum):
    """Allowed values for AnticipationRequestStatus."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    CONFIRMED = "CONFIRMED"
    CANCELED = "CANCELED"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    FAILED = "FAILED"


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


class BoletoTransactionStatus(BaseStrEnum):
    """Allowed values for BoletoTransactionStatus."""

    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class BoletoTransactionType(BaseStrEnum):
    """Allowed values for BoletoTransactionType."""

    BOLETO_IN = "BOLETO_IN"
    BOLETO_OUT = "BOLETO_OUT"


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


class FileContentType(BaseStrEnum):
    """Allowed values for FileContentType."""

    APPLICATION_PDF = "application/pdf"
    IMAGE_PNG = "image/png"
    IMAGE_JPEG = "image/jpeg"
    IMAGE_WEBP = "image/webp"


class FilePurpose(BaseStrEnum):
    """Allowed values for FilePurpose."""

    DISPUTE_EVIDENCE = "DISPUTE_EVIDENCE"


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


class GetDisputeResponseDisputeStatus(BaseStrEnum):
    """Allowed values for GetDisputeResponseDisputeStatus."""

    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"


class GetDisputeResponseDisputeType(BaseStrEnum):
    """Allowed values for GetDisputeResponseDisputeType."""

    MED = "MED"
    DISPUTE = "DISPUTE"
    CHARGEBACK = "CHARGEBACK"


class GetReceiptReceiptType(BaseStrEnum):
    """Allowed values for GetReceiptReceiptType."""

    PIX_IN = "pix-in"
    PIX_OUT = "pix-out"
    PIX_REFUND = "pix-refund"


class GetSubaccountStatementResponseItemOperationType(BaseStrEnum):
    """Allowed values for GetSubaccountStatementResponseItemOperationType."""

    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    TRANSFER_CREDIT = "TRANSFER_CREDIT"
    TRANSFER_DEBIT = "TRANSFER_DEBIT"
    WITHDRAWAL = "WITHDRAWAL"
    WITHDRAWAL_REVERSAL = "WITHDRAWAL_REVERSAL"
    WITHDRAWAL_FEE = "WITHDRAWAL_FEE"
    WITHDRAWAL_FEE_REVERSAL = "WITHDRAWAL_FEE_REVERSAL"


class GetSubaccountStatementResponseItemType(BaseStrEnum):
    """Allowed values for GetSubaccountStatementResponseItemType."""

    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class KycValidationReasonsItem(BaseStrEnum):
    """Allowed values for KycValidationReasonsItem."""

    FRAUD_HISTORY = "FRAUD_HISTORY"
    DISPUTE_HISTORY = "DISPUTE_HISTORY"
    SANCTIONS = "SANCTIONS"
    PEP = "PEP"
    CRIMINAL_LAWSUITS = "CRIMINAL_LAWSUITS"
    EXCESSIVE_LAWSUITS = "EXCESSIVE_LAWSUITS"


class KycValidationResult(BaseStrEnum):
    """Allowed values for KycValidationResult."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NONE = None


class KycValidationRiskLevel(BaseStrEnum):
    """Allowed values for KycValidationRiskLevel."""

    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    NONE_2 = None


class KycValidationStatus(BaseStrEnum):
    """Allowed values for KycValidationStatus."""

    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ListDisputesResponseDisputesItemType(BaseStrEnum):
    """Allowed values for ListDisputesResponseDisputesItemType."""

    MED = "MED"
    CHARGEBACK = "CHARGEBACK"


class ListTransactionsType(BaseStrEnum):
    """Allowed values for ListTransactionsType."""

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
    BNB = "BNB"


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


class WebhookCompanyBankAccountBlockedPayloadBlockingsItem(BaseStrEnum):
    """Allowed values for WebhookCompanyBankAccountBlockedPayloadBlockingsItem."""

    PIX_IN = "PIX_IN"
    PIX_OUT = "PIX_OUT"
    PIX_REFUND_SENT = "PIX_REFUND_SENT"
    PIX_REFUND_RECEIVED = "PIX_REFUND_RECEIVED"
    PIX_OUT_BLOCK_THIRD_PARTY_CNPJ = "PIX_OUT_BLOCK_THIRD_PARTY_CNPJ"
    PIX_IN_BLOCK_THIRD_PARTY_CNPJ = "PIX_IN_BLOCK_THIRD_PARTY_CNPJ"
    PIX_OUT_BLOCK_THIRD_PARTY_CPF = "PIX_OUT_BLOCK_THIRD_PARTY_CPF"
    BOLETO_OUT = "BOLETO_OUT"
    PIX_OUT_ALLOW_LIST_ONLY = "PIX_OUT_ALLOW_LIST_ONLY"
    INTERNAL_TRANSFER_OUT = "INTERNAL_TRANSFER_OUT"
    INTERNAL_TRANSFER_IN = "INTERNAL_TRANSFER_IN"
    PIX_REFUND = "PIX_REFUND"


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
    COMPANY_BANK_ACCOUNT_BLOCKED = "COMPANY_BANK_ACCOUNT_BLOCKED"
    STABLECOIN_DEPOSIT_COMPLETED = "STABLECOIN_DEPOSIT_COMPLETED"
    STABLECOIN_DEPOSIT_FAILED = "STABLECOIN_DEPOSIT_FAILED"
    STABLECOIN_PAYOUT_COMPLETED = "STABLECOIN_PAYOUT_COMPLETED"
    STABLECOIN_PAYOUT_FAILED = "STABLECOIN_PAYOUT_FAILED"
    STABLECOIN_PAYOUT_REFUND_CONFIRMED = "STABLECOIN_PAYOUT_REFUND_CONFIRMED"
    STABLECOIN_PAYOUT_REFUND_FAILED = "STABLECOIN_PAYOUT_REFUND_FAILED"
    STABLECOIN_SUBACCOUNT_CONFIRMED = "STABLECOIN_SUBACCOUNT_CONFIRMED"
    STABLECOIN_SUBACCOUNT_REJECTED = "STABLECOIN_SUBACCOUNT_REJECTED"
    BOLETO_SETTLED = "BOLETO_SETTLED"
    KYC_VALIDATION_COMPLETED = "KYC_VALIDATION_COMPLETED"
    KYC_VALIDATION_FAILED = "KYC_VALIDATION_FAILED"


class WebhookStablecoinPayoutRefundConfirmedPayloadRefundDest(BaseStrEnum):
    """Allowed values for
    WebhookStablecoinPayoutRefundConfirmedPayloadRefundDestination.
    """

    SUBACCOUNT_BALANCE = "SUBACCOUNT_BALANCE"
    MAIN_BALANCE = "MAIN_BALANCE"
    NONE = "NONE"


class WebhookStablecoinPayoutRefundConfirmedPayloadRefundStat(BaseStrEnum):
    """Allowed values for WebhookStablecoinPayoutRefundConfirmedPayloadRefundStatus."""

    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


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


class AnticipationBalanceBatchPayloadItemsItem(BaseSchema):
    """Schema generated for AnticipationBalanceBatchPayloadItemsItem.

    Attributes:
        tax_id (str): Beneficiary payout key (CPF or CNPJ).
        available_amount (int): Available balance, in cents.
        max_advanceable_amount (int): Advanceable limit, in cents. Cannot exceed
            availableAmount.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="Beneficiary payout key (CPF or CNPJ).",
    )
    available_amount: int = Field(
        validation_alias="availableAmount",
        serialization_alias="availableAmount",
        description="Available balance, in cents.",
        ge=0,
    )
    max_advanceable_amount: int = Field(
        validation_alias="maxAdvanceableAmount",
        serialization_alias="maxAdvanceableAmount",
        description="Advanceable limit, in cents. Cannot exceed availableAmount.",
        ge=0,
    )


class AnticipationBalanceBatchResultResultsItem(BaseSchema):
    """Schema generated for AnticipationBalanceBatchResultResultsItem.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        ok (bool | None): Undocumented in the spec.
        error (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    ok: bool | None = None
    error: str | None = None


class AnticipationBeneficiaryCreatePayloadFrequencyOverride(BaseSchema):
    """Per-beneficiary rolling frequency window.

    Attributes:
        max_advances (int | None): Undocumented in the spec.
        period_days (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    max_advances: int | None = Field(
        validation_alias="maxAdvances",
        serialization_alias="maxAdvances",
        ge=1,
        default=None,
    )
    period_days: int | None = Field(
        validation_alias="periodDays",
        serialization_alias="periodDays",
        ge=1,
        default=None,
    )


class AnticipationBeneficiaryTaxId(BaseSchema):
    """Schema generated for AnticipationBeneficiaryTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (AnticipationBeneficiaryTaxIdType | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: AnticipationBeneficiaryTaxIdType | None = None


class AnticipationError(BaseSchema):
    """Schema generated for AnticipationError.

    Attributes:
        error (str | None): Undocumented in the spec.
    """

    error: str | None = None


class AnticipationRequest(BaseSchema):
    """Schema generated for AnticipationRequest.

    Attributes:
        id (str | None): Anticipation request id (use it on the approve/reject routes).
        status (AnticipationRequestStatus | None): Undocumented in the spec.
        beneficiary_tax_id (str | None): Beneficiary payout key (CPF or CNPJ).
        requested_amount (int | None): Advanced amount, in cents.
        fee_amount (int | None): Fee charged to the beneficiary, in cents.
        net_amount (int | None): Net amount paid to the beneficiary, in cents.
        fee_mode (str | None): Undocumented in the spec.
        monthly_fee_percentage (float | None): Undocumented in the spec.
        days_until_due (int | None): Undocumented in the spec.
        due_date (datetime | None): Undocumented in the spec.
        approved_at (datetime | None): Undocumented in the spec.
        cancelled_at (datetime | None): Undocumented in the spec.
        cancel_reason (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Pix end-to-end id of the settled payout.
        failure_code (str | None): Coded failure cause when status is FAILED.
        failure_reason (str | None): Undocumented in the spec.
        created_at (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = Field(
        description="Anticipation request id (use it on the approve/reject routes).",
        default=None,
    )
    status: AnticipationRequestStatus | None = None
    beneficiary_tax_id: str | None = Field(
        validation_alias="beneficiaryTaxID",
        serialization_alias="beneficiaryTaxID",
        description="Beneficiary payout key (CPF or CNPJ).",
        default=None,
    )
    requested_amount: int | None = Field(
        validation_alias="requestedAmount",
        serialization_alias="requestedAmount",
        description="Advanced amount, in cents.",
        default=None,
    )
    fee_amount: int | None = Field(
        validation_alias="feeAmount",
        serialization_alias="feeAmount",
        description="Fee charged to the beneficiary, in cents.",
        default=None,
    )
    net_amount: int | None = Field(
        validation_alias="netAmount",
        serialization_alias="netAmount",
        description="Net amount paid to the beneficiary, in cents.",
        default=None,
    )
    fee_mode: str | None = Field(
        validation_alias="feeMode",
        serialization_alias="feeMode",
        default=None,
    )
    monthly_fee_percentage: float | None = Field(
        validation_alias="monthlyFeePercentage",
        serialization_alias="monthlyFeePercentage",
        default=None,
    )
    days_until_due: int | None = Field(
        validation_alias="daysUntilDue",
        serialization_alias="daysUntilDue",
        default=None,
    )
    due_date: datetime | None = Field(
        validation_alias="dueDate",
        serialization_alias="dueDate",
        default=None,
    )
    approved_at: datetime | None = Field(
        validation_alias="approvedAt",
        serialization_alias="approvedAt",
        default=None,
    )
    cancelled_at: datetime | None = Field(
        validation_alias="cancelledAt",
        serialization_alias="cancelledAt",
        default=None,
    )
    cancel_reason: str | None = Field(
        validation_alias="cancelReason",
        serialization_alias="cancelReason",
        default=None,
    )
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        description="Pix end-to-end id of the settled payout.",
        default=None,
    )
    failure_code: str | None = Field(
        validation_alias="failureCode",
        serialization_alias="failureCode",
        description="Coded failure cause when status is FAILED.",
        default=None,
    )
    failure_reason: str | None = Field(
        validation_alias="failureReason",
        serialization_alias="failureReason",
        default=None,
    )
    created_at: datetime | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )


class AnticipationUnauthorizedErrorsItem(BaseSchema):
    """Schema generated for AnticipationUnauthorizedErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
    """

    message: str | None = None


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
        scopes (list[str] | None): List of scopes to assign to the application. When
            provided, checkScopes will be enabled automatically.
    """

    name: str | None = Field(description="Name of the application", default=None)
    type: ApplicationPayloadApplicationType | None = Field(
        description="Type of the application (API)",
        default=None,
    )
    scopes: list[str] | None = Field(
        description=(
            "List of scopes to assign to the application. When provided, checkScopes "
            "will be enabled automatically."
        ),
        default=None,
    )


class ApproveStablecoinDepositBody(BaseSchema):
    """Schema generated for ApproveStablecoinDepositBody.

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


class ApproveStablecoinDepositResponse(BaseSchema):
    """Schema generated for ApproveStablecoinDepositResponse.

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


class BoletoTransactionCharge(BaseSchema):
    """The charge the payer settled. Only present on `BOLETO_IN`; a.

    Attributes:
        value (int | None): Emitted amount of the charge, in cents.
        status (str | None): Undocumented in the spec.
        boleto_barcode (str | None): Undocumented in the spec.
        boleto_digitable (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    value: int | None = Field(
        description="Emitted amount of the charge, in cents.",
        examples=[242898],
        default=None,
    )
    status: str | None = Field(examples=["COMPLETED"], default=None)
    boleto_barcode: str | None = Field(
        validation_alias="boletoBarcode",
        serialization_alias="boletoBarcode",
        default=None,
    )
    boleto_digitable: str | None = Field(
        validation_alias="boletoDigitable",
        serialization_alias="boletoDigitable",
        default=None,
    )


class BoletoTransactionError(BaseSchema):
    """Schema generated for BoletoTransactionError.

    Attributes:
        error (str | None): Human readable error message.
    """

    error: str | None = Field(description="Human readable error message.", default=None)


class BoletoTransactionListResponsePageInfo(BaseSchema):
    """Schema generated for BoletoTransactionListResponsePageInfo.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

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


class CancelInvoiceResponse(BaseSchema):
    """Schema generated for CancelInvoiceResponse.

    Attributes:
        success (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    success: bool | None = None


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


class CheckPixKeyBody(BaseSchema):
    """Schema generated for CheckPixKeyBody.

    Attributes:
        pix_key (str): The Pix key to check
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: str = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        description="The Pix key to check",
    )


class CloseAccountResponse(BaseSchema):
    """Schema generated for CloseAccountResponse.

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


class CreateCashbackFidelityBody(BaseSchema):
    """Schema generated for CreateCashbackFidelityBody.

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


class CreateCashbackFidelityResponseCashback(BaseSchema):
    """Object representing the existing cashback.

    Attributes:
        value (int | None): Cashback value in centavos
    """

    model_config = ConfigDict(extra="allow")

    value: int | None = Field(description="Cashback value in centavos", default=None)


class CreateInstallmentCobrBody(BaseSchema):
    """Schema generated for CreateInstallmentCobrBody.

    Attributes:
        value (int | None): Valor da cobrança (Opcional)
    """

    value: int | None = Field(description="Valor da cobrança (Opcional)", default=None)


class CreateInvoiceResponseInvoiceCharge(BaseSchema):
    """Schema generated for CreateInvoiceResponseInvoiceCharge.

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


class CreateInvoiceResponseInvoiceCustomer(BaseSchema):
    """Schema generated for CreateInvoiceResponseInvoiceCustomer.

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


class CreatePartnerApplicationBodyApplication(BaseSchema):
    """Schema generated for CreatePartnerApplicationBodyApplication.

    Attributes:
        name (str): The name you want to give your application
        type (ApplicationEnumTypePayload): Type of the application that you want to
            register. Each of this has some kind of permissions.
        scopes (list[str] | None): List of scopes to assign to the application. When
            provided, checkScopes will be enabled automatically.
    """

    name: str = Field(description="The name you want to give your application")
    type: ApplicationEnumTypePayload = Field(
        description=(
            "Type of the application that you want to register. Each of this has some "
            "kind of permissions."
        ),
    )
    scopes: list[str] | None = Field(
        description=(
            "List of scopes to assign to the application. When provided, checkScopes "
            "will be enabled automatically."
        ),
        default=None,
    )


class CreatePaymentBodyBoleto(BaseSchema):
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


class CreatePaymentBodyManualAccount(BaseSchema):
    """Schema generated for CreatePaymentBodyManualAccount.

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


class CreatePaymentBodyManualHolderTaxId(BaseSchema):
    """Schema generated for CreatePaymentBodyManualHolderTaxId.

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


class CreatePaymentBodyPixKey(BaseSchema):
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


class CreatePaymentBodyQrCode(BaseSchema):
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


class CreateStablecoinPayoutBody(BaseSchema):
    """Schema generated for CreateStablecoinPayoutBody.

    Attributes:
        value (int): Amount to pay out, in cents of the input asset.
        currency (StablecoinDepositRequestCurrency): Stablecoin asset to spend from the
            INTERNAL balance.
        pix_key (str): Destination Pix key.
        correlation_id (str | None): Optional idempotency key echoed back on the
            response.
        pix_message (str | None): Optional Pix message sent with the transfer.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int = Field(
        description="Amount to pay out, in cents of the input asset.",
        examples=[100000],
        ge=1,
    )
    currency: StablecoinDepositRequestCurrency = Field(
        description="Stablecoin asset to spend from the INTERNAL balance.",
        examples=["USDC"],
    )
    pix_key: str = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        description="Destination Pix key.",
        examples=["13d3109f-3a1e-4c56-b76d-d2db7213b9f2"],
    )
    correlation_id: str | None = Field(
        validation_alias="correlationId",
        serialization_alias="correlationId",
        description="Optional idempotency key echoed back on the response.",
        default=None,
    )
    pix_message: str | None = Field(
        validation_alias="pixMessage",
        serialization_alias="pixMessage",
        description="Optional Pix message sent with the transfer.",
        default=None,
    )


class CreateStablecoinPayoutResponsePixKeyOwner(BaseSchema):
    """Schema generated for CreateStablecoinPayoutResponsePixKeyOwner.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
        bank_name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxId",
        serialization_alias="taxId",
        default=None,
    )
    bank_name: str | None = Field(
        validation_alias="bankName",
        serialization_alias="bankName",
        default=None,
    )


class CreateStablecoinPayoutResponseQuote(BaseSchema):
    """Schema generated for CreateStablecoinPayoutResponseQuote.

    Attributes:
        input_amount (float | None): Undocumented in the spec.
        input_currency (str | None): Undocumented in the spec.
        output_amount (float | None): Undocumented in the spec.
        output_currency (str | None): Undocumented in the spec.
        rate (float | None): Undocumented in the spec.
        fee (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    input_amount: float | None = Field(
        validation_alias="inputAmount",
        serialization_alias="inputAmount",
        default=None,
    )
    input_currency: str | None = Field(
        validation_alias="inputCurrency",
        serialization_alias="inputCurrency",
        default=None,
    )
    output_amount: float | None = Field(
        validation_alias="outputAmount",
        serialization_alias="outputAmount",
        default=None,
    )
    output_currency: str | None = Field(
        validation_alias="outputCurrency",
        serialization_alias="outputCurrency",
        examples=["BRL"],
        default=None,
    )
    rate: float | None = None
    fee: int | None = None


class CreditSubaccountBody(BaseSchema):
    """Schema generated for CreditSubaccountBody.

    Attributes:
        value (int): Amount to credit to the account
        description (str | None): Optional description for the credit operation
    """

    value: int = Field(description="Amount to credit to the account")
    description: str | None = Field(
        description="Optional description for the credit operation",
        default=None,
    )


class CreditSubaccountResponse(BaseSchema):
    """Schema generated for CreditSubaccountResponse.

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


class DebitSubaccountBody(BaseSchema):
    """Schema generated for DebitSubaccountBody.

    Attributes:
        value (int): Amount to debit from the account
        description (str | None): Optional description for the debit operation
    """

    value: int = Field(description="Amount to debit from the account")
    description: str | None = Field(
        description="Optional description for the debit operation",
        default=None,
    )


class DebitSubaccountResponse(BaseSchema):
    """Schema generated for DebitSubaccountResponse.

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


class DecodeEmvBody(BaseSchema):
    """Schema generated for DecodeEmvBody.

    Attributes:
        emv (str): Raw EMV / PIX QR payload (text)
    """

    emv: str = Field(
        description="Raw EMV / PIX QR payload (text)",
        examples=[
            "00020126780014br.gov.bcb.pix0136f4c6089a-bfde-4c00-a2d9-9eaa584b02190216CobrancaEstatica5204000053039865406546.285802BR5903Pix6008BRASILIA6229052584767c56c2ab4e65b6670de2a80950014br.gov.bcb.pix2573qr-h.sandbox.pix.bcb.gov.br/rest/api/rec/4b62d4a088fe4f51bcb4c64cf078869163044486",
        ],
    )


class DecodeEmvResponseCobLocationPayloadAdditionalInfoItem(BaseSchema):
    """Schema generated for DecodeEmvResponseCobLocationPayloadAdditionalInfoItem.

    Attributes:
        name (str | None): Undocumented in the spec.
        value (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    value: str | None = None


class DecodeEmvResponseCobLocationPayloadCalendar(BaseSchema):
    """Schema generated for DecodeEmvResponseCobLocationPayloadCalendar.

    Attributes:
        presentation (datetime | None): Undocumented in the spec.
        expiration (int | None): Undocumented in the spec.
        creation (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    presentation: datetime | None = None
    expiration: int | None = None
    creation: datetime | None = None


class DecodeEmvResponseCobLocationPayloadDebtor(BaseSchema):
    """Schema generated for DecodeEmvResponseCobLocationPayloadDebtor.

    Attributes:
        cpf (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    cpf: str | None = None
    name: str | None = None


class DecodeEmvResponseCobLocationPayloadValue(BaseSchema):
    """Schema generated for DecodeEmvResponseCobLocationPayloadValue.

    Attributes:
        original (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    original: str | None = None


class DecodeEmvResponseEmvAdditionalDataFieldTemplate(BaseSchema):
    """Schema generated for DecodeEmvResponseEmvAdditionalDataFieldTemplate.

    Attributes:
        reference_label (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    reference_label: str | None = Field(
        validation_alias="referenceLabel",
        serialization_alias="referenceLabel",
        default=None,
    )


class DecodeEmvResponseEmvMerchantAccountInformationPix(BaseSchema):
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


class DecodeEmvResponseEmvUnreservedTemplates(BaseSchema):
    """Schema generated for DecodeEmvResponseEmvUnreservedTemplates.

    Attributes:
        gui (str | None): Undocumented in the spec.
        url (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    gui: str | None = None
    url: str | None = None


class DecodeEmvResponseRecLocationPayloadCalendar(BaseSchema):
    """Schema generated for DecodeEmvResponseRecLocationPayloadCalendar.

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


class DecodeEmvResponseRecLocationPayloadLinkDebtor(BaseSchema):
    """Schema generated for DecodeEmvResponseRecLocationPayloadLinkDebtor.

    Attributes:
        cpf (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    cpf: str | None = None
    name: str | None = None


class DecodeEmvResponseRecLocationPayloadReceiver(BaseSchema):
    """Schema generated for DecodeEmvResponseRecLocationPayloadReceiver.

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


class DecodeEmvResponseRecLocationPayloadUpdatesItem(BaseSchema):
    """Schema generated for DecodeEmvResponseRecLocationPayloadUpdatesItem.

    Attributes:
        date (datetime | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    date: datetime | None = None
    status: str | None = None


class DecodeEmvResponseRecLocationPayloadValue(BaseSchema):
    """Schema generated for DecodeEmvResponseRecLocationPayloadValue.

    Attributes:
        value_rec (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    value_rec: str | None = Field(
        validation_alias="valueRec",
        serialization_alias="valueRec",
        default=None,
    )


class DeleteAccountRegisterResponse(BaseSchema):
    """Schema generated for DeleteAccountRegisterResponse.

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


class DeleteApplicationResponse(BaseSchema):
    """Schema generated for DeleteApplicationResponse.

    Attributes:
        success (bool | None): Indicates the operation was successful
    """

    model_config = ConfigDict(extra="allow")

    success: bool | None = Field(
        description="Indicates the operation was successful",
        default=None,
    )


class DeleteChargeResponse(BaseSchema):
    """Schema generated for DeleteChargeResponse.

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


class DeleteStaticQrCodeResponse(BaseSchema):
    """Schema generated for DeleteStaticQrCodeResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    status: str | None = None
    id: str | None = None


class DeleteSubaccountResponse(BaseSchema):
    """Schema generated for DeleteSubaccountResponse.

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


class DeleteWebhookResponse(BaseSchema):
    """Schema generated for DeleteWebhookResponse.

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


class File(BaseSchema):
    """Schema generated for File.

    Attributes:
        id (str | None): Woovi identifier of the file
        correlation_id (str | None): Your identifier for this upload, generated when not
            sent
        purpose (FilePurpose | None): What the file will be used for
        file_name (str | None): Name of the file as you sent it
        content_type (FileContentType | None): Content type of the file
        size (int | None): Size in bytes
        url (str | None): Temporary pre-signed download URL
        url_expires_at (datetime | None): When `url` stops working
        created_at (datetime | None): When the file was stored
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = Field(
        description="Woovi identifier of the file",
        examples=["6712c2ac7c2f1e0012a4b8d1"],
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your identifier for this upload, generated when not sent",
        examples=["evidence-2026-08-1042"],
        default=None,
    )
    purpose: FilePurpose | None = Field(
        description="What the file will be used for",
        examples=["DISPUTE_EVIDENCE"],
        default=None,
    )
    file_name: str | None = Field(
        validation_alias="fileName",
        serialization_alias="fileName",
        description="Name of the file as you sent it",
        examples=["evidence.png"],
        default=None,
    )
    content_type: FileContentType | None = Field(
        validation_alias="contentType",
        serialization_alias="contentType",
        description="Content type of the file",
        examples=["image/png"],
        default=None,
    )
    size: int | None = Field(
        description="Size in bytes",
        examples=[20480],
        default=None,
    )
    url: str | None = Field(
        description="Temporary pre-signed download URL",
        examples=[
            "https://woovi-files.s3.amazonaws.com/company/6712c1f07c2f1e0012a4b8c9/dispute_evidence/6712c2ac7c2f1e0012a4b8d1?X-Amz-Signature=...",
        ],
        default=None,
    )
    url_expires_at: datetime | None = Field(
        validation_alias="urlExpiresAt",
        serialization_alias="urlExpiresAt",
        description="When `url` stops working",
        examples=["2026-08-22T15:30:00.000Z"],
        default=None,
    )
    created_at: datetime | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        description="When the file was stored",
        examples=["2026-08-22T14:30:00.000Z"],
        default=None,
    )


class FileError(BaseSchema):
    """Schema generated for FileError.

    Attributes:
        error (str | None): Message in the language of the company making the request
            (pt-BR, en or es)
    """

    error: str | None = Field(
        description=(
            "Message in the language of the company making the request (pt-BR, en or "
            "es)"
        ),
        default=None,
    )


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


class GetAccountRegisterResponseTaxId(BaseSchema):
    """Schema generated for GetAccountRegisterResponseTaxId.

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


class GetCashbackFidelityBalanceResponse(BaseSchema):
    """Schema generated for GetCashbackFidelityBalanceResponse.

    Attributes:
        balance (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    balance: int | None = None
    status: str | None = None


class GetChargeQrCodeBase64Response(BaseSchema):
    """Schema generated for GetChargeQrCodeBase64Response.

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


class GetCompanyResponseCompany(BaseSchema):
    """Schema generated for GetCompanyResponseCompany.

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


class GetDisputeResponseDispute(BaseSchema):
    """Schema generated for GetDisputeResponseDispute.

    Attributes:
        status (GetDisputeResponseDisputeStatus | None): Undocumented in the spec.
        name (str | None): The name of the payer who created this dispute.
        email (str | None): The Email of the payer who created this dispute.
        phone_number (str | None): The phone number of the payer who created this
            dispute.
        value (str | None): The value of the dispute.
        dispute_reason (str | None): Reason provided to justify the dispute.
        end_to_end_id (str | None): The endToEndId of the dispute (Is the same of the
            endToEndId transaction related).
        type (GetDisputeResponseDisputeType | None): The type of the dispute
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: GetDisputeResponseDisputeStatus | None = None
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
    type: GetDisputeResponseDisputeType | None = Field(
        description="The type of the dispute",
        default=None,
    )


class GetStablecoinQuoteResponseQuoteAppliedFeesItem(BaseSchema):
    """Schema generated for GetStablecoinQuoteResponseQuoteAppliedFeesItem.

    Attributes:
        type (str | None): Undocumented in the spec.
        amount (float | None): Undocumented in the spec.
        currency (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    type: str | None = Field(examples=["In Fee"], default=None)
    amount: float | None = Field(examples=[1.5], default=None)
    currency: str | None = Field(examples=["BRL"], default=None)


class GetStablecoinSubaccountBalancesResponse(BaseSchema):
    """Schema generated for GetStablecoinSubaccountBalancesResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        sub_account_id (str | None): Undocumented in the spec.
        balances (dict[str, float] | None): Asset to amount, in the asset unit (not
            cents).
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(examples=["ok"], default=None)
    sub_account_id: str | None = Field(
        validation_alias="subAccountId",
        serialization_alias="subAccountId",
        default=None,
    )
    balances: dict[str, float] | None = Field(
        description="Asset to amount, in the asset unit (not cents).",
        examples=[{"BRLA": 1250.35, "USDC": 0.2, "USDT": 0}],
        default=None,
    )


class GetStatementResponseItem(BaseSchema):
    """Schema generated for GetStatementResponseItem.

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


class GetSubaccountStatementResponseItem(BaseSchema):
    """Schema generated for GetSubaccountStatementResponseItem.

    Attributes:
        id (str | None): Undocumented in the spec.
        time (datetime | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        balance (int | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        type (GetSubaccountStatementResponseItemType | None): Undocumented in the spec.
        operation_type (GetSubaccountStatementResponseItemOperationType | None): |
            operationType           | Descrição
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
    type: GetSubaccountStatementResponseItemType | None = Field(
        examples=["CREDIT"],
        default=None,
    )
    operation_type: GetSubaccountStatementResponseItemOperationType | None = Field(
        validation_alias="operationType",
        serialization_alias="operationType",
        description=(
            "| operationType           | Descrição                                     "
            "    "
            "|\n|-------------------------|--------------------------------------------"
            "-------|\n| CREDIT                  | Valor recebido                      "
            "              |\n| DEBIT                   | Valor enviado                "
            "                     |\n| TRANSFER_CREDIT         | Crédito de "
            "transferência interna entre subcontas  |\n| TRANSFER_DEBIT          | "
            "Débito de transferência interna entre subcontas   |\n| WITHDRAWAL         "
            "     | Saque iniciado a partir da subconta               |\n| "
            "WITHDRAWAL_REVERSAL     | Estorno de um saque processado anteriormente    "
            "  |\n| WITHDRAWAL_FEE          | Taxa cobrada por uma operação de saque   "
            "         |\n| WITHDRAWAL_FEE_REVERSAL | Estorno da taxa de saque          "
            "                |"
        ),
        default=None,
    )


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


class KycValidation(BaseSchema):
    """Schema generated for KycValidation.

    Attributes:
        correlation_id (str | None): Undocumented in the spec.
        tax_id (str | None): Digits only, as screened.
        status (KycValidationStatus | None): `PROCESSING` — accepted and queued;
            `COMPLETED` — screened, `result` is filled; `FAILED` — every upstream source
            was unavailable, so no verdict was reached (the validation is still billed,
            and a retry needs a new `correlationID`).
        result (KycValidationResult | None): Null while `PROCESSING` and when `FAILED`.
        risk_level (KycValidationRiskLevel | None): Aggregated risk. Null while
            `PROCESSING` and when `FAILED`.
        reasons (list[KycValidationReasonsItem]): Source-agnostic signals behind the
            verdict — they say what was observed, never which bureau observed it. Empty
            while `PROCESSING`, and empty on an `APPROVED` validation with no signal.
        created_at (datetime | None): Undocumented in the spec.
        completed_at (datetime | None): Null while `PROCESSING`.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        examples=["my-unique-id"],
        default=None,
    )
    tax_id: str | None = Field(
        validation_alias="taxId",
        serialization_alias="taxId",
        description="Digits only, as screened.",
        examples=["02916265000160"],
        default=None,
    )
    status: KycValidationStatus | None = Field(
        description=(
            "`PROCESSING` — accepted and queued; `COMPLETED` — screened, `result` is "
            "filled;\n`FAILED` — every upstream source was unavailable, so no verdict "
            "was reached\n(the validation is still billed, and a retry needs a new "
            "`correlationID`)."
        ),
        examples=["COMPLETED"],
        default=None,
    )
    result: KycValidationResult | None = Field(
        description="Null while `PROCESSING` and when `FAILED`.",
        examples=["REJECTED"],
        default=None,
    )
    risk_level: KycValidationRiskLevel | None = Field(
        validation_alias="riskLevel",
        serialization_alias="riskLevel",
        description="Aggregated risk. Null while `PROCESSING` and when `FAILED`.",
        examples=["HIGH"],
        default=None,
    )
    reasons: list[KycValidationReasonsItem] = Field(
        description=(
            "Source-agnostic signals behind the verdict — they say what was observed, "
            "never\nwhich bureau observed it. Empty while `PROCESSING`, and empty on "
            "an `APPROVED`\nvalidation with no signal."
        ),
        examples=[["FRAUD_HISTORY", "DISPUTE_HISTORY"]],
        default_factory=list,
    )
    created_at: datetime | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        examples=["2026-08-24T14:00:06.386Z"],
        default=None,
    )
    completed_at: datetime | None = Field(
        validation_alias="completedAt",
        serialization_alias="completedAt",
        description="Null while `PROCESSING`.",
        examples=["2026-08-24T14:00:06.462Z"],
        default=None,
    )


class KycValidationError(BaseSchema):
    """Schema generated for KycValidationError.

    Attributes:
        error_code (str | None): Undocumented in the spec.
        error_message (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    error_code: str | None = Field(
        validation_alias="errorCode",
        serialization_alias="errorCode",
        examples=["KYC_VALIDATION_NOT_FOUND"],
        default=None,
    )
    error_message: str | None = Field(
        validation_alias="errorMessage",
        serialization_alias="errorMessage",
        examples=["validation not found"],
        default=None,
    )


class KycValidationRequest(BaseSchema):
    """Schema generated for KycValidationRequest.

    Attributes:
        tax_id (str): CPF (11 digits) or CNPJ (14 digits) to be screened. A mask is
            accepted — everything that is not a digit is stripped before validation.
        correlation_id (str): Your own identifier for this validation. It is the
            idempotency key: sending the same `correlationID` again returns the original
            validation with `200` and is **not** billed a second time. It is also how
            you read the result back.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str = Field(
        validation_alias="taxId",
        serialization_alias="taxId",
        description=(
            "CPF (11 digits) or CNPJ (14 digits) to be screened. A mask is accepted "
            "—\neverything that is not a digit is stripped before validation."
        ),
        examples=["02.916.265/0001-60"],
    )
    correlation_id: str = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description=(
            "Your own identifier for this validation. It is the idempotency key: "
            "sending\nthe same `correlationID` again returns the original validation "
            "with `200`\nand is **not** billed a second time. It is also how you read "
            "the result back."
        ),
        examples=["my-unique-id"],
        min_length=1,
        max_length=128,
    )


class ListAccountsResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListAccountsResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class ListChargesResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListChargesResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class ListCustomersResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListCustomersResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class ListDisputesResponseDisputesItem(BaseSchema):
    """Schema generated for ListDisputesResponseDisputesItem.

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
        type (ListDisputesResponseDisputesItemType | None): The type of the dispute
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
    type: ListDisputesResponseDisputesItemType | None = Field(
        description="The type of the dispute",
        default=None,
    )


class ListDisputesResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListDisputesResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class ListPartnerAffiliatesResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListPartnerAffiliatesResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class ListPartnerCompaniesResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListPartnerCompaniesResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class ListPaymentsResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListPaymentsResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class ListPixKeyTokenLogsResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListPixKeyTokenLogsResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class ListPspsResponsePspsItem(BaseSchema):
    """Schema generated for ListPspsResponsePspsItem.

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


class ListRefundsResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListRefundsResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class ListStablecoinSubaccountWalletsResponseWalletsItem(BaseSchema):
    """Schema generated for ListStablecoinSubaccountWalletsResponseWalletsItem.

    Attributes:
        address (str | None): Undocumented in the spec.
        currency (str | None): Undocumented in the spec.
        network (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    address: str | None = Field(
        examples=["0xbd374a94d88F19b80F6aD8A3AE418e3f1eb054AE"],
        default=None,
    )
    currency: str | None = Field(examples=["USDC"], default=None)
    network: str | None = Field(examples=["POLYGON"], default=None)


class ListStablecoinWalletsResponseWalletsItem(BaseSchema):
    """Schema generated for ListStablecoinWalletsResponseWalletsItem.

    Attributes:
        address (str | None): Undocumented in the spec.
        currency (str | None): Undocumented in the spec.
        network (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    address: str | None = None
    currency: str | None = None
    network: str | None = None


class ListStaticQrCodesResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListStaticQrCodesResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class ListSubaccountsResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListSubaccountsResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class ListSubaccountsResponseSubaccountsItem(BaseSchema):
    """Schema generated for ListSubaccountsResponseSubaccountsItem.

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


class ListTransactionsResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListTransactionsResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


class ListWebhookEventsResponseEventsItem(BaseSchema):
    """Schema generated for ListWebhookEventsResponseEventsItem.

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
            removed by a user  * **OPENPIX:DISPUTE_CREATED** - Dispute created *
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
            **PIX_AUTOMATIC_COBR_TRY_REQUESTED** - Pix Automatic cobr try requested  *
            **COMPANY_BANK_ACCOUNT_BLOCKED** - Blockings on a bank account changed  *
            **STABLECOIN_DEPOSIT_COMPLETED** - Stablecoin deposit completed *
            **STABLECOIN_DEPOSIT_FAILED** - Stablecoin deposit failed *
            **STABLECOIN_PAYOUT_COMPLETED** - Stablecoin payout completed *
            **STABLECOIN_PAYOUT_FAILED** - Stablecoin payout failed *
            **STABLECOIN_PAYOUT_REFUND_CONFIRMED** - A settled stablecoin payout came
            back and the funds are available again in your stablecoin balance *
            **STABLECOIN_PAYOUT_REFUND_FAILED** - A settled stablecoin payout came back
            but the funds are not available to you; needs reconciliation *
            **STABLECOIN_SUBACCOUNT_CONFIRMED** - Stablecoin sub-account confirmed *
            **STABLECOIN_SUBACCOUNT_REJECTED** - Stablecoin sub-account rejected  *
            **BOLETO_SETTLED** - Boleto settled by the issuing bank  *
            **KYC_VALIDATION_COMPLETED** - KYC validation completed *
            **KYC_VALIDATION_FAILED** - KYC validation failed
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
            "- Payment was removed by a user\n\n* **OPENPIX:DISPUTE_CREATED** - "
            "Dispute created\n* **OPENPIX:DISPUTE_ACCEPTED** - Dispute accepted\n* "
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
            "**PIX_AUTOMATIC_COBR_TRY_REQUESTED** - Pix Automatic cobr try "
            "requested\n\n* **COMPANY_BANK_ACCOUNT_BLOCKED** - Blockings on a bank "
            "account changed\n\n* **STABLECOIN_DEPOSIT_COMPLETED** - Stablecoin "
            "deposit completed\n* **STABLECOIN_DEPOSIT_FAILED** - Stablecoin deposit "
            "failed\n* **STABLECOIN_PAYOUT_COMPLETED** - Stablecoin payout "
            "completed\n* **STABLECOIN_PAYOUT_FAILED** - Stablecoin payout failed\n* "
            "**STABLECOIN_PAYOUT_REFUND_CONFIRMED** - A settled stablecoin payout came "
            "back and the\nfunds are available again in your stablecoin balance\n* "
            "**STABLECOIN_PAYOUT_REFUND_FAILED** - A settled stablecoin payout came "
            "back but the funds\nare not available to you; needs reconciliation\n* "
            "**STABLECOIN_SUBACCOUNT_CONFIRMED** - Stablecoin sub-account confirmed\n* "
            "**STABLECOIN_SUBACCOUNT_REJECTED** - Stablecoin sub-account rejected\n\n* "
            "**BOLETO_SETTLED** - Boleto settled by the issuing bank\n\n* "
            "**KYC_VALIDATION_COMPLETED** - KYC validation completed\n* "
            "**KYC_VALIDATION_FAILED** - KYC validation failed"
        ),
        default=None,
    )


class ListWebhookIpsResponse(BaseSchema):
    """Schema generated for ListWebhookIpsResponse.

    Attributes:
        ips (list[str]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    ips: list[str] = Field(default_factory=list)


class ListWebhookPublicKeysResponsePublicKeysItem(BaseSchema):
    """Schema generated for ListWebhookPublicKeysResponsePublicKeysItem.

    Attributes:
        key_identifier (str | None): SHA-256 da chave em DER. Identifica a chave de
            forma estável entre rotações.
        key (str | None): A chave pública em PEM (SPKI).
        is_current (bool | None): `true` na chave que está assinando os webhooks agora.
            Durante uma rotação mais de uma chave é válida, mas apenas uma é a atual.
    """

    model_config = ConfigDict(extra="allow")

    key_identifier: str | None = Field(
        description=(
            "SHA-256 da chave em DER. Identifica a chave de forma estável entre "
            "rotações."
        ),
        default=None,
    )
    key: str | None = Field(description="A chave pública em PEM (SPKI).", default=None)
    is_current: bool | None = Field(
        description=(
            "`true` na chave que está assinando os webhooks agora. Durante uma rotação "
            "mais de uma chave é válida, mas apenas uma é a atual."
        ),
        default=None,
    )


class ListWebhooksResponsePageInfoErrorsItemData(BaseSchema):
    """Schema generated for ListWebhooksResponsePageInfoErrorsItemData.

    Attributes:
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    skip: int | None = None
    limit: int | None = None


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
        value (int | None): Value of this QR code, in cents. The specification declares
            this `string` on the response while declaring the same field `number` on
            `PixQrCodePayload`, the request for the very same object.
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
    value: int | None = Field(
        description=(
            "Value of this QR code, in cents. The specification declares this `string` "
            "on the response while declaring the same field `number` on "
            "`PixQrCodePayload`, the request for the very same object."
        ),
        default=None,
    )
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


class QuoteStablecoinPayoutResponseQuote(BaseSchema):
    """Schema generated for QuoteStablecoinPayoutResponseQuote.

    Attributes:
        base_price (float | None): Undocumented in the spec.
        input_amount (float | None): Undocumented in the spec.
        input_currency (str | None): Undocumented in the spec.
        output_amount (float | None): Undocumented in the spec.
        output_currency (str | None): Undocumented in the spec.
        pair_name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    base_price: float | None = Field(
        validation_alias="basePrice",
        serialization_alias="basePrice",
        default=None,
    )
    input_amount: float | None = Field(
        validation_alias="inputAmount",
        serialization_alias="inputAmount",
        default=None,
    )
    input_currency: str | None = Field(
        validation_alias="inputCurrency",
        serialization_alias="inputCurrency",
        default=None,
    )
    output_amount: float | None = Field(
        validation_alias="outputAmount",
        serialization_alias="outputAmount",
        default=None,
    )
    output_currency: str | None = Field(
        validation_alias="outputCurrency",
        serialization_alias="outputCurrency",
        examples=["BRL"],
        default=None,
    )
    pair_name: str | None = Field(
        validation_alias="pairName",
        serialization_alias="pairName",
        default=None,
    )


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


class RejectAnticipationBody(BaseSchema):
    """Schema generated for RejectAnticipationBody.

    Attributes:
        reason (str | None): Optional rejection reason (audited).
    """

    reason: str | None = Field(
        description="Optional rejection reason (audited).",
        default=None,
    )


class RetryInstallmentCobrBody(BaseSchema):
    """Schema generated for RetryInstallmentCobrBody.

    Attributes:
        value (int | None): Valor da cobrança (Opcional)
    """

    value: int | None = Field(description="Valor da cobrança (Opcional)", default=None)


class SetInvoiceIntegrationStatusBody(BaseSchema):
    """Schema generated for SetInvoiceIntegrationStatusBody.

    Attributes:
        is_active (bool): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    is_active: bool = Field(validation_alias="isActive", serialization_alias="isActive")


class SetInvoiceIntegrationStatusResponseIntegration(BaseSchema):
    """Schema generated for SetInvoiceIntegrationStatusResponseIntegration.

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

    model_config = ConfigDict(populate_by_name=True)

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


class TestInvoiceIntegrationResponseIntegration(BaseSchema):
    """Schema generated for TestInvoiceIntegrationResponseIntegration.

    Attributes:
        id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None


class TestInvoiceIntegrationResponseInvoice(BaseSchema):
    """Schema generated for TestInvoiceIntegrationResponseInvoice.

    Attributes:
        id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None


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


class UpdateChargeResponse(BaseSchema):
    """Schema generated for UpdateChargeResponse.

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


class UpdateInvoiceIntegrationTaxFieldsBody(BaseSchema):
    """Schema generated for UpdateInvoiceIntegrationTaxFieldsBody.

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


class UpdateInvoiceIntegrationTaxFieldsResponse(BaseSchema):
    """Schema generated for UpdateInvoiceIntegrationTaxFieldsResponse.

    Attributes:
        integration (dict[str, Any] | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    integration: dict[str, Any] | None = None


class UploadDisputeEvidenceBodyDocumentsItem(BaseSchema):
    """Schema generated for UploadDisputeEvidenceBodyDocumentsItem.

    Attributes:
        url (str | None): Public url to download the document from. Send either url or
            fileId, not both.
        file_id (str | None): Id of a file previously uploaded to POST /api/v1/files
            with purpose DISPUTE_EVIDENCE. Send either url or fileId, not both. Requires
            the DISPUTE_EVIDENCE_FILE_ID feature.
        correlation_id (str | None): Id used by the client
        description (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    url: str | None = Field(
        description=(
            "Public url to download the document from. Send either url or fileId, not "
            "both."
        ),
        min_length=1,
        default=None,
    )
    file_id: str | None = Field(
        validation_alias="fileId",
        serialization_alias="fileId",
        description=(
            "Id of a file previously uploaded to POST /api/v1/files with purpose "
            "DISPUTE_EVIDENCE. Send either url or fileId, not both. Requires the "
            "DISPUTE_EVIDENCE_FILE_ID feature."
        ),
        min_length=1,
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Id used by the client",
        default=None,
    )
    description: str | None = None


class UploadDisputeEvidenceResponseDocumentsItem(BaseSchema):
    """Schema generated for UploadDisputeEvidenceResponseDocumentsItem.

    Attributes:
        url (str | None): Document url
        file_id (str | None): Id of the uploaded file, echoed back when the document was
            sent by fileId
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
    file_id: str | None = Field(
        validation_alias="fileId",
        serialization_alias="fileId",
        description=(
            "Id of the uploaded file, echoed back when the document was sent by fileId"
        ),
        examples=["68c7d0a1f0b2c3d4e5f60718"],
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


class UploadInvoiceIntegrationCertificateBody(BaseSchema):
    """Schema generated for UploadInvoiceIntegrationCertificateBody.

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


class UploadInvoiceIntegrationCertificateResponseIntegration(BaseSchema):
    """Schema generated for UploadInvoiceIntegrationCertificateResponseIntegration.

    Attributes:
        status (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    status: str | None = None


class UpsertInvoiceIntegrationBody(BaseSchema):
    """Schema generated for UpsertInvoiceIntegrationBody.

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


class UpsertInvoiceIntegrationResponseIntegrationMetadataNfei(BaseSchema):
    """Schema generated for UpsertInvoiceIntegrationResponseIntegrationMetadataNfei.

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
            removed by a user  * **OPENPIX:DISPUTE_CREATED** - Dispute created *
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
            **PIX_AUTOMATIC_COBR_TRY_REQUESTED** - Pix Automatic cobr try requested  *
            **COMPANY_BANK_ACCOUNT_BLOCKED** - Blockings on a bank account changed  *
            **STABLECOIN_DEPOSIT_COMPLETED** - Stablecoin deposit completed *
            **STABLECOIN_DEPOSIT_FAILED** - Stablecoin deposit failed *
            **STABLECOIN_PAYOUT_COMPLETED** - Stablecoin payout completed *
            **STABLECOIN_PAYOUT_FAILED** - Stablecoin payout failed *
            **STABLECOIN_PAYOUT_REFUND_CONFIRMED** - A settled stablecoin payout came
            back and the funds are available again in your stablecoin balance *
            **STABLECOIN_PAYOUT_REFUND_FAILED** - A settled stablecoin payout came back
            but the funds are not available to you; needs reconciliation *
            **STABLECOIN_SUBACCOUNT_CONFIRMED** - Stablecoin sub-account confirmed *
            **STABLECOIN_SUBACCOUNT_REJECTED** - Stablecoin sub-account rejected  *
            **BOLETO_SETTLED** - Boleto settled by the issuing bank  *
            **KYC_VALIDATION_COMPLETED** - KYC validation completed *
            **KYC_VALIDATION_FAILED** - KYC validation failed
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
            "- Payment was removed by a user\n\n* **OPENPIX:DISPUTE_CREATED** - "
            "Dispute created\n* **OPENPIX:DISPUTE_ACCEPTED** - Dispute accepted\n* "
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
            "**PIX_AUTOMATIC_COBR_TRY_REQUESTED** - Pix Automatic cobr try "
            "requested\n\n* **COMPANY_BANK_ACCOUNT_BLOCKED** - Blockings on a bank "
            "account changed\n\n* **STABLECOIN_DEPOSIT_COMPLETED** - Stablecoin "
            "deposit completed\n* **STABLECOIN_DEPOSIT_FAILED** - Stablecoin deposit "
            "failed\n* **STABLECOIN_PAYOUT_COMPLETED** - Stablecoin payout "
            "completed\n* **STABLECOIN_PAYOUT_FAILED** - Stablecoin payout failed\n* "
            "**STABLECOIN_PAYOUT_REFUND_CONFIRMED** - A settled stablecoin payout came "
            "back and the\nfunds are available again in your stablecoin balance\n* "
            "**STABLECOIN_PAYOUT_REFUND_FAILED** - A settled stablecoin payout came "
            "back but the funds\nare not available to you; needs reconciliation\n* "
            "**STABLECOIN_SUBACCOUNT_CONFIRMED** - Stablecoin sub-account confirmed\n* "
            "**STABLECOIN_SUBACCOUNT_REJECTED** - Stablecoin sub-account rejected\n\n* "
            "**BOLETO_SETTLED** - Boleto settled by the issuing bank\n\n* "
            "**KYC_VALIDATION_COMPLETED** - KYC validation completed\n* "
            "**KYC_VALIDATION_FAILED** - KYC validation failed"
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


class WebhookAccountRegisterApprovedPayloadAccount(BaseSchema):
    """Schema generated for WebhookAccountRegisterApprovedPayloadAccount.

    Attributes:
        status (str | None): Undocumented in the spec.
        account_id (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        branch (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: str | None = None
    account_id: str | None = Field(
        validation_alias="accountId",
        serialization_alias="accountId",
        default=None,
    )
    account: str | None = None
    branch: str | None = None


class WebhookAccountRegisterApprovedPayloadAccountRegisterTax(BaseSchema):
    """Schema generated for WebhookAccountRegisterApprovedPayloadAccountRegisterTax.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookAccountRegisterPendingPayloadAccountRegisterRequ2(BaseSchema):
    """Schema generated for WebhookAccountRegisterPendingPayloadAccountRegisterRequ2.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookAccountRegisterPendingPayloadAccountRegisterTaxI(BaseSchema):
    """Schema generated for WebhookAccountRegisterPendingPayloadAccountRegisterTaxI.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookAccountRegisterRejectedPayloadAccountRegisterTax(BaseSchema):
    """Schema generated for WebhookAccountRegisterRejectedPayloadAccountRegisterTax.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookBoletoSettledPayloadBoleto(BaseSchema):
    """Schema generated for WebhookBoletoSettledPayloadBoleto.

    Attributes:
        boleto_transaction_id (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        boleto_barcode (str | None): Undocumented in the spec.
        boleto_digitable (str | None): Undocumented in the spec.
        fee (int | None): Undocumented in the spec.
        settled_at (str | None): Undocumented in the spec.
        fines_value (int | None): Undocumented in the spec.
        interests_value (int | None): Undocumented in the spec.
        discount_value (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    boleto_transaction_id: str | None = Field(
        validation_alias="boletoTransactionID",
        serialization_alias="boletoTransactionID",
        default=None,
    )
    value: int | None = None
    status: str | None = None
    boleto_barcode: str | None = Field(
        validation_alias="boletoBarcode",
        serialization_alias="boletoBarcode",
        default=None,
    )
    boleto_digitable: str | None = Field(
        validation_alias="boletoDigitable",
        serialization_alias="boletoDigitable",
        default=None,
    )
    fee: int | None = None
    settled_at: str | None = Field(
        validation_alias="settledAt",
        serialization_alias="settledAt",
        default=None,
    )
    fines_value: int | None = Field(
        validation_alias="finesValue",
        serialization_alias="finesValue",
        default=None,
    )
    interests_value: int | None = Field(
        validation_alias="interestsValue",
        serialization_alias="interestsValue",
        default=None,
    )
    discount_value: int | None = Field(
        validation_alias="discountValue",
        serialization_alias="discountValue",
        default=None,
    )


class WebhookBoletoSettledPayloadCharge(BaseSchema):
    """Schema generated for WebhookBoletoSettledPayloadCharge.

    Attributes:
        correlation_id (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    value: int | None = None
    status: str | None = None


class WebhookChargeAdditionalInfoItem(BaseSchema):
    """Schema generated for WebhookChargeAdditionalInfoItem.

    Attributes:
        key (str | None): Undocumented in the spec.
        value (str | None): Undocumented in the spec.
    """

    key: str | None = None
    value: str | None = None


class WebhookChargeCustomerTaxId(BaseSchema):
    """Schema generated for WebhookChargeCustomerTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookChargePayerTaxId(BaseSchema):
    """Schema generated for WebhookChargePayerTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookCompanyBankAccountBlockedPayloadAccountTaxId(BaseSchema):
    """Schema generated for WebhookCompanyBankAccountBlockedPayloadAccountTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa10(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa10.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa4(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa4.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa6(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa6.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa7(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa7.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa8(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa8.

    Attributes:
        client_id (str | None): Undocumented in the spec.
        environment (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    client_id: str | None = Field(
        validation_alias="clientId",
        serialization_alias="clientId",
        default=None,
    )
    environment: str | None = None


class WebhookOpenpixChargeCompletedPayloadAccount(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedPayloadAccount.

    Attributes:
        environment (str | None): Undocumented in the spec.
    """

    environment: str | None = None


class WebhookOpenpixChargeCompletedPayloadCompany(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedPayloadCompany.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookOpenpixChargeCompletedPayloadPixCustomerTaxId(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedPayloadPixCustomerTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookOpenpixChargeCompletedPayloadPixPayerTaxId(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedPayloadPixPayerTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookOpenpixChargeCreatedPayloadAccount(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCreatedPayloadAccount.

    Attributes:
        environment (str | None): Undocumented in the spec.
    """

    environment: str | None = None


class WebhookOpenpixChargeCreatedPayloadCompany(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCreatedPayloadCompany.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookOpenpixChargeExpiredPayloadAccount(BaseSchema):
    """Schema generated for WebhookOpenpixChargeExpiredPayloadAccount.

    Attributes:
        environment (str | None): Undocumented in the spec.
    """

    environment: str | None = None


class WebhookOpenpixChargeExpiredPayloadCompany(BaseSchema):
    """Schema generated for WebhookOpenpixChargeExpiredPayloadCompany.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookOpenpixDisputeAcceptedPayloadDispute(BaseSchema):
    """Schema generated for WebhookOpenpixDisputeAcceptedPayloadDispute.

    Attributes:
        status (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone_number (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        dispute_reason (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    name: str | None = None
    email: str | None = None
    phone_number: str | None = Field(
        validation_alias="phoneNumber",
        serialization_alias="phoneNumber",
        default=None,
    )
    value: int | None = None
    dispute_reason: str | None = Field(
        validation_alias="disputeReason",
        serialization_alias="disputeReason",
        default=None,
    )


class WebhookOpenpixDisputeCanceledPayloadDispute(BaseSchema):
    """Schema generated for WebhookOpenpixDisputeCanceledPayloadDispute.

    Attributes:
        status (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone_number (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        dispute_reason (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    name: str | None = None
    email: str | None = None
    phone_number: str | None = Field(
        validation_alias="phoneNumber",
        serialization_alias="phoneNumber",
        default=None,
    )
    value: int | None = None
    dispute_reason: str | None = Field(
        validation_alias="disputeReason",
        serialization_alias="disputeReason",
        default=None,
    )


class WebhookOpenpixDisputeCreatedPayloadDispute(BaseSchema):
    """Schema generated for WebhookOpenpixDisputeCreatedPayloadDispute.

    Attributes:
        status (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone_number (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        dispute_reason (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    name: str | None = None
    email: str | None = None
    phone_number: str | None = Field(
        validation_alias="phoneNumber",
        serialization_alias="phoneNumber",
        default=None,
    )
    value: int | None = None
    dispute_reason: str | None = Field(
        validation_alias="disputeReason",
        serialization_alias="disputeReason",
        default=None,
    )


class WebhookOpenpixDisputeRejectedPayloadDispute(BaseSchema):
    """Schema generated for WebhookOpenpixDisputeRejectedPayloadDispute.

    Attributes:
        status (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone_number (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        dispute_reason (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    name: str | None = None
    email: str | None = None
    phone_number: str | None = Field(
        validation_alias="phoneNumber",
        serialization_alias="phoneNumber",
        default=None,
    )
    value: int | None = None
    dispute_reason: str | None = Field(
        validation_alias="disputeReason",
        serialization_alias="disputeReason",
        default=None,
    )


class WebhookOpenpixMovementConfirmedPayloadPayment(BaseSchema):
    """Schema generated for WebhookOpenpixMovementConfirmedPayloadPayment.

    Attributes:
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        destination_alias (str | None): Undocumented in the spec.
        comment (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int | None = None
    status: str | None = None
    destination_alias: str | None = Field(
        validation_alias="destinationAlias",
        serialization_alias="destinationAlias",
        default=None,
    )
    comment: str | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookOpenpixMovementConfirmedPayloadTransaction(BaseSchema):
    """Schema generated for WebhookOpenpixMovementConfirmedPayloadTransaction.

    Attributes:
        value (int | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    time: str | None = None


class WebhookOpenpixMovementFailedPayloadError(BaseSchema):
    """Schema generated for WebhookOpenpixMovementFailedPayloadError.

    Attributes:
        code (str | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
    """

    code: str | None = None
    description: str | None = None


class WebhookOpenpixMovementFailedPayloadPayment(BaseSchema):
    """Schema generated for WebhookOpenpixMovementFailedPayloadPayment.

    Attributes:
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        destination_alias (str | None): Undocumented in the spec.
        comment (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int | None = None
    status: str | None = None
    destination_alias: str | None = Field(
        validation_alias="destinationAlias",
        serialization_alias="destinationAlias",
        default=None,
    )
    comment: str | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookOpenpixMovementFailedPayloadTransaction(BaseSchema):
    """Schema generated for WebhookOpenpixMovementFailedPayloadTransaction.

    Attributes:
        value (int | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    time: str | None = None


class WebhookOpenpixMovementRemovedPayloadPayment(BaseSchema):
    """Schema generated for WebhookOpenpixMovementRemovedPayloadPayment.

    Attributes:
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookOpenpixTransactionReceivedPayloadAccount(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadAccount.

    Attributes:
        account_id (str | None): Undocumented in the spec.
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    account_id: str | None = Field(
        validation_alias="accountId",
        serialization_alias="accountId",
        default=None,
    )
    branch: str | None = None
    account: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookOpenpixTransactionReceivedPayloadCompany(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadCompany.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookOpenpixTransactionReceivedPayloadPixCreditPartyA(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixCreditPartyA.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookOpenpixTransactionReceivedPayloadPixCreditPartyH2(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixCreditPartyH2.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookOpenpixTransactionReceivedPayloadPixCreditPartyP(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixCreditPartyP.

    Attributes:
        pix_key (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    type: str | None = None


class WebhookOpenpixTransactionReceivedPayloadPixCreditPartyP2(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixCreditPartyP2.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookOpenpixTransactionReceivedPayloadPixDebitPartyAc(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixDebitPartyAc.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookOpenpixTransactionReceivedPayloadPixDebitPartyHo2(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixDebitPartyHo2.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookOpenpixTransactionReceivedPayloadPixDebitPartyPs(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixDebitPartyPs.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookOpenpixTransactionReceivedPayloadPixPayerTaxId(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixPayerTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookOpenpixTransactionRefundReceivedPayloadAccount(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionRefundReceivedPayloadAccount.

    Attributes:
        client_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    client_id: str | None = Field(
        validation_alias="clientId",
        serialization_alias="clientId",
        default=None,
    )


class WebhookOpenpixTransactionRefundReceivedPayloadCompany(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionRefundReceivedPayloadCompany.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookOpenpixTransactionRefundReceivedPayloadPix(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionRefundReceivedPayloadPix.

    Attributes:
        customer (Any | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        pix_key (str | None): Undocumented in the spec.
        partial (bool | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    customer: Any | None = None
    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    partial: bool | None = None
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
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
            removed by a user  * **OPENPIX:DISPUTE_CREATED** - Dispute created *
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
            **PIX_AUTOMATIC_COBR_TRY_REQUESTED** - Pix Automatic cobr try requested  *
            **COMPANY_BANK_ACCOUNT_BLOCKED** - Blockings on a bank account changed  *
            **STABLECOIN_DEPOSIT_COMPLETED** - Stablecoin deposit completed *
            **STABLECOIN_DEPOSIT_FAILED** - Stablecoin deposit failed *
            **STABLECOIN_PAYOUT_COMPLETED** - Stablecoin payout completed *
            **STABLECOIN_PAYOUT_FAILED** - Stablecoin payout failed *
            **STABLECOIN_PAYOUT_REFUND_CONFIRMED** - A settled stablecoin payout came
            back and the funds are available again in your stablecoin balance *
            **STABLECOIN_PAYOUT_REFUND_FAILED** - A settled stablecoin payout came back
            but the funds are not available to you; needs reconciliation *
            **STABLECOIN_SUBACCOUNT_CONFIRMED** - Stablecoin sub-account confirmed *
            **STABLECOIN_SUBACCOUNT_REJECTED** - Stablecoin sub-account rejected  *
            **BOLETO_SETTLED** - Boleto settled by the issuing bank  *
            **KYC_VALIDATION_COMPLETED** - KYC validation completed *
            **KYC_VALIDATION_FAILED** - KYC validation failed
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
            "- Payment was removed by a user\n\n* **OPENPIX:DISPUTE_CREATED** - "
            "Dispute created\n* **OPENPIX:DISPUTE_ACCEPTED** - Dispute accepted\n* "
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
            "**PIX_AUTOMATIC_COBR_TRY_REQUESTED** - Pix Automatic cobr try "
            "requested\n\n* **COMPANY_BANK_ACCOUNT_BLOCKED** - Blockings on a bank "
            "account changed\n\n* **STABLECOIN_DEPOSIT_COMPLETED** - Stablecoin "
            "deposit completed\n* **STABLECOIN_DEPOSIT_FAILED** - Stablecoin deposit "
            "failed\n* **STABLECOIN_PAYOUT_COMPLETED** - Stablecoin payout "
            "completed\n* **STABLECOIN_PAYOUT_FAILED** - Stablecoin payout failed\n* "
            "**STABLECOIN_PAYOUT_REFUND_CONFIRMED** - A settled stablecoin payout came "
            "back and the\nfunds are available again in your stablecoin balance\n* "
            "**STABLECOIN_PAYOUT_REFUND_FAILED** - A settled stablecoin payout came "
            "back but the funds\nare not available to you; needs reconciliation\n* "
            "**STABLECOIN_SUBACCOUNT_CONFIRMED** - Stablecoin sub-account confirmed\n* "
            "**STABLECOIN_SUBACCOUNT_REJECTED** - Stablecoin sub-account rejected\n\n* "
            "**BOLETO_SETTLED** - Boleto settled by the issuing bank\n\n* "
            "**KYC_VALIDATION_COMPLETED** - KYC validation completed\n* "
            "**KYC_VALIDATION_FAILED** - KYC validation failed"
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


class WebhookPixAutomaticApprovedPayloadCustomerAddressLocati(BaseSchema):
    """Schema generated for WebhookPixAutomaticApprovedPayloadCustomerAddressLocati.

    Attributes:
        coordinates (list[Any]): Undocumented in the spec.
    """

    coordinates: list[Any] = Field(default_factory=list)


class WebhookPixAutomaticApprovedPayloadCustomerTaxId(BaseSchema):
    """Schema generated for WebhookPixAutomaticApprovedPayloadCustomerTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixAutomaticApprovedPayloadPixRecurring(BaseSchema):
    """Schema generated for WebhookPixAutomaticApprovedPayloadPixRecurring.

    Attributes:
        recurrency_id (str | None): Undocumented in the spec.
        emv (str | None): Undocumented in the spec.
        journey (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    recurrency_id: str | None = Field(
        validation_alias="recurrencyId",
        serialization_alias="recurrencyId",
        default=None,
    )
    emv: str | None = None
    journey: str | None = None
    status: str | None = None


class WebhookPixAutomaticCobrApprovedPayloadCobrTriesItem(BaseSchema):
    """Schema generated for WebhookPixAutomaticCobrApprovedPayloadCobrTriesItem.

    Attributes:
        try_status (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        finality_purpose (str | None): Undocumented in the spec.
        requested_execution_date (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        updated_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    try_status: str | None = Field(
        validation_alias="tryStatus",
        serialization_alias="tryStatus",
        default=None,
    )
    value: int | None = None
    finality_purpose: str | None = Field(
        validation_alias="finalityPurpose",
        serialization_alias="finalityPurpose",
        default=None,
    )
    requested_execution_date: str | None = Field(
        validation_alias="requestedExecutionDate",
        serialization_alias="requestedExecutionDate",
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


class WebhookPixAutomaticCobrCompletedPayloadCobrTriesItem(BaseSchema):
    """Schema generated for WebhookPixAutomaticCobrCompletedPayloadCobrTriesItem.

    Attributes:
        try_status (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        finality_purpose (str | None): Undocumented in the spec.
        requested_execution_date (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        updated_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    try_status: str | None = Field(
        validation_alias="tryStatus",
        serialization_alias="tryStatus",
        default=None,
    )
    value: int | None = None
    finality_purpose: str | None = Field(
        validation_alias="finalityPurpose",
        serialization_alias="finalityPurpose",
        default=None,
    )
    requested_execution_date: str | None = Field(
        validation_alias="requestedExecutionDate",
        serialization_alias="requestedExecutionDate",
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


class WebhookPixAutomaticCobrCreatedPayloadCobrTriesItem(BaseSchema):
    """Schema generated for WebhookPixAutomaticCobrCreatedPayloadCobrTriesItem.

    Attributes:
        try_status (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        finality_purpose (str | None): Undocumented in the spec.
        requested_execution_date (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        updated_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    try_status: str | None = Field(
        validation_alias="tryStatus",
        serialization_alias="tryStatus",
        default=None,
    )
    value: int | None = None
    finality_purpose: str | None = Field(
        validation_alias="finalityPurpose",
        serialization_alias="finalityPurpose",
        default=None,
    )
    requested_execution_date: str | None = Field(
        validation_alias="requestedExecutionDate",
        serialization_alias="requestedExecutionDate",
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


class WebhookPixAutomaticCobrRejectedPayloadCobrTriesItem(BaseSchema):
    """Schema generated for WebhookPixAutomaticCobrRejectedPayloadCobrTriesItem.

    Attributes:
        try_status (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        finality_purpose (str | None): Undocumented in the spec.
        requested_execution_date (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        updated_at (str | None): Undocumented in the spec.
        reject_code (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    try_status: str | None = Field(
        validation_alias="tryStatus",
        serialization_alias="tryStatus",
        default=None,
    )
    value: int | None = None
    finality_purpose: str | None = Field(
        validation_alias="finalityPurpose",
        serialization_alias="finalityPurpose",
        default=None,
    )
    requested_execution_date: str | None = Field(
        validation_alias="requestedExecutionDate",
        serialization_alias="requestedExecutionDate",
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
    reject_code: str | None = Field(
        validation_alias="rejectCode",
        serialization_alias="rejectCode",
        default=None,
    )


class WebhookPixAutomaticCobrTryRejectedPayloadCobrTriesItem(BaseSchema):
    """Schema generated for WebhookPixAutomaticCobrTryRejectedPayloadCobrTriesItem.

    Attributes:
        try_status (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        finality_purpose (str | None): Undocumented in the spec.
        requested_execution_date (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        updated_at (str | None): Undocumented in the spec.
        reject_code (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    try_status: str | None = Field(
        validation_alias="tryStatus",
        serialization_alias="tryStatus",
        default=None,
    )
    value: int | None = None
    finality_purpose: str | None = Field(
        validation_alias="finalityPurpose",
        serialization_alias="finalityPurpose",
        default=None,
    )
    requested_execution_date: str | None = Field(
        validation_alias="requestedExecutionDate",
        serialization_alias="requestedExecutionDate",
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
    reject_code: str | None = Field(
        validation_alias="rejectCode",
        serialization_alias="rejectCode",
        default=None,
    )


class WebhookPixAutomaticCobrTryRequestedPayloadCobrTriesItem(BaseSchema):
    """Schema generated for WebhookPixAutomaticCobrTryRequestedPayloadCobrTriesItem.

    Attributes:
        try_status (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        finality_purpose (str | None): Undocumented in the spec.
        requested_execution_date (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        updated_at (str | None): Undocumented in the spec.
        reject_code (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    try_status: str | None = Field(
        validation_alias="tryStatus",
        serialization_alias="tryStatus",
        default=None,
    )
    value: int | None = None
    finality_purpose: str | None = Field(
        validation_alias="finalityPurpose",
        serialization_alias="finalityPurpose",
        default=None,
    )
    requested_execution_date: str | None = Field(
        validation_alias="requestedExecutionDate",
        serialization_alias="requestedExecutionDate",
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
    reject_code: str | None = Field(
        validation_alias="rejectCode",
        serialization_alias="rejectCode",
        default=None,
    )


class WebhookPixAutomaticRejectedPayloadCustomerAddressLocati(BaseSchema):
    """Schema generated for WebhookPixAutomaticRejectedPayloadCustomerAddressLocati.

    Attributes:
        coordinates (list[Any]): Undocumented in the spec.
    """

    coordinates: list[Any] = Field(default_factory=list)


class WebhookPixAutomaticRejectedPayloadCustomerTaxId(BaseSchema):
    """Schema generated for WebhookPixAutomaticRejectedPayloadCustomerTaxId.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixAutomaticRejectedPayloadPixRecurring(BaseSchema):
    """Schema generated for WebhookPixAutomaticRejectedPayloadPixRecurring.

    Attributes:
        recurrency_id (str | None): Undocumented in the spec.
        emv (str | None): Undocumented in the spec.
        journey (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    recurrency_id: str | None = Field(
        validation_alias="recurrencyId",
        serialization_alias="recurrencyId",
        default=None,
    )
    emv: str | None = None
    journey: str | None = None
    status: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadAcco(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadAcco.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        code (str | None): Undocumented in the spec.
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None
    status: str | None = None
    code: str | None = None
    branch: str | None = None
    account: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadComp(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadComp.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        name_friendly (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    name_friendly: str | None = Field(
        validation_alias="nameFriendly",
        serialization_alias="nameFriendly",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig10(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig10.

    Attributes:
        pix_key (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig11(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig11.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig12(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig12.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig14(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig14.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig3(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig3.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig5(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig5.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig6(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig6.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig8(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig8.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadRefu10(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadRefu10.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadRefu12(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadRefu12.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadRefu3(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadRefu3.

    Attributes:
        pix_key (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadRefu4(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadRefu4.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundReceivedConfirmedPayloadRefu5(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadRefu5.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadRefu7(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadRefu7.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadRefu9(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadRefu9.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundReceivedRejectedPayloadAccou(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadAccou.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        code (str | None): Undocumented in the spec.
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None
    status: str | None = None
    code: str | None = None
    branch: str | None = None
    account: str | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadCompa(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadCompa.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        name_friendly (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    name_friendly: str | None = Field(
        validation_alias="nameFriendly",
        serialization_alias="nameFriendly",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedRejectedPayloadOrigi10(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadOrigi10.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadOrigi12(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadOrigi12.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadOrigi3(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadOrigi3.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundReceivedRejectedPayloadOrigi4(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadOrigi4.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        code (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None
    code: str | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadOrigi6(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadOrigi6.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadOrigi8(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadOrigi8.

    Attributes:
        pix_key (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadOrigi9(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadOrigi9.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundReceivedRejectedPayloadRefun10(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadRefun10.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadRefun12(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadRefun12.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadRefun3(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadRefun3.

    Attributes:
        pix_key (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadRefun4(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadRefun4.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundReceivedRejectedPayloadRefun5(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadRefun5.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadRefun7(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadRefun7.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadRefun9(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadRefun9.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundSentConfirmedPayloadAccount(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadAccount.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        code (str | None): Undocumented in the spec.
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None
    status: str | None = None
    code: str | None = None
    branch: str | None = None
    account: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadCompany(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadCompany.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        name_friendly (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    name_friendly: str | None = Field(
        validation_alias="nameFriendly",
        serialization_alias="nameFriendly",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal10(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal10.

    Attributes:
        pix_key (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal11(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal11.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal12(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal12.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal14(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal14.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal3(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal3.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal5(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal5.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal6(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal6.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal8(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal8.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadRefundTr10(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadRefundTr10.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadRefundTr12(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadRefundTr12.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadRefundTr3(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadRefundTr3.

    Attributes:
        pix_key (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadRefundTr4(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadRefundTr4.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundSentConfirmedPayloadRefundTr5(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadRefundTr5.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadRefundTr7(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadRefundTr7.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadRefundTr9(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadRefundTr9.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundSentRejectedPayloadAccount(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadAccount.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        code (str | None): Undocumented in the spec.
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None
    status: str | None = None
    code: str | None = None
    branch: str | None = None
    account: str | None = None


class WebhookPixTransactionRefundSentRejectedPayloadCompany(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadCompany.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        name_friendly (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    name_friendly: str | None = Field(
        validation_alias="nameFriendly",
        serialization_alias="nameFriendly",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT10(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT10.

    Attributes:
        pix_key (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT11(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT11.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT12(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT12.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT14(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT14.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT3(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT3.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT5(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT5.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT6(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT6.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT8(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT8.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentRejectedPayloadRefundTra10(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadRefundTra10.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundSentRejectedPayloadRefundTra12(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadRefundTra12.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentRejectedPayloadRefundTra3(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadRefundTra3.

    Attributes:
        pix_key (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentRejectedPayloadRefundTra4(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadRefundTra4.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookPixTransactionRefundSentRejectedPayloadRefundTra5(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadRefundTra5.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None


class WebhookPixTransactionRefundSentRejectedPayloadRefundTra7(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadRefundTra7.

    Attributes:
        tax_id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    type: str | None = None


class WebhookPixTransactionRefundSentRejectedPayloadRefundTra9(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadRefundTra9.

    Attributes:
        branch (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        account_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    branch: str | None = None
    account: str | None = None
    account_type: str | None = Field(
        validation_alias="accountType",
        serialization_alias="accountType",
        default=None,
    )


class WebhookStablecoinDepositCompletedPayloadCompany(BaseSchema):
    """Public company info.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookStablecoinDepositCompletedPayloadStableDeposit(BaseSchema):
    """Schema generated for WebhookStablecoinDepositCompletedPayloadStableDeposit.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        input_amount (int | None): Undocumented in the spec.
        input_currency (str | None): Undocumented in the spec.
        output_amount (int | None): Undocumented in the spec.
        output_currency (str | None): Undocumented in the spec.
        tx_hash (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        completed_at (str | None): Undocumented in the spec.
        failed_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    status: str | None = None
    input_amount: int | None = Field(
        validation_alias="inputAmount",
        serialization_alias="inputAmount",
        default=None,
    )
    input_currency: str | None = Field(
        validation_alias="inputCurrency",
        serialization_alias="inputCurrency",
        default=None,
    )
    output_amount: int | None = Field(
        validation_alias="outputAmount",
        serialization_alias="outputAmount",
        default=None,
    )
    output_currency: str | None = Field(
        validation_alias="outputCurrency",
        serialization_alias="outputCurrency",
        default=None,
    )
    tx_hash: str | None = Field(
        validation_alias="txHash",
        serialization_alias="txHash",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    completed_at: str | None = Field(
        validation_alias="completedAt",
        serialization_alias="completedAt",
        default=None,
    )
    failed_at: str | None = Field(
        validation_alias="failedAt",
        serialization_alias="failedAt",
        default=None,
    )


class WebhookStablecoinDepositFailedPayloadCompany(BaseSchema):
    """Public company info.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookStablecoinDepositFailedPayloadStableDeposit(BaseSchema):
    """Schema generated for WebhookStablecoinDepositFailedPayloadStableDeposit.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        input_amount (int | None): Undocumented in the spec.
        input_currency (str | None): Undocumented in the spec.
        output_amount (int | None): Undocumented in the spec.
        output_currency (str | None): Undocumented in the spec.
        tx_hash (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        completed_at (str | None): Undocumented in the spec.
        failed_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    status: str | None = None
    input_amount: int | None = Field(
        validation_alias="inputAmount",
        serialization_alias="inputAmount",
        default=None,
    )
    input_currency: str | None = Field(
        validation_alias="inputCurrency",
        serialization_alias="inputCurrency",
        default=None,
    )
    output_amount: int | None = Field(
        validation_alias="outputAmount",
        serialization_alias="outputAmount",
        default=None,
    )
    output_currency: str | None = Field(
        validation_alias="outputCurrency",
        serialization_alias="outputCurrency",
        default=None,
    )
    tx_hash: str | None = Field(
        validation_alias="txHash",
        serialization_alias="txHash",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    completed_at: str | None = Field(
        validation_alias="completedAt",
        serialization_alias="completedAt",
        default=None,
    )
    failed_at: str | None = Field(
        validation_alias="failedAt",
        serialization_alias="failedAt",
        default=None,
    )


class WebhookStablecoinPayoutCompletedPayloadCompany(BaseSchema):
    """Public company info.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookStablecoinPayoutCompletedPayloadStablePayout(BaseSchema):
    """Schema generated for WebhookStablecoinPayoutCompletedPayloadStablePayout.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        input_amount (int | None): Undocumented in the spec.
        input_currency (str | None): Undocumented in the spec.
        output_amount (int | None): Undocumented in the spec.
        output_currency (str | None): Undocumented in the spec.
        pix_key (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        completed_at (str | None): Undocumented in the spec.
        failed_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    status: str | None = None
    input_amount: int | None = Field(
        validation_alias="inputAmount",
        serialization_alias="inputAmount",
        default=None,
    )
    input_currency: str | None = Field(
        validation_alias="inputCurrency",
        serialization_alias="inputCurrency",
        default=None,
    )
    output_amount: int | None = Field(
        validation_alias="outputAmount",
        serialization_alias="outputAmount",
        default=None,
    )
    output_currency: str | None = Field(
        validation_alias="outputCurrency",
        serialization_alias="outputCurrency",
        default=None,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    completed_at: str | None = Field(
        validation_alias="completedAt",
        serialization_alias="completedAt",
        default=None,
    )
    failed_at: str | None = Field(
        validation_alias="failedAt",
        serialization_alias="failedAt",
        default=None,
    )


class WebhookStablecoinPayoutFailedPayloadCompany(BaseSchema):
    """Public company info.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookStablecoinPayoutFailedPayloadStablePayout(BaseSchema):
    """Schema generated for WebhookStablecoinPayoutFailedPayloadStablePayout.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        input_amount (int | None): Undocumented in the spec.
        input_currency (str | None): Undocumented in the spec.
        output_amount (int | None): Undocumented in the spec.
        output_currency (str | None): Undocumented in the spec.
        pix_key (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        completed_at (str | None): Undocumented in the spec.
        failed_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    status: str | None = None
    input_amount: int | None = Field(
        validation_alias="inputAmount",
        serialization_alias="inputAmount",
        default=None,
    )
    input_currency: str | None = Field(
        validation_alias="inputCurrency",
        serialization_alias="inputCurrency",
        default=None,
    )
    output_amount: int | None = Field(
        validation_alias="outputAmount",
        serialization_alias="outputAmount",
        default=None,
    )
    output_currency: str | None = Field(
        validation_alias="outputCurrency",
        serialization_alias="outputCurrency",
        default=None,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    completed_at: str | None = Field(
        validation_alias="completedAt",
        serialization_alias="completedAt",
        default=None,
    )
    failed_at: str | None = Field(
        validation_alias="failedAt",
        serialization_alias="failedAt",
        default=None,
    )


class WebhookStablecoinPayoutRefundConfirmedPayloadCompany(BaseSchema):
    """Public company info.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookStablecoinPayoutRefundConfirmedPayloadRefund(BaseSchema):
    """The returned money, as reported by the provider.

    Attributes:
        status (WebhookStablecoinPayoutRefundConfirmedPayloadRefundStat | None):
            Undocumented in the spec.
        amount (int | None): Cents of `currency`, the same unit as
            `stablePayout.inputAmount` — never the BRL `outputAmount`.
        currency (str | None): The payout's input asset, e.g. `BRLA` or `USDC`.
        destination (WebhookStablecoinPayoutRefundConfirmedPayloadRefundDest | None):
            Where the returned funds ended up. Only `SUBACCOUNT_BALANCE` is withdrawable
            by you; `MAIN_BALANCE` means the provider credited its main account and a
            human has to move it; `NONE` means nothing was credited.
        provider_ticket_id (str | None): The return's own provider ticket, not the
            original payout's. Dedup on it: a replayed delivery repeats the same value.
        original_provider_ticket_id (str | None): Undocumented in the spec.
        reason (str | None): Undocumented in the spec.
        refund_end_to_end_id (str | None): The Pix devolution leg, when the provider
            reports one.
        refunded_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: WebhookStablecoinPayoutRefundConfirmedPayloadRefundStat | None = None
    amount: int | None = Field(
        description=(
            "Cents of `currency`, the same unit as `stablePayout.inputAmount` — never "
            "the BRL `outputAmount`."
        ),
        default=None,
    )
    currency: str | None = Field(
        description="The payout's input asset, e.g. `BRLA` or `USDC`.",
        default=None,
    )
    destination: WebhookStablecoinPayoutRefundConfirmedPayloadRefundDest | None = Field(
        description=(
            "Where the returned funds ended up. Only `SUBACCOUNT_BALANCE` is "
            "withdrawable by you; `MAIN_BALANCE` means the provider credited its main "
            "account and a human has to move it; `NONE` means nothing was credited."
        ),
        default=None,
    )
    provider_ticket_id: str | None = Field(
        validation_alias="providerTicketId",
        serialization_alias="providerTicketId",
        description=(
            "The return's own provider ticket, not the original payout's. Dedup on it: "
            "a replayed delivery repeats the same value."
        ),
        default=None,
    )
    original_provider_ticket_id: str | None = Field(
        validation_alias="originalProviderTicketId",
        serialization_alias="originalProviderTicketId",
        default=None,
    )
    reason: str | None = None
    refund_end_to_end_id: str | None = Field(
        validation_alias="refundEndToEndId",
        serialization_alias="refundEndToEndId",
        description="The Pix devolution leg, when the provider reports one.",
        default=None,
    )
    refunded_at: str | None = Field(
        validation_alias="refundedAt",
        serialization_alias="refundedAt",
        default=None,
    )


class WebhookStablecoinPayoutRefundConfirmedPayloadStablePayo(BaseSchema):
    """Schema generated for WebhookStablecoinPayoutRefundConfirmedPayloadStablePayo.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        input_amount (int | None): Undocumented in the spec.
        input_currency (str | None): Undocumented in the spec.
        output_amount (int | None): Undocumented in the spec.
        output_currency (str | None): Undocumented in the spec.
        pix_key (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    status: str | None = None
    input_amount: int | None = Field(
        validation_alias="inputAmount",
        serialization_alias="inputAmount",
        default=None,
    )
    input_currency: str | None = Field(
        validation_alias="inputCurrency",
        serialization_alias="inputCurrency",
        default=None,
    )
    output_amount: int | None = Field(
        validation_alias="outputAmount",
        serialization_alias="outputAmount",
        default=None,
    )
    output_currency: str | None = Field(
        validation_alias="outputCurrency",
        serialization_alias="outputCurrency",
        default=None,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookStablecoinPayoutRefundFailedPayloadCompany(BaseSchema):
    """Public company info.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookStablecoinPayoutRefundFailedPayloadRefund(BaseSchema):
    """The returned money, as reported by the provider.

    Attributes:
        status (WebhookStablecoinPayoutRefundConfirmedPayloadRefundStat | None):
            Undocumented in the spec.
        amount (int | None): Cents of `currency`, the same unit as
            `stablePayout.inputAmount` — never the BRL `outputAmount`.
        currency (str | None): The payout's input asset, e.g. `BRLA` or `USDC`.
        destination (WebhookStablecoinPayoutRefundConfirmedPayloadRefundDest | None):
            Where the returned funds ended up. Only `SUBACCOUNT_BALANCE` is withdrawable
            by you; `MAIN_BALANCE` means the provider credited its main account and a
            human has to move it; `NONE` means nothing was credited.
        provider_ticket_id (str | None): The return's own provider ticket, not the
            original payout's. Dedup on it: a replayed delivery repeats the same value.
        original_provider_ticket_id (str | None): Undocumented in the spec.
        reason (str | None): Undocumented in the spec.
        refund_end_to_end_id (str | None): The Pix devolution leg, when the provider
            reports one.
        failure_reason (str | None): Why the funds are not available to you.
        refunded_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: WebhookStablecoinPayoutRefundConfirmedPayloadRefundStat | None = None
    amount: int | None = Field(
        description=(
            "Cents of `currency`, the same unit as `stablePayout.inputAmount` — never "
            "the BRL `outputAmount`."
        ),
        default=None,
    )
    currency: str | None = Field(
        description="The payout's input asset, e.g. `BRLA` or `USDC`.",
        default=None,
    )
    destination: WebhookStablecoinPayoutRefundConfirmedPayloadRefundDest | None = Field(
        description=(
            "Where the returned funds ended up. Only `SUBACCOUNT_BALANCE` is "
            "withdrawable by you; `MAIN_BALANCE` means the provider credited its main "
            "account and a human has to move it; `NONE` means nothing was credited."
        ),
        default=None,
    )
    provider_ticket_id: str | None = Field(
        validation_alias="providerTicketId",
        serialization_alias="providerTicketId",
        description=(
            "The return's own provider ticket, not the original payout's. Dedup on it: "
            "a replayed delivery repeats the same value."
        ),
        default=None,
    )
    original_provider_ticket_id: str | None = Field(
        validation_alias="originalProviderTicketId",
        serialization_alias="originalProviderTicketId",
        default=None,
    )
    reason: str | None = None
    refund_end_to_end_id: str | None = Field(
        validation_alias="refundEndToEndId",
        serialization_alias="refundEndToEndId",
        description="The Pix devolution leg, when the provider reports one.",
        default=None,
    )
    failure_reason: str | None = Field(
        validation_alias="failureReason",
        serialization_alias="failureReason",
        description="Why the funds are not available to you.",
        default=None,
    )
    refunded_at: str | None = Field(
        validation_alias="refundedAt",
        serialization_alias="refundedAt",
        default=None,
    )


class WebhookStablecoinPayoutRefundFailedPayloadStablePayout(BaseSchema):
    """Schema generated for WebhookStablecoinPayoutRefundFailedPayloadStablePayout.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        input_amount (int | None): Undocumented in the spec.
        input_currency (str | None): Undocumented in the spec.
        output_amount (int | None): Undocumented in the spec.
        output_currency (str | None): Undocumented in the spec.
        pix_key (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    status: str | None = None
    input_amount: int | None = Field(
        validation_alias="inputAmount",
        serialization_alias="inputAmount",
        default=None,
    )
    input_currency: str | None = Field(
        validation_alias="inputCurrency",
        serialization_alias="inputCurrency",
        default=None,
    )
    output_amount: int | None = Field(
        validation_alias="outputAmount",
        serialization_alias="outputAmount",
        default=None,
    )
    output_currency: str | None = Field(
        validation_alias="outputCurrency",
        serialization_alias="outputCurrency",
        default=None,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookStablecoinSubaccountConfirmedPayloadCompany(BaseSchema):
    """Public company info.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookStablecoinSubaccountConfirmedPayloadStableSubAcc(BaseSchema):
    """Schema generated for WebhookStablecoinSubaccountConfirmedPayloadStableSubAcc.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        sub_account_id (str | None): Undocumented in the spec.
        account_register_id (str | None): Undocumented in the spec.
        confirmed_at (str | None): Undocumented in the spec.
        rejected_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    status: str | None = None
    sub_account_id: str | None = Field(
        validation_alias="subAccountId",
        serialization_alias="subAccountId",
        default=None,
    )
    account_register_id: str | None = Field(
        validation_alias="accountRegisterId",
        serialization_alias="accountRegisterId",
        default=None,
    )
    confirmed_at: str | None = Field(
        validation_alias="confirmedAt",
        serialization_alias="confirmedAt",
        default=None,
    )
    rejected_at: str | None = Field(
        validation_alias="rejectedAt",
        serialization_alias="rejectedAt",
        default=None,
    )


class WebhookStablecoinSubaccountRejectedPayloadCompany(BaseSchema):
    """Public company info.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        tax_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str | None = None
    tax_id: str | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookStablecoinSubaccountRejectedPayloadStableSubAcco(BaseSchema):
    """Schema generated for WebhookStablecoinSubaccountRejectedPayloadStableSubAcco.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        sub_account_id (str | None): Undocumented in the spec.
        account_register_id (str | None): Undocumented in the spec.
        confirmed_at (str | None): Undocumented in the spec.
        rejected_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    status: str | None = None
    sub_account_id: str | None = Field(
        validation_alias="subAccountId",
        serialization_alias="subAccountId",
        default=None,
    )
    account_register_id: str | None = Field(
        validation_alias="accountRegisterId",
        serialization_alias="accountRegisterId",
        default=None,
    )
    confirmed_at: str | None = Field(
        validation_alias="confirmedAt",
        serialization_alias="confirmedAt",
        default=None,
    )
    rejected_at: str | None = Field(
        validation_alias="rejectedAt",
        serialization_alias="rejectedAt",
        default=None,
    )


class WithdrawFromAccountBody(BaseSchema):
    """Schema generated for WithdrawFromAccountBody.

    Attributes:
        value (int | None): Value in cents
    """

    value: int | None = Field(description="Value in cents", default=None)


class WithdrawTransaction(BaseSchema):
    """Schema generated for WithdrawTransaction.

    Attributes:
        end_to_end_id (str | None): ID of the Withdraw Transaction
        value (int | None): Value withdrawn, in cents. The specification declares this
            `string` while declaring the same field `number` on
            `PixWithdrawTransaction`.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        description="ID of the Withdraw Transaction",
        default=None,
    )
    value: int | None = Field(
        description=(
            "Value withdrawn, in cents. The specification declares this `string` while "
            "declaring the same field `number` on `PixWithdrawTransaction`."
        ),
        default=None,
    )


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


class AnticipationBalanceBatchPayload(BaseSchema):
    """Schema generated for AnticipationBalanceBatchPayload.

    Attributes:
        items (list[AnticipationBalanceBatchPayloadItemsItem]): Undocumented in the
            spec.
    """

    items: list[AnticipationBalanceBatchPayloadItemsItem] = Field(
        min_length=1,
        max_length=1000,
    )


class AnticipationBalanceBatchResult(BaseSchema):
    """Schema generated for AnticipationBalanceBatchResult.

    Attributes:
        processed (int | None): Undocumented in the spec.
        succeeded (int | None): Undocumented in the spec.
        failed (int | None): Undocumented in the spec.
        results (list[AnticipationBalanceBatchResultResultsItem]): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    processed: int | None = None
    succeeded: int | None = None
    failed: int | None = None
    results: list[AnticipationBalanceBatchResultResultsItem] = Field(
        default_factory=list,
    )


class AnticipationBeneficiary(BaseSchema):
    """Schema generated for AnticipationBeneficiary.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (AnticipationBeneficiaryTaxId | None): Undocumented in the spec.
        is_active (bool | None): Undocumented in the spec.
        available_amount (int | None): Available balance, in cents.
        max_advanceable_amount (int | None): Advanceable limit, in cents.
        notify_email (str | None): Undocumented in the spec.
        notify_phone (str | None): Undocumented in the spec.
        verified (bool | None): Whether the beneficiary completed identity verification
            (pix-auth).
        created_at (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str | None = None
    tax_id: AnticipationBeneficiaryTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    is_active: bool | None = Field(
        validation_alias="isActive",
        serialization_alias="isActive",
        default=None,
    )
    available_amount: int | None = Field(
        validation_alias="availableAmount",
        serialization_alias="availableAmount",
        description="Available balance, in cents.",
        default=None,
    )
    max_advanceable_amount: int | None = Field(
        validation_alias="maxAdvanceableAmount",
        serialization_alias="maxAdvanceableAmount",
        description="Advanceable limit, in cents.",
        default=None,
    )
    notify_email: str | None = Field(
        validation_alias="notifyEmail",
        serialization_alias="notifyEmail",
        default=None,
    )
    notify_phone: str | None = Field(
        validation_alias="notifyPhone",
        serialization_alias="notifyPhone",
        default=None,
    )
    verified: bool | None = Field(
        description=(
            "Whether the beneficiary completed identity verification (pix-auth)."
        ),
        default=None,
    )
    created_at: datetime | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )


class AnticipationBeneficiaryCreatePayload(BaseSchema):
    """Schema generated for AnticipationBeneficiaryCreatePayload.

    Attributes:
        name (str): Beneficiary name.
        tax_id (str): Payout key (CPF or CNPJ), with or without mask.
        cpf (str | None): The person's CPF. Required when taxID is a CNPJ.
        notify_email (EmailStr | None): Email used for notifications.
        notify_phone (str | None): Phone used for notifications.
        available_amount (int | None): Available balance, in cents.
        max_advanceable_amount (int | None): Advanceable limit, in cents.
        auto_approve (bool | None): Per-beneficiary override of the company
            auto-approval.
        fee_destination_account_id (str | None): Fee destination account (overrides the
            company setting).
        payment_days_override (list[int] | None): Per-beneficiary payment days (1-31).
        frequency_override (AnticipationBeneficiaryCreatePayloadFrequencyOverride |
            None): Per-beneficiary rolling frequency window.
        correlation_id (str | None): Your correlation ID, echoed back in the response.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(description="Beneficiary name.", min_length=2, max_length=120)
    tax_id: str = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        description="Payout key (CPF or CNPJ), with or without mask.",
    )
    cpf: str | None = Field(
        description="The person's CPF. Required when taxID is a CNPJ.",
        default=None,
    )
    notify_email: EmailStr | None = Field(
        validation_alias="notifyEmail",
        serialization_alias="notifyEmail",
        description="Email used for notifications.",
        default=None,
    )
    notify_phone: str | None = Field(
        validation_alias="notifyPhone",
        serialization_alias="notifyPhone",
        description="Phone used for notifications.",
        default=None,
    )
    available_amount: int | None = Field(
        validation_alias="availableAmount",
        serialization_alias="availableAmount",
        description="Available balance, in cents.",
        ge=0,
        default=None,
    )
    max_advanceable_amount: int | None = Field(
        validation_alias="maxAdvanceableAmount",
        serialization_alias="maxAdvanceableAmount",
        description="Advanceable limit, in cents.",
        ge=0,
        default=None,
    )
    auto_approve: bool | None = Field(
        validation_alias="autoApprove",
        serialization_alias="autoApprove",
        description="Per-beneficiary override of the company auto-approval.",
        default=None,
    )
    fee_destination_account_id: str | None = Field(
        validation_alias="feeDestinationAccountId",
        serialization_alias="feeDestinationAccountId",
        description="Fee destination account (overrides the company setting).",
        default=None,
    )
    payment_days_override: list[int] | None = Field(
        validation_alias="paymentDaysOverride",
        serialization_alias="paymentDaysOverride",
        description="Per-beneficiary payment days (1-31).",
        default=None,
    )
    frequency_override: AnticipationBeneficiaryCreatePayloadFrequencyOverride | None = (
        Field(
            validation_alias="frequencyOverride",
            serialization_alias="frequencyOverride",
            description="Per-beneficiary rolling frequency window.",
            default=None,
        )
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        description="Your correlation ID, echoed back in the response.",
        default=None,
    )


class AnticipationUnauthorized(BaseSchema):
    """Schema generated for AnticipationUnauthorized.

    Attributes:
        data (dict[str, Any] | None): Undocumented in the spec.
        errors (list[AnticipationUnauthorizedErrorsItem]): Undocumented in the spec.
    """

    data: dict[str, Any] | None = None
    errors: list[AnticipationUnauthorizedErrorsItem] = Field(default_factory=list)


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


class ApproveAnticipationResponse(BaseSchema):
    """Schema generated for ApproveAnticipationResponse.

    Attributes:
        anticipation (AnticipationRequest | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    anticipation: AnticipationRequest | None = None


class BoletoTransaction(BaseSchema):
    """Schema generated for BoletoTransaction.

    Attributes:
        boleto_transaction_id (str): Id of this transaction, the same one delivered in
            the `BOLETO_SETTLED` webhook. Use it to look the transaction up.
        type (BoletoTransactionType): `BOLETO_IN` is a boleto your payer paid,
            `BOLETO_OUT` a boleto your company paid.
        status (BoletoTransactionStatus): Undocumented in the spec.
        value (int): Amount that moved, in cents. On a boleto paid after the due date
            this is above the emitted amount in `charge.value`, because of interest and
            fine — `finesValue` and `interestsValue` split that difference.
        fee (int | None): Woovi fee for this transaction, in cents.
        created_at (datetime): Undocumented in the spec.
        settled_at (datetime | None): When the amount was credited to your account. Only
            present on a settled `BOLETO_IN` — it is not the moment the payer paid, and
            a `BOLETO_OUT` never carries it.
        fines_value (int | None): Late-payment fine the payer paid, in cents, as charged
            by the bank. Only on `BOLETO_IN`, and absent when there was none — a boleto
            paid on time carries neither this nor `interestsValue`.
        interests_value (int | None): Late-payment interest the payer paid, in cents, as
            charged by the bank. Same presence rules as `finesValue`.
        discount_value (int | None): Discount the payer got, in cents. Same presence
            rules as `finesValue`.
        charge (BoletoTransactionCharge | None): The charge the payer settled. Only
            present on `BOLETO_IN`; a `BOLETO_OUT` is driven by a payment, not a charge.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    boleto_transaction_id: str = Field(
        validation_alias="boletoTransactionID",
        serialization_alias="boletoTransactionID",
        description=(
            "Id of this transaction, the same one delivered in the "
            "`BOLETO_SETTLED`\nwebhook. Use it to look the transaction up."
        ),
        examples=["btx_019fa55beec9775faf8a069d64dcde54"],
    )
    type: BoletoTransactionType = Field(
        description=(
            "`BOLETO_IN` is a boleto your payer paid, `BOLETO_OUT` a boleto "
            "your\ncompany paid."
        ),
    )
    status: BoletoTransactionStatus
    value: int = Field(
        description=(
            "Amount that moved, in cents. On a boleto paid after the due date this\nis "
            "above the emitted amount in `charge.value`, because of interest and\nfine "
            "— `finesValue` and `interestsValue` split that difference."
        ),
        examples=[245000],
    )
    fee: int | None = Field(
        description="Woovi fee for this transaction, in cents.",
        examples=[299],
        default=None,
    )
    created_at: datetime = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
    )
    settled_at: datetime | None = Field(
        validation_alias="settledAt",
        serialization_alias="settledAt",
        description=(
            "When the amount was credited to your account. Only present on a\nsettled "
            "`BOLETO_IN` — it is not the moment the payer paid, and a\n`BOLETO_OUT` "
            "never carries it."
        ),
        default=None,
    )
    fines_value: int | None = Field(
        validation_alias="finesValue",
        serialization_alias="finesValue",
        description=(
            "Late-payment fine the payer paid, in cents, as charged by the bank.\nOnly "
            "on `BOLETO_IN`, and absent when there was none — a boleto paid\non time "
            "carries neither this nor `interestsValue`."
        ),
        examples=[1902],
        default=None,
    )
    interests_value: int | None = Field(
        validation_alias="interestsValue",
        serialization_alias="interestsValue",
        description=(
            "Late-payment interest the payer paid, in cents, as charged by the\nbank. "
            "Same presence rules as `finesValue`."
        ),
        examples=[200],
        default=None,
    )
    discount_value: int | None = Field(
        validation_alias="discountValue",
        serialization_alias="discountValue",
        description=(
            "Discount the payer got, in cents. Same presence rules as `finesValue`."
        ),
        examples=[500],
        default=None,
    )
    charge: BoletoTransactionCharge | None = Field(
        description=(
            "The charge the payer settled. Only present on `BOLETO_IN`; "
            "a\n`BOLETO_OUT` is driven by a payment, not a charge."
        ),
        default=None,
    )


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
        discount_fixed_date (list[ChargePayloadDiscountSettingsDiscountFixedDateItem] |
            None): Absolute discounts applied to charge. Required when `modality` is
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
    discount_fixed_date: (
        list[ChargePayloadDiscountSettingsDiscountFixedDateItem] | None
    ) = Field(
        validation_alias="discountFixedDate",
        serialization_alias="discountFixedDate",
        description=(
            "Absolute discounts applied to charge. Required when `modality` is "
            "`FIXED_VALUE_UNTIL_SPECIFIED_DATE` or `PERCENTAGE_UNTIL_SPECIFIED_DATE`. "
            "Must contain at least one entry."
        ),
        min_length=1,
        default=None,
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


class CreateApplicationResponse(BaseSchema):
    """Schema generated for CreateApplicationResponse.

    Attributes:
        application (Application | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    application: Application | None = None


class CreateCashbackFidelityResponse(BaseSchema):
    """Schema generated for CreateCashbackFidelityResponse.

    Attributes:
        cashback (CreateCashbackFidelityResponseCashback | None): Object representing
            the existing cashback
        message (str | None): String explaining what happened
    """

    model_config = ConfigDict(extra="allow")

    cashback: CreateCashbackFidelityResponseCashback | None = Field(
        description="Object representing the existing cashback",
        default=None,
    )
    message: str | None = Field(
        description="String explaining what happened",
        default=None,
    )


class CreateInvoiceResponseInvoice(BaseSchema):
    """Schema generated for CreateInvoiceResponseInvoice.

    Attributes:
        id (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        date (datetime | None): Undocumented in the spec.
        billing_date (datetime | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        status_raw (str | None): Undocumented in the spec.
        customer (CreateInvoiceResponseInvoiceCustomer | None): Undocumented in the
            spec.
        charge (CreateInvoiceResponseInvoiceCharge | None): Undocumented in the spec.
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
    customer: CreateInvoiceResponseInvoiceCustomer | None = None
    charge: CreateInvoiceResponseInvoiceCharge | None = None


class CreatePartnerApplicationBody(BaseSchema):
    """Schema generated for CreatePartnerApplicationBody.

    Attributes:
        application (CreatePartnerApplicationBodyApplication | None): Undocumented in
            the spec.
        tax_id (TaxIdObjectPayload | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    application: CreatePartnerApplicationBodyApplication | None = None
    tax_id: TaxIdObjectPayload | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class CreatePartnerApplicationResponse(BaseSchema):
    """Schema generated for CreatePartnerApplicationResponse.

    Attributes:
        application (PartnerApplicationPayload | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    application: PartnerApplicationPayload | None = None


class CreatePaymentBodyManualHolder(BaseSchema):
    """Schema generated for CreatePaymentBodyManualHolder.

    Attributes:
        name (str): name of the account holder
        tax_id (CreatePaymentBodyManualHolderTaxId): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(description="name of the account holder")
    tax_id: CreatePaymentBodyManualHolderTaxId = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
    )


class CreateRefundResponse(BaseSchema):
    """Schema generated for CreateRefundResponse.

    Attributes:
        refund (Refund | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    refund: Refund | None = None


class CreateStablecoinPayoutResponse(BaseSchema):
    """Schema generated for CreateStablecoinPayoutResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        payout_id (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        pix_key (str | None): Undocumented in the spec.
        pix_key_owner (CreateStablecoinPayoutResponsePixKeyOwner | None): Undocumented
            in the spec.
        quote (CreateStablecoinPayoutResponseQuote | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(examples=["PROCESSING"], default=None)
    payout_id: str | None = Field(
        validation_alias="payoutId",
        serialization_alias="payoutId",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationId",
        serialization_alias="correlationId",
        default=None,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    pix_key_owner: CreateStablecoinPayoutResponsePixKeyOwner | None = Field(
        validation_alias="pixKeyOwner",
        serialization_alias="pixKeyOwner",
        default=None,
    )
    quote: CreateStablecoinPayoutResponseQuote | None = None


class CreateStaticQrCodeResponse(BaseSchema):
    """Schema generated for CreateStaticQrCodeResponse.

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


class CreateSubaccountResponse(BaseSchema):
    """Schema generated for CreateSubaccountResponse.

    Attributes:
        sub_account (SubAccount | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    sub_account: SubAccount | None = Field(
        validation_alias="SubAccount",
        serialization_alias="SubAccount",
        default=None,
    )


class CreateTransferResponse(BaseSchema):
    """Schema generated for CreateTransferResponse.

    Attributes:
        transaction (TransferTransaction | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    transaction: TransferTransaction | None = None


class CreateWebhookBody(BaseSchema):
    """Schema generated for CreateWebhookBody.

    Attributes:
        webhook (WebhookPayload | None): Undocumented in the spec.
    """

    webhook: WebhookPayload | None = None


class CreateWebhookResponse(BaseSchema):
    """Schema generated for CreateWebhookResponse.

    Attributes:
        webhook (Webhook | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    webhook: Webhook | None = None


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


class DecodeEmvResponseCobLocationPayload(BaseSchema):
    """Schema generated for DecodeEmvResponseCobLocationPayload.

    Attributes:
        calendar (DecodeEmvResponseCobLocationPayloadCalendar | None): Undocumented in
            the spec.
        key (str | None): Undocumented in the spec.
        debtor (DecodeEmvResponseCobLocationPayloadDebtor | None): Undocumented in the
            spec.
        additional_info (list[DecodeEmvResponseCobLocationPayloadAdditionalInfoItem]):
            Undocumented in the spec.
        revision (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        txid (str | None): Undocumented in the spec.
        value (DecodeEmvResponseCobLocationPayloadValue | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    calendar: DecodeEmvResponseCobLocationPayloadCalendar | None = None
    key: str | None = None
    debtor: DecodeEmvResponseCobLocationPayloadDebtor | None = None
    additional_info: list[DecodeEmvResponseCobLocationPayloadAdditionalInfoItem] = (
        Field(
            validation_alias="additionalInfo",
            serialization_alias="additionalInfo",
            default_factory=list,
        )
    )
    revision: int | None = None
    status: str | None = None
    txid: str | None = None
    value: DecodeEmvResponseCobLocationPayloadValue | None = None


class DecodeEmvResponseEmv(BaseSchema):
    """Schema generated for DecodeEmvResponseEmv.

    Attributes:
        payload_format_indicator (str | None): Undocumented in the spec.
        point_of_initiation_method (str | None): Present when EMV indicates a dynamic QR
            (e.g. "12")
        merchant_account_information_pix
            (DecodeEmvResponseEmvMerchantAccountInformationPix | None): Parsed
            "26"/"00"... Pix merchant account info
        merchant_category_code (str | None): Undocumented in the spec.
        transaction_currency (str | None): Undocumented in the spec.
        transaction_amount (str | None): Undocumented in the spec.
        country_code (str | None): Undocumented in the spec.
        merchant_name (str | None): Undocumented in the spec.
        merchant_city (str | None): Undocumented in the spec.
        additional_data_field_template (DecodeEmvResponseEmvAdditionalDataFieldTemplate
            | None): Undocumented in the spec.
        unreserved_templates (DecodeEmvResponseEmvUnreservedTemplates | None):
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
        DecodeEmvResponseEmvMerchantAccountInformationPix | None
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
        DecodeEmvResponseEmvAdditionalDataFieldTemplate | None
    ) = Field(
        validation_alias="additionalDataFieldTemplate",
        serialization_alias="additionalDataFieldTemplate",
        default=None,
    )
    unreserved_templates: DecodeEmvResponseEmvUnreservedTemplates | None = Field(
        validation_alias="unreservedTemplates",
        serialization_alias="unreservedTemplates",
        default=None,
    )
    crc: str | None = None


class DecodeEmvResponseRecLocationPayloadLink(BaseSchema):
    """Schema generated for DecodeEmvResponseRecLocationPayloadLink.

    Attributes:
        contract (str | None): Undocumented in the spec.
        debtor (DecodeEmvResponseRecLocationPayloadLinkDebtor | None): Undocumented in
            the spec.
    """

    model_config = ConfigDict(extra="allow")

    contract: str | None = None
    debtor: DecodeEmvResponseRecLocationPayloadLinkDebtor | None = None


class FilePayload(BaseSchema):
    """Schema generated for FilePayload.

    Attributes:
        file (File | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    file: File | None = None


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


class GetAccountLimitsResponse(BaseSchema):
    """Schema generated for GetAccountLimitsResponse.

    Attributes:
        limits (AccountLimit | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    limits: AccountLimit | None = None


class GetAccountRegisterResponse(BaseSchema):
    """Schema generated for GetAccountRegisterResponse.

    Attributes:
        official_name (str | None): Undocumented in the spec.
        trade_name (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        tax_id (GetAccountRegisterResponseTaxId | None): Undocumented in the spec.
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
    tax_id: GetAccountRegisterResponseTaxId | None = Field(
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


class GetCompanyResponse(BaseSchema):
    """Schema generated for GetCompanyResponse.

    Attributes:
        company (GetCompanyResponseCompany | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    company: GetCompanyResponseCompany | None = None


class GetDisputeResponse(BaseSchema):
    """Schema generated for GetDisputeResponse.

    Attributes:
        dispute (GetDisputeResponseDispute | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    dispute: GetDisputeResponseDispute | None = None


class GetRefundResponse(BaseSchema):
    """Schema generated for GetRefundResponse.

    Attributes:
        pix_transaction_refund (Refund | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pix_transaction_refund: Refund | None = Field(
        validation_alias="pixTransactionRefund",
        serialization_alias="pixTransactionRefund",
        default=None,
    )


class GetStablecoinQuoteResponseQuote(BaseSchema):
    """Schema generated for GetStablecoinQuoteResponseQuote.

    Attributes:
        base_price (float | None): Exchange rate applied (BRL per stablecoin unit).
        input_amount (float | None): Input amount in BRL (currency unit, not cents).
        input_currency (str | None): Undocumented in the spec.
        output_amount (float | None): Exact stablecoin amount the customer would
            receive.
        output_currency (str | None): Undocumented in the spec.
        applied_fees (list[GetStablecoinQuoteResponseQuoteAppliedFeesItem]):
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
    applied_fees: list[GetStablecoinQuoteResponseQuoteAppliedFeesItem] = Field(
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


class GetStaticQrCodeResponse(BaseSchema):
    """Schema generated for GetStaticQrCodeResponse.

    Attributes:
        pix_qr_code (PixQrCode | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pix_qr_code: PixQrCode | None = Field(
        validation_alias="pixQrCode",
        serialization_alias="pixQrCode",
        default=None,
    )


class GetSubaccountResponse(BaseSchema):
    """Schema generated for GetSubaccountResponse.

    Attributes:
        sub_account (SubAccount | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    sub_account: SubAccount | None = Field(
        validation_alias="SubAccount",
        serialization_alias="SubAccount",
        default=None,
    )


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
        representatives (list[KycOnboardingRepresentative] | None):
            Socios/representantes da empresa
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
    representatives: list[KycOnboardingRepresentative] | None = Field(
        description="Socios/representantes da empresa",
        default=None,
    )


class ListAccountsResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListAccountsResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListAccountsResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListAccountsResponsePageInfoErrorsItemData | None = None


class ListAnticipationRequestsResponse(BaseSchema):
    """Schema generated for ListAnticipationRequestsResponse.

    Attributes:
        anticipations (list[AnticipationRequest]): Undocumented in the spec.
        count (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    anticipations: list[AnticipationRequest] = Field(default_factory=list)
    count: int | None = None


class ListChargeRefundsResponse(BaseSchema):
    """Schema generated for ListChargeRefundsResponse.

    Attributes:
        refunds (list[ChargeRefund]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    refunds: list[ChargeRefund] = Field(default_factory=list)


class ListChargesResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListChargesResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListChargesResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListChargesResponsePageInfoErrorsItemData | None = None


class ListCustomersResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListCustomersResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListCustomersResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListCustomersResponsePageInfoErrorsItemData | None = None


class ListDisputesResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListDisputesResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListDisputesResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListDisputesResponsePageInfoErrorsItemData | None = None


class ListPartnerAffiliatesResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListPartnerAffiliatesResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListPartnerAffiliatesResponsePageInfoErrorsItemData | None): Undocumented
            in the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListPartnerAffiliatesResponsePageInfoErrorsItemData | None = None


class ListPartnerCompaniesResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListPartnerCompaniesResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListPartnerCompaniesResponsePageInfoErrorsItemData | None): Undocumented
            in the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListPartnerCompaniesResponsePageInfoErrorsItemData | None = None


class ListPaymentsResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListPaymentsResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListPaymentsResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListPaymentsResponsePageInfoErrorsItemData | None = None


class ListPixKeyTokenLogsResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListPixKeyTokenLogsResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListPixKeyTokenLogsResponsePageInfoErrorsItemData | None): Undocumented in
            the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListPixKeyTokenLogsResponsePageInfoErrorsItemData | None = None


class ListPspsResponse(BaseSchema):
    """Schema generated for ListPspsResponse.

    Attributes:
        success (bool | None): Undocumented in the spec.
        psps (list[ListPspsResponsePspsItem]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    success: bool | None = Field(examples=[True], default=None)
    psps: list[ListPspsResponsePspsItem] = Field(default_factory=list)


class ListRefundsResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListRefundsResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListRefundsResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListRefundsResponsePageInfoErrorsItemData | None = None


class ListStablecoinSubaccountWalletsResponse(BaseSchema):
    """Schema generated for ListStablecoinSubaccountWalletsResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        sub_account_id (str | None): Undocumented in the spec.
        wallets (list[ListStablecoinSubaccountWalletsResponseWalletsItem]): Undocumented
            in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(examples=["ok"], default=None)
    sub_account_id: str | None = Field(
        validation_alias="subAccountId",
        serialization_alias="subAccountId",
        default=None,
    )
    wallets: list[ListStablecoinSubaccountWalletsResponseWalletsItem] = Field(
        default_factory=list,
    )


class ListStablecoinWalletsResponse(BaseSchema):
    """Schema generated for ListStablecoinWalletsResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        company_bank_account_id (str | None): Undocumented in the spec.
        sub_account_id (str | None): Undocumented in the spec.
        wallets (list[ListStablecoinWalletsResponseWalletsItem]): Undocumented in the
            spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(examples=["ok"], default=None)
    company_bank_account_id: str | None = Field(
        validation_alias="companyBankAccountId",
        serialization_alias="companyBankAccountId",
        default=None,
    )
    sub_account_id: str | None = Field(
        validation_alias="subAccountId",
        serialization_alias="subAccountId",
        default=None,
    )
    wallets: list[ListStablecoinWalletsResponseWalletsItem] = Field(
        default_factory=list,
    )


class ListStaticQrCodesResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListStaticQrCodesResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListStaticQrCodesResponsePageInfoErrorsItemData | None): Undocumented in
            the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListStaticQrCodesResponsePageInfoErrorsItemData | None = None


class ListSubaccountsResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListSubaccountsResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListSubaccountsResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListSubaccountsResponsePageInfoErrorsItemData | None = None


class ListTransactionsResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListTransactionsResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListTransactionsResponsePageInfoErrorsItemData | None): Undocumented in
            the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListTransactionsResponsePageInfoErrorsItemData | None = None


class ListWebhookEventsResponse(BaseSchema):
    """Schema generated for ListWebhookEventsResponse.

    Attributes:
        events (list[ListWebhookEventsResponseEventsItem]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    events: list[ListWebhookEventsResponseEventsItem] = Field(default_factory=list)


class ListWebhookPublicKeysResponse(BaseSchema):
    """Schema generated for ListWebhookPublicKeysResponse.

    Attributes:
        public_keys (list[ListWebhookPublicKeysResponsePublicKeysItem]): Undocumented in
            the spec.
    """

    model_config = ConfigDict(extra="allow")

    public_keys: list[ListWebhookPublicKeysResponsePublicKeysItem] = Field(
        default_factory=list,
    )


class ListWebhooksResponsePageInfoErrorsItem(BaseSchema):
    """Schema generated for ListWebhooksResponsePageInfoErrorsItem.

    Attributes:
        message (str | None): Undocumented in the spec.
        data (ListWebhooksResponsePageInfoErrorsItemData | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str | None = None
    data: ListWebhooksResponsePageInfoErrorsItemData | None = None


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


class PreRegistrationObject(BaseSchema):
    """Schema generated for PreRegistrationObject.

    Attributes:
        name (str): The name of this preregistration. It'll be related as your company
            name too.
        website (str | None): A website that is related to this preregistration.
        tax_id (TaxIdObjectPayload): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

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

    model_config = ConfigDict(populate_by_name=True)

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


class QuoteStablecoinPayoutResponse(BaseSchema):
    """Schema generated for QuoteStablecoinPayoutResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        quote (QuoteStablecoinPayoutResponseQuote | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    status: str | None = Field(examples=["ok"], default=None)
    quote: QuoteStablecoinPayoutResponseQuote | None = None


class RefundChargeResponse(BaseSchema):
    """Schema generated for RefundChargeResponse.

    Attributes:
        refund (ChargeRefund | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    refund: ChargeRefund | None = None


class RejectAnticipationResponse(BaseSchema):
    """Schema generated for RejectAnticipationResponse.

    Attributes:
        anticipation (AnticipationRequest | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    anticipation: AnticipationRequest | None = None


class SetInvoiceIntegrationStatusResponse(BaseSchema):
    """Schema generated for SetInvoiceIntegrationStatusResponse.

    Attributes:
        integration (SetInvoiceIntegrationStatusResponseIntegration | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    integration: SetInvoiceIntegrationStatusResponseIntegration | None = None


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


class TestInvoiceIntegrationResponse(BaseSchema):
    """Schema generated for TestInvoiceIntegrationResponse.

    Attributes:
        invoice (TestInvoiceIntegrationResponseInvoice | None): Undocumented in the
            spec.
        integration (TestInvoiceIntegrationResponseIntegration | None): Undocumented in
            the spec.
    """

    model_config = ConfigDict(extra="allow")

    invoice: TestInvoiceIntegrationResponseInvoice | None = None
    integration: TestInvoiceIntegrationResponseIntegration | None = None


class UploadDisputeEvidenceBody(BaseSchema):
    """Schema generated for UploadDisputeEvidenceBody.

    Attributes:
        documents (list[UploadDisputeEvidenceBodyDocumentsItem] | None): documents for
            upload
    """

    documents: list[UploadDisputeEvidenceBodyDocumentsItem] | None = Field(
        description="documents for upload",
        default=None,
    )


class UploadDisputeEvidenceResponse(BaseSchema):
    """Schema generated for UploadDisputeEvidenceResponse.

    Attributes:
        documents (list[UploadDisputeEvidenceResponseDocumentsItem]): documents for
            upload
    """

    model_config = ConfigDict(extra="allow")

    documents: list[UploadDisputeEvidenceResponseDocumentsItem] = Field(
        description="documents for upload",
        default_factory=list,
    )


class UploadInvoiceIntegrationCertificateResponse(BaseSchema):
    """Schema generated for UploadInvoiceIntegrationCertificateResponse.

    Attributes:
        integration (UploadInvoiceIntegrationCertificateResponseIntegration | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    integration: UploadInvoiceIntegrationCertificateResponseIntegration | None = None


class UpsertInvoiceIntegrationResponseIntegrationMetadata(BaseSchema):
    """Schema generated for UpsertInvoiceIntegrationResponseIntegrationMetadata.

    Attributes:
        nfeio (UpsertInvoiceIntegrationResponseIntegrationMetadataNfei | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    nfeio: UpsertInvoiceIntegrationResponseIntegrationMetadataNfei | None = None


class WebhookAccountRegisterApprovedPayloadAccountRegister(BaseSchema):
    """Schema generated for WebhookAccountRegisterApprovedPayloadAccountRegister.

    Attributes:
        official_name (str | None): Undocumented in the spec.
        tax_id (WebhookAccountRegisterApprovedPayloadAccountRegisterTax | None):
            Undocumented in the spec.
        status (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    official_name: str | None = Field(
        validation_alias="officialName",
        serialization_alias="officialName",
        default=None,
    )
    tax_id: WebhookAccountRegisterApprovedPayloadAccountRegisterTax | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    status: str | None = None


class WebhookAccountRegisterPendingPayloadAccountRegisterRequ(BaseSchema):
    """Schema generated for WebhookAccountRegisterPendingPayloadAccountRegisterRequ.

    Attributes:
        tax_id (WebhookAccountRegisterPendingPayloadAccountRegisterRequ2 | None):
            Undocumented in the spec.
        request_documents (list[str]): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: WebhookAccountRegisterPendingPayloadAccountRegisterRequ2 | None = Field(
        validation_alias="taxId",
        serialization_alias="taxId",
        default=None,
    )
    request_documents: list[str] = Field(
        validation_alias="requestDocuments",
        serialization_alias="requestDocuments",
        default_factory=list,
    )


class WebhookAccountRegisterRejectedPayloadAccountRegister(BaseSchema):
    """Schema generated for WebhookAccountRegisterRejectedPayloadAccountRegister.

    Attributes:
        official_name (str | None): Undocumented in the spec.
        tax_id (WebhookAccountRegisterRejectedPayloadAccountRegisterTax | None):
            Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        rejected_reason (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    official_name: str | None = Field(
        validation_alias="officialName",
        serialization_alias="officialName",
        default=None,
    )
    tax_id: WebhookAccountRegisterRejectedPayloadAccountRegisterTax | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    status: str | None = None
    rejected_reason: str | None = Field(
        validation_alias="rejectedReason",
        serialization_alias="rejectedReason",
        default=None,
    )


class WebhookBoletoSettledPayload(BaseSchema):
    """A boleto was settled by the issuing bank. Emitted by service-boleto, and distinct
    from the charge being paid — settlement is when the funds clear.

    Attributes:
        event (str): Undocumented in the spec.
        charge (WebhookBoletoSettledPayloadCharge | None): Undocumented in the spec.
        boleto (WebhookBoletoSettledPayloadBoleto | None): Undocumented in the spec.
    """

    event: str
    charge: WebhookBoletoSettledPayloadCharge | None = None
    boleto: WebhookBoletoSettledPayloadBoleto | None = None


class WebhookChargeCustomer(BaseSchema):
    """Schema generated for WebhookChargeCustomer.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookChargeCustomerTaxId | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookChargeCustomerTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    email: str | None = None
    phone: str | None = None


class WebhookChargePayer(BaseSchema):
    """Schema generated for WebhookChargePayer.

    Attributes:
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone (str | None): Undocumented in the spec.
        tax_id (WebhookChargePayerTaxId | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    tax_id: WebhookChargePayerTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookCompanyBankAccountBlockedPayloadAccount(BaseSchema):
    """Schema generated for WebhookCompanyBankAccountBlockedPayloadAccount.

    Attributes:
        account_id (str | None): Undocumented in the spec.
        account (str | None): Undocumented in the spec.
        official_name (str | None): Undocumented in the spec.
        trade_name (str | None): Undocumented in the spec.
        tax_id (WebhookCompanyBankAccountBlockedPayloadAccountTaxId | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    account_id: str | None = Field(
        validation_alias="accountId",
        serialization_alias="accountId",
        default=None,
    )
    account: str | None = None
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
    tax_id: WebhookCompanyBankAccountBlockedPayloadAccountTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa3(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa3.

    Attributes:
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone (str | None): Undocumented in the spec.
        tax_id (WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa4 | None):
            Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    tax_id: WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa4 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa5(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa5.

    Attributes:
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone (str | None): Undocumented in the spec.
        tax_id (WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa6 | None):
            Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    tax_id: WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa6 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa9(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa9.

    Attributes:
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone (str | None): Undocumented in the spec.
        tax_id (WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa10 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    tax_id: WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa10 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookOpenpixChargeCompletedPayloadPixCustomer(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedPayloadPixCustomer.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookOpenpixChargeCompletedPayloadPixCustomerTaxId | None):
            Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookOpenpixChargeCompletedPayloadPixCustomerTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookOpenpixChargeCompletedPayloadPixPayer(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedPayloadPixPayer.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookOpenpixChargeCompletedPayloadPixPayerTaxId | None): Undocumented
            in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookOpenpixChargeCompletedPayloadPixPayerTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookOpenpixDisputeAcceptedPayload(BaseSchema):
    """A MED dispute was accepted and the amount is returned to the payer.

    Attributes:
        event (str): Undocumented in the spec.
        dispute (WebhookOpenpixDisputeAcceptedPayloadDispute | None): Undocumented in
            the spec.
    """

    event: str
    dispute: WebhookOpenpixDisputeAcceptedPayloadDispute | None = None


class WebhookOpenpixDisputeCanceledPayload(BaseSchema):
    """A MED dispute was canceled by the reporting institution.

    Attributes:
        event (str): Undocumented in the spec.
        dispute (WebhookOpenpixDisputeCanceledPayloadDispute | None): Undocumented in
            the spec.
    """

    event: str
    dispute: WebhookOpenpixDisputeCanceledPayloadDispute | None = None


class WebhookOpenpixDisputeCreatedPayload(BaseSchema):
    """A MED dispute was opened against a transaction of this account.

    Attributes:
        event (str): Undocumented in the spec.
        dispute (WebhookOpenpixDisputeCreatedPayloadDispute | None): Undocumented in the
            spec.
    """

    event: str
    dispute: WebhookOpenpixDisputeCreatedPayloadDispute | None = None


class WebhookOpenpixDisputeRejectedPayload(BaseSchema):
    """A MED dispute was rejected and the amount stays with this account.

    Attributes:
        event (str): Undocumented in the spec.
        dispute (WebhookOpenpixDisputeRejectedPayloadDispute | None): Undocumented in
            the spec.
    """

    event: str
    dispute: WebhookOpenpixDisputeRejectedPayloadDispute | None = None


class WebhookOpenpixMovementConfirmedPayload(BaseSchema):
    """An outbound payment was confirmed and the Pix left the account.

    Attributes:
        event (str): Undocumented in the spec.
        payment (WebhookOpenpixMovementConfirmedPayloadPayment | None): Undocumented in
            the spec.
        transaction (WebhookOpenpixMovementConfirmedPayloadTransaction | None):
            Undocumented in the spec.
    """

    event: str
    payment: WebhookOpenpixMovementConfirmedPayloadPayment | None = None
    transaction: WebhookOpenpixMovementConfirmedPayloadTransaction | None = None


class WebhookOpenpixMovementFailedPayload(BaseSchema):
    """An outbound payment was approved but failed on the way out. `error` carries the
    reason.

    Attributes:
        event (str): Undocumented in the spec.
        payment (WebhookOpenpixMovementFailedPayloadPayment | None): Undocumented in the
            spec.
        transaction (WebhookOpenpixMovementFailedPayloadTransaction | None):
            Undocumented in the spec.
        error (WebhookOpenpixMovementFailedPayloadError | None): Undocumented in the
            spec.
    """

    event: str
    payment: WebhookOpenpixMovementFailedPayloadPayment | None = None
    transaction: WebhookOpenpixMovementFailedPayloadTransaction | None = None
    error: WebhookOpenpixMovementFailedPayloadError | None = None


class WebhookOpenpixMovementRemovedPayload(BaseSchema):
    """An outbound payment was removed before being approved.

    Attributes:
        event (str): Undocumented in the spec.
        payment (WebhookOpenpixMovementRemovedPayloadPayment | None): Undocumented in
            the spec.
    """

    event: str
    payment: WebhookOpenpixMovementRemovedPayloadPayment | None = None


class WebhookOpenpixTransactionReceivedPayloadPixCreditPartyH(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixCreditPartyH.

    Attributes:
        tax_id (WebhookOpenpixTransactionReceivedPayloadPixCreditPartyH2 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: WebhookOpenpixTransactionReceivedPayloadPixCreditPartyH2 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookOpenpixTransactionReceivedPayloadPixDebitPartyHo(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixDebitPartyHo.

    Attributes:
        tax_id (WebhookOpenpixTransactionReceivedPayloadPixDebitPartyHo2 | None):
            Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: WebhookOpenpixTransactionReceivedPayloadPixDebitPartyHo2 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    name: str | None = None


class WebhookOpenpixTransactionReceivedPayloadPixPayer(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixPayer.

    Attributes:
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone (str | None): Undocumented in the spec.
        tax_id (WebhookOpenpixTransactionReceivedPayloadPixPayerTaxId | None):
            Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    tax_id: WebhookOpenpixTransactionReceivedPayloadPixPayerTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookPixAutomaticApprovedPayloadCustomerAddress(BaseSchema):
    """Schema generated for WebhookPixAutomaticApprovedPayloadCustomerAddress.

    Attributes:
        zipcode (str | None): Undocumented in the spec.
        street (str | None): Undocumented in the spec.
        number (str | None): Undocumented in the spec.
        neighborhood (str | None): Undocumented in the spec.
        city (str | None): Undocumented in the spec.
        state (str | None): Undocumented in the spec.
        complement (str | None): Undocumented in the spec.
        country (str | None): Undocumented in the spec.
        location (WebhookPixAutomaticApprovedPayloadCustomerAddressLocati | None):
            Undocumented in the spec.
        id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    zipcode: str | None = None
    street: str | None = None
    number: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    complement: str | None = None
    country: str | None = None
    location: WebhookPixAutomaticApprovedPayloadCustomerAddressLocati | None = None
    id: str | None = Field(
        validation_alias="_id",
        serialization_alias="_id",
        default=None,
    )


class WebhookPixAutomaticCobrApprovedPayloadCobr(BaseSchema):
    """Schema generated for WebhookPixAutomaticCobrApprovedPayloadCobr.

    Attributes:
        identifier_id (str | None): Undocumented in the spec.
        recurrency_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        tries (list[WebhookPixAutomaticCobrApprovedPayloadCobrTriesItem]): Undocumented
            in the spec.
        value (int | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

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
    status: str | None = None
    tries: list[WebhookPixAutomaticCobrApprovedPayloadCobrTriesItem] = Field(
        default_factory=list,
    )
    value: int | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )


class WebhookPixAutomaticCobrCompletedPayloadCobr(BaseSchema):
    """Schema generated for WebhookPixAutomaticCobrCompletedPayloadCobr.

    Attributes:
        identifier_id (str | None): Undocumented in the spec.
        recurrency_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        tries (list[WebhookPixAutomaticCobrCompletedPayloadCobrTriesItem]): Undocumented
            in the spec.
        value (int | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

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
    status: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    tries: list[WebhookPixAutomaticCobrCompletedPayloadCobrTriesItem] = Field(
        default_factory=list,
    )
    value: int | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )


class WebhookPixAutomaticCobrCreatedPayloadCobr(BaseSchema):
    """Schema generated for WebhookPixAutomaticCobrCreatedPayloadCobr.

    Attributes:
        identifier_id (str | None): Undocumented in the spec.
        recurrency_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        tries (list[WebhookPixAutomaticCobrCreatedPayloadCobrTriesItem]): Undocumented
            in the spec.
        value (int | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

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
    status: str | None = None
    tries: list[WebhookPixAutomaticCobrCreatedPayloadCobrTriesItem] = Field(
        default_factory=list,
    )
    value: int | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )


class WebhookPixAutomaticCobrRejectedPayloadCobr(BaseSchema):
    """Schema generated for WebhookPixAutomaticCobrRejectedPayloadCobr.

    Attributes:
        identifier_id (str | None): Undocumented in the spec.
        recurrency_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        tries (list[WebhookPixAutomaticCobrRejectedPayloadCobrTriesItem]): Undocumented
            in the spec.
        reject_code (str | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

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
    status: str | None = None
    tries: list[WebhookPixAutomaticCobrRejectedPayloadCobrTriesItem] = Field(
        default_factory=list,
    )
    reject_code: str | None = Field(
        validation_alias="rejectCode",
        serialization_alias="rejectCode",
        default=None,
    )
    value: int | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )


class WebhookPixAutomaticCobrTryRejectedPayloadCobr(BaseSchema):
    """Schema generated for WebhookPixAutomaticCobrTryRejectedPayloadCobr.

    Attributes:
        identifier_id (str | None): Undocumented in the spec.
        recurrency_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        tries (list[WebhookPixAutomaticCobrTryRejectedPayloadCobrTriesItem]):
            Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

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
    status: str | None = None
    tries: list[WebhookPixAutomaticCobrTryRejectedPayloadCobrTriesItem] = Field(
        default_factory=list,
    )
    value: int | None = None
    description: str | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )


class WebhookPixAutomaticCobrTryRequestedPayloadCobr(BaseSchema):
    """Schema generated for WebhookPixAutomaticCobrTryRequestedPayloadCobr.

    Attributes:
        identifier_id (str | None): Undocumented in the spec.
        recurrency_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        tries (list[WebhookPixAutomaticCobrTryRequestedPayloadCobrTriesItem]):
            Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

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
    status: str | None = None
    tries: list[WebhookPixAutomaticCobrTryRequestedPayloadCobrTriesItem] = Field(
        default_factory=list,
    )
    value: int | None = None
    description: str | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )


class WebhookPixAutomaticRejectedPayloadCustomerAddress(BaseSchema):
    """Schema generated for WebhookPixAutomaticRejectedPayloadCustomerAddress.

    Attributes:
        zipcode (str | None): Undocumented in the spec.
        street (str | None): Undocumented in the spec.
        number (str | None): Undocumented in the spec.
        neighborhood (str | None): Undocumented in the spec.
        city (str | None): Undocumented in the spec.
        state (str | None): Undocumented in the spec.
        complement (str | None): Undocumented in the spec.
        country (str | None): Undocumented in the spec.
        location (WebhookPixAutomaticRejectedPayloadCustomerAddressLocati | None):
            Undocumented in the spec.
        id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    zipcode: str | None = None
    street: str | None = None
    number: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    complement: str | None = None
    country: str | None = None
    location: WebhookPixAutomaticRejectedPayloadCustomerAddressLocati | None = None
    id: str | None = Field(
        validation_alias="_id",
        serialization_alias="_id",
        default=None,
    )


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig13(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig13.

    Attributes:
        tax_id (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig14 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig14 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig2(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig2.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig3 | None):
            Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig3 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig7(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig7.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig8 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig8 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedConfirmedPayloadRefu11(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadRefu11.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookPixTransactionRefundReceivedConfirmedPayloadRefu12 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookPixTransactionRefundReceivedConfirmedPayloadRefu12 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedConfirmedPayloadRefu6(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadRefu6.

    Attributes:
        tax_id (WebhookPixTransactionRefundReceivedConfirmedPayloadRefu7 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: WebhookPixTransactionRefundReceivedConfirmedPayloadRefu7 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedRejectedPayloadOrigi11(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadOrigi11.

    Attributes:
        name (str | None): Undocumented in the spec.
        name_friendly (str | None): Undocumented in the spec.
        tax_id (WebhookPixTransactionRefundReceivedRejectedPayloadOrigi12 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    name_friendly: str | None = Field(
        validation_alias="nameFriendly",
        serialization_alias="nameFriendly",
        default=None,
    )
    tax_id: WebhookPixTransactionRefundReceivedRejectedPayloadOrigi12 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedRejectedPayloadOrigi5(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadOrigi5.

    Attributes:
        name (str | None): Undocumented in the spec.
        name_friendly (str | None): Undocumented in the spec.
        tax_id (WebhookPixTransactionRefundReceivedRejectedPayloadOrigi6 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    name_friendly: str | None = Field(
        validation_alias="nameFriendly",
        serialization_alias="nameFriendly",
        default=None,
    )
    tax_id: WebhookPixTransactionRefundReceivedRejectedPayloadOrigi6 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedRejectedPayloadRefun11(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadRefun11.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookPixTransactionRefundReceivedRejectedPayloadRefun12 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookPixTransactionRefundReceivedRejectedPayloadRefun12 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedRejectedPayloadRefun6(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadRefun6.

    Attributes:
        tax_id (WebhookPixTransactionRefundReceivedRejectedPayloadRefun7 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: WebhookPixTransactionRefundReceivedRejectedPayloadRefun7 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal13(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal13.

    Attributes:
        tax_id (WebhookPixTransactionRefundSentConfirmedPayloadOriginal14 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: WebhookPixTransactionRefundSentConfirmedPayloadOriginal14 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal2(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal2.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookPixTransactionRefundSentConfirmedPayloadOriginal3 | None):
            Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookPixTransactionRefundSentConfirmedPayloadOriginal3 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal7(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal7.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookPixTransactionRefundSentConfirmedPayloadOriginal8 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookPixTransactionRefundSentConfirmedPayloadOriginal8 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundSentConfirmedPayloadRefundTr11(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadRefundTr11.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookPixTransactionRefundSentConfirmedPayloadRefundTr12 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookPixTransactionRefundSentConfirmedPayloadRefundTr12 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundSentConfirmedPayloadRefundTr6(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadRefundTr6.

    Attributes:
        tax_id (WebhookPixTransactionRefundSentConfirmedPayloadRefundTr7 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: WebhookPixTransactionRefundSentConfirmedPayloadRefundTr7 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT13(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT13.

    Attributes:
        tax_id (WebhookPixTransactionRefundSentRejectedPayloadOriginalT14 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: WebhookPixTransactionRefundSentRejectedPayloadOriginalT14 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT2(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT2.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookPixTransactionRefundSentRejectedPayloadOriginalT3 | None):
            Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookPixTransactionRefundSentRejectedPayloadOriginalT3 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT7(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT7.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookPixTransactionRefundSentRejectedPayloadOriginalT8 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookPixTransactionRefundSentRejectedPayloadOriginalT8 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundSentRejectedPayloadRefundTra11(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadRefundTra11.

    Attributes:
        name (str | None): Undocumented in the spec.
        tax_id (WebhookPixTransactionRefundSentRejectedPayloadRefundTra12 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    tax_id: WebhookPixTransactionRefundSentRejectedPayloadRefundTra12 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookPixTransactionRefundSentRejectedPayloadRefundTra6(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadRefundTra6.

    Attributes:
        tax_id (WebhookPixTransactionRefundSentRejectedPayloadRefundTra7 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    tax_id: WebhookPixTransactionRefundSentRejectedPayloadRefundTra7 | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )


class WebhookStablecoinDepositCompletedPayload(BaseSchema):
    """A stablecoin deposit settled. Emitted by woovi-stablecoin.

    Attributes:
        event (str): Undocumented in the spec.
        stable_deposit (WebhookStablecoinDepositCompletedPayloadStableDeposit | None):
            Undocumented in the spec.
        company (WebhookStablecoinDepositCompletedPayloadCompany | None): Public company
            info.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    stable_deposit: WebhookStablecoinDepositCompletedPayloadStableDeposit | None = (
        Field(
            validation_alias="stableDeposit",
            serialization_alias="stableDeposit",
            default=None,
        )
    )
    company: WebhookStablecoinDepositCompletedPayloadCompany | None = Field(
        description="Public company info.",
        default=None,
    )


class WebhookStablecoinDepositFailedPayload(BaseSchema):
    """A stablecoin deposit did not settle. `reason` always comes; `errorCode` only when
    the provider gave one.

    Attributes:
        event (str): Undocumented in the spec.
        stable_deposit (WebhookStablecoinDepositFailedPayloadStableDeposit | None):
            Undocumented in the spec.
        company (WebhookStablecoinDepositFailedPayloadCompany | None): Public company
            info.
        reason (str | None): Undocumented in the spec.
        error_code (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    stable_deposit: WebhookStablecoinDepositFailedPayloadStableDeposit | None = Field(
        validation_alias="stableDeposit",
        serialization_alias="stableDeposit",
        default=None,
    )
    company: WebhookStablecoinDepositFailedPayloadCompany | None = Field(
        description="Public company info.",
        default=None,
    )
    reason: str | None = None
    error_code: str | None = Field(
        validation_alias="errorCode",
        serialization_alias="errorCode",
        default=None,
    )


class WebhookStablecoinPayoutCompletedPayload(BaseSchema):
    """A stablecoin payout was paid out over Pix. Emitted by woovi-stablecoin.

    Attributes:
        event (str): Undocumented in the spec.
        stable_payout (WebhookStablecoinPayoutCompletedPayloadStablePayout | None):
            Undocumented in the spec.
        company (WebhookStablecoinPayoutCompletedPayloadCompany | None): Public company
            info.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    stable_payout: WebhookStablecoinPayoutCompletedPayloadStablePayout | None = Field(
        validation_alias="stablePayout",
        serialization_alias="stablePayout",
        default=None,
    )
    company: WebhookStablecoinPayoutCompletedPayloadCompany | None = Field(
        description="Public company info.",
        default=None,
    )


class WebhookStablecoinPayoutFailedPayload(BaseSchema):
    """A stablecoin payout did not go out.

    Attributes:
        event (str): Undocumented in the spec.
        stable_payout (WebhookStablecoinPayoutFailedPayloadStablePayout | None):
            Undocumented in the spec.
        company (WebhookStablecoinPayoutFailedPayloadCompany | None): Public company
            info.
        reason (str | None): Undocumented in the spec.
        error_code (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    stable_payout: WebhookStablecoinPayoutFailedPayloadStablePayout | None = Field(
        validation_alias="stablePayout",
        serialization_alias="stablePayout",
        default=None,
    )
    company: WebhookStablecoinPayoutFailedPayloadCompany | None = Field(
        description="Public company info.",
        default=None,
    )
    reason: str | None = None
    error_code: str | None = Field(
        validation_alias="errorCode",
        serialization_alias="errorCode",
        default=None,
    )


class WebhookStablecoinPayoutRefundConfirmedPayload(BaseSchema):
    """A settled stablecoin payout came back. `stablePayout` repeats the payout as it
    was delivered on `STABLECOIN_PAYOUT_COMPLETED` — `status` stays `COMPLETED` — and
    everything new lives under `refund`.

    Attributes:
        event (str): Undocumented in the spec.
        stable_payout (WebhookStablecoinPayoutRefundConfirmedPayloadStablePayo | None):
            Undocumented in the spec.
        company (WebhookStablecoinPayoutRefundConfirmedPayloadCompany | None): Public
            company info.
        refund (WebhookStablecoinPayoutRefundConfirmedPayloadRefund | None): The
            returned money, as reported by the provider.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    stable_payout: WebhookStablecoinPayoutRefundConfirmedPayloadStablePayo | None = (
        Field(
            validation_alias="stablePayout",
            serialization_alias="stablePayout",
            default=None,
        )
    )
    company: WebhookStablecoinPayoutRefundConfirmedPayloadCompany | None = Field(
        description="Public company info.",
        default=None,
    )
    refund: WebhookStablecoinPayoutRefundConfirmedPayloadRefund | None = Field(
        description="The returned money, as reported by the provider.",
        default=None,
    )


class WebhookStablecoinPayoutRefundFailedPayload(BaseSchema):
    """A settled stablecoin payout came back. `stablePayout` repeats the payout as it
    was delivered on `STABLECOIN_PAYOUT_COMPLETED` — `status` stays `COMPLETED` — and
    everything new lives under `refund`.

    Attributes:
        event (str): Undocumented in the spec.
        stable_payout (WebhookStablecoinPayoutRefundFailedPayloadStablePayout | None):
            Undocumented in the spec.
        company (WebhookStablecoinPayoutRefundFailedPayloadCompany | None): Public
            company info.
        refund (WebhookStablecoinPayoutRefundFailedPayloadRefund | None): The returned
            money, as reported by the provider.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    stable_payout: WebhookStablecoinPayoutRefundFailedPayloadStablePayout | None = (
        Field(
            validation_alias="stablePayout",
            serialization_alias="stablePayout",
            default=None,
        )
    )
    company: WebhookStablecoinPayoutRefundFailedPayloadCompany | None = Field(
        description="Public company info.",
        default=None,
    )
    refund: WebhookStablecoinPayoutRefundFailedPayloadRefund | None = Field(
        description="The returned money, as reported by the provider.",
        default=None,
    )


class WebhookStablecoinSubaccountConfirmedPayload(BaseSchema):
    """A stablecoin sub-account cleared onboarding and can transact.

    Attributes:
        event (str): Undocumented in the spec.
        stable_sub_account (WebhookStablecoinSubaccountConfirmedPayloadStableSubAcc |
            None): Undocumented in the spec.
        company (WebhookStablecoinSubaccountConfirmedPayloadCompany | None): Public
            company info.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    stable_sub_account: (
        WebhookStablecoinSubaccountConfirmedPayloadStableSubAcc | None
    ) = Field(
        validation_alias="stableSubAccount",
        serialization_alias="stableSubAccount",
        default=None,
    )
    company: WebhookStablecoinSubaccountConfirmedPayloadCompany | None = Field(
        description="Public company info.",
        default=None,
    )


class WebhookStablecoinSubaccountRejectedPayload(BaseSchema):
    """A stablecoin sub-account was rejected. `rejectionLabels` carries the compliance
    labels when there are any.

    Attributes:
        event (str): Undocumented in the spec.
        stable_sub_account (WebhookStablecoinSubaccountRejectedPayloadStableSubAcco |
            None): Undocumented in the spec.
        company (WebhookStablecoinSubaccountRejectedPayloadCompany | None): Public
            company info.
        reason (str | None): Undocumented in the spec.
        rejection_labels (list[str]): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    stable_sub_account: (
        WebhookStablecoinSubaccountRejectedPayloadStableSubAcco | None
    ) = Field(
        validation_alias="stableSubAccount",
        serialization_alias="stableSubAccount",
        default=None,
    )
    company: WebhookStablecoinSubaccountRejectedPayloadCompany | None = Field(
        description="Public company info.",
        default=None,
    )
    reason: str | None = None
    rejection_labels: list[str] = Field(
        validation_alias="rejectionLabels",
        serialization_alias="rejectionLabels",
        default_factory=list,
    )


class WithdrawFromSubaccountResponseWithdraw(BaseSchema):
    """Schema generated for WithdrawFromSubaccountResponseWithdraw.

    Attributes:
        account (Transaction2 | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    account: Transaction2 | None = None


class ActivateAnticipationBeneficiaryResponse(BaseSchema):
    """Schema generated for ActivateAnticipationBeneficiaryResponse.

    Attributes:
        beneficiary (AnticipationBeneficiary | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    beneficiary: AnticipationBeneficiary | None = None


class BoletoTransactionListResponse(BaseSchema):
    """Schema generated for BoletoTransactionListResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        page_info (BoletoTransactionListResponsePageInfo | None): Undocumented in the
            spec.
        boleto_transactions (list[BoletoTransaction]): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = Field(examples=["OK"], default=None)
    page_info: BoletoTransactionListResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )
    boleto_transactions: list[BoletoTransaction] = Field(
        validation_alias="boletoTransactions",
        serialization_alias="boletoTransactions",
        default_factory=list,
    )


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
        additional_info (list[ChargePayloadAdditionalInfoItem] | None): Additional info
            of the charge
        enable_cashback_percentage (bool | None): true to enable cashback and false to
            disable.
        enable_cashback_exclusive_percentage (bool | None): true to enable fidelity
            cashback and false to disable.
        subaccount (str | None): Pix key of the subaccount to receive the charge
        splits (list[ChargePayloadSplitsItem] | None): This is the array that will
            configure how will be splitted the value of the charge
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
    additional_info: list[ChargePayloadAdditionalInfoItem] | None = Field(
        validation_alias="additionalInfo",
        serialization_alias="additionalInfo",
        description="Additional info of the charge",
        default=None,
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
    splits: list[ChargePayloadSplitsItem] | None = Field(
        description=(
            "This is the array that will configure how will be splitted the value of "
            "the charge"
        ),
        default=None,
    )


class ChargePaymentMethods(BaseSchema):
    """Schema generated for ChargePaymentMethods.

    Attributes:
        pix (ChargePaymentMethodsPix | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    pix: ChargePaymentMethodsPix | None = None


class CreateAnticipationBeneficiaryResponse(BaseSchema):
    """Schema generated for CreateAnticipationBeneficiaryResponse.

    Attributes:
        beneficiary (AnticipationBeneficiary | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    beneficiary: AnticipationBeneficiary | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class CreateCustomerResponse(BaseSchema):
    """Schema generated for CreateCustomerResponse.

    Attributes:
        customer (Customer | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    customer: Customer | None = None


class CreateInvoiceResponse(BaseSchema):
    """Schema generated for CreateInvoiceResponse.

    Attributes:
        invoice (CreateInvoiceResponseInvoice | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    invoice: CreateInvoiceResponseInvoice | None = None


class CreatePaymentBodyManual(BaseSchema):
    """Manual.

    Attributes:
        type (PaymentCreatePayloadPixKeyType): type of the payment
        value (int): value of the requested payment in cents
        correlation_id (str): a unique identifier for your payment
        pix_key_end_to_end_id (str | None): the end to end id of the pix key used for
            track pix key consultations
        psp (str): the PSP (Payment Service Provider) identifier
        holder (CreatePaymentBodyManualHolder): Undocumented in the spec.
        account (CreatePaymentBodyManualAccount): Undocumented in the spec.
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
    holder: CreatePaymentBodyManualHolder
    account: CreatePaymentBodyManualAccount
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


class DeactivateAnticipationBeneficiaryResponse(BaseSchema):
    """Schema generated for DeactivateAnticipationBeneficiaryResponse.

    Attributes:
        beneficiary (AnticipationBeneficiary | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    beneficiary: AnticipationBeneficiary | None = None


class DecodeEmvResponseCobLocation(BaseSchema):
    """Resolved COB (charge) location details when the EMV points to a COB endpoint.

    Attributes:
        is_valid (bool | None): Undocumented in the spec.
        location_errors (list[str]): Undocumented in the spec.
        payload (DecodeEmvResponseCobLocationPayload | None): Undocumented in the spec.
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
    payload: DecodeEmvResponseCobLocationPayload | None = None
    url: str | None = None


class DecodeEmvResponseRecLocationPayload(BaseSchema):
    """Schema generated for DecodeEmvResponseRecLocationPayload.

    Attributes:
        updates (list[DecodeEmvResponseRecLocationPayloadUpdatesItem]): Undocumented in
            the spec.
        calendar (DecodeEmvResponseRecLocationPayloadCalendar | None): Undocumented in
            the spec.
        id_rec (str | None): Undocumented in the spec.
        retry_policy (str | None): Undocumented in the spec.
        receiver (DecodeEmvResponseRecLocationPayloadReceiver | None): Undocumented in
            the spec.
        value (DecodeEmvResponseRecLocationPayloadValue | None): Undocumented in the
            spec.
        link (DecodeEmvResponseRecLocationPayloadLink | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    updates: list[DecodeEmvResponseRecLocationPayloadUpdatesItem] = Field(
        default_factory=list,
    )
    calendar: DecodeEmvResponseRecLocationPayloadCalendar | None = None
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
    receiver: DecodeEmvResponseRecLocationPayloadReceiver | None = None
    value: DecodeEmvResponseRecLocationPayloadValue | None = None
    link: DecodeEmvResponseRecLocationPayloadLink | None = None


class DuplicateAccountResponse(BaseSchema):
    """Schema generated for DuplicateAccountResponse.

    Attributes:
        account (CompanyBankAccount | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    account: CompanyBankAccount | None = None


class GetAccountResponse(BaseSchema):
    """Schema generated for GetAccountResponse.

    Attributes:
        account (CompanyBankAccount | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    account: CompanyBankAccount | None = None


class GetBoletoTransactionResponse(BaseSchema):
    """Schema generated for GetBoletoTransactionResponse.

    Attributes:
        boleto_transaction (BoletoTransaction | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    boleto_transaction: BoletoTransaction | None = Field(
        validation_alias="boletoTransaction",
        serialization_alias="boletoTransaction",
        default=None,
    )


class GetCustomerResponse(BaseSchema):
    """Schema generated for GetCustomerResponse.

    Attributes:
        customer (Customer | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    customer: Customer | None = None


class GetPartnerCompanyResponsePreRegistration(BaseSchema):
    """Schema generated for GetPartnerCompanyResponsePreRegistration.

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


class GetStablecoinQuoteResponse(BaseSchema):
    """Schema generated for GetStablecoinQuoteResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        quote (GetStablecoinQuoteResponseQuote | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    status: str | None = Field(examples=["ok"], default=None)
    quote: GetStablecoinQuoteResponseQuote | None = None


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


class ListAccountsResponsePageInfo(BaseSchema):
    """Schema generated for ListAccountsResponsePageInfo.

    Attributes:
        errors (list[ListAccountsResponsePageInfoErrorsItem]): Undocumented in the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[ListAccountsResponsePageInfoErrorsItem] = Field(default_factory=list)
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


class ListChargesResponsePageInfo(BaseSchema):
    """Schema generated for ListChargesResponsePageInfo.

    Attributes:
        errors (list[ListChargesResponsePageInfoErrorsItem]): Undocumented in the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[ListChargesResponsePageInfoErrorsItem] = Field(default_factory=list)
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


class ListCustomersResponsePageInfo(BaseSchema):
    """Schema generated for ListCustomersResponsePageInfo.

    Attributes:
        errors (list[ListCustomersResponsePageInfoErrorsItem]): Undocumented in the
            spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[ListCustomersResponsePageInfoErrorsItem] = Field(default_factory=list)
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


class ListDisputesResponsePageInfo(BaseSchema):
    """Schema generated for ListDisputesResponsePageInfo.

    Attributes:
        errors (list[ListDisputesResponsePageInfoErrorsItem]): Undocumented in the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[ListDisputesResponsePageInfoErrorsItem] = Field(default_factory=list)
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


class ListPartnerAffiliatesResponseAffiliatesItem(BaseSchema):
    """Schema generated for ListPartnerAffiliatesResponseAffiliatesItem.

    Attributes:
        company (CompanyObjectPayload): Undocumented in the spec.
        account (AccountObjectPayload | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    company: CompanyObjectPayload
    account: AccountObjectPayload | None = None


class ListPartnerAffiliatesResponsePageInfo(BaseSchema):
    """Schema generated for ListPartnerAffiliatesResponsePageInfo.

    Attributes:
        errors (list[ListPartnerAffiliatesResponsePageInfoErrorsItem]): Undocumented in
            the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[ListPartnerAffiliatesResponsePageInfoErrorsItem] = Field(
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


class ListPartnerCompaniesResponsePageInfo(BaseSchema):
    """Schema generated for ListPartnerCompaniesResponsePageInfo.

    Attributes:
        errors (list[ListPartnerCompaniesResponsePageInfoErrorsItem]): Undocumented in
            the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[ListPartnerCompaniesResponsePageInfoErrorsItem] = Field(
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


class ListPartnerCompaniesResponsePreRegistrationsItem(BaseSchema):
    """Schema generated for ListPartnerCompaniesResponsePreRegistrationsItem.

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


class ListPaymentsResponsePageInfo(BaseSchema):
    """Schema generated for ListPaymentsResponsePageInfo.

    Attributes:
        errors (list[ListPaymentsResponsePageInfoErrorsItem]): Undocumented in the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[ListPaymentsResponsePageInfoErrorsItem] = Field(default_factory=list)
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


class ListPixKeyTokenLogsResponsePageInfo(BaseSchema):
    """Schema generated for ListPixKeyTokenLogsResponsePageInfo.

    Attributes:
        errors (list[ListPixKeyTokenLogsResponsePageInfoErrorsItem]): Undocumented in
            the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[ListPixKeyTokenLogsResponsePageInfoErrorsItem] = Field(
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


class ListPixKeysResponse(BaseSchema):
    """Schema generated for ListPixKeysResponse.

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


class ListRefundsResponsePageInfo(BaseSchema):
    """Schema generated for ListRefundsResponsePageInfo.

    Attributes:
        errors (list[ListRefundsResponsePageInfoErrorsItem]): Undocumented in the spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[ListRefundsResponsePageInfoErrorsItem] = Field(default_factory=list)
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


class ListStaticQrCodesResponsePageInfo(BaseSchema):
    """Schema generated for ListStaticQrCodesResponsePageInfo.

    Attributes:
        errors (list[ListStaticQrCodesResponsePageInfoErrorsItem]): Undocumented in the
            spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[ListStaticQrCodesResponsePageInfoErrorsItem] = Field(
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


class ListSubaccountsResponsePageInfo(BaseSchema):
    """Schema generated for ListSubaccountsResponsePageInfo.

    Attributes:
        errors (list[ListSubaccountsResponsePageInfoErrorsItem]): Undocumented in the
            spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[ListSubaccountsResponsePageInfoErrorsItem] = Field(
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


class ListTransactionsResponsePageInfo(BaseSchema):
    """Schema generated for ListTransactionsResponsePageInfo.

    Attributes:
        errors (list[ListTransactionsResponsePageInfoErrorsItem]): Undocumented in the
            spec.
        skip (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        has_previous_page (bool | None): Undocumented in the spec.
        has_next_page (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    errors: list[ListTransactionsResponsePageInfoErrorsItem] = Field(
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


class ListWebhooksResponsePageInfo(BaseSchema):
    """Schema generated for ListWebhooksResponsePageInfo.

    Attributes:
        errors (list[ListWebhooksResponsePageInfoErrorsItem]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    errors: list[ListWebhooksResponsePageInfoErrorsItem] = Field(default_factory=list)


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


class PreRegistrationPayloadObject(BaseSchema):
    """Schema generated for PreRegistrationPayloadObject.

    Attributes:
        pre_registration (PreRegistrationObject | None): Undocumented in the spec.
        user (PreRegistrationUserObject | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

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
        installments_count (float | None): Total number of installments currently linked
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
    installments_count: float | None = Field(
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
        additional_info (list[SubscriptionPayloadAdditionalInfoItem] | None):
            Undocumented in the spec.
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
    additional_info: list[SubscriptionPayloadAdditionalInfoItem] | None = Field(
        validation_alias="additionalInfo",
        serialization_alias="additionalInfo",
        default=None,
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


class UpdateCustomerResponse(BaseSchema):
    """Schema generated for UpdateCustomerResponse.

    Attributes:
        customer (Customer | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    customer: Customer | None = None


class UpsertInvoiceIntegrationResponseIntegration(BaseSchema):
    """Schema generated for UpsertInvoiceIntegrationResponseIntegration.

    Attributes:
        id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        is_active (bool | None): Undocumented in the spec.
        metadata (UpsertInvoiceIntegrationResponseIntegrationMetadata | None):
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
    metadata: UpsertInvoiceIntegrationResponseIntegrationMetadata | None = None


class WebhookAccountRegisterApprovedPayload(BaseSchema):
    """A sub-account register was approved by compliance and the account can transact.

    Attributes:
        event (str): Undocumented in the spec.
        account_register (WebhookAccountRegisterApprovedPayloadAccountRegister | None):
            Undocumented in the spec.
        account (WebhookAccountRegisterApprovedPayloadAccount | None): Undocumented in
            the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    account_register: WebhookAccountRegisterApprovedPayloadAccountRegister | None = (
        Field(
            validation_alias="accountRegister",
            serialization_alias="accountRegister",
            default=None,
        )
    )
    account: WebhookAccountRegisterApprovedPayloadAccount | None = None


class WebhookAccountRegisterPendingPayloadAccountRegister(BaseSchema):
    """Schema generated for WebhookAccountRegisterPendingPayloadAccountRegister.

    Attributes:
        official_name (str | None): Undocumented in the spec.
        tax_id (WebhookAccountRegisterPendingPayloadAccountRegisterTaxI | None):
            Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        request_documents (list[str]): Undocumented in the spec.
        request_reason (str | None): Undocumented in the spec.
        request_documents_representatives
            (list[WebhookAccountRegisterPendingPayloadAccountRegisterRequ]):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    official_name: str | None = Field(
        validation_alias="officialName",
        serialization_alias="officialName",
        default=None,
    )
    tax_id: WebhookAccountRegisterPendingPayloadAccountRegisterTaxI | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    status: str | None = None
    request_documents: list[str] = Field(
        validation_alias="requestDocuments",
        serialization_alias="requestDocuments",
        default_factory=list,
    )
    request_reason: str | None = Field(
        validation_alias="requestReason",
        serialization_alias="requestReason",
        default=None,
    )
    request_documents_representatives: list[
        WebhookAccountRegisterPendingPayloadAccountRegisterRequ
    ] = Field(
        validation_alias="requestDocumentsRepresentatives",
        serialization_alias="requestDocumentsRepresentatives",
        default_factory=list,
    )


class WebhookAccountRegisterRejectedPayload(BaseSchema):
    """A sub-account register was rejected by compliance.

    Attributes:
        event (str): Undocumented in the spec.
        account_register (WebhookAccountRegisterRejectedPayloadAccountRegister | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    account_register: WebhookAccountRegisterRejectedPayloadAccountRegister | None = (
        Field(
            validation_alias="accountRegister",
            serialization_alias="accountRegister",
            default=None,
        )
    )


class WebhookCharge(BaseSchema):
    """The charge the event refers to. Superset of the fields observed across the charge
    and transaction events; a given event carries the subset that applies to it.

    Attributes:
        value (int | None): Undocumented in the spec.
        comment (str | None): Undocumented in the spec.
        identifier (str | None): Undocumented in the spec.
        transaction_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        additional_info (list[WebhookChargeAdditionalInfoItem]): Additional info of the
            charge
        fee (int | None): Undocumented in the spec.
        discount (int | None): Undocumented in the spec.
        value_with_discount (int | None): Undocumented in the spec.
        expires_date (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        payment_link_id (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        updated_at (str | None): Undocumented in the spec.
        customer (WebhookChargeCustomer | None): Undocumented in the spec.
        paid_at (str | None): Undocumented in the spec.
        payer (WebhookChargePayer | None): Undocumented in the spec.
        ensure_same_tax_id (bool | None): Undocumented in the spec.
        br_code (str | None): Undocumented in the spec.
        expires_in (int | None): Undocumented in the spec.
        pix_key (str | None): Undocumented in the spec.
        payment_link_url (str | None): Undocumented in the spec.
        qr_code_image (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
        giftback_applied_value (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int | None = None
    comment: str | None = None
    identifier: str | None = None
    transaction_id: str | None = Field(
        validation_alias="transactionID",
        serialization_alias="transactionID",
        default=None,
    )
    status: str | None = None
    additional_info: list[WebhookChargeAdditionalInfoItem] = Field(
        validation_alias="additionalInfo",
        serialization_alias="additionalInfo",
        description="Additional info of the charge",
        default_factory=list,
    )
    fee: int | None = None
    discount: int | None = None
    value_with_discount: int | None = Field(
        validation_alias="valueWithDiscount",
        serialization_alias="valueWithDiscount",
        default=None,
    )
    expires_date: str | None = Field(
        validation_alias="expiresDate",
        serialization_alias="expiresDate",
        default=None,
    )
    type: str | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    payment_link_id: str | None = Field(
        validation_alias="paymentLinkID",
        serialization_alias="paymentLinkID",
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
    customer: WebhookChargeCustomer | None = None
    paid_at: str | None = Field(
        validation_alias="paidAt",
        serialization_alias="paidAt",
        default=None,
    )
    payer: WebhookChargePayer | None = None
    ensure_same_tax_id: bool | None = Field(
        validation_alias="ensureSameTaxID",
        serialization_alias="ensureSameTaxID",
        default=None,
    )
    br_code: str | None = Field(
        validation_alias="brCode",
        serialization_alias="brCode",
        default=None,
    )
    expires_in: int | None = Field(
        validation_alias="expiresIn",
        serialization_alias="expiresIn",
        default=None,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    payment_link_url: str | None = Field(
        validation_alias="paymentLinkUrl",
        serialization_alias="paymentLinkUrl",
        default=None,
    )
    qr_code_image: str | None = Field(
        validation_alias="qrCodeImage",
        serialization_alias="qrCodeImage",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )
    giftback_applied_value: int | None = Field(
        validation_alias="giftbackAppliedValue",
        serialization_alias="giftbackAppliedValue",
        default=None,
    )


class WebhookCompanyBankAccountBlockedPayload(BaseSchema):
    """The blockings on a bank account changed. Requires the
    `SEND_WEBHOOK_TO_BLOCK_COMPANY_BANK_ACCOUNT` feature.

    Attributes:
        event (str): Undocumented in the spec.
        account (WebhookCompanyBankAccountBlockedPayloadAccount | None): Undocumented in
            the spec.
        blockings (list[WebhookCompanyBankAccountBlockedPayloadBlockingsItem]):
            Undocumented in the spec.
    """

    event: str
    account: WebhookCompanyBankAccountBlockedPayloadAccount | None = None
    blockings: list[WebhookCompanyBankAccountBlockedPayloadBlockingsItem] = Field(
        default_factory=list,
    )


class WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa2(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa2.

    Attributes:
        customer (WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa3 | None):
            Undocumented in the spec.
        payer (WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa5 | None):
            Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        info_pagador (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    customer: WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa3 | None = None
    payer: WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa5 | None = None
    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    info_pagador: str | None = Field(
        validation_alias="infoPagador",
        serialization_alias="infoPagador",
        default=None,
    )
    status: str | None = None
    type: str | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookOpenpixChargeCompletedPayloadPix(BaseSchema):
    """Schema generated for WebhookOpenpixChargeCompletedPayloadPix.

    Attributes:
        customer (WebhookOpenpixChargeCompletedPayloadPixCustomer | None): Undocumented
            in the spec.
        payer (WebhookOpenpixChargeCompletedPayloadPixPayer | None): Undocumented in the
            spec.
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        transaction_id (str | None): Undocumented in the spec.
        info_pagador (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    customer: WebhookOpenpixChargeCompletedPayloadPixCustomer | None = None
    payer: WebhookOpenpixChargeCompletedPayloadPixPayer | None = None
    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
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
    status: str | None = None
    type: str | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookOpenpixTransactionReceivedPayloadPixCreditParty(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixCreditParty.

    Attributes:
        pix_key (WebhookOpenpixTransactionReceivedPayloadPixCreditPartyP | None):
            Undocumented in the spec.
        account (WebhookOpenpixTransactionReceivedPayloadPixCreditPartyA | None):
            Undocumented in the spec.
        psp (WebhookOpenpixTransactionReceivedPayloadPixCreditPartyP2 | None):
            Undocumented in the spec.
        holder (WebhookOpenpixTransactionReceivedPayloadPixCreditPartyH | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: WebhookOpenpixTransactionReceivedPayloadPixCreditPartyP | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    account: WebhookOpenpixTransactionReceivedPayloadPixCreditPartyA | None = None
    psp: WebhookOpenpixTransactionReceivedPayloadPixCreditPartyP2 | None = None
    holder: WebhookOpenpixTransactionReceivedPayloadPixCreditPartyH | None = None


class WebhookOpenpixTransactionReceivedPayloadPixDebitParty(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPixDebitParty.

    Attributes:
        account (WebhookOpenpixTransactionReceivedPayloadPixDebitPartyAc | None):
            Undocumented in the spec.
        psp (WebhookOpenpixTransactionReceivedPayloadPixDebitPartyPs | None):
            Undocumented in the spec.
        holder (WebhookOpenpixTransactionReceivedPayloadPixDebitPartyHo | None):
            Undocumented in the spec.
    """

    account: WebhookOpenpixTransactionReceivedPayloadPixDebitPartyAc | None = None
    psp: WebhookOpenpixTransactionReceivedPayloadPixDebitPartyPs | None = None
    holder: WebhookOpenpixTransactionReceivedPayloadPixDebitPartyHo | None = None


class WebhookPixAutomaticApprovedPayloadCustomer(BaseSchema):
    """Schema generated for WebhookPixAutomaticApprovedPayloadCustomer.

    Attributes:
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone (str | None): Undocumented in the spec.
        address (WebhookPixAutomaticApprovedPayloadCustomerAddress | None): Undocumented
            in the spec.
        tax_id (WebhookPixAutomaticApprovedPayloadCustomerTaxId | None): Undocumented in
            the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: WebhookPixAutomaticApprovedPayloadCustomerAddress | None = None
    tax_id: WebhookPixAutomaticApprovedPayloadCustomerTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookPixAutomaticCobrApprovedPayload(BaseSchema):
    """The payer's bank approved the recurring charge.

    Attributes:
        event (str): Undocumented in the spec.
        date_generate_charge (str | None): Undocumented in the spec.
        expiration (int | None): Undocumented in the spec.
        installment_number (int | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        cobr (WebhookPixAutomaticCobrApprovedPayloadCobr | None): Undocumented in the
            spec.
        correlation_id (str | None): Undocumented in the spec.
        payment_subscription_global_id (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    date_generate_charge: str | None = Field(
        validation_alias="dateGenerateCharge",
        serialization_alias="dateGenerateCharge",
        default=None,
    )
    expiration: int | None = None
    installment_number: int | None = Field(
        validation_alias="installmentNumber",
        serialization_alias="installmentNumber",
        default=None,
    )
    value: int | None = None
    status: str | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    cobr: WebhookPixAutomaticCobrApprovedPayloadCobr | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    payment_subscription_global_id: str | None = Field(
        validation_alias="paymentSubscriptionGlobalID",
        serialization_alias="paymentSubscriptionGlobalID",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixAutomaticCobrCompletedPayload(BaseSchema):
    """A recurring charge was paid.

    Attributes:
        event (str): Undocumented in the spec.
        date_generate_charge (str | None): Undocumented in the spec.
        expiration (int | None): Undocumented in the spec.
        installment_number (int | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        cobr (WebhookPixAutomaticCobrCompletedPayloadCobr | None): Undocumented in the
            spec.
        correlation_id (str | None): Undocumented in the spec.
        payment_subscription_global_id (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    date_generate_charge: str | None = Field(
        validation_alias="dateGenerateCharge",
        serialization_alias="dateGenerateCharge",
        default=None,
    )
    expiration: int | None = None
    installment_number: int | None = Field(
        validation_alias="installmentNumber",
        serialization_alias="installmentNumber",
        default=None,
    )
    value: int | None = None
    status: str | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    cobr: WebhookPixAutomaticCobrCompletedPayloadCobr | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    payment_subscription_global_id: str | None = Field(
        validation_alias="paymentSubscriptionGlobalID",
        serialization_alias="paymentSubscriptionGlobalID",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixAutomaticCobrCreatedPayload(BaseSchema):
    """A recurring charge was created for an installment of the mandate.

    Attributes:
        event (str): Undocumented in the spec.
        date_generate_charge (str | None): Undocumented in the spec.
        expiration (int | None): Undocumented in the spec.
        installment_number (int | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        cobr (WebhookPixAutomaticCobrCreatedPayloadCobr | None): Undocumented in the
            spec.
        correlation_id (str | None): Undocumented in the spec.
        payment_subscription_global_id (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    date_generate_charge: str | None = Field(
        validation_alias="dateGenerateCharge",
        serialization_alias="dateGenerateCharge",
        default=None,
    )
    expiration: int | None = None
    installment_number: int | None = Field(
        validation_alias="installmentNumber",
        serialization_alias="installmentNumber",
        default=None,
    )
    value: int | None = None
    status: str | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    cobr: WebhookPixAutomaticCobrCreatedPayloadCobr | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    payment_subscription_global_id: str | None = Field(
        validation_alias="paymentSubscriptionGlobalID",
        serialization_alias="paymentSubscriptionGlobalID",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixAutomaticCobrRejectedPayload(BaseSchema):
    """The payer's bank rejected the recurring charge.

    Attributes:
        event (str): Undocumented in the spec.
        date_generate_charge (str | None): Undocumented in the spec.
        expiration (int | None): Undocumented in the spec.
        installment_number (int | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        cobr (WebhookPixAutomaticCobrRejectedPayloadCobr | None): Undocumented in the
            spec.
        correlation_id (str | None): Undocumented in the spec.
        payment_subscription_global_id (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    date_generate_charge: str | None = Field(
        validation_alias="dateGenerateCharge",
        serialization_alias="dateGenerateCharge",
        default=None,
    )
    expiration: int | None = None
    installment_number: int | None = Field(
        validation_alias="installmentNumber",
        serialization_alias="installmentNumber",
        default=None,
    )
    value: int | None = None
    status: str | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    cobr: WebhookPixAutomaticCobrRejectedPayloadCobr | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    payment_subscription_global_id: str | None = Field(
        validation_alias="paymentSubscriptionGlobalID",
        serialization_alias="paymentSubscriptionGlobalID",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixAutomaticCobrTryRejectedPayload(BaseSchema):
    """The payer's bank rejected the retry.

    Attributes:
        event (str): Undocumented in the spec.
        date_generate_charge (str | None): Undocumented in the spec.
        expiration (int | None): Undocumented in the spec.
        installment_number (int | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        cobr (WebhookPixAutomaticCobrTryRejectedPayloadCobr | None): Undocumented in the
            spec.
        correlation_id (str | None): Undocumented in the spec.
        payment_subscription_global_id (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    date_generate_charge: str | None = Field(
        validation_alias="dateGenerateCharge",
        serialization_alias="dateGenerateCharge",
        default=None,
    )
    expiration: int | None = None
    installment_number: int | None = Field(
        validation_alias="installmentNumber",
        serialization_alias="installmentNumber",
        default=None,
    )
    value: int | None = None
    status: str | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    cobr: WebhookPixAutomaticCobrTryRejectedPayloadCobr | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    payment_subscription_global_id: str | None = Field(
        validation_alias="paymentSubscriptionGlobalID",
        serialization_alias="paymentSubscriptionGlobalID",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixAutomaticCobrTryRequestedPayload(BaseSchema):
    """A new attempt was requested for a recurring charge.

    Attributes:
        event (str): Undocumented in the spec.
        date_generate_charge (str | None): Undocumented in the spec.
        expiration (int | None): Undocumented in the spec.
        installment_number (int | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        cobr (WebhookPixAutomaticCobrTryRequestedPayloadCobr | None): Undocumented in
            the spec.
        correlation_id (str | None): Undocumented in the spec.
        payment_subscription_global_id (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    date_generate_charge: str | None = Field(
        validation_alias="dateGenerateCharge",
        serialization_alias="dateGenerateCharge",
        default=None,
    )
    expiration: int | None = None
    installment_number: int | None = Field(
        validation_alias="installmentNumber",
        serialization_alias="installmentNumber",
        default=None,
    )
    value: int | None = None
    status: str | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    cobr: WebhookPixAutomaticCobrTryRequestedPayloadCobr | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    payment_subscription_global_id: str | None = Field(
        validation_alias="paymentSubscriptionGlobalID",
        serialization_alias="paymentSubscriptionGlobalID",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixAutomaticRejectedPayloadCustomer(BaseSchema):
    """Schema generated for WebhookPixAutomaticRejectedPayloadCustomer.

    Attributes:
        name (str | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        phone (str | None): Undocumented in the spec.
        address (WebhookPixAutomaticRejectedPayloadCustomerAddress | None): Undocumented
            in the spec.
        tax_id (WebhookPixAutomaticRejectedPayloadCustomerTaxId | None): Undocumented in
            the spec.
        correlation_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: WebhookPixAutomaticRejectedPayloadCustomerAddress | None = None
    tax_id: WebhookPixAutomaticRejectedPayloadCustomerTaxId | None = Field(
        validation_alias="taxID",
        serialization_alias="taxID",
        default=None,
    )
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig4(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig4.

    Attributes:
        account (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig5 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig6 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig7 | None):
            Undocumented in the spec.
    """

    account: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig5 | None = None
    psp: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig6 | None = None
    holder: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig7 | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig9(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig9.

    Attributes:
        pix_key (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig10 | None):
            Undocumented in the spec.
        account (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig11 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig12 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig13 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig10 | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    account: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig11 | None = None
    psp: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig12 | None = None
    holder: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig13 | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadRefu2(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadRefu2.

    Attributes:
        pix_key (WebhookPixTransactionRefundReceivedConfirmedPayloadRefu3 | None):
            Undocumented in the spec.
        account (WebhookPixTransactionRefundReceivedConfirmedPayloadRefu4 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundReceivedConfirmedPayloadRefu5 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundReceivedConfirmedPayloadRefu6 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: WebhookPixTransactionRefundReceivedConfirmedPayloadRefu3 | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    account: WebhookPixTransactionRefundReceivedConfirmedPayloadRefu4 | None = None
    psp: WebhookPixTransactionRefundReceivedConfirmedPayloadRefu5 | None = None
    holder: WebhookPixTransactionRefundReceivedConfirmedPayloadRefu6 | None = None


class WebhookPixTransactionRefundReceivedConfirmedPayloadRefu8(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadRefu8.

    Attributes:
        account (WebhookPixTransactionRefundReceivedConfirmedPayloadRefu9 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundReceivedConfirmedPayloadRefu10 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundReceivedConfirmedPayloadRefu11 | None):
            Undocumented in the spec.
    """

    account: WebhookPixTransactionRefundReceivedConfirmedPayloadRefu9 | None = None
    psp: WebhookPixTransactionRefundReceivedConfirmedPayloadRefu10 | None = None
    holder: WebhookPixTransactionRefundReceivedConfirmedPayloadRefu11 | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadOrigi2(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadOrigi2.

    Attributes:
        account (WebhookPixTransactionRefundReceivedRejectedPayloadOrigi3 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundReceivedRejectedPayloadOrigi4 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundReceivedRejectedPayloadOrigi5 | None):
            Undocumented in the spec.
    """

    account: WebhookPixTransactionRefundReceivedRejectedPayloadOrigi3 | None = None
    psp: WebhookPixTransactionRefundReceivedRejectedPayloadOrigi4 | None = None
    holder: WebhookPixTransactionRefundReceivedRejectedPayloadOrigi5 | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadOrigi7(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadOrigi7.

    Attributes:
        pix_key (WebhookPixTransactionRefundReceivedRejectedPayloadOrigi8 | None):
            Undocumented in the spec.
        account (WebhookPixTransactionRefundReceivedRejectedPayloadOrigi9 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundReceivedRejectedPayloadOrigi10 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundReceivedRejectedPayloadOrigi11 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: WebhookPixTransactionRefundReceivedRejectedPayloadOrigi8 | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    account: WebhookPixTransactionRefundReceivedRejectedPayloadOrigi9 | None = None
    psp: WebhookPixTransactionRefundReceivedRejectedPayloadOrigi10 | None = None
    holder: WebhookPixTransactionRefundReceivedRejectedPayloadOrigi11 | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadRefun2(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadRefun2.

    Attributes:
        pix_key (WebhookPixTransactionRefundReceivedRejectedPayloadRefun3 | None):
            Undocumented in the spec.
        account (WebhookPixTransactionRefundReceivedRejectedPayloadRefun4 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundReceivedRejectedPayloadRefun5 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundReceivedRejectedPayloadRefun6 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: WebhookPixTransactionRefundReceivedRejectedPayloadRefun3 | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    account: WebhookPixTransactionRefundReceivedRejectedPayloadRefun4 | None = None
    psp: WebhookPixTransactionRefundReceivedRejectedPayloadRefun5 | None = None
    holder: WebhookPixTransactionRefundReceivedRejectedPayloadRefun6 | None = None


class WebhookPixTransactionRefundReceivedRejectedPayloadRefun8(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadRefun8.

    Attributes:
        account (WebhookPixTransactionRefundReceivedRejectedPayloadRefun9 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundReceivedRejectedPayloadRefun10 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundReceivedRejectedPayloadRefun11 | None):
            Undocumented in the spec.
    """

    account: WebhookPixTransactionRefundReceivedRejectedPayloadRefun9 | None = None
    psp: WebhookPixTransactionRefundReceivedRejectedPayloadRefun10 | None = None
    holder: WebhookPixTransactionRefundReceivedRejectedPayloadRefun11 | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal4(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal4.

    Attributes:
        account (WebhookPixTransactionRefundSentConfirmedPayloadOriginal5 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundSentConfirmedPayloadOriginal6 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundSentConfirmedPayloadOriginal7 | None):
            Undocumented in the spec.
    """

    account: WebhookPixTransactionRefundSentConfirmedPayloadOriginal5 | None = None
    psp: WebhookPixTransactionRefundSentConfirmedPayloadOriginal6 | None = None
    holder: WebhookPixTransactionRefundSentConfirmedPayloadOriginal7 | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal9(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal9.

    Attributes:
        pix_key (WebhookPixTransactionRefundSentConfirmedPayloadOriginal10 | None):
            Undocumented in the spec.
        account (WebhookPixTransactionRefundSentConfirmedPayloadOriginal11 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundSentConfirmedPayloadOriginal12 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundSentConfirmedPayloadOriginal13 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: WebhookPixTransactionRefundSentConfirmedPayloadOriginal10 | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    account: WebhookPixTransactionRefundSentConfirmedPayloadOriginal11 | None = None
    psp: WebhookPixTransactionRefundSentConfirmedPayloadOriginal12 | None = None
    holder: WebhookPixTransactionRefundSentConfirmedPayloadOriginal13 | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadRefundTr2(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadRefundTr2.

    Attributes:
        pix_key (WebhookPixTransactionRefundSentConfirmedPayloadRefundTr3 | None):
            Undocumented in the spec.
        account (WebhookPixTransactionRefundSentConfirmedPayloadRefundTr4 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundSentConfirmedPayloadRefundTr5 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundSentConfirmedPayloadRefundTr6 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: WebhookPixTransactionRefundSentConfirmedPayloadRefundTr3 | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    account: WebhookPixTransactionRefundSentConfirmedPayloadRefundTr4 | None = None
    psp: WebhookPixTransactionRefundSentConfirmedPayloadRefundTr5 | None = None
    holder: WebhookPixTransactionRefundSentConfirmedPayloadRefundTr6 | None = None


class WebhookPixTransactionRefundSentConfirmedPayloadRefundTr8(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadRefundTr8.

    Attributes:
        account (WebhookPixTransactionRefundSentConfirmedPayloadRefundTr9 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundSentConfirmedPayloadRefundTr10 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundSentConfirmedPayloadRefundTr11 | None):
            Undocumented in the spec.
    """

    account: WebhookPixTransactionRefundSentConfirmedPayloadRefundTr9 | None = None
    psp: WebhookPixTransactionRefundSentConfirmedPayloadRefundTr10 | None = None
    holder: WebhookPixTransactionRefundSentConfirmedPayloadRefundTr11 | None = None


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT4(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT4.

    Attributes:
        account (WebhookPixTransactionRefundSentRejectedPayloadOriginalT5 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundSentRejectedPayloadOriginalT6 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundSentRejectedPayloadOriginalT7 | None):
            Undocumented in the spec.
    """

    account: WebhookPixTransactionRefundSentRejectedPayloadOriginalT5 | None = None
    psp: WebhookPixTransactionRefundSentRejectedPayloadOriginalT6 | None = None
    holder: WebhookPixTransactionRefundSentRejectedPayloadOriginalT7 | None = None


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT9(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT9.

    Attributes:
        pix_key (WebhookPixTransactionRefundSentRejectedPayloadOriginalT10 | None):
            Undocumented in the spec.
        account (WebhookPixTransactionRefundSentRejectedPayloadOriginalT11 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundSentRejectedPayloadOriginalT12 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundSentRejectedPayloadOriginalT13 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: WebhookPixTransactionRefundSentRejectedPayloadOriginalT10 | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    account: WebhookPixTransactionRefundSentRejectedPayloadOriginalT11 | None = None
    psp: WebhookPixTransactionRefundSentRejectedPayloadOriginalT12 | None = None
    holder: WebhookPixTransactionRefundSentRejectedPayloadOriginalT13 | None = None


class WebhookPixTransactionRefundSentRejectedPayloadRefundTra2(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadRefundTra2.

    Attributes:
        pix_key (WebhookPixTransactionRefundSentRejectedPayloadRefundTra3 | None):
            Undocumented in the spec.
        account (WebhookPixTransactionRefundSentRejectedPayloadRefundTra4 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundSentRejectedPayloadRefundTra5 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundSentRejectedPayloadRefundTra6 | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    pix_key: WebhookPixTransactionRefundSentRejectedPayloadRefundTra3 | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    account: WebhookPixTransactionRefundSentRejectedPayloadRefundTra4 | None = None
    psp: WebhookPixTransactionRefundSentRejectedPayloadRefundTra5 | None = None
    holder: WebhookPixTransactionRefundSentRejectedPayloadRefundTra6 | None = None


class WebhookPixTransactionRefundSentRejectedPayloadRefundTra8(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadRefundTra8.

    Attributes:
        account (WebhookPixTransactionRefundSentRejectedPayloadRefundTra9 | None):
            Undocumented in the spec.
        psp (WebhookPixTransactionRefundSentRejectedPayloadRefundTra10 | None):
            Undocumented in the spec.
        holder (WebhookPixTransactionRefundSentRejectedPayloadRefundTra11 | None):
            Undocumented in the spec.
    """

    account: WebhookPixTransactionRefundSentRejectedPayloadRefundTra9 | None = None
    psp: WebhookPixTransactionRefundSentRejectedPayloadRefundTra10 | None = None
    holder: WebhookPixTransactionRefundSentRejectedPayloadRefundTra11 | None = None


class WithdrawFromAccountResponseWithdraw(BaseSchema):
    """Schema generated for WithdrawFromAccountResponseWithdraw.

    Attributes:
        account (CompanyBankAccount | None): Undocumented in the spec.
        transaction (WithdrawTransaction | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    account: CompanyBankAccount | None = None
    transaction: WithdrawTransaction | None = None


class WithdrawFromSubaccountResponse(BaseSchema):
    """Schema generated for WithdrawFromSubaccountResponse.

    Attributes:
        withdraw (WithdrawFromSubaccountResponseWithdraw | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    withdraw: WithdrawFromSubaccountResponseWithdraw | None = None


class ApprovePaymentResponse(BaseSchema):
    """Schema generated for ApprovePaymentResponse.

    Attributes:
        payment (Payment | None): Undocumented in the spec.
        transaction (PaymentTransaction | None): Undocumented in the spec.
        destination (PaymentDestination | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    payment: Payment | None = None
    transaction: PaymentTransaction | None = None
    destination: PaymentDestination | None = None


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
        expires_in (int | None): Seconds until the charge expires. The specification
            declares this `string`; the API returns an integer.
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
    expires_in: int | None = Field(
        validation_alias="expiresIn",
        serialization_alias="expiresIn",
        description=(
            "Seconds until the charge expires. The specification declares this "
            "`string`; the API returns an integer."
        ),
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


class CreateKycOnboardingResponse(BaseSchema):
    """Schema generated for CreateKycOnboardingResponse.

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


CreatePaymentBody = (
    CreatePaymentBodyPixKey
    | CreatePaymentBodyQrCode
    | CreatePaymentBodyManual
    | CreatePaymentBodyBoleto
)
"""Request body of CreatePaymentBody, one variant per shape."""


class CreatePaymentResponse(BaseSchema):
    """Schema generated for CreatePaymentResponse.

    Attributes:
        payment (Payment | None): Undocumented in the spec.
        transaction (PaymentTransaction | None): Undocumented in the spec.
        destination (PaymentDestination | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    payment: Payment | None = None
    transaction: PaymentTransaction | None = None
    destination: PaymentDestination | None = None


class CreateSubscriptionResponse(BaseSchema):
    """Schema generated for CreateSubscriptionResponse.

    Attributes:
        subscription (Subscription | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    subscription: Subscription | None = None


class DecodeEmvResponseRecLocation(BaseSchema):
    """Resolved REC (request for payment) location details when EMV points to a REC
    endpoint.

    Attributes:
        is_valid (bool | None): Undocumented in the spec.
        location_errors (list[str]): Undocumented in the spec.
        payload (DecodeEmvResponseRecLocationPayload | None): Undocumented in the spec.
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
    payload: DecodeEmvResponseRecLocationPayload | None = None
    url: str | None = None


class GetInstallmentResponse(BaseSchema):
    """Schema generated for GetInstallmentResponse.

    Attributes:
        installment (Installment | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    installment: Installment | None = None


class GetPartnerCompanyResponse(BaseSchema):
    """Schema generated for GetPartnerCompanyResponse.

    Attributes:
        pre_registration (GetPartnerCompanyResponsePreRegistration | None): Undocumented
            in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pre_registration: GetPartnerCompanyResponsePreRegistration | None = Field(
        validation_alias="preRegistration",
        serialization_alias="preRegistration",
        default=None,
    )


class GetPaymentResponse(BaseSchema):
    """Schema generated for GetPaymentResponse.

    Attributes:
        payment (Payment | None): Undocumented in the spec.
        transaction (PaymentTransaction | None): Undocumented in the spec.
        destination (PaymentDestination | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    payment: Payment | None = None
    transaction: PaymentTransaction | None = None
    destination: PaymentDestination | None = None


class GetSubscriptionResponse(BaseSchema):
    """Schema generated for GetSubscriptionResponse.

    Attributes:
        subscription (Subscription | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    subscription: Subscription | None = None


class ListAccountsResponse(BaseSchema):
    """Schema generated for ListAccountsResponse.

    Attributes:
        accounts (list[CompanyBankAccount]): Undocumented in the spec.
        page_info (ListAccountsResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    accounts: list[CompanyBankAccount] = Field(default_factory=list)
    page_info: ListAccountsResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class ListCustomersResponse(BaseSchema):
    """Schema generated for ListCustomersResponse.

    Attributes:
        customers (list[Customer]): Undocumented in the spec.
        page_info (ListCustomersResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    customers: list[Customer] = Field(default_factory=list)
    page_info: ListCustomersResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class ListDisputesResponse(BaseSchema):
    """Schema generated for ListDisputesResponse.

    Attributes:
        disputes (list[ListDisputesResponseDisputesItem]): Undocumented in the spec.
        page_info (ListDisputesResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    disputes: list[ListDisputesResponseDisputesItem] = Field(default_factory=list)
    page_info: ListDisputesResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class ListPartnerAffiliatesResponse(BaseSchema):
    """Schema generated for ListPartnerAffiliatesResponse.

    Attributes:
        affiliates (list[ListPartnerAffiliatesResponseAffiliatesItem]): Undocumented in
            the spec.
        page_info (ListPartnerAffiliatesResponsePageInfo | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    affiliates: list[ListPartnerAffiliatesResponseAffiliatesItem] = Field(
        default_factory=list,
    )
    page_info: ListPartnerAffiliatesResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class ListPartnerCompaniesResponse(BaseSchema):
    """Schema generated for ListPartnerCompaniesResponse.

    Attributes:
        pre_registrations (list[ListPartnerCompaniesResponsePreRegistrationsItem]):
            Undocumented in the spec.
        page_info (ListPartnerCompaniesResponsePageInfo | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pre_registrations: list[ListPartnerCompaniesResponsePreRegistrationsItem] = Field(
        validation_alias="preRegistrations",
        serialization_alias="preRegistrations",
        default_factory=list,
    )
    page_info: ListPartnerCompaniesResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class ListPaymentsResponsePaymentsItem(BaseSchema):
    """Schema generated for ListPaymentsResponsePaymentsItem.

    Attributes:
        payment (Payment | None): Undocumented in the spec.
        transaction (PaymentTransaction | None): Undocumented in the spec.
        destination (PaymentDestination | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    payment: Payment | None = None
    transaction: PaymentTransaction | None = None
    destination: PaymentDestination | None = None


class ListPixKeyTokenLogsResponse(BaseSchema):
    """Schema generated for ListPixKeyTokenLogsResponse.

    Attributes:
        logs (list[TokenBucketLog]): Undocumented in the spec.
        page_info (ListPixKeyTokenLogsResponsePageInfo | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    logs: list[TokenBucketLog] = Field(default_factory=list)
    page_info: ListPixKeyTokenLogsResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class ListRefundsResponse(BaseSchema):
    """Schema generated for ListRefundsResponse.

    Attributes:
        refunds (list[Refund]): Undocumented in the spec.
        page_info (ListRefundsResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    refunds: list[Refund] = Field(default_factory=list)
    page_info: ListRefundsResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class ListStaticQrCodesResponse(BaseSchema):
    """Schema generated for ListStaticQrCodesResponse.

    Attributes:
        pix_qr_codes (list[PixQrCode]): Undocumented in the spec.
        page_info (ListStaticQrCodesResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pix_qr_codes: list[PixQrCode] = Field(
        validation_alias="pixQrCodes",
        serialization_alias="pixQrCodes",
        default_factory=list,
    )
    page_info: ListStaticQrCodesResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class ListSubaccountsResponse(BaseSchema):
    """Schema generated for ListSubaccountsResponse.

    Attributes:
        subaccounts (list[ListSubaccountsResponseSubaccountsItem]): Undocumented in the
            spec.
        page_info (ListSubaccountsResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    subaccounts: list[ListSubaccountsResponseSubaccountsItem] = Field(
        default_factory=list,
    )
    page_info: ListSubaccountsResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class ListSubscriptionInstallmentsResponse(BaseSchema):
    """Schema generated for ListSubscriptionInstallmentsResponse.

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


class ListSubscriptionsResponse(BaseSchema):
    """Schema generated for ListSubscriptionsResponse.

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


class ListWebhooksResponse(BaseSchema):
    """Schema generated for ListWebhooksResponse.

    Attributes:
        webhooks (list[Webhook]): Undocumented in the spec.
        page_info (ListWebhooksResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    webhooks: list[Webhook] = Field(default_factory=list)
    page_info: ListWebhooksResponsePageInfo | None = Field(
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


class UpsertInvoiceIntegrationResponse(BaseSchema):
    """Schema generated for UpsertInvoiceIntegrationResponse.

    Attributes:
        integration (UpsertInvoiceIntegrationResponseIntegration | None): Undocumented
            in the spec.
    """

    model_config = ConfigDict(extra="allow")

    integration: UpsertInvoiceIntegrationResponseIntegration | None = None


class WebhookAccountRegisterPendingPayload(BaseSchema):
    """A sub-account register is under compliance analysis.

    Attributes:
        event (str): Undocumented in the spec.
        account_register (WebhookAccountRegisterPendingPayloadAccountRegister | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    account_register: WebhookAccountRegisterPendingPayloadAccountRegister | None = (
        Field(
            validation_alias="accountRegister",
            serialization_alias="accountRegister",
            default=None,
        )
    )


class WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa(BaseSchema):
    """A charge was paid by a payer whose taxID differs from the charge customer. Only
    sent when the charge does not enforce the same taxID.

    Attributes:
        event (str): Undocumented in the spec.
        charge (WebhookCharge | None): The charge the event refers to. Superset of the
            fields observed across the charge and transaction events; a given event
            carries the subset that applies to it.
        pix (WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa2 | None):
            Undocumented in the spec.
        company (WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa7 | None):
            Undocumented in the spec.
        account (WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa8 | None):
            Undocumented in the spec.
        payer (WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa9 | None):
            Undocumented in the spec.
    """

    event: str
    charge: WebhookCharge | None = Field(
        description=(
            "The charge the event refers to. Superset of the fields observed across "
            "the charge and transaction events; a given event carries the subset that "
            "applies to it."
        ),
        default=None,
    )
    pix: WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa2 | None = None
    company: WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa7 | None = None
    account: WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa8 | None = None
    payer: WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa9 | None = None


class WebhookOpenpixChargeCompletedPayload(BaseSchema):
    """A charge was paid in full.

    Attributes:
        event (str): Undocumented in the spec.
        charge (WebhookCharge | None): The charge the event refers to. Superset of the
            fields observed across the charge and transaction events; a given event
            carries the subset that applies to it.
        pix (WebhookOpenpixChargeCompletedPayloadPix | None): Undocumented in the spec.
        company (WebhookOpenpixChargeCompletedPayloadCompany | None): Undocumented in
            the spec.
        account (WebhookOpenpixChargeCompletedPayloadAccount | None): Undocumented in
            the spec.
    """

    event: str
    charge: WebhookCharge | None = Field(
        description=(
            "The charge the event refers to. Superset of the fields observed across "
            "the charge and transaction events; a given event carries the subset that "
            "applies to it."
        ),
        default=None,
    )
    pix: WebhookOpenpixChargeCompletedPayloadPix | None = None
    company: WebhookOpenpixChargeCompletedPayloadCompany | None = None
    account: WebhookOpenpixChargeCompletedPayloadAccount | None = None


class WebhookOpenpixChargeCreatedPayload(BaseSchema):
    """A charge was created.

    Attributes:
        event (str): Undocumented in the spec.
        charge (WebhookCharge | None): The charge the event refers to. Superset of the
            fields observed across the charge and transaction events; a given event
            carries the subset that applies to it.
        company (WebhookOpenpixChargeCreatedPayloadCompany | None): Undocumented in the
            spec.
        account (WebhookOpenpixChargeCreatedPayloadAccount | None): Undocumented in the
            spec.
    """

    event: str
    charge: WebhookCharge | None = Field(
        description=(
            "The charge the event refers to. Superset of the fields observed across "
            "the charge and transaction events; a given event carries the subset that "
            "applies to it."
        ),
        default=None,
    )
    company: WebhookOpenpixChargeCreatedPayloadCompany | None = None
    account: WebhookOpenpixChargeCreatedPayloadAccount | None = None


class WebhookOpenpixChargeExpiredPayload(BaseSchema):
    """A charge reached its expiration without being paid in full.

    Attributes:
        event (str): Undocumented in the spec.
        charge (WebhookCharge | None): The charge the event refers to. Superset of the
            fields observed across the charge and transaction events; a given event
            carries the subset that applies to it.
        company (WebhookOpenpixChargeExpiredPayloadCompany | None): Undocumented in the
            spec.
        account (WebhookOpenpixChargeExpiredPayloadAccount | None): Undocumented in the
            spec.
    """

    event: str
    charge: WebhookCharge | None = Field(
        description=(
            "The charge the event refers to. Superset of the fields observed across "
            "the charge and transaction events; a given event carries the subset that "
            "applies to it."
        ),
        default=None,
    )
    company: WebhookOpenpixChargeExpiredPayloadCompany | None = None
    account: WebhookOpenpixChargeExpiredPayloadAccount | None = None


class WebhookOpenpixTransactionReceivedPayloadPix(BaseSchema):
    """Schema generated for WebhookOpenpixTransactionReceivedPayloadPix.

    Attributes:
        debit_party (WebhookOpenpixTransactionReceivedPayloadPixDebitParty | None):
            Undocumented in the spec.
        credit_party (WebhookOpenpixTransactionReceivedPayloadPixCreditParty | None):
            Undocumented in the spec.
        payer (WebhookOpenpixTransactionReceivedPayloadPixPayer | None): Undocumented in
            the spec.
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        fee (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        pix_key (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    debit_party: WebhookOpenpixTransactionReceivedPayloadPixDebitParty | None = Field(
        validation_alias="debitParty",
        serialization_alias="debitParty",
        default=None,
    )
    credit_party: WebhookOpenpixTransactionReceivedPayloadPixCreditParty | None = Field(
        validation_alias="creditParty",
        serialization_alias="creditParty",
        default=None,
    )
    payer: WebhookOpenpixTransactionReceivedPayloadPixPayer | None = None
    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    fee: int | None = None
    status: str | None = None
    type: str | None = None
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    pix_key: str | None = Field(
        validation_alias="pixKey",
        serialization_alias="pixKey",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookOpenpixTransactionRefundReceivedPayload(BaseSchema):
    """Superseded by the `PIX_TRANSACTION_REFUND_*` events, which report the refund leg
    and its outcome separately.

    Attributes:
        event (str): Undocumented in the spec.
        charge (WebhookCharge | None): The charge the event refers to. Superset of the
            fields observed across the charge and transaction events; a given event
            carries the subset that applies to it.
        pix_qr_code (PixQrCode | None): Undocumented in the spec.
        pix (WebhookOpenpixTransactionRefundReceivedPayloadPix | None): Undocumented in
            the spec.
        company (WebhookOpenpixTransactionRefundReceivedPayloadCompany | None):
            Undocumented in the spec.
        account (WebhookOpenpixTransactionRefundReceivedPayloadAccount | None):
            Undocumented in the spec.
        refunds (list[Refund]): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    charge: WebhookCharge | None = Field(
        description=(
            "The charge the event refers to. Superset of the fields observed across "
            "the charge and transaction events; a given event carries the subset that "
            "applies to it."
        ),
        default=None,
    )
    pix_qr_code: PixQrCode | None = Field(
        validation_alias="pixQrCode",
        serialization_alias="pixQrCode",
        default=None,
    )
    pix: WebhookOpenpixTransactionRefundReceivedPayloadPix | None = None
    company: WebhookOpenpixTransactionRefundReceivedPayloadCompany | None = None
    account: WebhookOpenpixTransactionRefundReceivedPayloadAccount | None = None
    refunds: list[Refund] = Field(default_factory=list)


class WebhookPixAutomaticApprovedPayload(BaseSchema):
    """The payer's bank approved the recurring mandate.

    Attributes:
        event (str): Undocumented in the spec.
        customer (WebhookPixAutomaticApprovedPayloadCustomer | None): Undocumented in
            the spec.
        day_generate_charge (int | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        pix_recurring (WebhookPixAutomaticApprovedPayloadPixRecurring | None):
            Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    customer: WebhookPixAutomaticApprovedPayloadCustomer | None = None
    day_generate_charge: int | None = Field(
        validation_alias="dayGenerateCharge",
        serialization_alias="dayGenerateCharge",
        default=None,
    )
    value: int | None = None
    status: str | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    pix_recurring: WebhookPixAutomaticApprovedPayloadPixRecurring | None = Field(
        validation_alias="pixRecurring",
        serialization_alias="pixRecurring",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixAutomaticRejectedPayload(BaseSchema):
    """The payer rejected the recurring mandate.

    Attributes:
        event (str): Undocumented in the spec.
        customer (WebhookPixAutomaticRejectedPayloadCustomer | None): Undocumented in
            the spec.
        day_generate_charge (int | None): Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        correlation_id (str | None): Undocumented in the spec.
        pix_recurring (WebhookPixAutomaticRejectedPayloadPixRecurring | None):
            Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    customer: WebhookPixAutomaticRejectedPayloadCustomer | None = None
    day_generate_charge: int | None = Field(
        validation_alias="dayGenerateCharge",
        serialization_alias="dayGenerateCharge",
        default=None,
    )
    value: int | None = None
    status: str | None = None
    correlation_id: str | None = Field(
        validation_alias="correlationID",
        serialization_alias="correlationID",
        default=None,
    )
    pix_recurring: WebhookPixAutomaticRejectedPayloadPixRecurring | None = Field(
        validation_alias="pixRecurring",
        serialization_alias="pixRecurring",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedConfirmedPayloadOrig(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadOrig.

    Attributes:
        payer (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig2 | None):
            Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        debit_party (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig4 | None):
            Undocumented in the spec.
        credit_party (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig9 | None):
            Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    payer: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig2 | None = None
    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    status: str | None = None
    type: str | None = None
    debit_party: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig4 | None = (
        Field(
            validation_alias="debitParty",
            serialization_alias="debitParty",
            default=None,
        )
    )
    credit_party: WebhookPixTransactionRefundReceivedConfirmedPayloadOrig9 | None = (
        Field(
            validation_alias="creditParty",
            serialization_alias="creditParty",
            default=None,
        )
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedConfirmedPayloadRefu(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedConfirmedPayloadRefu.

    Attributes:
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        debit_party (WebhookPixTransactionRefundReceivedConfirmedPayloadRefu2 | None):
            Undocumented in the spec.
        credit_party (WebhookPixTransactionRefundReceivedConfirmedPayloadRefu8 | None):
            Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        partial (bool | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    status: str | None = None
    type: str | None = None
    debit_party: WebhookPixTransactionRefundReceivedConfirmedPayloadRefu2 | None = (
        Field(
            validation_alias="debitParty",
            serialization_alias="debitParty",
            default=None,
        )
    )
    credit_party: WebhookPixTransactionRefundReceivedConfirmedPayloadRefu8 | None = (
        Field(
            validation_alias="creditParty",
            serialization_alias="creditParty",
            default=None,
        )
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    partial: bool | None = None
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedRejectedPayloadOrigi(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadOrigi.

    Attributes:
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        debit_party (WebhookPixTransactionRefundReceivedRejectedPayloadOrigi2 | None):
            Undocumented in the spec.
        credit_party (WebhookPixTransactionRefundReceivedRejectedPayloadOrigi7 | None):
            Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    status: str | None = None
    type: str | None = None
    debit_party: WebhookPixTransactionRefundReceivedRejectedPayloadOrigi2 | None = (
        Field(
            validation_alias="debitParty",
            serialization_alias="debitParty",
            default=None,
        )
    )
    credit_party: WebhookPixTransactionRefundReceivedRejectedPayloadOrigi7 | None = (
        Field(
            validation_alias="creditParty",
            serialization_alias="creditParty",
            default=None,
        )
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixTransactionRefundReceivedRejectedPayloadRefun(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundReceivedRejectedPayloadRefun.

    Attributes:
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        debit_party (WebhookPixTransactionRefundReceivedRejectedPayloadRefun2 | None):
            Undocumented in the spec.
        credit_party (WebhookPixTransactionRefundReceivedRejectedPayloadRefun8 | None):
            Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        partial (bool | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    status: str | None = None
    type: str | None = None
    debit_party: WebhookPixTransactionRefundReceivedRejectedPayloadRefun2 | None = (
        Field(
            validation_alias="debitParty",
            serialization_alias="debitParty",
            default=None,
        )
    )
    credit_party: WebhookPixTransactionRefundReceivedRejectedPayloadRefun8 | None = (
        Field(
            validation_alias="creditParty",
            serialization_alias="creditParty",
            default=None,
        )
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    partial: bool | None = None
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixTransactionRefundSentConfirmedPayloadOriginal(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadOriginal.

    Attributes:
        payer (WebhookPixTransactionRefundSentConfirmedPayloadOriginal2 | None):
            Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        debit_party (WebhookPixTransactionRefundSentConfirmedPayloadOriginal4 | None):
            Undocumented in the spec.
        credit_party (WebhookPixTransactionRefundSentConfirmedPayloadOriginal9 | None):
            Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    payer: WebhookPixTransactionRefundSentConfirmedPayloadOriginal2 | None = None
    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    status: str | None = None
    type: str | None = None
    debit_party: WebhookPixTransactionRefundSentConfirmedPayloadOriginal4 | None = (
        Field(
            validation_alias="debitParty",
            serialization_alias="debitParty",
            default=None,
        )
    )
    credit_party: WebhookPixTransactionRefundSentConfirmedPayloadOriginal9 | None = (
        Field(
            validation_alias="creditParty",
            serialization_alias="creditParty",
            default=None,
        )
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixTransactionRefundSentConfirmedPayloadRefundTr(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentConfirmedPayloadRefundTr.

    Attributes:
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        debit_party (WebhookPixTransactionRefundSentConfirmedPayloadRefundTr2 | None):
            Undocumented in the spec.
        credit_party (WebhookPixTransactionRefundSentConfirmedPayloadRefundTr8 | None):
            Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        partial (bool | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    status: str | None = None
    type: str | None = None
    debit_party: WebhookPixTransactionRefundSentConfirmedPayloadRefundTr2 | None = (
        Field(
            validation_alias="debitParty",
            serialization_alias="debitParty",
            default=None,
        )
    )
    credit_party: WebhookPixTransactionRefundSentConfirmedPayloadRefundTr8 | None = (
        Field(
            validation_alias="creditParty",
            serialization_alias="creditParty",
            default=None,
        )
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    partial: bool | None = None
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixTransactionRefundSentRejectedPayloadOriginalT(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadOriginalT.

    Attributes:
        payer (WebhookPixTransactionRefundSentRejectedPayloadOriginalT2 | None):
            Undocumented in the spec.
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        debit_party (WebhookPixTransactionRefundSentRejectedPayloadOriginalT4 | None):
            Undocumented in the spec.
        credit_party (WebhookPixTransactionRefundSentRejectedPayloadOriginalT9 | None):
            Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    payer: WebhookPixTransactionRefundSentRejectedPayloadOriginalT2 | None = None
    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    status: str | None = None
    type: str | None = None
    debit_party: WebhookPixTransactionRefundSentRejectedPayloadOriginalT4 | None = (
        Field(
            validation_alias="debitParty",
            serialization_alias="debitParty",
            default=None,
        )
    )
    credit_party: WebhookPixTransactionRefundSentRejectedPayloadOriginalT9 | None = (
        Field(
            validation_alias="creditParty",
            serialization_alias="creditParty",
            default=None,
        )
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WebhookPixTransactionRefundSentRejectedPayloadRefundTra(BaseSchema):
    """Schema generated for WebhookPixTransactionRefundSentRejectedPayloadRefundTra.

    Attributes:
        value (int | None): Undocumented in the spec.
        time (str | None): Undocumented in the spec.
        end_to_end_id (str | None): Undocumented in the spec.
        info_pagador (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        debit_party (WebhookPixTransactionRefundSentRejectedPayloadRefundTra2 | None):
            Undocumented in the spec.
        credit_party (WebhookPixTransactionRefundSentRejectedPayloadRefundTra8 | None):
            Undocumented in the spec.
        created_at (str | None): Undocumented in the spec.
        partial (bool | None): Undocumented in the spec.
        global_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    value: int | None = None
    time: str | None = None
    end_to_end_id: str | None = Field(
        validation_alias="endToEndId",
        serialization_alias="endToEndId",
        default=None,
    )
    info_pagador: str | None = Field(
        validation_alias="infoPagador",
        serialization_alias="infoPagador",
        default=None,
    )
    status: str | None = None
    type: str | None = None
    debit_party: WebhookPixTransactionRefundSentRejectedPayloadRefundTra2 | None = (
        Field(
            validation_alias="debitParty",
            serialization_alias="debitParty",
            default=None,
        )
    )
    credit_party: WebhookPixTransactionRefundSentRejectedPayloadRefundTra8 | None = (
        Field(
            validation_alias="creditParty",
            serialization_alias="creditParty",
            default=None,
        )
    )
    created_at: str | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    partial: bool | None = None
    global_id: str | None = Field(
        validation_alias="globalID",
        serialization_alias="globalID",
        default=None,
    )


class WithdrawFromAccountResponse(BaseSchema):
    """Schema generated for WithdrawFromAccountResponse.

    Attributes:
        withdraw (WithdrawFromAccountResponseWithdraw | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    withdraw: WithdrawFromAccountResponseWithdraw | None = None


class CreateChargeResponse(BaseSchema):
    """Schema generated for CreateChargeResponse.

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


class DecodeEmvResponse(BaseSchema):
    """Schema generated for DecodeEmvResponse.

    Attributes:
        emv (DecodeEmvResponseEmv | None): Undocumented in the spec.
        cob_location (DecodeEmvResponseCobLocation | None): Resolved COB (charge)
            location details when the EMV points to a COB endpoint
        rec_location (DecodeEmvResponseRecLocation | None): Resolved REC (request for
            payment) location details when EMV points to a REC endpoint
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    emv: DecodeEmvResponseEmv | None = None
    cob_location: DecodeEmvResponseCobLocation | None = Field(
        validation_alias="cobLocation",
        serialization_alias="cobLocation",
        description=(
            "Resolved COB (charge) location details when the EMV points to a COB "
            "endpoint"
        ),
        default=None,
    )
    rec_location: DecodeEmvResponseRecLocation | None = Field(
        validation_alias="recLocation",
        serialization_alias="recLocation",
        description=(
            "Resolved REC (request for payment) location details when EMV points to a "
            "REC endpoint"
        ),
        default=None,
    )


class GetChargeResponse(BaseSchema):
    """Schema generated for GetChargeResponse.

    Attributes:
        charge (Charge | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    charge: Charge | None = None


class ListChargesResponse(BaseSchema):
    """Schema generated for ListChargesResponse.

    Attributes:
        charges (list[Charge]): Undocumented in the spec.
        page_info (ListChargesResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    charges: list[Charge] = Field(default_factory=list)
    page_info: ListChargesResponsePageInfo | None = Field(
        validation_alias="pageInfo",
        serialization_alias="pageInfo",
        default=None,
    )


class ListPaymentsResponse(BaseSchema):
    """Schema generated for ListPaymentsResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        payments (list[ListPaymentsResponsePaymentsItem]): Undocumented in the spec.
        page_info (ListPaymentsResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = None
    payments: list[ListPaymentsResponsePaymentsItem] = Field(default_factory=list)
    page_info: ListPaymentsResponsePageInfo | None = Field(
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


class WebhookOpenpixTransactionReceivedPayload(BaseSchema):
    """A Pix credit was received, either against a charge or against a static QR code.
    `charge` and `pixQrCode` are null when the Pix arrived with neither.

    Attributes:
        event (str): Undocumented in the spec.
        charge (WebhookCharge | None): Undocumented in the spec.
        pix_qr_code (PixQrCode | None): Undocumented in the spec.
        pix (WebhookOpenpixTransactionReceivedPayloadPix | None): Undocumented in the
            spec.
        company (WebhookOpenpixTransactionReceivedPayloadCompany | None): Undocumented
            in the spec.
        account (WebhookOpenpixTransactionReceivedPayloadAccount | None): Undocumented
            in the spec.
        refunds (list[Refund]): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    charge: WebhookCharge | None = None
    pix_qr_code: PixQrCode | None = Field(
        validation_alias="pixQrCode",
        serialization_alias="pixQrCode",
        default=None,
    )
    pix: WebhookOpenpixTransactionReceivedPayloadPix | None = None
    company: WebhookOpenpixTransactionReceivedPayloadCompany | None = None
    account: WebhookOpenpixTransactionReceivedPayloadAccount | None = None
    refunds: list[Refund] = Field(default_factory=list)


class WebhookPixTransactionRefundReceivedConfirmedPayload(BaseSchema):
    """A refund credited to this account was confirmed.

    Attributes:
        event (str): Undocumented in the spec.
        refund_transaction (WebhookPixTransactionRefundReceivedConfirmedPayloadRefu |
            None): Undocumented in the spec.
        original_transaction (WebhookPixTransactionRefundReceivedConfirmedPayloadOrig |
            None): Undocumented in the spec.
        company (WebhookPixTransactionRefundReceivedConfirmedPayloadComp | None):
            Undocumented in the spec.
        account (WebhookPixTransactionRefundReceivedConfirmedPayloadAcco | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    refund_transaction: (
        WebhookPixTransactionRefundReceivedConfirmedPayloadRefu | None
    ) = Field(
        validation_alias="refundTransaction",
        serialization_alias="refundTransaction",
        default=None,
    )
    original_transaction: (
        WebhookPixTransactionRefundReceivedConfirmedPayloadOrig | None
    ) = Field(
        validation_alias="originalTransaction",
        serialization_alias="originalTransaction",
        default=None,
    )
    company: WebhookPixTransactionRefundReceivedConfirmedPayloadComp | None = None
    account: WebhookPixTransactionRefundReceivedConfirmedPayloadAcco | None = None


class WebhookPixTransactionRefundReceivedRejectedPayload(BaseSchema):
    """A refund credited to this account was rejected. `error` carries the reason.

    Attributes:
        event (str): Undocumented in the spec.
        refund_transaction (WebhookPixTransactionRefundReceivedRejectedPayloadRefun |
            None): Undocumented in the spec.
        original_transaction (WebhookPixTransactionRefundReceivedRejectedPayloadOrigi |
            None): Undocumented in the spec.
        company (WebhookPixTransactionRefundReceivedRejectedPayloadCompa | None):
            Undocumented in the spec.
        account (WebhookPixTransactionRefundReceivedRejectedPayloadAccou | None):
            Undocumented in the spec.
        error (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    refund_transaction: (
        WebhookPixTransactionRefundReceivedRejectedPayloadRefun | None
    ) = Field(
        validation_alias="refundTransaction",
        serialization_alias="refundTransaction",
        default=None,
    )
    original_transaction: (
        WebhookPixTransactionRefundReceivedRejectedPayloadOrigi | None
    ) = Field(
        validation_alias="originalTransaction",
        serialization_alias="originalTransaction",
        default=None,
    )
    company: WebhookPixTransactionRefundReceivedRejectedPayloadCompa | None = None
    account: WebhookPixTransactionRefundReceivedRejectedPayloadAccou | None = None
    error: str | None = None


class WebhookPixTransactionRefundSentConfirmedPayload(BaseSchema):
    """A refund this account sent was confirmed.

    Attributes:
        event (str): Undocumented in the spec.
        refund_transaction (WebhookPixTransactionRefundSentConfirmedPayloadRefundTr |
            None): Undocumented in the spec.
        original_transaction (WebhookPixTransactionRefundSentConfirmedPayloadOriginal |
            None): Undocumented in the spec.
        company (WebhookPixTransactionRefundSentConfirmedPayloadCompany | None):
            Undocumented in the spec.
        account (WebhookPixTransactionRefundSentConfirmedPayloadAccount | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    refund_transaction: (
        WebhookPixTransactionRefundSentConfirmedPayloadRefundTr | None
    ) = Field(
        validation_alias="refundTransaction",
        serialization_alias="refundTransaction",
        default=None,
    )
    original_transaction: (
        WebhookPixTransactionRefundSentConfirmedPayloadOriginal | None
    ) = Field(
        validation_alias="originalTransaction",
        serialization_alias="originalTransaction",
        default=None,
    )
    company: WebhookPixTransactionRefundSentConfirmedPayloadCompany | None = None
    account: WebhookPixTransactionRefundSentConfirmedPayloadAccount | None = None


class WebhookPixTransactionRefundSentRejectedPayload(BaseSchema):
    """A refund this account sent was rejected. `error` carries the reason.

    Attributes:
        event (str): Undocumented in the spec.
        refund_transaction (WebhookPixTransactionRefundSentRejectedPayloadRefundTra |
            None): Undocumented in the spec.
        original_transaction (WebhookPixTransactionRefundSentRejectedPayloadOriginalT |
            None): Undocumented in the spec.
        company (WebhookPixTransactionRefundSentRejectedPayloadCompany | None):
            Undocumented in the spec.
        account (WebhookPixTransactionRefundSentRejectedPayloadAccount | None):
            Undocumented in the spec.
        error (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: str
    refund_transaction: (
        WebhookPixTransactionRefundSentRejectedPayloadRefundTra | None
    ) = Field(
        validation_alias="refundTransaction",
        serialization_alias="refundTransaction",
        default=None,
    )
    original_transaction: (
        WebhookPixTransactionRefundSentRejectedPayloadOriginalT | None
    ) = Field(
        validation_alias="originalTransaction",
        serialization_alias="originalTransaction",
        default=None,
    )
    company: WebhookPixTransactionRefundSentRejectedPayloadCompany | None = None
    account: WebhookPixTransactionRefundSentRejectedPayloadAccount | None = None
    error: str | None = None


class GetTransactionResponse(BaseSchema):
    """Schema generated for GetTransactionResponse.

    Attributes:
        transaction (Transaction | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    transaction: Transaction | None = None


class ListTransactionsResponse(BaseSchema):
    """Schema generated for ListTransactionsResponse.

    Attributes:
        status (str | None): Undocumented in the spec.
        transactions (list[Transaction]): Undocumented in the spec.
        page_info (ListTransactionsResponsePageInfo | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    status: str | None = None
    transactions: list[Transaction] = Field(default_factory=list)
    page_info: ListTransactionsResponsePageInfo | None = Field(
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
    "ActivateAnticipationBeneficiaryResponse",
    "AnticipationBalanceBatchPayload",
    "AnticipationBalanceBatchPayloadItemsItem",
    "AnticipationBalanceBatchResult",
    "AnticipationBalanceBatchResultResultsItem",
    "AnticipationBeneficiary",
    "AnticipationBeneficiaryCreatePayload",
    "AnticipationBeneficiaryCreatePayloadFrequencyOverride",
    "AnticipationBeneficiaryTaxId",
    "AnticipationBeneficiaryTaxIdType",
    "AnticipationError",
    "AnticipationRequest",
    "AnticipationRequestStatus",
    "AnticipationUnauthorized",
    "AnticipationUnauthorizedErrorsItem",
    "Application",
    "ApplicationDeletePayload",
    "ApplicationEnumTypePayload",
    "ApplicationPayload",
    "ApplicationPayloadApplication",
    "ApplicationPayloadApplicationType",
    "ApplicationType",
    "ApproveAnticipationResponse",
    "ApprovePaymentResponse",
    "ApproveStablecoinDepositBody",
    "ApproveStablecoinDepositResponse",
    "BoletoTransaction",
    "BoletoTransactionCharge",
    "BoletoTransactionError",
    "BoletoTransactionListResponse",
    "BoletoTransactionListResponsePageInfo",
    "BoletoTransactionStatus",
    "BoletoTransactionType",
    "BoletoValidateError",
    "BoletoValidateRequest",
    "BoletoValidateResponse",
    "BoletoValidatedInfo",
    "BoletoValidatedInfoFinalBeneficiary",
    "BoletoValidatedInfoIssuingEntity",
    "CancelInvoiceResponse",
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
    "CheckPixKeyBody",
    "CloseAccountResponse",
    "Company",
    "CompanyBankAccount",
    "CompanyBankAccountBalance",
    "CompanyObjectPayload",
    "CompanyResponse",
    "CreateAnticipationBeneficiaryResponse",
    "CreateApplicationResponse",
    "CreateCashbackFidelityBody",
    "CreateCashbackFidelityResponse",
    "CreateCashbackFidelityResponseCashback",
    "CreateChargeResponse",
    "CreateCustomerResponse",
    "CreateInstallmentCobrBody",
    "CreateInvoiceResponse",
    "CreateInvoiceResponseInvoice",
    "CreateInvoiceResponseInvoiceCharge",
    "CreateInvoiceResponseInvoiceCustomer",
    "CreateKycOnboardingResponse",
    "CreatePartnerApplicationBody",
    "CreatePartnerApplicationBodyApplication",
    "CreatePartnerApplicationResponse",
    "CreatePaymentBody",
    "CreatePaymentBodyBoleto",
    "CreatePaymentBodyManual",
    "CreatePaymentBodyManualAccount",
    "CreatePaymentBodyManualHolder",
    "CreatePaymentBodyManualHolderTaxId",
    "CreatePaymentBodyPixKey",
    "CreatePaymentBodyQrCode",
    "CreatePaymentResponse",
    "CreateRefundResponse",
    "CreateStablecoinPayoutBody",
    "CreateStablecoinPayoutResponse",
    "CreateStablecoinPayoutResponsePixKeyOwner",
    "CreateStablecoinPayoutResponseQuote",
    "CreateStaticQrCodeResponse",
    "CreateSubaccountResponse",
    "CreateSubscriptionResponse",
    "CreateTransferResponse",
    "CreateWebhookBody",
    "CreateWebhookResponse",
    "CreditSubaccountBody",
    "CreditSubaccountResponse",
    "Customer",
    "CustomerAddress",
    "CustomerPatchPayload",
    "CustomerPatchPayloadAddress",
    "CustomerPayload",
    "CustomerPayloadAddress",
    "CustomerTaxId",
    "DeactivateAnticipationBeneficiaryResponse",
    "DebitSubaccountBody",
    "DebitSubaccountResponse",
    "DecodeEmvBody",
    "DecodeEmvResponse",
    "DecodeEmvResponseCobLocation",
    "DecodeEmvResponseCobLocationPayload",
    "DecodeEmvResponseCobLocationPayloadAdditionalInfoItem",
    "DecodeEmvResponseCobLocationPayloadCalendar",
    "DecodeEmvResponseCobLocationPayloadDebtor",
    "DecodeEmvResponseCobLocationPayloadValue",
    "DecodeEmvResponseEmv",
    "DecodeEmvResponseEmvAdditionalDataFieldTemplate",
    "DecodeEmvResponseEmvMerchantAccountInformationPix",
    "DecodeEmvResponseEmvUnreservedTemplates",
    "DecodeEmvResponseRecLocation",
    "DecodeEmvResponseRecLocationPayload",
    "DecodeEmvResponseRecLocationPayloadCalendar",
    "DecodeEmvResponseRecLocationPayloadLink",
    "DecodeEmvResponseRecLocationPayloadLinkDebtor",
    "DecodeEmvResponseRecLocationPayloadReceiver",
    "DecodeEmvResponseRecLocationPayloadUpdatesItem",
    "DecodeEmvResponseRecLocationPayloadValue",
    "DeleteAccountRegisterResponse",
    "DeleteApplicationResponse",
    "DeleteChargeResponse",
    "DeleteStaticQrCodeResponse",
    "DeleteSubaccountResponse",
    "DeleteWebhookResponse",
    "Dispute",
    "DisputePayload",
    "DisputePayloadStatus",
    "DisputeStatus",
    "DuplicateAccountResponse",
    "Error",
    "ErrorResponse",
    "File",
    "FileContentType",
    "FileError",
    "FilePayload",
    "FilePurpose",
    "FraudMarkers",
    "FundsRecovery",
    "FundsRecoveryDirection",
    "FundsRecoveryEventsItem",
    "FundsRecoveryPayload",
    "FundsRecoverySituationType",
    "FundsRecoveryStatus",
    "GetAccountLimitsResponse",
    "GetAccountRegisterResponse",
    "GetAccountRegisterResponseTaxId",
    "GetAccountResponse",
    "GetBoletoTransactionResponse",
    "GetCashbackFidelityBalanceResponse",
    "GetChargeQrCodeBase64Response",
    "GetChargeResponse",
    "GetCompanyResponse",
    "GetCompanyResponseCompany",
    "GetCustomerResponse",
    "GetDisputeResponse",
    "GetDisputeResponseDispute",
    "GetDisputeResponseDisputeStatus",
    "GetDisputeResponseDisputeType",
    "GetInstallmentResponse",
    "GetPartnerCompanyResponse",
    "GetPartnerCompanyResponsePreRegistration",
    "GetPaymentResponse",
    "GetReceiptReceiptType",
    "GetRefundResponse",
    "GetStablecoinQuoteResponse",
    "GetStablecoinQuoteResponseQuote",
    "GetStablecoinQuoteResponseQuoteAppliedFeesItem",
    "GetStablecoinSubaccountBalancesResponse",
    "GetStatementResponseItem",
    "GetStaticQrCodeResponse",
    "GetSubaccountResponse",
    "GetSubaccountStatementResponseItem",
    "GetSubaccountStatementResponseItemOperationType",
    "GetSubaccountStatementResponseItemType",
    "GetSubscriptionResponse",
    "GetTransactionResponse",
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
    "KycValidation",
    "KycValidationError",
    "KycValidationReasonsItem",
    "KycValidationRequest",
    "KycValidationResult",
    "KycValidationRiskLevel",
    "KycValidationStatus",
    "ListAccountsResponse",
    "ListAccountsResponsePageInfo",
    "ListAccountsResponsePageInfoErrorsItem",
    "ListAccountsResponsePageInfoErrorsItemData",
    "ListAnticipationRequestsResponse",
    "ListChargeRefundsResponse",
    "ListChargesResponse",
    "ListChargesResponsePageInfo",
    "ListChargesResponsePageInfoErrorsItem",
    "ListChargesResponsePageInfoErrorsItemData",
    "ListCustomersResponse",
    "ListCustomersResponsePageInfo",
    "ListCustomersResponsePageInfoErrorsItem",
    "ListCustomersResponsePageInfoErrorsItemData",
    "ListDisputesResponse",
    "ListDisputesResponseDisputesItem",
    "ListDisputesResponseDisputesItemType",
    "ListDisputesResponsePageInfo",
    "ListDisputesResponsePageInfoErrorsItem",
    "ListDisputesResponsePageInfoErrorsItemData",
    "ListPartnerAffiliatesResponse",
    "ListPartnerAffiliatesResponseAffiliatesItem",
    "ListPartnerAffiliatesResponsePageInfo",
    "ListPartnerAffiliatesResponsePageInfoErrorsItem",
    "ListPartnerAffiliatesResponsePageInfoErrorsItemData",
    "ListPartnerCompaniesResponse",
    "ListPartnerCompaniesResponsePageInfo",
    "ListPartnerCompaniesResponsePageInfoErrorsItem",
    "ListPartnerCompaniesResponsePageInfoErrorsItemData",
    "ListPartnerCompaniesResponsePreRegistrationsItem",
    "ListPaymentsResponse",
    "ListPaymentsResponsePageInfo",
    "ListPaymentsResponsePageInfoErrorsItem",
    "ListPaymentsResponsePageInfoErrorsItemData",
    "ListPaymentsResponsePaymentsItem",
    "ListPixKeyTokenLogsResponse",
    "ListPixKeyTokenLogsResponsePageInfo",
    "ListPixKeyTokenLogsResponsePageInfoErrorsItem",
    "ListPixKeyTokenLogsResponsePageInfoErrorsItemData",
    "ListPixKeysResponse",
    "ListPspsResponse",
    "ListPspsResponsePspsItem",
    "ListRefundsResponse",
    "ListRefundsResponsePageInfo",
    "ListRefundsResponsePageInfoErrorsItem",
    "ListRefundsResponsePageInfoErrorsItemData",
    "ListStablecoinSubaccountWalletsResponse",
    "ListStablecoinSubaccountWalletsResponseWalletsItem",
    "ListStablecoinWalletsResponse",
    "ListStablecoinWalletsResponseWalletsItem",
    "ListStaticQrCodesResponse",
    "ListStaticQrCodesResponsePageInfo",
    "ListStaticQrCodesResponsePageInfoErrorsItem",
    "ListStaticQrCodesResponsePageInfoErrorsItemData",
    "ListSubaccountsResponse",
    "ListSubaccountsResponsePageInfo",
    "ListSubaccountsResponsePageInfoErrorsItem",
    "ListSubaccountsResponsePageInfoErrorsItemData",
    "ListSubaccountsResponseSubaccountsItem",
    "ListSubscriptionInstallmentsResponse",
    "ListSubscriptionsResponse",
    "ListTransactionsResponse",
    "ListTransactionsResponsePageInfo",
    "ListTransactionsResponsePageInfoErrorsItem",
    "ListTransactionsResponsePageInfoErrorsItemData",
    "ListTransactionsType",
    "ListWebhookEventsResponse",
    "ListWebhookEventsResponseEventsItem",
    "ListWebhookIpsResponse",
    "ListWebhookPublicKeysResponse",
    "ListWebhookPublicKeysResponsePublicKeysItem",
    "ListWebhooksResponse",
    "ListWebhooksResponsePageInfo",
    "ListWebhooksResponsePageInfoErrorsItem",
    "ListWebhooksResponsePageInfoErrorsItemData",
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
    "PreRegistrationObject",
    "PreRegistrationObjectPayload",
    "PreRegistrationPayloadObject",
    "PreRegistrationUserObject",
    "Psp",
    "QuoteStablecoinPayoutResponse",
    "QuoteStablecoinPayoutResponseQuote",
    "Refund",
    "RefundChargeResponse",
    "RefundPayload",
    "RefundStatus",
    "RejectAnticipationBody",
    "RejectAnticipationResponse",
    "RetryInstallmentCobrBody",
    "SetInvoiceIntegrationStatusBody",
    "SetInvoiceIntegrationStatusResponse",
    "SetInvoiceIntegrationStatusResponseIntegration",
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
    "TestInvoiceIntegrationResponse",
    "TestInvoiceIntegrationResponseIntegration",
    "TestInvoiceIntegrationResponseInvoice",
    "TokenBucketLog",
    "TokenBucketLogOperation",
    "Transaction",
    "Transaction2",
    "TransactionStatus",
    "TransactionType",
    "TransactionWebhookSentItem",
    "TransferCreatePayload",
    "TransferTransaction",
    "UpdateChargeResponse",
    "UpdateCustomerResponse",
    "UpdateInvoiceIntegrationTaxFieldsBody",
    "UpdateInvoiceIntegrationTaxFieldsResponse",
    "UploadDisputeEvidenceBody",
    "UploadDisputeEvidenceBodyDocumentsItem",
    "UploadDisputeEvidenceResponse",
    "UploadDisputeEvidenceResponseDocumentsItem",
    "UploadInvoiceIntegrationCertificateBody",
    "UploadInvoiceIntegrationCertificateResponse",
    "UploadInvoiceIntegrationCertificateResponseIntegration",
    "UpsertInvoiceIntegrationBody",
    "UpsertInvoiceIntegrationResponse",
    "UpsertInvoiceIntegrationResponseIntegration",
    "UpsertInvoiceIntegrationResponseIntegrationMetadata",
    "UpsertInvoiceIntegrationResponseIntegrationMetadataNfei",
    "Webhook",
    "WebhookAccountRegisterApprovedPayload",
    "WebhookAccountRegisterApprovedPayloadAccount",
    "WebhookAccountRegisterApprovedPayloadAccountRegister",
    "WebhookAccountRegisterApprovedPayloadAccountRegisterTax",
    "WebhookAccountRegisterPendingPayload",
    "WebhookAccountRegisterPendingPayloadAccountRegister",
    "WebhookAccountRegisterPendingPayloadAccountRegisterRequ",
    "WebhookAccountRegisterPendingPayloadAccountRegisterRequ2",
    "WebhookAccountRegisterPendingPayloadAccountRegisterTaxI",
    "WebhookAccountRegisterRejectedPayload",
    "WebhookAccountRegisterRejectedPayloadAccountRegister",
    "WebhookAccountRegisterRejectedPayloadAccountRegisterTax",
    "WebhookBoletoSettledPayload",
    "WebhookBoletoSettledPayloadBoleto",
    "WebhookBoletoSettledPayloadCharge",
    "WebhookCharge",
    "WebhookChargeAdditionalInfoItem",
    "WebhookChargeCustomer",
    "WebhookChargeCustomerTaxId",
    "WebhookChargePayer",
    "WebhookChargePayerTaxId",
    "WebhookCompanyBankAccountBlockedPayload",
    "WebhookCompanyBankAccountBlockedPayloadAccount",
    "WebhookCompanyBankAccountBlockedPayloadAccountTaxId",
    "WebhookCompanyBankAccountBlockedPayloadBlockingsItem",
    "WebhookEventEnum",
    "WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa",
    "WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa2",
    "WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa3",
    "WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa4",
    "WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa5",
    "WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa6",
    "WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa7",
    "WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa8",
    "WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa9",
    "WebhookOpenpixChargeCompletedNotSameCustomerPayerPayloa10",
    "WebhookOpenpixChargeCompletedPayload",
    "WebhookOpenpixChargeCompletedPayloadAccount",
    "WebhookOpenpixChargeCompletedPayloadCompany",
    "WebhookOpenpixChargeCompletedPayloadPix",
    "WebhookOpenpixChargeCompletedPayloadPixCustomer",
    "WebhookOpenpixChargeCompletedPayloadPixCustomerTaxId",
    "WebhookOpenpixChargeCompletedPayloadPixPayer",
    "WebhookOpenpixChargeCompletedPayloadPixPayerTaxId",
    "WebhookOpenpixChargeCreatedPayload",
    "WebhookOpenpixChargeCreatedPayloadAccount",
    "WebhookOpenpixChargeCreatedPayloadCompany",
    "WebhookOpenpixChargeExpiredPayload",
    "WebhookOpenpixChargeExpiredPayloadAccount",
    "WebhookOpenpixChargeExpiredPayloadCompany",
    "WebhookOpenpixDisputeAcceptedPayload",
    "WebhookOpenpixDisputeAcceptedPayloadDispute",
    "WebhookOpenpixDisputeCanceledPayload",
    "WebhookOpenpixDisputeCanceledPayloadDispute",
    "WebhookOpenpixDisputeCreatedPayload",
    "WebhookOpenpixDisputeCreatedPayloadDispute",
    "WebhookOpenpixDisputeRejectedPayload",
    "WebhookOpenpixDisputeRejectedPayloadDispute",
    "WebhookOpenpixMovementConfirmedPayload",
    "WebhookOpenpixMovementConfirmedPayloadPayment",
    "WebhookOpenpixMovementConfirmedPayloadTransaction",
    "WebhookOpenpixMovementFailedPayload",
    "WebhookOpenpixMovementFailedPayloadError",
    "WebhookOpenpixMovementFailedPayloadPayment",
    "WebhookOpenpixMovementFailedPayloadTransaction",
    "WebhookOpenpixMovementRemovedPayload",
    "WebhookOpenpixMovementRemovedPayloadPayment",
    "WebhookOpenpixTransactionReceivedPayload",
    "WebhookOpenpixTransactionReceivedPayloadAccount",
    "WebhookOpenpixTransactionReceivedPayloadCompany",
    "WebhookOpenpixTransactionReceivedPayloadPix",
    "WebhookOpenpixTransactionReceivedPayloadPixCreditParty",
    "WebhookOpenpixTransactionReceivedPayloadPixCreditPartyA",
    "WebhookOpenpixTransactionReceivedPayloadPixCreditPartyH",
    "WebhookOpenpixTransactionReceivedPayloadPixCreditPartyH2",
    "WebhookOpenpixTransactionReceivedPayloadPixCreditPartyP",
    "WebhookOpenpixTransactionReceivedPayloadPixCreditPartyP2",
    "WebhookOpenpixTransactionReceivedPayloadPixDebitParty",
    "WebhookOpenpixTransactionReceivedPayloadPixDebitPartyAc",
    "WebhookOpenpixTransactionReceivedPayloadPixDebitPartyHo",
    "WebhookOpenpixTransactionReceivedPayloadPixDebitPartyHo2",
    "WebhookOpenpixTransactionReceivedPayloadPixDebitPartyPs",
    "WebhookOpenpixTransactionReceivedPayloadPixPayer",
    "WebhookOpenpixTransactionReceivedPayloadPixPayerTaxId",
    "WebhookOpenpixTransactionRefundReceivedPayload",
    "WebhookOpenpixTransactionRefundReceivedPayloadAccount",
    "WebhookOpenpixTransactionRefundReceivedPayloadCompany",
    "WebhookOpenpixTransactionRefundReceivedPayloadPix",
    "WebhookPayload",
    "WebhookPixAutomaticApprovedPayload",
    "WebhookPixAutomaticApprovedPayloadCustomer",
    "WebhookPixAutomaticApprovedPayloadCustomerAddress",
    "WebhookPixAutomaticApprovedPayloadCustomerAddressLocati",
    "WebhookPixAutomaticApprovedPayloadCustomerTaxId",
    "WebhookPixAutomaticApprovedPayloadPixRecurring",
    "WebhookPixAutomaticCobrApprovedPayload",
    "WebhookPixAutomaticCobrApprovedPayloadCobr",
    "WebhookPixAutomaticCobrApprovedPayloadCobrTriesItem",
    "WebhookPixAutomaticCobrCompletedPayload",
    "WebhookPixAutomaticCobrCompletedPayloadCobr",
    "WebhookPixAutomaticCobrCompletedPayloadCobrTriesItem",
    "WebhookPixAutomaticCobrCreatedPayload",
    "WebhookPixAutomaticCobrCreatedPayloadCobr",
    "WebhookPixAutomaticCobrCreatedPayloadCobrTriesItem",
    "WebhookPixAutomaticCobrRejectedPayload",
    "WebhookPixAutomaticCobrRejectedPayloadCobr",
    "WebhookPixAutomaticCobrRejectedPayloadCobrTriesItem",
    "WebhookPixAutomaticCobrTryRejectedPayload",
    "WebhookPixAutomaticCobrTryRejectedPayloadCobr",
    "WebhookPixAutomaticCobrTryRejectedPayloadCobrTriesItem",
    "WebhookPixAutomaticCobrTryRequestedPayload",
    "WebhookPixAutomaticCobrTryRequestedPayloadCobr",
    "WebhookPixAutomaticCobrTryRequestedPayloadCobrTriesItem",
    "WebhookPixAutomaticRejectedPayload",
    "WebhookPixAutomaticRejectedPayloadCustomer",
    "WebhookPixAutomaticRejectedPayloadCustomerAddress",
    "WebhookPixAutomaticRejectedPayloadCustomerAddressLocati",
    "WebhookPixAutomaticRejectedPayloadCustomerTaxId",
    "WebhookPixAutomaticRejectedPayloadPixRecurring",
    "WebhookPixTransactionRefundReceivedConfirmedPayload",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadAcco",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadComp",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig2",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig3",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig4",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig5",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig6",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig7",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig8",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig9",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig10",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig11",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig12",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig13",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadOrig14",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadRefu",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadRefu2",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadRefu3",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadRefu4",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadRefu5",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadRefu6",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadRefu7",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadRefu8",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadRefu9",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadRefu10",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadRefu11",
    "WebhookPixTransactionRefundReceivedConfirmedPayloadRefu12",
    "WebhookPixTransactionRefundReceivedRejectedPayload",
    "WebhookPixTransactionRefundReceivedRejectedPayloadAccou",
    "WebhookPixTransactionRefundReceivedRejectedPayloadCompa",
    "WebhookPixTransactionRefundReceivedRejectedPayloadOrigi",
    "WebhookPixTransactionRefundReceivedRejectedPayloadOrigi2",
    "WebhookPixTransactionRefundReceivedRejectedPayloadOrigi3",
    "WebhookPixTransactionRefundReceivedRejectedPayloadOrigi4",
    "WebhookPixTransactionRefundReceivedRejectedPayloadOrigi5",
    "WebhookPixTransactionRefundReceivedRejectedPayloadOrigi6",
    "WebhookPixTransactionRefundReceivedRejectedPayloadOrigi7",
    "WebhookPixTransactionRefundReceivedRejectedPayloadOrigi8",
    "WebhookPixTransactionRefundReceivedRejectedPayloadOrigi9",
    "WebhookPixTransactionRefundReceivedRejectedPayloadOrigi10",
    "WebhookPixTransactionRefundReceivedRejectedPayloadOrigi11",
    "WebhookPixTransactionRefundReceivedRejectedPayloadOrigi12",
    "WebhookPixTransactionRefundReceivedRejectedPayloadRefun",
    "WebhookPixTransactionRefundReceivedRejectedPayloadRefun2",
    "WebhookPixTransactionRefundReceivedRejectedPayloadRefun3",
    "WebhookPixTransactionRefundReceivedRejectedPayloadRefun4",
    "WebhookPixTransactionRefundReceivedRejectedPayloadRefun5",
    "WebhookPixTransactionRefundReceivedRejectedPayloadRefun6",
    "WebhookPixTransactionRefundReceivedRejectedPayloadRefun7",
    "WebhookPixTransactionRefundReceivedRejectedPayloadRefun8",
    "WebhookPixTransactionRefundReceivedRejectedPayloadRefun9",
    "WebhookPixTransactionRefundReceivedRejectedPayloadRefun10",
    "WebhookPixTransactionRefundReceivedRejectedPayloadRefun11",
    "WebhookPixTransactionRefundReceivedRejectedPayloadRefun12",
    "WebhookPixTransactionRefundSentConfirmedPayload",
    "WebhookPixTransactionRefundSentConfirmedPayloadAccount",
    "WebhookPixTransactionRefundSentConfirmedPayloadCompany",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal2",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal3",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal4",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal5",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal6",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal7",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal8",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal9",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal10",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal11",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal12",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal13",
    "WebhookPixTransactionRefundSentConfirmedPayloadOriginal14",
    "WebhookPixTransactionRefundSentConfirmedPayloadRefundTr",
    "WebhookPixTransactionRefundSentConfirmedPayloadRefundTr2",
    "WebhookPixTransactionRefundSentConfirmedPayloadRefundTr3",
    "WebhookPixTransactionRefundSentConfirmedPayloadRefundTr4",
    "WebhookPixTransactionRefundSentConfirmedPayloadRefundTr5",
    "WebhookPixTransactionRefundSentConfirmedPayloadRefundTr6",
    "WebhookPixTransactionRefundSentConfirmedPayloadRefundTr7",
    "WebhookPixTransactionRefundSentConfirmedPayloadRefundTr8",
    "WebhookPixTransactionRefundSentConfirmedPayloadRefundTr9",
    "WebhookPixTransactionRefundSentConfirmedPayloadRefundTr10",
    "WebhookPixTransactionRefundSentConfirmedPayloadRefundTr11",
    "WebhookPixTransactionRefundSentConfirmedPayloadRefundTr12",
    "WebhookPixTransactionRefundSentRejectedPayload",
    "WebhookPixTransactionRefundSentRejectedPayloadAccount",
    "WebhookPixTransactionRefundSentRejectedPayloadCompany",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT2",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT3",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT4",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT5",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT6",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT7",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT8",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT9",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT10",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT11",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT12",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT13",
    "WebhookPixTransactionRefundSentRejectedPayloadOriginalT14",
    "WebhookPixTransactionRefundSentRejectedPayloadRefundTra",
    "WebhookPixTransactionRefundSentRejectedPayloadRefundTra2",
    "WebhookPixTransactionRefundSentRejectedPayloadRefundTra3",
    "WebhookPixTransactionRefundSentRejectedPayloadRefundTra4",
    "WebhookPixTransactionRefundSentRejectedPayloadRefundTra5",
    "WebhookPixTransactionRefundSentRejectedPayloadRefundTra6",
    "WebhookPixTransactionRefundSentRejectedPayloadRefundTra7",
    "WebhookPixTransactionRefundSentRejectedPayloadRefundTra8",
    "WebhookPixTransactionRefundSentRejectedPayloadRefundTra9",
    "WebhookPixTransactionRefundSentRejectedPayloadRefundTra10",
    "WebhookPixTransactionRefundSentRejectedPayloadRefundTra11",
    "WebhookPixTransactionRefundSentRejectedPayloadRefundTra12",
    "WebhookStablecoinDepositCompletedPayload",
    "WebhookStablecoinDepositCompletedPayloadCompany",
    "WebhookStablecoinDepositCompletedPayloadStableDeposit",
    "WebhookStablecoinDepositFailedPayload",
    "WebhookStablecoinDepositFailedPayloadCompany",
    "WebhookStablecoinDepositFailedPayloadStableDeposit",
    "WebhookStablecoinPayoutCompletedPayload",
    "WebhookStablecoinPayoutCompletedPayloadCompany",
    "WebhookStablecoinPayoutCompletedPayloadStablePayout",
    "WebhookStablecoinPayoutFailedPayload",
    "WebhookStablecoinPayoutFailedPayloadCompany",
    "WebhookStablecoinPayoutFailedPayloadStablePayout",
    "WebhookStablecoinPayoutRefundConfirmedPayload",
    "WebhookStablecoinPayoutRefundConfirmedPayloadCompany",
    "WebhookStablecoinPayoutRefundConfirmedPayloadRefund",
    "WebhookStablecoinPayoutRefundConfirmedPayloadRefundDest",
    "WebhookStablecoinPayoutRefundConfirmedPayloadRefundStat",
    "WebhookStablecoinPayoutRefundConfirmedPayloadStablePayo",
    "WebhookStablecoinPayoutRefundFailedPayload",
    "WebhookStablecoinPayoutRefundFailedPayloadCompany",
    "WebhookStablecoinPayoutRefundFailedPayloadRefund",
    "WebhookStablecoinPayoutRefundFailedPayloadStablePayout",
    "WebhookStablecoinSubaccountConfirmedPayload",
    "WebhookStablecoinSubaccountConfirmedPayloadCompany",
    "WebhookStablecoinSubaccountConfirmedPayloadStableSubAcc",
    "WebhookStablecoinSubaccountRejectedPayload",
    "WebhookStablecoinSubaccountRejectedPayloadCompany",
    "WebhookStablecoinSubaccountRejectedPayloadStableSubAcco",
    "WithdrawFromAccountBody",
    "WithdrawFromAccountResponse",
    "WithdrawFromAccountResponseWithdraw",
    "WithdrawFromSubaccountResponse",
    "WithdrawFromSubaccountResponseWithdraw",
    "WithdrawTransaction",
]
