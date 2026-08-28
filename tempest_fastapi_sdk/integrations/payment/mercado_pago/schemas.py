"""Pydantic schemas generated from the MercadoPago API OpenAPI specification.

Do not edit by hand — rerun `tempest openapi-client` to refresh.

Field names are Python-idiomatic; the wire name is attached as a
Pydantic ``alias`` whenever the two differ, and every model enables
``populate_by_name`` so both spellings are accepted on input. Call
``model_dump(by_alias=True)`` to serialize back to the wire shape.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, EmailStr, Field

from tempest_fastapi_sdk import BaseSchema, BaseStrEnum


class AuthorizedPaymentStatus(BaseStrEnum):
    """Allowed values for AuthorizedPaymentStatus."""

    SCHEDULED = "scheduled"
    PROCESSED = "processed"
    RECYCLING = "recycling"
    CANCELLED = "cancelled"


class AutoRecurringFrequencyType(BaseStrEnum):
    """Allowed values for AutoRecurringFrequencyType."""

    MONTHS = "months"
    DAYS = "days"


class BankAccountAccountType(BaseStrEnum):
    """Allowed values for BankAccountAccountType."""

    CHECKING = "checking"
    SAVINGS = "savings"


class CardSecurityCodeMode(BaseStrEnum):
    """Allowed values for CardSecurityCodeMode."""

    MANDATORY = "mandatory"
    OPTIONAL = "optional"


class CardTokenStatus(BaseStrEnum):
    """Allowed values for CardTokenStatus."""

    ACTIVE = "active"
    USED = "used"
    EXPIRED = "expired"


class ClaimEvidenceType(BaseStrEnum):
    """Allowed values for ClaimEvidenceType."""

    TRACKING_CODE = "tracking_code"
    PHOTO = "photo"
    INVOICE = "invoice"
    OTHER = "other"


class ClaimHistoryEntryChangedBy(BaseStrEnum):
    """Allowed values for ClaimHistoryEntryChangedBy."""

    BUYER = "buyer"
    SELLER = "seller"
    SYSTEM = "system"
    MEDIATOR = "mediator"


class ClaimMessageFromRole(BaseStrEnum):
    """Allowed values for ClaimMessageFromRole."""

    COMPLAINANT = "complainant"
    RESPONDENT = "respondent"
    MEDIATOR = "mediator"


class ClaimPlayersItemRole(BaseStrEnum):
    """Allowed values for ClaimPlayersItemRole."""

    COMPLAINANT = "complainant"
    RESPONDENT = "respondent"


class ClaimResource(BaseStrEnum):
    """Allowed values for ClaimResource."""

    PAYMENT = "payment"
    SHIPMENT = "shipment"
    ORDER = "order"


class ClaimStage(BaseStrEnum):
    """Allowed values for ClaimStage."""

    CLAIM = "claim"
    DISPUTE = "dispute"
    RESOLUTION = "resolution"


class ClaimStatus(BaseStrEnum):
    """Allowed values for ClaimStatus."""

    OPENED = "opened"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class ClaimType(BaseStrEnum):
    """Allowed values for ClaimType."""

    MEDIATIONS = "mediations"
    CLAIMS = "claims"


class ConfirmCashoutQrBodyStatus(BaseStrEnum):
    """Allowed values for ConfirmCashoutQrBodyStatus."""

    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class CreateMerchantOrderBodySiteId(BaseStrEnum):
    """Allowed values for CreateMerchantOrderBodySiteId."""

    MLA = "MLA"
    MLB = "MLB"
    MLM = "MLM"
    MLC = "MLC"
    MCO = "MCO"
    MPE = "MPE"
    MLU = "MLU"


class CreateMerchantOrderResponseOrderStatus(BaseStrEnum):
    """Allowed values for CreateMerchantOrderResponseOrderStatus."""

    PAYMENT_REQUIRED = "payment_required"
    PAYMENT_IN_PROCESS = "payment_in_process"
    PAID = "paid"
    PARTIALLY_PAID = "partially_paid"
    PARTIALLY_REFUNDED = "partially_refunded"
    PENDING_CANCEL = "pending_cancel"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class CreateOAuthTokenBodyGrantType(BaseStrEnum):
    """Allowed values for CreateOAuthTokenBodyGrantType."""

    AUTHORIZATION_CODE = "authorization_code"
    REFRESH_TOKEN = "refresh_token"
    CLIENT_CREDENTIALS = "client_credentials"


class CreatePointPaymentIntentBodyPaymentType(BaseStrEnum):
    """Allowed values for CreatePointPaymentIntentBodyPaymentType."""

    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"


class CreatePointPaymentIntentResponseStatus(BaseStrEnum):
    """Allowed values for CreatePointPaymentIntentResponseStatus."""

    OPEN = "open"
    ON_TERMINAL = "on_terminal"
    PROCESSING = "processing"
    PROCESSED = "processed"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"
    ERROR = "error"


class CreateTerminalActionBodyType(BaseStrEnum):
    """Allowed values for CreateTerminalActionBodyType."""

    PRINT_INFO = "PRINT_INFO"
    PRINT_DTE = "PRINT_DTE"


class CreateTerminalActionResponseStatus(BaseStrEnum):
    """Allowed values for CreateTerminalActionResponseStatus."""

    PENDING = "pending"
    SENT = "sent"
    PRINTED = "printed"
    FAILED = "failed"


class CurrencyId(BaseStrEnum):
    """Allowed values for CurrencyId."""

    ARS = "ARS"
    BRL = "BRL"
    MXN = "MXN"
    CLP = "CLP"
    COP = "COP"
    PEN = "PEN"
    UYU = "UYU"
    BOB = "BOB"
    PYG = "PYG"
    USD = "USD"
    CRC = "CRC"
    DOP = "DOP"
    HNL = "HNL"
    NIO = "NIO"
    GTQ = "GTQ"
    VES = "VES"


class CustomerResponseStatus(BaseStrEnum):
    """Allowed values for CustomerResponseStatus."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


class DigitalWalletProvider(BaseStrEnum):
    """Allowed values for DigitalWalletProvider."""

    PAYPAL = "paypal"
    APPLE_PAY = "apple_pay"
    GOOGLE_PAY = "google_pay"
    VENMO = "venmo"


class ExportSubscriptionsSort(BaseStrEnum):
    """Allowed values for ExportSubscriptionsSort."""

    DATE_CREATED = "date_created"
    LAST_MODIFIED = "last_modified"


class GetChargebackResponseDocumentationStatus(BaseStrEnum):
    """Allowed values for GetChargebackResponseDocumentationStatus."""

    VALID = "valid"
    INVALID = "invalid"
    NOT_SUPPLIED = "not_supplied"


class GetWalletAgreementResponseSiteId(BaseStrEnum):
    """Allowed values for GetWalletAgreementResponseSiteId."""

    MLA = "MLA"
    MLB = "MLB"
    MLM = "MLM"


class GetWalletAgreementResponseStatus(BaseStrEnum):
    """Allowed values for GetWalletAgreementResponseStatus."""

    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ListIdentificationTypesResponseItemType(BaseStrEnum):
    """Allowed values for ListIdentificationTypesResponseItemType."""

    NUMBER = "number"
    LETTER = "letter"


class ListPaymentMethodsResponseItemDeferredCapture(BaseStrEnum):
    """Allowed values for ListPaymentMethodsResponseItemDeferredCapture."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    DOES_NOT_APPLY = "does_not_apply"


class ListPaymentMethodsResponseItemStatus(BaseStrEnum):
    """Allowed values for ListPaymentMethodsResponseItemStatus."""

    ACTIVE = "active"
    DEACTIVATED = "deactivated"


class ListPointDevicesResponseDevicesItemOperatingMode(BaseStrEnum):
    """Allowed values for ListPointDevicesResponseDevicesItemOperatingMode."""

    PDV = "PDV"
    STANDALONE = "STANDALONE"


class MediationResolutionType(BaseStrEnum):
    """Allowed values for MediationResolutionType."""

    REFUND = "refund"
    RETURN_ = "return"
    PARTIAL_REFUND = "partial_refund"


class MerchantOrderStatus(BaseStrEnum):
    """Allowed values for MerchantOrderStatus."""

    OPENED = "opened"
    CLOSED = "closed"
    EXPIRED = "expired"


class MerchantResponseStatus(BaseStrEnum):
    """Allowed values for MerchantResponseStatus."""

    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"


class OrderConfigOnlineTransactionSecurityLiabilityShift(BaseStrEnum):
    """Allowed values for OrderConfigOnlineTransactionSecurityLiabilityShift."""

    REQUIRED = "required"
    NOT_REQUIRED = "not_required"


class OrderConfigOnlineTransactionSecurityValidation(BaseStrEnum):
    """Allowed values for OrderConfigOnlineTransactionSecurityValidation."""

    SUPPORTED = "supported"
    REQUIRED = "required"
    NEVER = "never"


class OrderPayerEntityType(BaseStrEnum):
    """Allowed values for OrderPayerEntityType."""

    INDIVIDUAL = "individual"
    ASSOCIATION = "association"


class OrderPaymentMethodType(BaseStrEnum):
    """Allowed values for OrderPaymentMethodType."""

    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    BANK_TRANSFER = "bank_transfer"
    TICKET = "ticket"
    DIGITAL_WALLET = "digital_wallet"
    ACCOUNT_MONEY = "account_money"


class OrderRequestCaptureMode(BaseStrEnum):
    """Allowed values for OrderRequestCaptureMode."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"
    AUTOMATIC_ASYNC = "automatic_async"


class OrderRequestProcessingMode(BaseStrEnum):
    """Allowed values for OrderRequestProcessingMode."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"


class OrderRequestType(BaseStrEnum):
    """Allowed values for OrderRequestType."""

    ONLINE = "online"


class OrderStatus(BaseStrEnum):
    """Allowed values for OrderStatus."""

    CREATED = "created"
    PROCESSED = "processed"
    ACTION_REQUIRED = "action_required"
    PROCESSING = "processing"
    CANCELED = "canceled"


class OrderStatusDetail(BaseStrEnum):
    """Allowed values for OrderStatusDetail."""

    CREATED = "created"
    ACCREDITED = "accredited"
    IN_PROCESS = "in_process"
    IN_REVIEW = "in_review"
    WAITING_PAYMENT = "waiting_payment"
    WAITING_CAPTURE = "waiting_capture"
    WAITING_TRANSFER = "waiting_transfer"


class OrderTransactionPaymentPaymentMethodTransactionSecurity2(BaseStrEnum):
    """Allowed values for
    OrderTransactionPaymentPaymentMethodTransactionSecurityStatus.
    """

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class OrderTransactionPaymentStatus(BaseStrEnum):
    """Allowed values for OrderTransactionPaymentStatus."""

    CREATED = "created"
    PROCESSED = "processed"
    ACTION_REQUIRED = "action_required"
    PROCESSING = "processing"


class OrderTransactionPaymentStatusDetail(BaseStrEnum):
    """Allowed values for OrderTransactionPaymentStatusDetail."""

    ACCREDITED = "accredited"
    WAITING_CAPTURE = "waiting_capture"
    CREATED = "created"
    PENDING_REVIEW_MANUAL = "pending_review_manual"
    IN_PROCESS = "in_process"


class PayerType(BaseStrEnum):
    """Allowed values for PayerType."""

    CUSTOMER = "customer"
    REGISTERED = "registered"
    GUEST = "guest"


class PaymentMethodType(BaseStrEnum):
    """Allowed values for PaymentMethodType."""

    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    BANK_TRANSFER = "bank_transfer"
    DIGITAL_WALLET = "digital_wallet"
    CRYPTOCURRENCY = "cryptocurrency"


class PaymentOperationType(BaseStrEnum):
    """Allowed values for PaymentOperationType."""

    REGULAR_PAYMENT = "regular_payment"
    MONEY_TRANSFER = "money_transfer"
    RECURRING_PAYMENT = "recurring_payment"
    ACCOUNT_FUND = "account_fund"
    PAYMENT_ADDITION = "payment_addition"
    CELLPHONE_RECHARGE = "cellphone_recharge"
    POS_PAYMENT = "pos_payment"
    MONEY_EXCHANGE = "money_exchange"


class PaymentPaymentTypeId(BaseStrEnum):
    """Allowed values for PaymentPaymentTypeId."""

    ACCOUNT_MONEY = "account_money"
    TICKET = "ticket"
    BANK_TRANSFER = "bank_transfer"
    ATM = "atm"
    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    PREPAID_CARD = "prepaid_card"
    DIGITAL_CURRENCY = "digital_currency"
    DIGITAL_WALLET = "digital_wallet"
    VOUCHER_CARD = "voucher_card"
    CRYPTO = "crypto"
    PIX = "pix"


class PaymentProcessingMode(BaseStrEnum):
    """Allowed values for PaymentProcessingMode."""

    AGGREGATOR = "aggregator"
    GATEWAY = "gateway"


class PaymentResponseStatus(BaseStrEnum):
    """Allowed values for PaymentResponseStatus."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaymentStatus(BaseStrEnum):
    """Allowed values for PaymentStatus."""

    PENDING = "pending"
    APPROVED = "approved"
    AUTHORIZED = "authorized"
    IN_PROCESS = "in_process"
    IN_MEDIATION = "in_mediation"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    CHARGED_BACK = "charged_back"


class PaymentUpdateRequestStatus(BaseStrEnum):
    """Allowed values for PaymentUpdateRequestStatus."""

    CANCELLED = "cancelled"


class PreferenceRequestAutoReturn(BaseStrEnum):
    """Allowed values for PreferenceRequestAutoReturn."""

    APPROVED = "approved"
    ALL = "all"


class PreferenceShipmentsMode(BaseStrEnum):
    """Allowed values for PreferenceShipmentsMode."""

    CUSTOM = "custom"
    ME2 = "me2"
    NOT_SPECIFIED = "not_specified"


class RefundRefundMode(BaseStrEnum):
    """Allowed values for RefundRefundMode."""

    STANDARD = "standard"
    INSTANT = "instant"


class RefundResponseStatus(BaseStrEnum):
    """Allowed values for RefundResponseStatus."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class RefundStatus(BaseStrEnum):
    """Allowed values for RefundStatus."""

    APPROVED = "approved"
    IN_PROCESS = "in_process"
    REJECTED = "rejected"


class ReportConfigFrequencyType(BaseStrEnum):
    """Allowed values for ReportConfigFrequencyType."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ReportConfigSeparator(BaseStrEnum):
    """Allowed values for ReportConfigSeparator."""

    VALUE = ","
    VALUE_2 = ";"
    EMPTY = ""


class ReportEntryStatus(BaseStrEnum):
    """Allowed values for ReportEntryStatus."""

    AVAILABLE = "available"
    EXPIRED = "expired"


class ReportTaskStatus(BaseStrEnum):
    """Allowed values for ReportTaskStatus."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class SearchPaymentsRange(BaseStrEnum):
    """Allowed values for SearchPaymentsRange."""

    DATE_CREATED = "date_created"
    DATE_LAST_UPDATED = "date_last_updated"
    MONEY_RELEASE_DATE = "money_release_date"


class SearchSubscriptionPlansCriteria(BaseStrEnum):
    """Allowed values for SearchSubscriptionPlansCriteria."""

    ASC = "asc"
    DESC = "desc"


class SubscriptionPlanStatus(BaseStrEnum):
    """Allowed values for SubscriptionPlanStatus."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class SubscriptionRequestStatus(BaseStrEnum):
    """Allowed values for SubscriptionRequestStatus."""

    PENDING = "pending"
    AUTHORIZED = "authorized"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class SubscriptionResponseFrequency(BaseStrEnum):
    """Allowed values for SubscriptionResponseFrequency."""

    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class SubscriptionResponseStatus(BaseStrEnum):
    """Allowed values for SubscriptionResponseStatus."""

    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class UploadShippingEvidenceBodyType(BaseStrEnum):
    """Allowed values for UploadShippingEvidenceBodyType."""

    TRACKING_CODE = "tracking_code"
    PROOF_OF_DELIVERY = "proof_of_delivery"


class ValidateWalletCouponResponseStatus(BaseStrEnum):
    """Allowed values for ValidateWalletCouponResponseStatus."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    EXPIRED = "expired"


class WebhookEventEventType(BaseStrEnum):
    """Allowed values for WebhookEventEventType."""

    PAYMENT_COMPLETED = "payment.completed"
    PAYMENT_FAILED = "payment.failed"
    REFUND_PROCESSED = "refund.processed"
    SUBSCRIPTION_CREATED = "subscription.created"
    SUBSCRIPTION_CANCELLED = "subscription.cancelled"


class WebhookNotificationAction(BaseStrEnum):
    """Allowed values for WebhookNotificationAction."""

    PAYMENT_CREATED = "payment.created"
    PAYMENT_UPDATED = "payment.updated"
    SUBSCRIPTION_AUTHORIZED = "subscription.authorized"
    SUBSCRIPTION_CANCELLED = "subscription.cancelled"
    ORDER_CREATED = "order.created"
    ORDER_UPDATED = "order.updated"
    POINT_INTEGRATION_WH = "point_integration_wh"


class WebhookNotificationType(BaseStrEnum):
    """Allowed values for WebhookNotificationType."""

    PAYMENT = "payment"
    MERCHANT_ORDER = "merchant_order"
    SUBSCRIPTION_PREAPPROVAL = "subscription_preapproval"
    SUBSCRIPTION_PREAPPROVAL_PLAN = "subscription_preapproval_plan"
    SUBSCRIPTION_AUTHORIZED_PAYMENT = "subscription_authorized_payment"
    POINT_INTEGRATION_WH = "point_integration_wh"
    CHARGEBACKS = "chargebacks"
    DELIVERY = "delivery"


class Address(BaseSchema):
    """Schema generated for Address.

    Attributes:
        zip_code (str | None): Undocumented in the spec.
        street_name (str | None): Undocumented in the spec.
        street_number (str | None): Undocumented in the spec.
        city (str | None): Undocumented in the spec.
        state (str | None): Undocumented in the spec.
        country (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    zip_code: str | None = Field(examples=["01310-100"], default=None)
    street_name: str | None = Field(examples=["Av. Paulista"], default=None)
    street_number: str | None = Field(examples=["1000"], default=None)
    city: str | None = None
    state: str | None = None
    country: str | None = None


class AttachClaimFileResponse(BaseSchema):
    """Schema generated for AttachClaimFileResponse.

    Attributes:
        file_id (str | None): Undocumented in the spec.
        file_name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    file_id: str | None = None
    file_name: str | None = None


class AuthorizedPaymentPayment(BaseSchema):
    """Schema generated for AuthorizedPaymentPayment.

    Attributes:
        id (int | None): Linked payment ID in /v1/payments
        status (str | None): Undocumented in the spec.
        status_detail (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = Field(
        description="Linked payment ID in /v1/payments",
        default=None,
    )
    status: str | None = None
    status_detail: str | None = None


class AutoRecurringFreeTrial(BaseSchema):
    """Optional free trial period before billing starts.

    Attributes:
        frequency (int | None): Undocumented in the spec.
        frequency_type (AutoRecurringFrequencyType | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    frequency: int | None = Field(examples=[14], default=None)
    frequency_type: AutoRecurringFrequencyType | None = Field(
        examples=["days"],
        default=None,
    )


class BankAccount(BaseSchema):
    """Schema generated for BankAccount.

    Attributes:
        account_number (str): Bank account number
        routing_number (str): Bank routing number
        account_type (BankAccountAccountType | None): Type of bank account
    """

    account_number: str = Field(description="Bank account number")
    routing_number: str = Field(description="Bank routing number")
    account_type: BankAccountAccountType | None = Field(
        description="Type of bank account",
        default=None,
    )


class CancelPaymentBody(BaseSchema):
    """Schema generated for CancelPaymentBody.

    Attributes:
        status (PaymentUpdateRequestStatus): Must be "cancelled"
    """

    status: PaymentUpdateRequestStatus = Field(description='Must be "cancelled"')


class CaptureOrderResponse(BaseSchema):
    """Schema generated for CaptureOrderResponse.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        status_detail (str | None): Undocumented in the spec.
        transactions (dict[str, Any] | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    status: str | None = None
    status_detail: str | None = None
    transactions: dict[str, Any] | None = None


class CardDetails(BaseSchema):
    """Schema generated for CardDetails.

    Attributes:
        number (str): Card number
        expiry_month (int): Card expiry month
        expiry_year (int): Card expiry year
        cvv (str): Card verification value
        cardholder_name (str | None): Name on the card
    """

    number: str = Field(
        description="Card number",
        examples=["4111111111111111"],
        pattern="^[0-9]{13,19}$",
    )
    expiry_month: int = Field(
        description="Card expiry month",
        examples=[12],
        ge=1,
        le=12,
    )
    expiry_year: int = Field(description="Card expiry year", examples=[2026], ge=2024)
    cvv: str = Field(
        description="Card verification value",
        examples=["123"],
        pattern="^[0-9]{3,4}$",
    )
    cardholder_name: str | None = Field(
        description="Name on the card",
        examples=["John Doe"],
        max_length=100,
        default=None,
    )


class CardIssuer(BaseSchema):
    """Schema generated for CardIssuer.

    Attributes:
        id (int | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    name: str | None = None


class CardPaymentMethod(BaseSchema):
    """Schema generated for CardPaymentMethod.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        payment_type_id (str | None): Undocumented in the spec.
        thumbnail (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(examples=["visa"], default=None)
    name: str | None = Field(examples=["Visa"], default=None)
    payment_type_id: str | None = Field(examples=["credit_card"], default=None)
    thumbnail: str | None = None


class CardSecurityCode(BaseSchema):
    """Schema generated for CardSecurityCode.

    Attributes:
        mode (CardSecurityCodeMode | None): Undocumented in the spec.
        length (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    mode: CardSecurityCodeMode | None = None
    length: int | None = Field(examples=[3], default=None)


class ClaimEvidence(BaseSchema):
    """Schema generated for ClaimEvidence.

    Attributes:
        id (str | None): Undocumented in the spec.
        type (ClaimEvidenceType | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        file_name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    type: ClaimEvidenceType | None = None
    date_created: datetime | None = None
    file_name: str | None = None


class ClaimHistoryEntry(BaseSchema):
    """Schema generated for ClaimHistoryEntry.

    Attributes:
        date (datetime | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        stage (str | None): Undocumented in the spec.
        changed_by (ClaimHistoryEntryChangedBy | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    date: datetime | None = None
    status: str | None = None
    stage: str | None = None
    changed_by: ClaimHistoryEntryChangedBy | None = None


class ClaimMessageAttachmentsItem(BaseSchema):
    """Schema generated for ClaimMessageAttachmentsItem.

    Attributes:
        file_name (str | None): Undocumented in the spec.
        file_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    file_name: str | None = None
    file_id: str | None = None


class ClaimMessageFrom(BaseSchema):
    """Schema generated for ClaimMessageFrom.

    Attributes:
        user_id (int | None): Undocumented in the spec.
        role (ClaimMessageFromRole | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    user_id: int | None = None
    role: ClaimMessageFromRole | None = None


class ClaimPlayersItem(BaseSchema):
    """Schema generated for ClaimPlayersItem.

    Attributes:
        role (ClaimPlayersItemRole | None): Undocumented in the spec.
        user_id (int | None): Undocumented in the spec.
        available_actions (list[str]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    role: ClaimPlayersItemRole | None = None
    user_id: int | None = None
    available_actions: list[str] = Field(default_factory=list)


class ClaimReason(BaseSchema):
    """A reason code for opening a claim.

    Attributes:
        id (str | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        type (ClaimType | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(examples=["PNR"], default=None)
    description: str | None = Field(examples=["Product not received"], default=None)
    type: ClaimType | None = None


class ConfirmCashoutQrBody(BaseSchema):
    """Schema generated for ConfirmCashoutQrBody.

    Attributes:
        status (ConfirmCashoutQrBodyStatus | None): Undocumented in the spec.
    """

    status: ConfirmCashoutQrBodyStatus | None = None


class CreateAdvancedPaymentBodyPayer(BaseSchema):
    """Schema generated for CreateAdvancedPaymentBodyPayer.

    Attributes:
        token (str | None): Undocumented in the spec.
        type_token (str | None): Undocumented in the spec.
    """

    token: str | None = None
    type_token: str | None = None


class CreateAdvancedPaymentBodyWalletPayment(BaseSchema):
    """Schema generated for CreateAdvancedPaymentBodyWalletPayment.

    Attributes:
        transaction_amount (float | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        external_reference (str | None): Undocumented in the spec.
        statement_descriptor (str | None): Undocumented in the spec.
    """

    transaction_amount: float | None = None
    description: str | None = None
    external_reference: str | None = None
    statement_descriptor: str | None = None


class CreateAdvancedPaymentResponsePayer(BaseSchema):
    """Schema generated for CreateAdvancedPaymentResponsePayer.

    Attributes:
        token (str | None): Undocumented in the spec.
        type_token (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    token: str | None = None
    type_token: str | None = None


class CreateAdvancedPaymentResponseWalletPayment(BaseSchema):
    """Schema generated for CreateAdvancedPaymentResponseWalletPayment.

    Attributes:
        transaction_amount (float | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        external_reference (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    transaction_amount: float | None = None
    description: str | None = None
    external_reference: str | None = None


class CreateMerchantOrderBodyPayer(BaseSchema):
    """Schema generated for CreateMerchantOrderBodyPayer.

    Attributes:
        id (int | None): Undocumented in the spec.
        email (EmailStr | None): Undocumented in the spec.
    """

    id: int | None = None
    email: EmailStr | None = None


class CreateMerchantOrderResponseCollector(BaseSchema):
    """Schema generated for CreateMerchantOrderResponseCollector.

    Attributes:
        id (int | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        nickname (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    email: str | None = None
    nickname: str | None = None


class CreateMerchantOrderResponsePayer(BaseSchema):
    """Schema generated for CreateMerchantOrderResponsePayer.

    Attributes:
        id (int | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        nickname (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    email: str | None = None
    nickname: str | None = None


class CreateOAuthTokenBody(BaseSchema):
    """Schema generated for CreateOAuthTokenBody.

    Attributes:
        client_id (str): Your application's client ID
        client_secret (str): Your application's client secret. Never expose in
            client-side code. Store in a secrets manager.
        grant_type (CreateOAuthTokenBodyGrantType): OAuth 2.0 grant type
        code (str | None): Authorization code from the OAuth redirect
            (authorization_code flow)
        redirect_uri (str | None): Must match the registered redirect URI exactly
        code_verifier (str | None): PKCE code verifier (recommended for mobile/SPA)
        refresh_token (str | None): Refresh token for the refresh_token grant
    """

    client_id: str = Field(description="Your application's client ID")
    client_secret: str = Field(
        description=(
            "Your application's client secret. Never expose in client-side code. Store "
            "in a secrets manager."
        ),
    )
    grant_type: CreateOAuthTokenBodyGrantType = Field(
        description="OAuth 2.0 grant type",
    )
    code: str | None = Field(
        description=(
            "Authorization code from the OAuth redirect (authorization_code flow)"
        ),
        default=None,
    )
    redirect_uri: str | None = Field(
        description="Must match the registered redirect URI exactly",
        default=None,
    )
    code_verifier: str | None = Field(
        description="PKCE code verifier (recommended for mobile/SPA)",
        default=None,
    )
    refresh_token: str | None = Field(
        description="Refresh token for the refresh_token grant",
        default=None,
    )


class CreateOAuthTokenResponse(BaseSchema):
    """Schema generated for CreateOAuthTokenResponse.

    Attributes:
        access_token (str | None): Bearer access token for API calls
        token_type (str | None): Undocumented in the spec.
        expires_in (int | None): Access token TTL in seconds
        scope (str | None): Undocumented in the spec.
        refresh_token (str | None): Long-lived token to refresh access_token
        user_id (int | None): MP user ID of the authorized user
        public_key (str | None): Public key for client-side card tokenization
        live_mode (bool | None): true = production token.
    """

    model_config = ConfigDict(extra="allow")

    access_token: str | None = Field(
        description="Bearer access token for API calls",
        examples=["APP_USR-1234-mmdd-abcd-xxxxxxxxxxxx"],
        default=None,
    )
    token_type: str | None = Field(examples=["bearer"], default=None)
    expires_in: int | None = Field(
        description="Access token TTL in seconds",
        examples=[15552000],
        default=None,
    )
    scope: str | None = Field(examples=["offline_access read write"], default=None)
    refresh_token: str | None = Field(
        description="Long-lived token to refresh access_token",
        default=None,
    )
    user_id: int | None = Field(
        description="MP user ID of the authorized user",
        default=None,
    )
    public_key: str | None = Field(
        description="Public key for client-side card tokenization",
        default=None,
    )
    live_mode: bool | None = Field(description="true = production token.", default=None)


class CreatePayoutBodyTransactionsItem(BaseSchema):
    """Schema generated for CreatePayoutBodyTransactionsItem.

    Attributes:
        amount (float | None): Undocumented in the spec.
        currency_id (CurrencyId | None): ISO 4217 currency code for the applicable site
        payment_method_id (str | None): Undocumented in the spec.
        beneficiary (dict[str, Any] | None): Undocumented in the spec.
    """

    amount: float | None = None
    currency_id: CurrencyId | None = Field(
        description="ISO 4217 currency code for the applicable site",
        examples=["BRL"],
        default=None,
    )
    payment_method_id: str | None = None
    beneficiary: dict[str, Any] | None = None


class CreatePointPaymentIntentBodyPayment(BaseSchema):
    """Schema generated for CreatePointPaymentIntentBodyPayment.

    Attributes:
        installments (int | None): Undocumented in the spec.
        type (CreatePointPaymentIntentBodyPaymentType | None): Undocumented in the spec.
    """

    installments: int | None = None
    type: CreatePointPaymentIntentBodyPaymentType | None = None


class CreatePointPaymentIntentResponse(BaseSchema):
    """Schema generated for CreatePointPaymentIntentResponse.

    Attributes:
        id (str | None): Undocumented in the spec.
        device_id (str | None): Undocumented in the spec.
        amount (int | None): Undocumented in the spec.
        status (CreatePointPaymentIntentResponseStatus | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    device_id: str | None = None
    amount: int | None = None
    status: CreatePointPaymentIntentResponseStatus | None = None


class CreatePointRefundIntentBody(BaseSchema):
    """Schema generated for CreatePointRefundIntentBody.

    Attributes:
        payment_id (int): ID of the payment to refund
        amount (float | None): Amount to refund (omit for full refund)
    """

    payment_id: int = Field(description="ID of the payment to refund")
    amount: float | None = Field(
        description="Amount to refund (omit for full refund)",
        default=None,
    )


class CreatePointRefundIntentResponse(BaseSchema):
    """Schema generated for CreatePointRefundIntentResponse.

    Attributes:
        id (str | None): Refund intent ID
        status (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(description="Refund intent ID", default=None)
    status: str | None = None


class CreateQrIntegratorConfigBody(BaseSchema):
    """Schema generated for CreateQrIntegratorConfigBody.

    Attributes:
        callback_url (str | None): Webhook URL for QR payment events
        notification_url (str | None): Undocumented in the spec.
    """

    callback_url: str | None = Field(
        description="Webhook URL for QR payment events",
        default=None,
    )
    notification_url: str | None = None


class CreateRefundResponseSource(BaseSchema):
    """Schema generated for CreateRefundResponseSource.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    type: str | None = None


class CreateStoreBody(BaseSchema):
    """Schema generated for CreateStoreBody.

    Attributes:
        name (str): Undocumented in the spec.
        business_hours (dict[str, Any] | None): Undocumented in the spec.
        location (dict[str, Any] | None): Undocumented in the spec.
        external_id (str | None): Undocumented in the spec.
    """

    name: str
    business_hours: dict[str, Any] | None = None
    location: dict[str, Any] | None = None
    external_id: str | None = None


class CreateTerminalActionBodyConfig(BaseSchema):
    """Target terminal configuration.

    Attributes:
        device_id (str | None): Terminal device ID
    """

    device_id: str | None = Field(description="Terminal device ID", default=None)


class CreateTerminalActionBodyContent(BaseSchema):
    """Print content (varies by type).

    Attributes:
        source (str | None): URL of image to print (PRINT_INFO)
        dte_data (str | None): Base64-encoded DTE XML (PRINT_DTE, MLC only)
    """

    source: str | None = Field(
        description="URL of image to print (PRINT_INFO)",
        default=None,
    )
    dte_data: str | None = Field(
        description="Base64-encoded DTE XML (PRINT_DTE, MLC only)",
        default=None,
    )


class CreateTerminalActionResponse(BaseSchema):
    """Schema generated for CreateTerminalActionResponse.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (CreateTerminalActionResponseStatus | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    status: CreateTerminalActionResponseStatus | None = None


class CreateWalletAgreementBodyAgreementData(BaseSchema):
    """Schema generated for CreateWalletAgreementBodyAgreementData.

    Attributes:
        validation_amount (float | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
    """

    validation_amount: float | None = None
    description: str | None = None


class CreateWalletAgreementBodyExternalUser(BaseSchema):
    """Schema generated for CreateWalletAgreementBodyExternalUser.

    Attributes:
        id (str | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
    """

    id: str | None = None
    description: str | None = None


class CreateWalletAgreementResponse(BaseSchema):
    """Schema generated for CreateWalletAgreementResponse.

    Attributes:
        agreement_id (str | None): Undocumented in the spec.
        agreement_uri (str | None): Redirect payer to this URL to authorize the
            agreement
    """

    model_config = ConfigDict(extra="allow")

    agreement_id: str | None = None
    agreement_uri: str | None = Field(
        description="Redirect payer to this URL to authorize the agreement",
        default=None,
    )


class CreateWalletDiscountBody(BaseSchema):
    """Schema generated for CreateWalletDiscountBody.

    Attributes:
        coupon (str): Undocumented in the spec.
        amount (float): Undocumented in the spec.
    """

    coupon: str
    amount: float


class CreateWalletDiscountResponseDiscount(BaseSchema):
    """Schema generated for CreateWalletDiscountResponseDiscount.

    Attributes:
        amount (float | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        coupon_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    amount: float | None = None
    type: str | None = None
    coupon_id: str | None = None


class CreateWalletPayerTokenBody(BaseSchema):
    """Schema generated for CreateWalletPayerTokenBody.

    Attributes:
        code (str | None): Authorization code from the Wallet Connect flow
    """

    code: str | None = Field(
        description="Authorization code from the Wallet Connect flow",
        default=None,
    )


class CreateWalletPayerTokenResponse(BaseSchema):
    """Schema generated for CreateWalletPayerTokenResponse.

    Attributes:
        payer_token (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    payer_token: str | None = None


class DigitalWallet(BaseSchema):
    """Schema generated for DigitalWallet.

    Attributes:
        provider (DigitalWalletProvider): Digital wallet provider
        wallet_id (str): Wallet identifier or token
    """

    provider: DigitalWalletProvider = Field(description="Digital wallet provider")
    wallet_id: str = Field(description="Wallet identifier or token")


class ErrorCause(BaseSchema):
    """Schema generated for ErrorCause.

    Attributes:
        code (int | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        data (str | None): Undocumented in the spec.
    """

    code: int | None = Field(examples=[2001], default=None)
    description: str | None = Field(examples=["Invalid access token"], default=None)
    data: str | None = None


class GetAdvancedPaymentResponsePayer(BaseSchema):
    """Schema generated for GetAdvancedPaymentResponsePayer.

    Attributes:
        token (str | None): Undocumented in the spec.
        type_token (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    token: str | None = None
    type_token: str | None = None


class GetAdvancedPaymentResponseWalletPayment(BaseSchema):
    """Schema generated for GetAdvancedPaymentResponseWalletPayment.

    Attributes:
        transaction_amount (float | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        external_reference (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    transaction_amount: float | None = None
    description: str | None = None
    external_reference: str | None = None


class GetChargebackResponse(BaseSchema):
    """Schema generated for GetChargebackResponse.

    Attributes:
        id (str | None): Undocumented in the spec.
        payments (list[dict[str, Any]]): Undocumented in the spec.
        currency (str | None): Undocumented in the spec.
        amount (float | None): Undocumented in the spec.
        coverage_applied (bool | None): Undocumented in the spec.
        coverage_elegible (bool | None): Undocumented in the spec.
        documentation_required (bool | None): Undocumented in the spec.
        documentation_status (GetChargebackResponseDocumentationStatus | None):
            Undocumented in the spec.
        documentation (dict[str, Any] | None): Undocumented in the spec.
        date_documentation_deadline (datetime | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        date_last_updated (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    payments: list[dict[str, Any]] = Field(default_factory=list)
    currency: str | None = None
    amount: float | None = None
    coverage_applied: bool | None = None
    coverage_elegible: bool | None = None
    documentation_required: bool | None = None
    documentation_status: GetChargebackResponseDocumentationStatus | None = None
    documentation: dict[str, Any] | None = None
    date_documentation_deadline: datetime | None = None
    date_created: datetime | None = None
    date_last_updated: datetime | None = None


class GetClaimFileResponse(BaseSchema):
    """Schema generated for GetClaimFileResponse.

    Attributes:
        file_name (str | None): Undocumented in the spec.
        file_id (str | None): Undocumented in the spec.
        content_type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    file_name: str | None = None
    file_id: str | None = None
    content_type: str | None = None


class GetInstallmentsResponseItemIssuer(BaseSchema):
    """Schema generated for GetInstallmentsResponseItemIssuer.

    Attributes:
        id (int | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    name: str | None = None


class GetInstallmentsResponseItemPayerCostsItem(BaseSchema):
    """Schema generated for GetInstallmentsResponseItemPayerCostsItem.

    Attributes:
        installments (int | None): Undocumented in the spec.
        installment_rate (float | None): Undocumented in the spec.
        discount_rate (float | None): Undocumented in the spec.
        reimbursement_rate (float | None): Undocumented in the spec.
        labels (list[str]): Undocumented in the spec.
        total_amount (float | None): Undocumented in the spec.
        installment_amount (float | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    installments: int | None = None
    installment_rate: float | None = None
    discount_rate: float | None = None
    reimbursement_rate: float | None = None
    labels: list[str] = Field(default_factory=list)
    total_amount: float | None = None
    installment_amount: float | None = None


class GetMerchantOrderResponseCollector(BaseSchema):
    """Schema generated for GetMerchantOrderResponseCollector.

    Attributes:
        id (int | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        nickname (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    email: str | None = None
    nickname: str | None = None


class GetMerchantOrderResponsePayer(BaseSchema):
    """Schema generated for GetMerchantOrderResponsePayer.

    Attributes:
        id (int | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        nickname (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    email: str | None = None
    nickname: str | None = None


class GetPointRefundIntentResponse(BaseSchema):
    """Schema generated for GetPointRefundIntentResponse.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        payment_id (int | None): Undocumented in the spec.
        amount (float | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    status: str | None = None
    payment_id: int | None = None
    amount: float | None = None


class GetQrIntegratorConfigResponse(BaseSchema):
    """Schema generated for GetQrIntegratorConfigResponse.

    Attributes:
        callback_url (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    callback_url: str | None = None


class GetRefundResponseSource(BaseSchema):
    """Schema generated for GetRefundResponseSource.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    type: str | None = None


class GetTerminalActionResponse(BaseSchema):
    """Schema generated for GetTerminalActionResponse.

    Attributes:
        id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        status (CreateTerminalActionResponseStatus | None): Undocumented in the spec.
        external_reference (str | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    type: str | None = None
    status: CreateTerminalActionResponseStatus | None = None
    external_reference: str | None = None
    date_created: datetime | None = None


class GetWalletAgreementResponseAgreementData(BaseSchema):
    """Schema generated for GetWalletAgreementResponseAgreementData.

    Attributes:
        validation_amount (float | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    validation_amount: float | None = None
    description: str | None = None


class GetWalletAgreementResponseExternalUser(BaseSchema):
    """Schema generated for GetWalletAgreementResponseExternalUser.

    Attributes:
        id (str | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    description: str | None = None


class Identification(BaseSchema):
    """Payer identification document. Valid types vary by country.

    Attributes:
        type (str | None): Identification type (e.g., CPF, CNPJ for Brazil; DNI for
            Argentina; RFC for Mexico)
        number (str | None): Identification number (no formatting — digits only)
    """

    model_config = ConfigDict(extra="allow")

    type: str | None = Field(
        description=(
            "Identification type (e.g., CPF, CNPJ for Brazil; DNI for Argentina; RFC "
            "for Mexico)"
        ),
        examples=["CPF"],
        default=None,
    )
    number: str | None = Field(
        description="Identification number (no formatting — digits only)",
        examples=["12345678909"],
        default=None,
    )


class ListIdentificationTypesResponseItem(BaseSchema):
    """Schema generated for ListIdentificationTypesResponseItem.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        type (ListIdentificationTypesResponseItemType | None): Undocumented in the spec.
        min_length (int | None): Undocumented in the spec.
        max_length (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(examples=["CPF"], default=None)
    name: str | None = Field(examples=["CPF"], default=None)
    type: ListIdentificationTypesResponseItemType | None = None
    min_length: int | None = None
    max_length: int | None = None


class ListPaymentMethodsResponseItemFinancialInstitutionsItem(BaseSchema):
    """Schema generated for ListPaymentMethodsResponseItemFinancialInstitutionsItem.

    Attributes:
        id (str | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    description: str | None = None


class ListPointDevicesResponseDevicesItem(BaseSchema):
    """Schema generated for ListPointDevicesResponseDevicesItem.

    Attributes:
        id (str | None): Undocumented in the spec.
        pos_id (int | None): Undocumented in the spec.
        store_id (str | None): Undocumented in the spec.
        operating_mode (ListPointDevicesResponseDevicesItemOperatingMode | None):
            Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    pos_id: int | None = None
    store_id: str | None = None
    operating_mode: ListPointDevicesResponseDevicesItemOperatingMode | None = None


class ListRefundsResponseSource(BaseSchema):
    """Schema generated for ListRefundsResponseSource.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    type: str | None = None


class ListTerminalsResponseTerminalsItem(BaseSchema):
    """Schema generated for ListTerminalsResponseTerminalsItem.

    Attributes:
        id (str | None): Undocumented in the spec.
        pos_id (int | None): Undocumented in the spec.
        store_id (str | None): Undocumented in the spec.
        operating_mode (ListPointDevicesResponseDevicesItemOperatingMode | None):
            Undocumented in the spec.
        status (SubscriptionPlanStatus | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    pos_id: int | None = None
    store_id: str | None = None
    operating_mode: ListPointDevicesResponseDevicesItemOperatingMode | None = None
    status: SubscriptionPlanStatus | None = None


class MediationResolution(BaseSchema):
    """Expected resolution options at mediation stage.

    Attributes:
        type (MediationResolutionType | None): Undocumented in the spec.
        amount (float | None): Undocumented in the spec.
        reason (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    type: MediationResolutionType | None = None
    amount: float | None = None
    reason: str | None = None


class MerchantAnalyticsResponsePeriod(BaseSchema):
    """Schema generated for MerchantAnalyticsResponsePeriod.

    Attributes:
        end_date (date): End date of the analytics period
        start_date (date): Start date of the analytics period
    """

    end_date: date = Field(
        description="End date of the analytics period",
        examples=["2024-01-31"],
    )
    start_date: date = Field(
        description="Start date of the analytics period",
        examples=["2024-01-01"],
    )


class MerchantAnalyticsResponseTopCustomersItem(BaseSchema):
    """Schema generated for MerchantAnalyticsResponseTopCustomersItem.

    Attributes:
        transaction_count (int | None): Number of transactions
        customer_id (UUID | None): Customer identifier
        total_volume (Decimal | None): Total volume from this customer
    """

    transaction_count: int | None = Field(
        description="Number of transactions",
        examples=[15],
        default=None,
    )
    customer_id: UUID | None = Field(
        description="Customer identifier",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
        default=None,
    )
    total_volume: Decimal | None = Field(
        description="Total volume from this customer",
        examples=[2456.78],
        default=None,
    )


class MerchantOrderPaymentsItem(BaseSchema):
    """Schema generated for MerchantOrderPaymentsItem.

    Attributes:
        id (int | None): Undocumented in the spec.
        transaction_amount (float | None): Undocumented in the spec.
        total_paid_amount (float | None): Undocumented in the spec.
        shipping_cost (float | None): Undocumented in the spec.
        currency_id (CurrencyId | None): ISO 4217 currency code for the applicable site
        status (str | None): Undocumented in the spec.
        status_detail (str | None): Undocumented in the spec.
        operation_type (str | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        date_approved (datetime | None): Undocumented in the spec.
    """

    id: int | None = None
    transaction_amount: float | None = None
    total_paid_amount: float | None = None
    shipping_cost: float | None = None
    currency_id: CurrencyId | None = Field(
        description="ISO 4217 currency code for the applicable site",
        examples=["BRL"],
        default=None,
    )
    status: str | None = None
    status_detail: str | None = None
    operation_type: str | None = None
    date_created: datetime | None = None
    date_approved: datetime | None = None


class Money(BaseSchema):
    """Monetary amount. MercadoPago uses decimal amounts (e.g., 100.50 for R$100,50),
    NOT integer cents. Precision: 2 decimal places for most currencies; 0 for CLP.

    Attributes:
        amount (float | None): Undocumented in the spec.
        currency_id (CurrencyId | None): ISO 4217 currency code for the applicable site
    """

    amount: float | None = Field(examples=[100.5], default=None)
    currency_id: CurrencyId | None = Field(
        description="ISO 4217 currency code for the applicable site",
        examples=["BRL"],
        default=None,
    )


class OrderConfigOnlineTransactionSecurity(BaseSchema):
    """3DS (3D Secure) configuration. After creating the order, the response indicates
    if a challenge is required via
    transactions.payments[].payment_method.transaction_security.

    Attributes:
        validation (OrderConfigOnlineTransactionSecurityValidation | None): supported —
            run 3DS when supported by issuer; required — always run 3DS, reject if not
            supported; never — skip 3DS entirely.
        liability_shift (OrderConfigOnlineTransactionSecurityLiabilityShift | None):
            Required when validation is not "never". required — only approve if
            liability shifts to issuer; not_required — approve regardless of liability
            shift outcome.
    """

    model_config = ConfigDict(extra="allow")

    validation: OrderConfigOnlineTransactionSecurityValidation | None = Field(
        description=(
            "supported — run 3DS when supported by issuer; required — always run 3DS, "
            "reject if not supported; never — skip 3DS entirely."
        ),
        default=None,
    )
    liability_shift: OrderConfigOnlineTransactionSecurityLiabilityShift | None = Field(
        description=(
            'Required when validation is not "never". required — only approve if '
            "liability shifts to issuer; not_required — approve regardless of "
            "liability shift outcome."
        ),
        default=None,
    )


class OrderItem(BaseSchema):
    """Schema generated for OrderItem.

    Attributes:
        id (str | None): Undocumented in the spec.
        title (str | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        quantity (int | None): Undocumented in the spec.
        unit_price (float | None): Undocumented in the spec.
        category_id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    title: str | None = None
    description: str | None = None
    quantity: int | None = Field(ge=1, default=None)
    unit_price: float | None = None
    category_id: str | None = None


class OrderPayerAddress(BaseSchema):
    """Schema generated for OrderPayerAddress.

    Attributes:
        zip_code (str | None): Undocumented in the spec.
        street_name (str | None): Undocumented in the spec.
        street_number (str | None): Undocumented in the spec.
        neighborhood (str | None): Undocumented in the spec.
        city (str | None): Undocumented in the spec.
        state (str | None): Undocumented in the spec.
        complement (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    zip_code: str | None = None
    street_name: str | None = None
    street_number: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    complement: str | None = None


class OrderPayerIdentification(BaseSchema):
    """Schema generated for OrderPayerIdentification.

    Attributes:
        type (str | None): Document type (e.g. CPF, CNPJ, DNI, NIT, RFC)
        number (str | None): Document number (digits only, no formatting)
    """

    model_config = ConfigDict(extra="allow")

    type: str | None = Field(
        description="Document type (e.g. CPF, CNPJ, DNI, NIT, RFC)",
        examples=["CPF"],
        default=None,
    )
    number: str | None = Field(
        description="Document number (digits only, no formatting)",
        examples=["99999999999"],
        default=None,
    )


class OrderPayerPhone(BaseSchema):
    """Schema generated for OrderPayerPhone.

    Attributes:
        area_code (str | None): Undocumented in the spec.
        number (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    area_code: str | None = Field(examples=["11"], default=None)
    number: str | None = Field(examples=["43434343"], default=None)


class OrderPaymentMethod(BaseSchema):
    """Payment method for the transaction. Use GET /v1/payment_methods to list available
    methods for your site.

    Attributes:
        id (str): Payment method identifier. Examples by type: Cards: visa, master, elo,
            hipercard, amex. Ticket/cash: bolbradesco (Boleto), oxxo, rapipago,
            pagofacil. Bank transfer: pix, clabe (SPEI), pse.
        type (OrderPaymentMethodType): Undocumented in the spec.
        token (str | None): Card token. Required for credit_card and debit_card types.
        installments (int | None): Number of installments. Use 1 for no installments.
        statement_descriptor (str | None): Text shown on payer's card statement. Maximum
            50 characters.
        financial_institution (str | None): Bank code. Required for PSE (Colombia) bank
            transfer payments.
    """

    id: str = Field(
        description=(
            "Payment method identifier. Examples by type: Cards: visa, master, elo, "
            "hipercard, amex. Ticket/cash: bolbradesco (Boleto), oxxo, rapipago, "
            "pagofacil. Bank transfer: pix, clabe (SPEI), pse."
        ),
        examples=["visa"],
    )
    type: OrderPaymentMethodType = Field(examples=["credit_card"])
    token: str | None = Field(
        description="Card token. Required for credit_card and debit_card types.",
        examples=["1c87b6b301010101ddcd92f9bbbb3be2"],
        default=None,
    )
    installments: int | None = Field(
        description="Number of installments. Use 1 for no installments.",
        examples=[1],
        ge=1,
        default=None,
    )
    statement_descriptor: str | None = Field(
        description="Text shown on payer's card statement. Maximum 50 characters.",
        max_length=50,
        default=None,
    )
    financial_institution: str | None = Field(
        description="Bank code. Required for PSE (Colombia) bank transfer payments.",
        examples=["1051"],
        default=None,
    )


class OrderRefundRequestTransactionsItem(BaseSchema):
    """Schema generated for OrderRefundRequestTransactionsItem.

    Attributes:
        id (str | None): Transaction ID to refund.
        amount (str | None): Amount to refund. Omit for full transaction refund.
    """

    id: str | None = Field(description="Transaction ID to refund.", default=None)
    amount: str | None = Field(
        description="Amount to refund. Omit for full transaction refund.",
        default=None,
    )


class OrderRequestAdditionalInfoPayer(BaseSchema):
    """Schema generated for OrderRequestAdditionalInfoPayer.

    Attributes:
        ip_address (str | None): IP address of the payer. Required for PSE payments.
    """

    ip_address: str | None = Field(
        description="IP address of the payer. Required for PSE payments.",
        examples=["200.100.50.25"],
        default=None,
    )


class OrderRequestIntegrationData(BaseSchema):
    """Integration metadata used by MercadoPago internally.

    Attributes:
        platform_id (str | None): Undocumented in the spec.
        sponsor_id (int | None): Undocumented in the spec.
    """

    platform_id: str | None = None
    sponsor_id: int | None = None


class OrderSearchResultPaging(BaseSchema):
    """Schema generated for OrderSearchResultPaging.

    Attributes:
        total (int | None): Undocumented in the spec.
        limit (int | None): Undocumented in the spec.
        offset (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    total: int | None = None
    limit: int | None = None
    offset: int | None = None


class OrderShipmentAddress(BaseSchema):
    """Schema generated for OrderShipmentAddress.

    Attributes:
        zip_code (str | None): Undocumented in the spec.
        street_name (str | None): Undocumented in the spec.
        street_number (str | None): Undocumented in the spec.
        neighborhood (str | None): Undocumented in the spec.
        city (str | None): Undocumented in the spec.
        state (str | None): Undocumented in the spec.
        complement (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    zip_code: str | None = None
    street_name: str | None = None
    street_number: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    complement: str | None = None


class OrderTransactionPaymentPaymentMethodTransactionSecurity(BaseSchema):
    """3DS challenge result.

    Attributes:
        status (OrderTransactionPaymentPaymentMethodTransactionSecurity2 | None):
            Undocumented in the spec.
        redirect_url (str | None): URL to redirect payer to complete 3DS challenge (when
            status=pending).
    """

    model_config = ConfigDict(extra="allow")

    status: OrderTransactionPaymentPaymentMethodTransactionSecurity2 | None = None
    redirect_url: str | None = Field(
        description=(
            "URL to redirect payer to complete 3DS challenge (when status=pending)."
        ),
        default=None,
    )


class Pagination(BaseSchema):
    """Pagination metadata returned in list/search responses.

    Attributes:
        total (int | None): Total number of results
        limit (int | None): Results per page
        offset (int | None): Offset of the current page
    """

    model_config = ConfigDict(extra="allow")

    total: int | None = Field(
        description="Total number of results",
        examples=[42],
        default=None,
    )
    limit: int | None = Field(
        description="Results per page",
        examples=[30],
        default=None,
    )
    offset: int | None = Field(
        description="Offset of the current page",
        examples=[0],
        default=None,
    )


class PaymentAnalyticsResponsePeriod(BaseSchema):
    """Schema generated for PaymentAnalyticsResponsePeriod.

    Attributes:
        start_date (date): Start date of the analytics period
        end_date (date): End date of the analytics period
    """

    start_date: date = Field(
        description="Start date of the analytics period",
        examples=["2024-01-01"],
    )
    end_date: date = Field(
        description="End date of the analytics period",
        examples=["2024-01-31"],
    )


class PaymentFees(BaseSchema):
    """Schema generated for PaymentFees.

    Attributes:
        processing_fee (Decimal | None): Processing fee charged
        platform_fee (Decimal | None): Platform fee charged
        total_fees (Decimal | None): Total fees charged
    """

    processing_fee: Decimal | None = Field(
        description="Processing fee charged",
        examples=[0.89],
        default=None,
    )
    platform_fee: Decimal | None = Field(
        description="Platform fee charged",
        examples=[1.5],
        default=None,
    )
    total_fees: Decimal | None = Field(
        description="Total fees charged",
        examples=[2.39],
        default=None,
    )


class PaymentItem(BaseSchema):
    """Schema generated for PaymentItem.

    Attributes:
        id (str | None): Undocumented in the spec.
        title (str | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        category_id (str | None): Undocumented in the spec.
        quantity (int | None): Undocumented in the spec.
        unit_price (float | None): Undocumented in the spec.
    """

    id: str | None = None
    title: str | None = None
    description: str | None = None
    category_id: str | None = None
    quantity: int | None = None
    unit_price: float | None = None


class PaymentTransactionDetails(BaseSchema):
    """Schema generated for PaymentTransactionDetails.

    Attributes:
        net_received_amount (float | None): Undocumented in the spec.
        total_paid_amount (float | None): Undocumented in the spec.
        overpaid_amount (float | None): Undocumented in the spec.
        external_resource_url (str | None): Boleto/OXXO/cash payment URL or barcode
        installment_amount (float | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    net_received_amount: float | None = None
    total_paid_amount: float | None = None
    overpaid_amount: float | None = None
    external_resource_url: str | None = Field(
        description="Boleto/OXXO/cash payment URL or barcode",
        default=None,
    )
    installment_amount: float | None = None


class PaymentUpdateRequest(BaseSchema):
    """Request body to update a payment (capture, cancel, or extend expiration).

    Attributes:
        capture (bool | None): Set true to capture an authorized payment.
        status (PaymentUpdateRequestStatus | None): Set to cancelled to cancel an
            authorized payment.
        transaction_amount (float | None): Partial capture amount (must be ≤ original
            authorized amount).
        date_of_expiration (datetime | None): New expiration date for cash payment
            methods.
    """

    capture: bool | None = Field(
        description="Set true to capture an authorized payment.",
        default=None,
    )
    status: PaymentUpdateRequestStatus | None = Field(
        description="Set to cancelled to cancel an authorized payment.",
        default=None,
    )
    transaction_amount: float | None = Field(
        description="Partial capture amount (must be ≤ original authorized amount).",
        default=None,
    )
    date_of_expiration: datetime | None = Field(
        description="New expiration date for cash payment methods.",
        default=None,
    )


class Phone(BaseSchema):
    """Schema generated for Phone.

    Attributes:
        area_code (str | None): Undocumented in the spec.
        number (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    area_code: str | None = Field(examples=["11"], default=None)
    number: str | None = Field(examples=["987654321"], default=None)


class Pos(BaseSchema):
    """Schema generated for Pos.

    Attributes:
        name (str): POS display name
        store_id (str): Parent store ID
        external_id (str | None): Your internal POS identifier
        external_store_id (str | None): Undocumented in the spec.
        category (int | None): Business category code
        fixed_amount (bool | None): When true the POS amount is fixed and cannot be
            changed by payer
        url (str | None): Webhook URL for this specific POS
        id (str | None): Undocumented in the spec.
        qr_code (str | None): QR code data string for this POS
        qr_code_image (str | None): URL of QR code image
        date_created (datetime | None): Undocumented in the spec.
        date_last_updated (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(description="POS display name", examples=["Caixa 1"])
    store_id: str = Field(description="Parent store ID")
    external_id: str | None = Field(
        description="Your internal POS identifier",
        default=None,
    )
    external_store_id: str | None = None
    category: int | None = Field(description="Business category code", default=None)
    fixed_amount: bool | None = Field(
        description="When true the POS amount is fixed and cannot be changed by payer",
        default=None,
    )
    url: str | None = Field(
        description="Webhook URL for this specific POS",
        default=None,
    )
    id: str | None = None
    qr_code: str | None = Field(
        description="QR code data string for this POS",
        default=None,
    )
    qr_code_image: str | None = Field(description="URL of QR code image", default=None)
    date_created: datetime | None = None
    date_last_updated: datetime | None = None


class PosRequest(BaseSchema):
    """Schema generated for PosRequest.

    Attributes:
        name (str): POS display name
        store_id (str): Parent store ID
        external_id (str | None): Your internal POS identifier
        external_store_id (str | None): Undocumented in the spec.
        category (int | None): Business category code
        fixed_amount (bool | None): When true the POS amount is fixed and cannot be
            changed by payer
        url (str | None): Webhook URL for this specific POS
    """

    name: str = Field(description="POS display name", examples=["Caixa 1"])
    store_id: str = Field(description="Parent store ID")
    external_id: str | None = Field(
        description="Your internal POS identifier",
        default=None,
    )
    external_store_id: str | None = None
    category: int | None = Field(description="Business category code", default=None)
    fixed_amount: bool | None = Field(
        description="When true the POS amount is fixed and cannot be changed by payer",
        default=None,
    )
    url: str | None = Field(
        description="Webhook URL for this specific POS",
        default=None,
    )


class PreferenceBackUrls(BaseSchema):
    """Redirect URLs after checkout completion. Configure auto_return to control when
    automatic redirects happen.

    Attributes:
        success (str | None): Redirect after approved payment
        pending (str | None): Redirect for pending payments (e.g., Boleto)
        failure (str | None): Redirect after rejected payment
    """

    model_config = ConfigDict(extra="allow")

    success: str | None = Field(
        description="Redirect after approved payment",
        default=None,
    )
    pending: str | None = Field(
        description="Redirect for pending payments (e.g., Boleto)",
        default=None,
    )
    failure: str | None = Field(
        description="Redirect after rejected payment",
        default=None,
    )


class PreferenceItem(BaseSchema):
    """Schema generated for PreferenceItem.

    Attributes:
        id (str | None): Undocumented in the spec.
        title (str): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        picture_url (str | None): Undocumented in the spec.
        category_id (str | None): Undocumented in the spec.
        quantity (int): Undocumented in the spec.
        currency_id (CurrencyId | None): ISO 4217 currency code for the applicable site
        unit_price (float): Unit price as a decimal (not integer cents)
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    title: str = Field(examples=["Premium Plan"])
    description: str | None = None
    picture_url: str | None = None
    category_id: str | None = None
    quantity: int = Field(examples=[1], ge=1)
    currency_id: CurrencyId | None = Field(
        description="ISO 4217 currency code for the applicable site",
        examples=["BRL"],
        default=None,
    )
    unit_price: float = Field(
        description="Unit price as a decimal (not integer cents)",
        examples=[99.9],
    )


class PreferencePaymentMethodsExcludedPaymentMethodsItem(BaseSchema):
    """Schema generated for PreferencePaymentMethodsExcludedPaymentMethodsItem.

    Attributes:
        id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None


class PreferencePaymentMethodsExcludedPaymentTypesItem(BaseSchema):
    """Schema generated for PreferencePaymentMethodsExcludedPaymentTypesItem.

    Attributes:
        id (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None


class PreferenceRequestDifferentialPricing(BaseSchema):
    """Schema generated for PreferenceRequestDifferentialPricing.

    Attributes:
        id (int | None): Undocumented in the spec.
    """

    id: int | None = None


class ProcessTransactionIntentBodyPointOfInteraction(BaseSchema):
    """Schema generated for ProcessTransactionIntentBodyPointOfInteraction.

    Attributes:
        type (str | None): Undocumented in the spec.
    """

    type: str | None = None


class ProcessTransactionIntentBodyTransaction(BaseSchema):
    """Schema generated for ProcessTransactionIntentBodyTransaction.

    Attributes:
        amount (float | None): Undocumented in the spec.
        currency_id (str | None): Undocumented in the spec.
        receiver (dict[str, Any] | None): Undocumented in the spec.
    """

    amount: float | None = None
    currency_id: str | None = Field(examples=["BRL"], default=None)
    receiver: dict[str, Any] | None = None


class RefundOrderResponse(BaseSchema):
    """Schema generated for RefundOrderResponse.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (str | None): Undocumented in the spec.
        status_detail (str | None): Undocumented in the spec.
        transactions (dict[str, Any] | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    status: str | None = None
    status_detail: str | None = None
    transactions: dict[str, Any] | None = None


class RefundRequest(BaseSchema):
    """Request body for creating a refund. Omit amount for a full refund.

    Attributes:
        amount (float | None): Amount to refund. Omit for a full refund.
    """

    amount: float | None = Field(
        description="Amount to refund. Omit for a full refund.",
        examples=[50.0],
        default=None,
    )


class RefundResponse(BaseSchema):
    """Schema generated for RefundResponse.

    Attributes:
        refund_id (UUID): Unique refund identifier
        updated_at (datetime | None): Last update timestamp
        payment_id (UUID): Original payment identifier
        created_at (datetime): Refund creation timestamp
        amount (Decimal): Refunded amount
        status (RefundResponseStatus): Current refund status
    """

    refund_id: UUID = Field(
        description="Unique refund identifier",
        examples=["ref_123e4567-e89b-12d3-a456-426614174000"],
    )
    updated_at: datetime | None = Field(
        description="Last update timestamp",
        examples=["2024-01-15T11:30:15Z"],
        default=None,
    )
    payment_id: UUID = Field(
        description="Original payment identifier",
        examples=["987fcdeb-51a2-43d1-9c15-246531579012"],
    )
    created_at: datetime = Field(
        description="Refund creation timestamp",
        examples=["2024-01-15T11:30:00Z"],
    )
    amount: Decimal = Field(description="Refunded amount", examples=[15.99])
    status: RefundResponseStatus = Field(
        description="Current refund status",
        examples=["completed"],
    )


class RefundSource(BaseSchema):
    """Schema generated for RefundSource.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
    """

    id: str | None = None
    name: str | None = None
    type: str | None = None


class ReportConfigColumnsItem(BaseSchema):
    """Schema generated for ReportConfigColumnsItem.

    Attributes:
        key (str | None): Undocumented in the spec.
        alias (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    key: str | None = Field(examples=["DATE"], default=None)
    alias: str | None = Field(examples=["transaction_date"], default=None)


class ReportConfigFrequency(BaseSchema):
    """Schedule frequency for automatic generation.

    Attributes:
        hour (int | None): Undocumented in the spec.
        type (ReportConfigFrequencyType | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    hour: int | None = Field(ge=0, le=23, default=None)
    type: ReportConfigFrequencyType | None = None


class ReportConfigSftpInfo(BaseSchema):
    """SFTP destination for automatic delivery (optional).

    Attributes:
        server (str | None): Undocumented in the spec.
        port (int | None): Undocumented in the spec.
        username (str | None): Undocumented in the spec.
        remote_dir (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    server: str | None = None
    port: int | None = None
    username: str | None = None
    remote_dir: str | None = None


class ReportEntry(BaseSchema):
    """A generated report file entry.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (ReportEntryStatus | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        date_last_updated (datetime | None): Undocumented in the spec.
        begin_date (datetime | None): Undocumented in the spec.
        end_date (datetime | None): Undocumented in the spec.
        file_name (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    status: ReportEntryStatus | None = None
    date_created: datetime | None = None
    date_last_updated: datetime | None = None
    begin_date: datetime | None = None
    end_date: datetime | None = None
    file_name: str | None = Field(
        examples=["releases_report_2024-01-01_2024-01-31.csv"],
        default=None,
    )


class ReportRequest(BaseSchema):
    """Request body to generate a one-time report.

    Attributes:
        begin_date (datetime): Start of the reporting period (ISO 8601)
        end_date (datetime): End of the reporting period (ISO 8601)
    """

    begin_date: datetime = Field(
        description="Start of the reporting period (ISO 8601)",
        examples=["2024-01-01T00:00:00Z"],
    )
    end_date: datetime = Field(
        description="End of the reporting period (ISO 8601)",
        examples=["2024-01-31T23:59:59Z"],
    )


class ReportTask(BaseSchema):
    """Status of a report generation task.

    Attributes:
        id (str | None): Task unique identifier
        status (ReportTaskStatus | None): pending — queued; in_progress — generating;
            done — ready to download; failed — generation error
        begin_date (datetime | None): Undocumented in the spec.
        end_date (datetime | None): Undocumented in the spec.
        created_at (datetime | None): Undocumented in the spec.
        updated_at (datetime | None): Undocumented in the spec.
        download_url (str | None): Download URL (present when status=done)
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(
        description="Task unique identifier",
        examples=["task_abc123"],
        default=None,
    )
    status: ReportTaskStatus | None = Field(
        description=(
            "pending — queued; in_progress — generating; done — ready to download; "
            "failed — generation error"
        ),
        default=None,
    )
    begin_date: datetime | None = None
    end_date: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    download_url: str | None = Field(
        description="Download URL (present when status=done)",
        default=None,
    )


class SaveCardRequest(BaseSchema):
    """Request body to save a card to a customer. The card token must be created
    client-side via MercadoPago.js / MP Secure Fields before calling this endpoint. Raw
    card data (PAN, CVV) must never be sent to your server.

    Attributes:
        token (str): Single-use card token from MercadoPago.js
    """

    token: str = Field(
        description="Single-use card token from MercadoPago.js",
        examples=["YOUR_ACCESS_TOKEN"],
    )


class SearchMerchantOrdersResponseCollector(BaseSchema):
    """Schema generated for SearchMerchantOrdersResponseCollector.

    Attributes:
        id (int | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        nickname (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    email: str | None = None
    nickname: str | None = None


class SearchMerchantOrdersResponsePayer(BaseSchema):
    """Schema generated for SearchMerchantOrdersResponsePayer.

    Attributes:
        id (int | None): Undocumented in the spec.
        email (str | None): Undocumented in the spec.
        nickname (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    email: str | None = None
    nickname: str | None = None


class SendMessageRequest(BaseSchema):
    """Schema generated for SendMessageRequest.

    Attributes:
        message (str): Text content of the message to send
        attachments (list[str]): File IDs to attach (upload first via attachments
            endpoint)
    """

    message: str = Field(
        description="Text content of the message to send",
        examples=["I can provide the tracking code for the shipment."],
    )
    attachments: list[str] = Field(
        description="File IDs to attach (upload first via attachments endpoint)",
        default_factory=list,
    )


class StoreBusinessHoursMondayItem(BaseSchema):
    """Schema generated for StoreBusinessHoursMondayItem.

    Attributes:
        open (str | None): Undocumented in the spec.
        close (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    open: str | None = Field(examples=["09:00"], default=None)
    close: str | None = Field(examples=["18:00"], default=None)


class StoreLocation(BaseSchema):
    """Schema generated for StoreLocation.

    Attributes:
        street_number (str | None): Undocumented in the spec.
        street_name (str | None): Undocumented in the spec.
        city_name (str | None): Undocumented in the spec.
        state_name (str | None): Undocumented in the spec.
        zip_code (str | None): Undocumented in the spec.
        latitude (float | None): Undocumented in the spec.
        longitude (float | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    street_number: str | None = None
    street_name: str | None = None
    city_name: str | None = None
    state_name: str | None = None
    zip_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class StoreRequestBusinessHoursMondayItem(BaseSchema):
    """Schema generated for StoreRequestBusinessHoursMondayItem.

    Attributes:
        open (str | None): Undocumented in the spec.
        close (str | None): Undocumented in the spec.
    """

    open: str | None = Field(examples=["09:00"], default=None)
    close: str | None = Field(examples=["18:00"], default=None)


class StoreRequestLocation(BaseSchema):
    """Schema generated for StoreRequestLocation.

    Attributes:
        street_number (str | None): Undocumented in the spec.
        street_name (str | None): Undocumented in the spec.
        city_name (str | None): Undocumented in the spec.
        state_name (str | None): Undocumented in the spec.
        zip_code (str | None): Undocumented in the spec.
        latitude (float | None): Undocumented in the spec.
        longitude (float | None): Undocumented in the spec.
    """

    street_number: str | None = None
    street_name: str | None = None
    city_name: str | None = None
    state_name: str | None = None
    zip_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class StoredPaymentMethodResponse(BaseSchema):
    """Schema generated for StoredPaymentMethodResponse.

    Attributes:
        is_default (bool): Whether this is the default payment method
        updated_at (datetime | None): Last update timestamp
        payment_method_id (UUID): Unique payment method identifier
        created_at (datetime): Payment method creation timestamp
        customer_id (UUID): Customer identifier
        last_four (str | None): Last four digits of card (for card payments only)
        type (PaymentMethodType): Type of payment method
    """

    is_default: bool = Field(
        description="Whether this is the default payment method",
        examples=[True],
    )
    updated_at: datetime | None = Field(
        description="Last update timestamp",
        examples=["2024-01-15T07:30:15Z"],
        default=None,
    )
    payment_method_id: UUID = Field(
        description="Unique payment method identifier",
        examples=["pm_456e7890-a12b-34c5-d678-901234567890"],
    )
    created_at: datetime = Field(
        description="Payment method creation timestamp",
        examples=["2024-01-15T07:30:00Z"],
    )
    customer_id: UUID = Field(
        description="Customer identifier",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )
    last_four: str | None = Field(
        description="Last four digits of card (for card payments only)",
        examples=["1111"],
        default=None,
    )
    type: PaymentMethodType = Field(
        description="Type of payment method",
        examples=["credit_card"],
    )


class SubscriptionCreateRequest(BaseSchema):
    """Schema generated for SubscriptionCreateRequest.

    Attributes:
        merchant_id (UUID): Merchant identifier
        payment_method_id (UUID): Payment method to use for recurring payments
        amount (Decimal): Recurring payment amount
        currency (str): ISO 4217 currency code
        frequency (SubscriptionResponseFrequency): Payment frequency
        start_date (date | None): Date to start the recurring payments
        customer_id (UUID): Customer identifier
    """

    merchant_id: UUID = Field(
        description="Merchant identifier",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    payment_method_id: UUID = Field(
        description="Payment method to use for recurring payments",
        examples=["pm_456e7890-a12b-34c5-d678-901234567890"],
    )
    amount: Decimal = Field(
        description="Recurring payment amount",
        examples=[29.99],
        ge=0.01,
    )
    currency: str = Field(
        description="ISO 4217 currency code",
        examples=["USD"],
        pattern="^[A-Z]{3}$",
    )
    frequency: SubscriptionResponseFrequency = Field(
        description="Payment frequency",
        examples=["monthly"],
    )
    start_date: date | None = Field(
        description="Date to start the recurring payments",
        examples=["2024-01-15"],
        default=None,
    )
    customer_id: UUID = Field(
        description="Customer identifier",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )


class SubscriptionPlanRequestPaymentMethodsAllowedPaymentMeth(BaseSchema):
    """Schema generated for SubscriptionPlanRequestPaymentMethodsAllowedPaymentMeth.

    Attributes:
        id (str | None): Undocumented in the spec.
    """

    id: str | None = None


class SubscriptionPlanRequestPaymentMethodsAllowedPaymentType(BaseSchema):
    """Schema generated for SubscriptionPlanRequestPaymentMethodsAllowedPaymentType.

    Attributes:
        id (str | None): Undocumented in the spec.
    """

    id: str | None = None


class SubscriptionResponse(BaseSchema):
    """Schema generated for SubscriptionResponse.

    Attributes:
        merchant_id (UUID): Merchant identifier
        payment_method_id (UUID | None): Payment method to use for recurring payments
        amount (Decimal): Recurring payment amount
        updated_at (datetime | None): Last update timestamp
        currency (str): ISO 4217 currency code
        frequency (SubscriptionResponseFrequency): Payment frequency
        created_at (datetime): Subscription creation timestamp
        next_payment_date (date): Next scheduled payment date
        customer_id (UUID): Customer identifier
        subscription_id (UUID): Unique subscription identifier
        status (SubscriptionResponseStatus): Current subscription status
    """

    merchant_id: UUID = Field(
        description="Merchant identifier",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    payment_method_id: UUID | None = Field(
        description="Payment method to use for recurring payments",
        examples=["pm_456e7890-a12b-34c5-d678-901234567890"],
        default=None,
    )
    amount: Decimal = Field(description="Recurring payment amount", examples=[29.99])
    updated_at: datetime | None = Field(
        description="Last update timestamp",
        examples=["2024-01-15T06:30:15Z"],
        default=None,
    )
    currency: str = Field(
        description="ISO 4217 currency code",
        examples=["USD"],
        pattern="^[A-Z]{3}$",
    )
    frequency: SubscriptionResponseFrequency = Field(
        description="Payment frequency",
        examples=["monthly"],
    )
    created_at: datetime = Field(
        description="Subscription creation timestamp",
        examples=["2024-01-15T06:30:00Z"],
    )
    next_payment_date: date = Field(
        description="Next scheduled payment date",
        examples=["2024-02-15"],
    )
    customer_id: UUID = Field(
        description="Customer identifier",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )
    subscription_id: UUID = Field(
        description="Unique subscription identifier",
        examples=["sub_789fcdeb-51a2-43d1-9c15-246531579012"],
    )
    status: SubscriptionResponseStatus = Field(
        description="Current subscription status",
        examples=["active"],
    )


class SubscriptionSummarized(BaseSchema):
    """Subscription billing summary.

    Attributes:
        quotas (int | None): Undocumented in the spec.
        charged_quantity (int | None): Undocumented in the spec.
        pending_charge_quantity (int | None): Undocumented in the spec.
        charged_amount (float | None): Undocumented in the spec.
        pending_charge_amount (float | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    quotas: int | None = None
    charged_quantity: int | None = None
    pending_charge_quantity: int | None = None
    charged_amount: float | None = None
    pending_charge_amount: float | None = None


class UpdateAdvancedPaymentBodyWalletPayment(BaseSchema):
    """Schema generated for UpdateAdvancedPaymentBodyWalletPayment.

    Attributes:
        transaction_amount (float | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        external_reference (str | None): Undocumented in the spec.
    """

    transaction_amount: float | None = None
    description: str | None = None
    external_reference: str | None = None


class UpdateAdvancedPaymentResponsePayer(BaseSchema):
    """Schema generated for UpdateAdvancedPaymentResponsePayer.

    Attributes:
        token (str | None): Undocumented in the spec.
        type_token (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    token: str | None = None
    type_token: str | None = None


class UpdateAdvancedPaymentResponseWalletPayment(BaseSchema):
    """Schema generated for UpdateAdvancedPaymentResponseWalletPayment.

    Attributes:
        transaction_amount (float | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        external_reference (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    transaction_amount: float | None = None
    description: str | None = None
    external_reference: str | None = None


class UpdateChargebackBodyFilesItem(BaseSchema):
    """Schema generated for UpdateChargebackBodyFilesItem.

    Attributes:
        name (str | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        url (str | None): Undocumented in the spec.
    """

    name: str | None = None
    description: str | None = None
    url: str | None = None


class UpdateMerchantOrderBodyPayer(BaseSchema):
    """Schema generated for UpdateMerchantOrderBodyPayer.

    Attributes:
        id (int | None): Undocumented in the spec.
        email (EmailStr | None): Undocumented in the spec.
    """

    id: int | None = None
    email: EmailStr | None = None


class UpdateTerminalOperationModeBodyTerminalsItem(BaseSchema):
    """Schema generated for UpdateTerminalOperationModeBodyTerminalsItem.

    Attributes:
        id (str): Terminal ID
        operating_mode (ListPointDevicesResponseDevicesItemOperatingMode): Undocumented
            in the spec.
    """

    id: str = Field(description="Terminal ID")
    operating_mode: ListPointDevicesResponseDevicesItemOperatingMode


class UploadShippingEvidenceBody(BaseSchema):
    """Schema generated for UploadShippingEvidenceBody.

    Attributes:
        type (UploadShippingEvidenceBodyType | None): Undocumented in the spec.
        value (str | None): Tracking code or delivery confirmation reference
    """

    type: UploadShippingEvidenceBodyType | None = None
    value: str | None = Field(
        description="Tracking code or delivery confirmation reference",
        default=None,
    )


class ValidateWalletCouponBody(BaseSchema):
    """Schema generated for ValidateWalletCouponBody.

    Attributes:
        id (str): Undocumented in the spec.
    """

    id: str


class ValidateWalletCouponResponse(BaseSchema):
    """Schema generated for ValidateWalletCouponResponse.

    Attributes:
        status (ValidateWalletCouponResponseStatus | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        legal_terms (str | None): Undocumented in the spec.
        detail (dict[str, Any] | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    status: ValidateWalletCouponResponseStatus | None = None
    description: str | None = None
    legal_terms: str | None = None
    detail: dict[str, Any] | None = None


class ValidationResponseErrorsItem(BaseSchema):
    """Schema generated for ValidationResponseErrorsItem.

    Attributes:
        message (str | None): Validation error message
        field (str | None): Field that failed validation
    """

    message: str | None = Field(
        description="Validation error message",
        examples=["Card has expired"],
        default=None,
    )
    field: str | None = Field(
        description="Field that failed validation",
        examples=["expiry_month"],
        default=None,
    )


class WebhookEvent(BaseSchema):
    """Schema generated for WebhookEvent.

    Attributes:
        event_id (UUID): Unique event identifier
        event_type (WebhookEventEventType): Type of webhook event
        resource_id (UUID): ID of the resource that triggered the event
        created_at (datetime): Event creation timestamp
        data (dict[str, Any]): Event payload data
    """

    event_id: UUID = Field(
        description="Unique event identifier",
        examples=["evt_987fcdeb-51a2-43d1-9c15-246531579012"],
    )
    event_type: WebhookEventEventType = Field(
        description="Type of webhook event",
        examples=["payment.completed"],
    )
    resource_id: UUID = Field(
        description="ID of the resource that triggered the event",
        examples=["987fcdeb-51a2-43d1-9c15-246531579012"],
    )
    created_at: datetime = Field(
        description="Event creation timestamp",
        examples=["2024-01-15T05:30:00Z"],
    )
    data: dict[str, Any] = Field(description="Event payload data")


class WebhookNotificationData(BaseSchema):
    """Schema generated for WebhookNotificationData.

    Attributes:
        id (str): Resource ID. Fetch this via GET at the resource URL to get full
            details. Do not rely on the webhook payload alone — always GET the resource.
    """

    id: str = Field(
        description=(
            "Resource ID. Fetch this via GET at the resource URL to get full details. "
            "Do not rely on the webhook payload alone — always GET the resource."
        ),
        examples=["1234567890"],
    )


class WebhookSignatureHeader(BaseSchema):
    """The x-signature header value format: ts=<timestamp>,v1=<hmac_sha256_signature>.
    Validate using HMAC-SHA256 with your webhook secret key. See:
    https://www.mercadopago.com/developers/en/docs/your-integrations/notifications/webhooks.

    Attributes:
        ts (str | None): Unix timestamp of the notification
        v1 (str | None): HMAC-SHA256 signature of
            "id:[notif_id];request-id:[req_id];ts:[ts];"
    """

    ts: str | None = Field(
        description="Unix timestamp of the notification",
        default=None,
    )
    v1: str | None = Field(
        description=(
            'HMAC-SHA256 signature of "id:[notif_id];request-id:[req_id];ts:[ts];"'
        ),
        default=None,
    )


class AuthorizedPayment(BaseSchema):
    """An invoice generated by a subscription billing cycle.

    Attributes:
        id (int | None): Undocumented in the spec.
        preapproval_id (str | None): Undocumented in the spec.
        status (AuthorizedPaymentStatus | None): Undocumented in the spec.
        status_detail (str | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        last_modified (datetime | None): Undocumented in the spec.
        transaction_amount (float | None): Undocumented in the spec.
        currency_id (CurrencyId | None): ISO 4217 currency code for the applicable site
        payment (AuthorizedPaymentPayment | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    preapproval_id: str | None = None
    status: AuthorizedPaymentStatus | None = None
    status_detail: str | None = None
    date_created: datetime | None = None
    last_modified: datetime | None = None
    transaction_amount: float | None = None
    currency_id: CurrencyId | None = Field(
        description="ISO 4217 currency code for the applicable site",
        examples=["BRL"],
        default=None,
    )
    payment: AuthorizedPaymentPayment | None = None


class AutoRecurring(BaseSchema):
    """Schema generated for AutoRecurring.

    Attributes:
        frequency (int): Billing interval (e.g., 1 for monthly, 7 for weekly)
        frequency_type (AutoRecurringFrequencyType): Unit for the billing interval
        transaction_amount (float): Amount to charge each cycle (decimal, not cents)
        currency_id (CurrencyId): ISO 4217 currency code for the applicable site
        free_trial (AutoRecurringFreeTrial | None): Optional free trial period before
            billing starts
        repetitions (int | None): Total number of billing cycles (null = indefinite)
    """

    model_config = ConfigDict(extra="allow")

    frequency: int = Field(
        description="Billing interval (e.g., 1 for monthly, 7 for weekly)",
        examples=[1],
    )
    frequency_type: AutoRecurringFrequencyType = Field(
        description="Unit for the billing interval",
        examples=["months"],
    )
    transaction_amount: float = Field(
        description="Amount to charge each cycle (decimal, not cents)",
        examples=[29.9],
    )
    currency_id: CurrencyId = Field(
        description="ISO 4217 currency code for the applicable site",
        examples=["BRL"],
    )
    free_trial: AutoRecurringFreeTrial | None = Field(
        description="Optional free trial period before billing starts",
        default=None,
    )
    repetitions: int | None = Field(
        description="Total number of billing cycles (null = indefinite)",
        default=None,
    )


class CardCardholder(BaseSchema):
    """Schema generated for CardCardholder.

    Attributes:
        name (str | None): Name on card
        identification (Identification | None): Payer identification document. Valid
            types vary by country.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = Field(description="Name on card", default=None)
    identification: Identification | None = Field(
        description="Payer identification document. Valid types vary by country.",
        default=None,
    )


class CardTokenCardholder(BaseSchema):
    """Schema generated for CardTokenCardholder.

    Attributes:
        name (str | None): Undocumented in the spec.
        identification (Identification | None): Payer identification document. Valid
            types vary by country.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    identification: Identification | None = Field(
        description="Payer identification document. Valid types vary by country.",
        default=None,
    )


class CardTokenRequestCardholder(BaseSchema):
    """Schema generated for CardTokenRequestCardholder.

    Attributes:
        name (str): Name exactly as printed on card
        identification (Identification | None): Payer identification document. Valid
            types vary by country.
    """

    name: str = Field(
        description="Name exactly as printed on card",
        examples=["JOHN DOE"],
    )
    identification: Identification | None = Field(
        description="Payer identification document. Valid types vary by country.",
        default=None,
    )


class Claim(BaseSchema):
    """A post-sale claim (dispute) between buyer and seller.

    Attributes:
        id (int | None): Claim unique identifier
        type (ClaimType | None): mediations — buyer-initiated dispute requiring MP
            mediation; claims — seller-initiated claim
        stage (ClaimStage | None): Current stage in the dispute lifecycle
        status (ClaimStatus | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        last_updated (datetime | None): Undocumented in the spec.
        resource (ClaimResource | None): Resource type the claim is about
        reason_id (str | None): Reason code for the claim
        players (list[ClaimPlayersItem]): Parties involved (buyer, seller)
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = Field(
        description="Claim unique identifier",
        examples=[4567890123],
        default=None,
    )
    type: ClaimType | None = Field(
        description=(
            "mediations — buyer-initiated dispute requiring MP mediation; claims — "
            "seller-initiated claim"
        ),
        default=None,
    )
    stage: ClaimStage | None = Field(
        description="Current stage in the dispute lifecycle",
        default=None,
    )
    status: ClaimStatus | None = None
    date_created: datetime | None = None
    last_updated: datetime | None = None
    resource: ClaimResource | None = Field(
        description="Resource type the claim is about",
        default=None,
    )
    reason_id: str | None = Field(description="Reason code for the claim", default=None)
    players: list[ClaimPlayersItem] = Field(
        description="Parties involved (buyer, seller)",
        default_factory=list,
    )


class ClaimMessage(BaseSchema):
    """A message within a claim thread.

    Attributes:
        id (int | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        from_ (ClaimMessageFrom | None): Undocumented in the spec.
        message (str | None): Text content of the message
        attachments (list[ClaimMessageAttachmentsItem]): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: int | None = None
    date_created: datetime | None = None
    from_: ClaimMessageFrom | None = Field(
        validation_alias="from",
        serialization_alias="from",
        default=None,
    )
    message: str | None = Field(description="Text content of the message", default=None)
    attachments: list[ClaimMessageAttachmentsItem] = Field(default_factory=list)


class CreateAdvancedPaymentBody(BaseSchema):
    """Schema generated for CreateAdvancedPaymentBody.

    Attributes:
        wallet_payment (CreateAdvancedPaymentBodyWalletPayment | None): Undocumented in
            the spec.
        payer (CreateAdvancedPaymentBodyPayer | None): Undocumented in the spec.
        binary_mode (bool | None): Undocumented in the spec.
        capture (bool | None): Undocumented in the spec.
    """

    wallet_payment: CreateAdvancedPaymentBodyWalletPayment | None = None
    payer: CreateAdvancedPaymentBodyPayer | None = None
    binary_mode: bool | None = None
    capture: bool | None = None


class CreateAdvancedPaymentResponse(BaseSchema):
    """Schema generated for CreateAdvancedPaymentResponse.

    Attributes:
        id (int | None): Undocumented in the spec.
        payments (list[dict[str, Any]]): Undocumented in the spec.
        wallet_payment (CreateAdvancedPaymentResponseWalletPayment | None): Undocumented
            in the spec.
        disbursements (list[dict[str, Any]]): Undocumented in the spec.
        payer (CreateAdvancedPaymentResponsePayer | None): Undocumented in the spec.
        site_id (str | None): Undocumented in the spec.
        binary_mode (bool | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        date_last_updated (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    payments: list[dict[str, Any]] = Field(default_factory=list)
    wallet_payment: CreateAdvancedPaymentResponseWalletPayment | None = None
    disbursements: list[dict[str, Any]] = Field(default_factory=list)
    payer: CreateAdvancedPaymentResponsePayer | None = None
    site_id: str | None = None
    binary_mode: bool | None = None
    date_created: datetime | None = None
    date_last_updated: datetime | None = None


class CreateMerchantOrderBody(BaseSchema):
    """Schema generated for CreateMerchantOrderBody.

    Attributes:
        external_reference (str | None): Undocumented in the spec.
        preference_id (str | None): Undocumented in the spec.
        marketplace (str | None): Undocumented in the spec.
        notification_url (str | None): Undocumented in the spec.
        sponsor_id (int | None): Undocumented in the spec.
        payer (CreateMerchantOrderBodyPayer | None): Undocumented in the spec.
        site_id (CreateMerchantOrderBodySiteId | None): Undocumented in the spec.
        items (list[dict[str, Any]]): Undocumented in the spec.
        additional_info (str | None): Undocumented in the spec.
        application_id (str | None): Undocumented in the spec.
    """

    external_reference: str | None = None
    preference_id: str | None = None
    marketplace: str | None = None
    notification_url: str | None = None
    sponsor_id: int | None = None
    payer: CreateMerchantOrderBodyPayer | None = None
    site_id: CreateMerchantOrderBodySiteId | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    additional_info: str | None = None
    application_id: str | None = None


class CreateMerchantOrderResponse(BaseSchema):
    """Schema generated for CreateMerchantOrderResponse.

    Attributes:
        id (int | None): Undocumented in the spec.
        status (MerchantOrderStatus | None): Undocumented in the spec.
        order_status (CreateMerchantOrderResponseOrderStatus | None): Undocumented in
            the spec.
        external_reference (str | None): Undocumented in the spec.
        preference_id (str | None): Undocumented in the spec.
        marketplace (str | None): Undocumented in the spec.
        application_id (str | None): Undocumented in the spec.
        site_id (CreateMerchantOrderBodySiteId | None): Undocumented in the spec.
        notification_url (str | None): Undocumented in the spec.
        sponsor_id (int | None): Undocumented in the spec.
        collector (CreateMerchantOrderResponseCollector | None): Undocumented in the
            spec.
        payer (CreateMerchantOrderResponsePayer | None): Undocumented in the spec.
        payments (list[dict[str, Any]]): Undocumented in the spec.
        shipments (list[dict[str, Any]]): Undocumented in the spec.
        payouts (list[dict[str, Any]]): Undocumented in the spec.
        items (list[dict[str, Any]]): Undocumented in the spec.
        additional_info (str | None): Undocumented in the spec.
        shipping_cost (float | None): Undocumented in the spec.
        total_amount (float | None): Undocumented in the spec.
        paid_amount (float | None): Undocumented in the spec.
        refunded_amount (float | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        last_updated (datetime | None): Undocumented in the spec.
        canceled (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    status: MerchantOrderStatus | None = None
    order_status: CreateMerchantOrderResponseOrderStatus | None = None
    external_reference: str | None = None
    preference_id: str | None = None
    marketplace: str | None = None
    application_id: str | None = None
    site_id: CreateMerchantOrderBodySiteId | None = None
    notification_url: str | None = None
    sponsor_id: int | None = None
    collector: CreateMerchantOrderResponseCollector | None = None
    payer: CreateMerchantOrderResponsePayer | None = None
    payments: list[dict[str, Any]] = Field(default_factory=list)
    shipments: list[dict[str, Any]] = Field(default_factory=list)
    payouts: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)
    additional_info: str | None = None
    shipping_cost: float | None = None
    total_amount: float | None = None
    paid_amount: float | None = None
    refunded_amount: float | None = None
    date_created: datetime | None = None
    last_updated: datetime | None = None
    canceled: bool | None = None


class CreatePayoutBody(BaseSchema):
    """Schema generated for CreatePayoutBody.

    Attributes:
        external_reference (str | None): Undocumented in the spec.
        transactions (list[CreatePayoutBodyTransactionsItem]): Undocumented in the spec.
    """

    external_reference: str | None = None
    transactions: list[CreatePayoutBodyTransactionsItem] = Field(default_factory=list)


class CreatePointPaymentIntentBody(BaseSchema):
    """Schema generated for CreatePointPaymentIntentBody.

    Attributes:
        amount (int): Amount in cents (integer for Point devices)
        description (str | None): Undocumented in the spec.
        payment (CreatePointPaymentIntentBodyPayment | None): Undocumented in the spec.
        print_on_terminal (bool | None): Print receipt on the device after payment
    """

    amount: int = Field(
        description="Amount in cents (integer for Point devices)",
        examples=[1500],
    )
    description: str | None = None
    payment: CreatePointPaymentIntentBodyPayment | None = None
    print_on_terminal: bool | None = Field(
        description="Print receipt on the device after payment",
        default=None,
    )


class CreateRefundResponse(BaseSchema):
    """Schema generated for CreateRefundResponse.

    Attributes:
        id (int | None): Undocumented in the spec.
        payment_id (int | None): Undocumented in the spec.
        amount (float | None): Undocumented in the spec.
        metadata (dict[str, Any] | None): Undocumented in the spec.
        source (CreateRefundResponseSource | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        unique_sequence_number (str | None): Undocumented in the spec.
        refund_mode (RefundRefundMode | None): Undocumented in the spec.
        adjustment_amount (float | None): Undocumented in the spec.
        status (RefundStatus | None): Undocumented in the spec.
        reason (str | None): Undocumented in the spec.
        label (str | None): Undocumented in the spec.
        partition_details (list[dict[str, Any]]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    payment_id: int | None = None
    amount: float | None = None
    metadata: dict[str, Any] | None = None
    source: CreateRefundResponseSource | None = None
    date_created: datetime | None = None
    unique_sequence_number: str | None = None
    refund_mode: RefundRefundMode | None = None
    adjustment_amount: float | None = None
    status: RefundStatus | None = None
    reason: str | None = None
    label: str | None = None
    partition_details: list[dict[str, Any]] = Field(default_factory=list)


class CreateTerminalActionBody(BaseSchema):
    """Schema generated for CreateTerminalActionBody.

    Attributes:
        type (CreateTerminalActionBodyType): PRINT_INFO — print a custom image or
            receipt; PRINT_DTE — print a DTE document (MLC only)
        external_reference (str): Your reference ID for this print job
        config (CreateTerminalActionBodyConfig): Target terminal configuration
        content (CreateTerminalActionBodyContent): Print content (varies by type)
    """

    type: CreateTerminalActionBodyType = Field(
        description=(
            "PRINT_INFO — print a custom image or receipt; PRINT_DTE — print a DTE "
            "document (MLC only)"
        ),
    )
    external_reference: str = Field(description="Your reference ID for this print job")
    config: CreateTerminalActionBodyConfig = Field(
        description="Target terminal configuration",
    )
    content: CreateTerminalActionBodyContent = Field(
        description="Print content (varies by type)",
    )


class CreateWalletAgreementBody(BaseSchema):
    """Schema generated for CreateWalletAgreementBody.

    Attributes:
        return_uri (str): URL to redirect user back after authorization.
        external_flow_id (str): Seller-side flow state identifier.
        external_user (CreateWalletAgreementBodyExternalUser | None): Undocumented in
            the spec.
        agreement_data (CreateWalletAgreementBodyAgreementData | None): Undocumented in
            the spec.
    """

    return_uri: str = Field(
        description="URL to redirect user back after authorization.",
    )
    external_flow_id: str = Field(description="Seller-side flow state identifier.")
    external_user: CreateWalletAgreementBodyExternalUser | None = None
    agreement_data: CreateWalletAgreementBodyAgreementData | None = None


class CreateWalletDiscountResponse(BaseSchema):
    """Schema generated for CreateWalletDiscountResponse.

    Attributes:
        transaction_amount (float | None): Undocumented in the spec.
        currency_id (CurrencyId | None): ISO 4217 currency code for the applicable site
        legal_terms (str | None): Undocumented in the spec.
        discount (CreateWalletDiscountResponseDiscount | None): Undocumented in the
            spec.
    """

    model_config = ConfigDict(extra="allow")

    transaction_amount: float | None = None
    currency_id: CurrencyId | None = Field(
        description="ISO 4217 currency code for the applicable site",
        examples=["BRL"],
        default=None,
    )
    legal_terms: str | None = None
    discount: CreateWalletDiscountResponseDiscount | None = None


class CustomerCreateRequest(BaseSchema):
    """Schema generated for CustomerCreateRequest.

    Attributes:
        first_name (str): Customer first name
        address (Address | None): Undocumented in the spec.
        phone (str | None): Customer phone number in E.164 format
        last_name (str): Customer last name
        email (EmailStr): Customer email address
    """

    first_name: str = Field(
        description="Customer first name",
        examples=["John"],
        max_length=50,
    )
    address: Address | None = None
    phone: str | None = Field(
        description="Customer phone number in E.164 format",
        examples=["+1-555-987-6543"],
        pattern="^\\+?[1-9]\\d{1,14}$",
        default=None,
    )
    last_name: str = Field(
        description="Customer last name",
        examples=["Doe"],
        max_length=50,
    )
    email: EmailStr = Field(
        description="Customer email address",
        examples=["john.doe@example.com"],
    )


class CustomerRequest(BaseSchema):
    """Request body to create or update a customer.

    Attributes:
        email (EmailStr | None): Customer email address (unique identifier)
        first_name (str | None): Undocumented in the spec.
        last_name (str | None): Undocumented in the spec.
        phone (Phone | None): Undocumented in the spec.
        identification (Identification | None): Payer identification document. Valid
            types vary by country.
        address (Address | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        metadata (dict[str, Any] | None): Undocumented in the spec.
    """

    email: EmailStr | None = Field(
        description="Customer email address (unique identifier)",
        examples=["customer@example.com"],
        default=None,
    )
    first_name: str | None = None
    last_name: str | None = None
    phone: Phone | None = None
    identification: Identification | None = Field(
        description="Payer identification document. Valid types vary by country.",
        default=None,
    )
    address: Address | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None


class CustomerResponse(BaseSchema):
    """Schema generated for CustomerResponse.

    Attributes:
        first_name (str): Customer first name
        address (Address | None): Undocumented in the spec.
        updated_at (datetime | None): Last update timestamp
        phone (str | None): Customer phone number
        created_at (datetime): Customer creation timestamp
        customer_id (UUID): Unique customer identifier
        last_name (str): Customer last name
        email (EmailStr): Customer email address
        status (CustomerResponseStatus): Current customer status
    """

    first_name: str = Field(description="Customer first name", examples=["John"])
    address: Address | None = None
    updated_at: datetime | None = Field(
        description="Last update timestamp",
        examples=["2024-01-15T08:30:15Z"],
        default=None,
    )
    phone: str | None = Field(
        description="Customer phone number",
        examples=["+1-555-987-6543"],
        default=None,
    )
    created_at: datetime = Field(
        description="Customer creation timestamp",
        examples=["2024-01-15T08:30:00Z"],
    )
    customer_id: UUID = Field(
        description="Unique customer identifier",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )
    last_name: str = Field(description="Customer last name", examples=["Doe"])
    email: EmailStr = Field(
        description="Customer email address",
        examples=["john.doe@example.com"],
    )
    status: CustomerResponseStatus = Field(
        description="Current customer status",
        examples=["active"],
    )


class Error(BaseSchema):
    """Schema generated for Error.

    Attributes:
        status (int): HTTP status code
        error (str): Error code identifier
        message (str): Human-readable error message (English)
        cause (list[ErrorCause]): Undocumented in the spec.
    """

    status: int = Field(description="HTTP status code", examples=[400])
    error: str = Field(description="Error code identifier", examples=["bad_request"])
    message: str = Field(
        description="Human-readable error message (English)",
        examples=["The access token is invalid"],
    )
    cause: list[ErrorCause] = Field(default_factory=list)


class GetAdvancedPaymentResponse(BaseSchema):
    """Schema generated for GetAdvancedPaymentResponse.

    Attributes:
        id (int | None): Undocumented in the spec.
        payments (list[dict[str, Any]]): Undocumented in the spec.
        wallet_payment (GetAdvancedPaymentResponseWalletPayment | None): Undocumented in
            the spec.
        disbursements (list[dict[str, Any]]): Undocumented in the spec.
        payer (GetAdvancedPaymentResponsePayer | None): Undocumented in the spec.
        site_id (str | None): Undocumented in the spec.
        binary_mode (bool | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        date_last_updated (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    payments: list[dict[str, Any]] = Field(default_factory=list)
    wallet_payment: GetAdvancedPaymentResponseWalletPayment | None = None
    disbursements: list[dict[str, Any]] = Field(default_factory=list)
    payer: GetAdvancedPaymentResponsePayer | None = None
    site_id: str | None = None
    binary_mode: bool | None = None
    date_created: datetime | None = None
    date_last_updated: datetime | None = None


class GetInstallmentsResponseItem(BaseSchema):
    """Schema generated for GetInstallmentsResponseItem.

    Attributes:
        payment_method_id (str | None): Undocumented in the spec.
        payment_type_id (str | None): Undocumented in the spec.
        issuer (GetInstallmentsResponseItemIssuer | None): Undocumented in the spec.
        payer_costs (list[GetInstallmentsResponseItemPayerCostsItem]): Undocumented in
            the spec.
    """

    model_config = ConfigDict(extra="allow")

    payment_method_id: str | None = None
    payment_type_id: str | None = None
    issuer: GetInstallmentsResponseItemIssuer | None = None
    payer_costs: list[GetInstallmentsResponseItemPayerCostsItem] = Field(
        default_factory=list,
    )


class GetMerchantOrderResponse(BaseSchema):
    """Schema generated for GetMerchantOrderResponse.

    Attributes:
        id (int | None): Undocumented in the spec.
        status (MerchantOrderStatus | None): Undocumented in the spec.
        order_status (CreateMerchantOrderResponseOrderStatus | None): Undocumented in
            the spec.
        external_reference (str | None): Undocumented in the spec.
        preference_id (str | None): Undocumented in the spec.
        marketplace (str | None): Undocumented in the spec.
        application_id (str | None): Undocumented in the spec.
        site_id (CreateMerchantOrderBodySiteId | None): Undocumented in the spec.
        notification_url (str | None): Undocumented in the spec.
        sponsor_id (int | None): Undocumented in the spec.
        collector (GetMerchantOrderResponseCollector | None): Undocumented in the spec.
        payer (GetMerchantOrderResponsePayer | None): Undocumented in the spec.
        payments (list[dict[str, Any]]): Undocumented in the spec.
        shipments (list[dict[str, Any]]): Undocumented in the spec.
        payouts (list[dict[str, Any]]): Undocumented in the spec.
        items (list[dict[str, Any]]): Undocumented in the spec.
        additional_info (str | None): Undocumented in the spec.
        shipping_cost (float | None): Undocumented in the spec.
        total_amount (float | None): Undocumented in the spec.
        paid_amount (float | None): Undocumented in the spec.
        refunded_amount (float | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        last_updated (datetime | None): Undocumented in the spec.
        canceled (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    status: MerchantOrderStatus | None = None
    order_status: CreateMerchantOrderResponseOrderStatus | None = None
    external_reference: str | None = None
    preference_id: str | None = None
    marketplace: str | None = None
    application_id: str | None = None
    site_id: CreateMerchantOrderBodySiteId | None = None
    notification_url: str | None = None
    sponsor_id: int | None = None
    collector: GetMerchantOrderResponseCollector | None = None
    payer: GetMerchantOrderResponsePayer | None = None
    payments: list[dict[str, Any]] = Field(default_factory=list)
    shipments: list[dict[str, Any]] = Field(default_factory=list)
    payouts: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)
    additional_info: str | None = None
    shipping_cost: float | None = None
    total_amount: float | None = None
    paid_amount: float | None = None
    refunded_amount: float | None = None
    date_created: datetime | None = None
    last_updated: datetime | None = None
    canceled: bool | None = None


class GetRefundResponse(BaseSchema):
    """Schema generated for GetRefundResponse.

    Attributes:
        id (int | None): Undocumented in the spec.
        payment_id (int | None): Undocumented in the spec.
        amount (float | None): Undocumented in the spec.
        metadata (dict[str, Any] | None): Undocumented in the spec.
        source (GetRefundResponseSource | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        unique_sequence_number (str | None): Undocumented in the spec.
        refund_mode (RefundRefundMode | None): Undocumented in the spec.
        adjustment_amount (float | None): Undocumented in the spec.
        status (RefundStatus | None): Undocumented in the spec.
        reason (str | None): Undocumented in the spec.
        label (str | None): Undocumented in the spec.
        partition_details (list[dict[str, Any]]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    payment_id: int | None = None
    amount: float | None = None
    metadata: dict[str, Any] | None = None
    source: GetRefundResponseSource | None = None
    date_created: datetime | None = None
    unique_sequence_number: str | None = None
    refund_mode: RefundRefundMode | None = None
    adjustment_amount: float | None = None
    status: RefundStatus | None = None
    reason: str | None = None
    label: str | None = None
    partition_details: list[dict[str, Any]] = Field(default_factory=list)


class GetWalletAgreementResponse(BaseSchema):
    """Schema generated for GetWalletAgreementResponse.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (GetWalletAgreementResponseStatus | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        date_canceled (datetime | None): Undocumented in the spec.
        date_expire (datetime | None): Undocumented in the spec.
        validation_code (str | None): Undocumented in the spec.
        approval_uri (str | None): Undocumented in the spec.
        redirect_uri (str | None): Undocumented in the spec.
        external_flow_id (str | None): Undocumented in the spec.
        external_user (GetWalletAgreementResponseExternalUser | None): Undocumented in
            the spec.
        agreement_data (GetWalletAgreementResponseAgreementData | None): Undocumented in
            the spec.
        site_id (GetWalletAgreementResponseSiteId | None): Undocumented in the spec.
        application_id (str | None): Undocumented in the spec.
        collector_id (int | None): Undocumented in the spec.
        model_version (int | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    status: GetWalletAgreementResponseStatus | None = None
    date_created: datetime | None = None
    date_canceled: datetime | None = None
    date_expire: datetime | None = None
    validation_code: str | None = None
    approval_uri: str | None = None
    redirect_uri: str | None = None
    external_flow_id: str | None = None
    external_user: GetWalletAgreementResponseExternalUser | None = None
    agreement_data: GetWalletAgreementResponseAgreementData | None = None
    site_id: GetWalletAgreementResponseSiteId | None = None
    application_id: str | None = None
    collector_id: int | None = None
    model_version: int | None = None


class ListPaymentMethodsResponseItem(BaseSchema):
    """Schema generated for ListPaymentMethodsResponseItem.

    Attributes:
        id (str | None): Undocumented in the spec.
        name (str | None): Undocumented in the spec.
        payment_type_id (str | None): Undocumented in the spec.
        status (ListPaymentMethodsResponseItemStatus | None): Undocumented in the spec.
        secure_thumbnail (str | None): Undocumented in the spec.
        thumbnail (str | None): Undocumented in the spec.
        deferred_capture (ListPaymentMethodsResponseItemDeferredCapture | None):
            Undocumented in the spec.
        settings (dict[str, Any] | None): Card settings (bin ranges, security codes,
            card length).
        additional_info_needed (list[str]): Additional fields required at checkout.
        min_allowed_amount (float | None): Undocumented in the spec.
        max_allowed_amount (float | None): Undocumented in the spec.
        accreditation_time (int | None): Undocumented in the spec.
        financial_institutions
            (list[ListPaymentMethodsResponseItemFinancialInstitutionsItem]):
            Undocumented in the spec.
        processing_modes (list[PaymentProcessingMode]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    payment_type_id: str | None = None
    status: ListPaymentMethodsResponseItemStatus | None = None
    secure_thumbnail: str | None = None
    thumbnail: str | None = None
    deferred_capture: ListPaymentMethodsResponseItemDeferredCapture | None = None
    settings: dict[str, Any] | None = Field(
        description="Card settings (bin ranges, security codes, card length).",
        default=None,
    )
    additional_info_needed: list[str] = Field(
        description="Additional fields required at checkout.",
        default_factory=list,
    )
    min_allowed_amount: float | None = None
    max_allowed_amount: float | None = None
    accreditation_time: int | None = None
    financial_institutions: list[
        ListPaymentMethodsResponseItemFinancialInstitutionsItem
    ] = Field(
        default_factory=list,
    )
    processing_modes: list[PaymentProcessingMode] = Field(default_factory=list)


class ListPointDevicesResponse(BaseSchema):
    """Schema generated for ListPointDevicesResponse.

    Attributes:
        devices (list[ListPointDevicesResponseDevicesItem]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    devices: list[ListPointDevicesResponseDevicesItem] = Field(default_factory=list)


class ListRefundsResponse(BaseSchema):
    """Schema generated for ListRefundsResponse.

    Attributes:
        id (int | None): Undocumented in the spec.
        payment_id (int | None): Undocumented in the spec.
        amount (float | None): Undocumented in the spec.
        metadata (dict[str, Any] | None): Undocumented in the spec.
        source (ListRefundsResponseSource | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        unique_sequence_number (str | None): Undocumented in the spec.
        refund_mode (RefundRefundMode | None): Undocumented in the spec.
        adjustment_amount (float | None): Undocumented in the spec.
        status (RefundStatus | None): Undocumented in the spec.
        reason (str | None): Undocumented in the spec.
        label (str | None): Undocumented in the spec.
        partition_details (list[dict[str, Any]]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    payment_id: int | None = None
    amount: float | None = None
    metadata: dict[str, Any] | None = None
    source: ListRefundsResponseSource | None = None
    date_created: datetime | None = None
    unique_sequence_number: str | None = None
    refund_mode: RefundRefundMode | None = None
    adjustment_amount: float | None = None
    status: RefundStatus | None = None
    reason: str | None = None
    label: str | None = None
    partition_details: list[dict[str, Any]] = Field(default_factory=list)


class ListTerminalsResponse(BaseSchema):
    """Schema generated for ListTerminalsResponse.

    Attributes:
        terminals (list[ListTerminalsResponseTerminalsItem]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    terminals: list[ListTerminalsResponseTerminalsItem] = Field(default_factory=list)


class MerchantAnalyticsResponse(BaseSchema):
    """Schema generated for MerchantAnalyticsResponse.

    Attributes:
        period (MerchantAnalyticsResponsePeriod): Undocumented in the spec.
        total_fees_collected (Decimal): Total fees collected from this merchant
        top_customers (list[MerchantAnalyticsResponseTopCustomersItem]): Top customers
            by transaction volume
        total_volume (Decimal): Total payment volume for this merchant
        total_transactions (int): Total number of transactions for this merchant
        merchant_id (UUID): Merchant identifier
    """

    period: MerchantAnalyticsResponsePeriod
    total_fees_collected: Decimal = Field(
        description="Total fees collected from this merchant",
        examples=[1247.83],
    )
    top_customers: list[MerchantAnalyticsResponseTopCustomersItem] = Field(
        description="Top customers by transaction volume",
        default_factory=list,
    )
    total_volume: Decimal = Field(
        description="Total payment volume for this merchant",
        examples=[87459.32],
    )
    total_transactions: int = Field(
        description="Total number of transactions for this merchant",
        examples=[456],
    )
    merchant_id: UUID = Field(
        description="Merchant identifier",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )


class MerchantCreateRequest(BaseSchema):
    """Schema generated for MerchantCreateRequest.

    Attributes:
        business_name (str): Name of the business
        phone (str): Business phone number in E.164 format
        website (str | None): Business website URL
        address (Address): Undocumented in the spec.
        email (EmailStr): Business email address
    """

    business_name: str = Field(
        description="Name of the business",
        examples=["Acme Corporation"],
        max_length=100,
    )
    phone: str = Field(
        description="Business phone number in E.164 format",
        examples=["+1-555-123-4567"],
        pattern="^\\+?[1-9]\\d{1,14}$",
    )
    website: str | None = Field(
        description="Business website URL",
        examples=["https://www.acme.com"],
        default=None,
    )
    address: Address
    email: EmailStr = Field(
        description="Business email address",
        examples=["contact@acme.com"],
    )


class MerchantOrder(BaseSchema):
    """A merchant order groups multiple payments for a single purchase. Updated when any
    associated payment changes status.

    Attributes:
        id (int | None): Undocumented in the spec.
        status (MerchantOrderStatus | None): Undocumented in the spec.
        external_reference (str | None): Undocumented in the spec.
        preference_id (str | None): Undocumented in the spec.
        payments (list[MerchantOrderPaymentsItem]): Undocumented in the spec.
        items (list[dict[str, Any]]): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        last_updated (datetime | None): Undocumented in the spec.
        total_amount (float | None): Undocumented in the spec.
        paid_amount (float | None): Undocumented in the spec.
        refunded_amount (float | None): Undocumented in the spec.
        shipping (dict[str, Any] | None): Undocumented in the spec.
        notification_url (str | None): Undocumented in the spec.
    """

    id: int | None = None
    status: MerchantOrderStatus | None = None
    external_reference: str | None = None
    preference_id: str | None = None
    payments: list[MerchantOrderPaymentsItem] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)
    date_created: datetime | None = None
    last_updated: datetime | None = None
    total_amount: float | None = None
    paid_amount: float | None = None
    refunded_amount: float | None = None
    shipping: dict[str, Any] | None = None
    notification_url: str | None = None


class MerchantResponse(BaseSchema):
    """Schema generated for MerchantResponse.

    Attributes:
        business_name (str): Business name
        phone (str): Business phone number
        website (str | None): Business website URL
        updated_at (datetime | None): Last update timestamp
        address (Address | None): Undocumented in the spec.
        email (EmailStr): Business email address
        merchant_id (UUID): Unique merchant identifier
        created_at (datetime): Merchant creation timestamp
        status (MerchantResponseStatus): Current merchant status
    """

    business_name: str = Field(
        description="Business name",
        examples=["Acme Corporation"],
    )
    phone: str = Field(
        description="Business phone number",
        examples=["+1-555-123-4567"],
    )
    website: str | None = Field(
        description="Business website URL",
        examples=["https://www.acme.com"],
        default=None,
    )
    updated_at: datetime | None = Field(
        description="Last update timestamp",
        examples=["2024-01-15T09:30:15Z"],
        default=None,
    )
    address: Address | None = None
    email: EmailStr = Field(
        description="Business email address",
        examples=["contact@acme.com"],
    )
    merchant_id: UUID = Field(
        description="Unique merchant identifier",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    created_at: datetime = Field(
        description="Merchant creation timestamp",
        examples=["2024-01-15T09:30:00Z"],
    )
    status: MerchantResponseStatus = Field(
        description="Current merchant status",
        examples=["active"],
    )


class MerchantUpdateRequest(BaseSchema):
    """Schema generated for MerchantUpdateRequest.

    Attributes:
        business_name (str | None): Name of the business
        website (str | None): Business website URL
        address (Address | None): Undocumented in the spec.
        phone (str | None): Business phone number in E.164 format
        email (EmailStr | None): Business email address
    """

    business_name: str | None = Field(
        description="Name of the business",
        examples=["Acme Corporation Ltd"],
        max_length=100,
        default=None,
    )
    website: str | None = Field(
        description="Business website URL",
        examples=["https://www.acme.com"],
        default=None,
    )
    address: Address | None = None
    phone: str | None = Field(
        description="Business phone number in E.164 format",
        examples=["+1-555-123-4567"],
        pattern="^\\+?[1-9]\\d{1,14}$",
        default=None,
    )
    email: EmailStr | None = Field(
        description="Business email address",
        examples=["contact@acme.com"],
        default=None,
    )


class OrderConfigOnline(BaseSchema):
    """Settings for online card transactions.

    Attributes:
        transaction_security (OrderConfigOnlineTransactionSecurity | None): 3DS (3D
            Secure) configuration. After creating the order, the response indicates if a
            challenge is required via
            transactions.payments[].payment_method.transaction_security.
        callback_url (str | None): Redirect URL after PSE bank authentication completes.
    """

    model_config = ConfigDict(extra="allow")

    transaction_security: OrderConfigOnlineTransactionSecurity | None = Field(
        description=(
            "3DS (3D Secure) configuration. After creating the order, the response "
            "indicates if a challenge is required via "
            "transactions.payments[].payment_method.transaction_security."
        ),
        default=None,
    )
    callback_url: str | None = Field(
        description="Redirect URL after PSE bank authentication completes.",
        examples=["https://merchant.com/pse/return"],
        default=None,
    )


class OrderPayer(BaseSchema):
    """Schema generated for OrderPayer.

    Attributes:
        email (EmailStr): Undocumented in the spec.
        first_name (str | None): Undocumented in the spec.
        last_name (str | None): Undocumented in the spec.
        identification (OrderPayerIdentification | None): Undocumented in the spec.
        phone (OrderPayerPhone | None): Undocumented in the spec.
        address (OrderPayerAddress | None): Undocumented in the spec.
        entity_type (OrderPayerEntityType | None): Payer entity type. Required for PSE
            (Colombia) payments.
    """

    model_config = ConfigDict(extra="allow")

    email: EmailStr = Field(examples=["customer@example.com"])
    first_name: str | None = None
    last_name: str | None = None
    identification: OrderPayerIdentification | None = None
    phone: OrderPayerPhone | None = None
    address: OrderPayerAddress | None = None
    entity_type: OrderPayerEntityType | None = Field(
        description="Payer entity type. Required for PSE (Colombia) payments.",
        default=None,
    )


class OrderPayment(BaseSchema):
    """Schema generated for OrderPayment.

    Attributes:
        amount (str): Transaction amount as a decimal string.
        payment_method (OrderPaymentMethod): Payment method for the transaction. Use GET
            /v1/payment_methods to list available methods for your site.
        expiration_time (str | None): Transaction expiration in ISO 8601 duration format
            (e.g. "P1D" = 1 day, "PT20M" = 20 minutes). Applies to ticket and bank
            transfer payment methods.
        date_of_expiration (datetime | None): Absolute expiration date-time. Takes
            precedence over expiration_time if both provided.
    """

    amount: str = Field(
        description="Transaction amount as a decimal string.",
        examples=["100.00"],
    )
    payment_method: OrderPaymentMethod = Field(
        description=(
            "Payment method for the transaction. Use GET /v1/payment_methods to list "
            "available methods for your site."
        ),
    )
    expiration_time: str | None = Field(
        description=(
            'Transaction expiration in ISO 8601 duration format (e.g. "P1D" = 1 day, '
            '"PT20M" = 20 minutes). Applies to ticket and bank transfer payment '
            "methods."
        ),
        examples=["P1D"],
        default=None,
    )
    date_of_expiration: datetime | None = Field(
        description=(
            "Absolute expiration date-time. Takes precedence over expiration_time if "
            "both provided."
        ),
        default=None,
    )


class OrderRefundRequest(BaseSchema):
    """Refund request for an order. Omit transactions for a full refund. Include
    specific transaction amounts for partial refunds.

    Attributes:
        transactions (list[OrderRefundRequestTransactionsItem]): Transactions to refund.
            Omit for full order refund.
    """

    transactions: list[OrderRefundRequestTransactionsItem] = Field(
        description="Transactions to refund. Omit for full order refund.",
        default_factory=list,
    )


class OrderRequestAdditionalInfo(BaseSchema):
    """Additional information required for specific payment methods (e.g. PSE).

    Attributes:
        payer (OrderRequestAdditionalInfoPayer | None): Undocumented in the spec.
    """

    payer: OrderRequestAdditionalInfoPayer | None = None


class OrderShipment(BaseSchema):
    """Schema generated for OrderShipment.

    Attributes:
        address (OrderShipmentAddress | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    address: OrderShipmentAddress | None = None


class OrderTransactionPaymentPaymentMethod(BaseSchema):
    """Schema generated for OrderTransactionPaymentPaymentMethod.

    Attributes:
        id (str | None): Undocumented in the spec.
        type (str | None): Undocumented in the spec.
        token (str | None): Undocumented in the spec.
        installments (int | None): Undocumented in the spec.
        statement_descriptor (str | None): Undocumented in the spec.
        ticket_url (str | None): URL to view or print the ticket (Boleto, OXXO, etc.).
        barcode_content (str | None): Barcode string for offline payment methods.
        reference (str | None): Reference number for offline payment methods.
        verification_code (str | None): Undocumented in the spec.
        financial_institution (str | None): Undocumented in the spec.
        redirect_url (str | None): Redirect URL for PSE bank transfer (Colombia). In
            production, redirect the payer to this URL to complete authentication at
            their bank.
        digitable_line (str | None): Digitable line for Boleto payments.
        qr_code (str | None): QR code string for Pix payments.
        qr_code_base64 (str | None): QR code image as Base64 for Pix payments.
        e2e_id (str | None): End-to-end identifier for Pix transactions (mandatory
            tracking code).
        transaction_security (OrderTransactionPaymentPaymentMethodTransactionSecurity |
            None): 3DS challenge result.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    type: str | None = None
    token: str | None = None
    installments: int | None = None
    statement_descriptor: str | None = None
    ticket_url: str | None = Field(
        description="URL to view or print the ticket (Boleto, OXXO, etc.).",
        default=None,
    )
    barcode_content: str | None = Field(
        description="Barcode string for offline payment methods.",
        default=None,
    )
    reference: str | None = Field(
        description="Reference number for offline payment methods.",
        default=None,
    )
    verification_code: str | None = None
    financial_institution: str | None = None
    redirect_url: str | None = Field(
        description=(
            "Redirect URL for PSE bank transfer (Colombia). In production, redirect "
            "the payer to this URL to complete authentication at their bank."
        ),
        default=None,
    )
    digitable_line: str | None = Field(
        description="Digitable line for Boleto payments.",
        default=None,
    )
    qr_code: str | None = Field(
        description="QR code string for Pix payments.",
        default=None,
    )
    qr_code_base64: str | None = Field(
        description="QR code image as Base64 for Pix payments.",
        default=None,
    )
    e2e_id: str | None = Field(
        description=(
            "End-to-end identifier for Pix transactions (mandatory tracking code)."
        ),
        default=None,
    )
    transaction_security: (
        OrderTransactionPaymentPaymentMethodTransactionSecurity | None
    ) = Field(
        description="3DS challenge result.",
        default=None,
    )


class Payer(BaseSchema):
    """Payment payer information.

    Attributes:
        id (str | None): MP payer ID (registered users only)
        email (EmailStr | None): Payer email address
        identification (Identification | None): Payer identification document. Valid
            types vary by country.
        type (PayerType | None): Payer type
        first_name (str | None): Undocumented in the spec.
        last_name (str | None): Undocumented in the spec.
        phone (Phone | None): Undocumented in the spec.
        address (Address | None): Undocumented in the spec.
    """

    id: str | None = Field(
        description="MP payer ID (registered users only)",
        examples=["123456789"],
        default=None,
    )
    email: EmailStr | None = Field(
        description="Payer email address",
        examples=["test@example.com"],
        default=None,
    )
    identification: Identification | None = Field(
        description="Payer identification document. Valid types vary by country.",
        default=None,
    )
    type: PayerType | None = Field(description="Payer type", default=None)
    first_name: str | None = None
    last_name: str | None = None
    phone: Phone | None = None
    address: Address | None = None


class PaymentAdditionalInfoPayer(BaseSchema):
    """Schema generated for PaymentAdditionalInfoPayer.

    Attributes:
        first_name (str | None): Undocumented in the spec.
        last_name (str | None): Undocumented in the spec.
        phone (Phone | None): Undocumented in the spec.
        address (Address | None): Undocumented in the spec.
        registration_date (datetime | None): Undocumented in the spec.
    """

    first_name: str | None = None
    last_name: str | None = None
    phone: Phone | None = None
    address: Address | None = None
    registration_date: datetime | None = None


class PaymentAdditionalInfoShipments(BaseSchema):
    """Schema generated for PaymentAdditionalInfoShipments.

    Attributes:
        receiver_address (Address | None): Undocumented in the spec.
    """

    receiver_address: Address | None = None


class PaymentAnalyticsResponse(BaseSchema):
    """Schema generated for PaymentAnalyticsResponse.

    Attributes:
        average_transaction_amount (Decimal | None): Average transaction amount in the
            period
        total_volume (Decimal): Total payment volume in the specified currency
        failed_transactions (int | None): Number of failed transactions
        currency (str | None): Currency for monetary values
        total_transactions (int): Total number of transactions in the period
        period (PaymentAnalyticsResponsePeriod): Undocumented in the spec.
        success_rate (float): Payment success rate as a percentage
        successful_transactions (int | None): Number of successful transactions
    """

    average_transaction_amount: Decimal | None = Field(
        description="Average transaction amount in the period",
        examples=[127.45],
        default=None,
    )
    total_volume: Decimal = Field(
        description="Total payment volume in the specified currency",
        examples=[125847.32],
    )
    failed_transactions: int | None = Field(
        description="Number of failed transactions",
        examples=[23],
        default=None,
    )
    currency: str | None = Field(
        description="Currency for monetary values",
        examples=["USD"],
        default=None,
    )
    total_transactions: int = Field(
        description="Total number of transactions in the period",
        examples=[1250],
    )
    period: PaymentAnalyticsResponsePeriod
    success_rate: float = Field(
        description="Payment success rate as a percentage",
        examples=[98.16],
        ge=0,
        le=100,
    )
    successful_transactions: int | None = Field(
        description="Number of successful transactions",
        examples=[1227],
        default=None,
    )


class PaymentCardCardholder(BaseSchema):
    """Schema generated for PaymentCardCardholder.

    Attributes:
        name (str | None): Undocumented in the spec.
        identification (Identification | None): Payer identification document. Valid
            types vary by country.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    identification: Identification | None = Field(
        description="Payer identification document. Valid types vary by country.",
        default=None,
    )


class PaymentMethod(BaseSchema):
    """Schema generated for PaymentMethod.

    Attributes:
        type (PaymentMethodType): Type of payment method
        card (CardDetails | None): Undocumented in the spec.
        bank_account (BankAccount | None): Undocumented in the spec.
        digital_wallet (DigitalWallet | None): Undocumented in the spec.
    """

    type: PaymentMethodType = Field(description="Type of payment method")
    card: CardDetails | None = None
    bank_account: BankAccount | None = None
    digital_wallet: DigitalWallet | None = None


class PaymentMethodListResponse(BaseSchema):
    """Schema generated for PaymentMethodListResponse.

    Attributes:
        data (list[StoredPaymentMethodResponse]): List of stored payment methods
    """

    data: list[StoredPaymentMethodResponse] = Field(
        description="List of stored payment methods",
    )


class PaymentPayer(BaseSchema):
    """Schema generated for PaymentPayer.

    Attributes:
        email (EmailStr): Undocumented in the spec.
        id (str | None): MercadoPago user ID (for registered users)
        identification (Identification | None): Payer identification document. Valid
            types vary by country.
        type (PayerType | None): Undocumented in the spec.
    """

    email: EmailStr = Field(examples=["customer@example.com"])
    id: str | None = Field(
        description="MercadoPago user ID (for registered users)",
        default=None,
    )
    identification: Identification | None = Field(
        description="Payer identification document. Valid types vary by country.",
        default=None,
    )
    type: PayerType | None = None


class PaymentPayer2(BaseSchema):
    """Schema generated for PaymentPayer2.

    Attributes:
        id (str | None): Undocumented in the spec.
        email (EmailStr | None): Undocumented in the spec.
        identification (Identification | None): Payer identification document. Valid
            types vary by country.
        type (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    email: EmailStr | None = None
    identification: Identification | None = Field(
        description="Payer identification document. Valid types vary by country.",
        default=None,
    )
    type: str | None = None


class PaymentResponse(BaseSchema):
    """Schema generated for PaymentResponse.

    Attributes:
        payment_id (UUID): Unique payment identifier
        status (PaymentResponseStatus): Current payment status
        amount (Decimal): Payment amount
        currency (str): Payment currency
        transaction_id (str | None): External transaction identifier from payment
            processor
        created_at (datetime): Payment creation timestamp
        updated_at (datetime | None): Last update timestamp
        fees (PaymentFees | None): Undocumented in the spec.
    """

    payment_id: UUID = Field(
        description="Unique payment identifier",
        examples=["987fcdeb-51a2-43d1-9c15-246531579012"],
    )
    status: PaymentResponseStatus = Field(
        description="Current payment status",
        examples=["completed"],
    )
    amount: Decimal = Field(description="Payment amount", examples=[29.99])
    currency: str = Field(description="Payment currency", examples=["USD"])
    transaction_id: str | None = Field(
        description="External transaction identifier from payment processor",
        examples=["txn_1234567890"],
        default=None,
    )
    created_at: datetime = Field(
        description="Payment creation timestamp",
        examples=["2024-01-15T10:30:00Z"],
    )
    updated_at: datetime | None = Field(
        description="Last update timestamp",
        examples=["2024-01-15T10:30:15Z"],
        default=None,
    )
    fees: PaymentFees | None = None


class PreferencePayer(BaseSchema):
    """Schema generated for PreferencePayer.

    Attributes:
        name (str | None): Undocumented in the spec.
        surname (str | None): Undocumented in the spec.
        email (EmailStr | None): Undocumented in the spec.
        phone (Phone | None): Undocumented in the spec.
        identification (Identification | None): Payer identification document. Valid
            types vary by country.
        address (Address | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    surname: str | None = None
    email: EmailStr | None = None
    phone: Phone | None = None
    identification: Identification | None = Field(
        description="Payer identification document. Valid types vary by country.",
        default=None,
    )
    address: Address | None = None
    date_created: datetime | None = None


class PreferencePaymentMethods(BaseSchema):
    """Schema generated for PreferencePaymentMethods.

    Attributes:
        excluded_payment_methods
            (list[PreferencePaymentMethodsExcludedPaymentMethodsItem]): Payment methods
            to hide from checkout UI
        excluded_payment_types (list[PreferencePaymentMethodsExcludedPaymentTypesItem]):
            Payment type groups to exclude
        installments (int | None): Maximum number of installments to offer
        default_installments (int | None): Pre-selected installments in the UI
    """

    model_config = ConfigDict(extra="allow")

    excluded_payment_methods: list[
        PreferencePaymentMethodsExcludedPaymentMethodsItem
    ] = Field(
        description="Payment methods to hide from checkout UI",
        default_factory=list,
    )
    excluded_payment_types: list[PreferencePaymentMethodsExcludedPaymentTypesItem] = (
        Field(
            description="Payment type groups to exclude",
            default_factory=list,
        )
    )
    installments: int | None = Field(
        description="Maximum number of installments to offer",
        examples=[12],
        default=None,
    )
    default_installments: int | None = Field(
        description="Pre-selected installments in the UI",
        default=None,
    )


class PreferenceShipments(BaseSchema):
    """Schema generated for PreferenceShipments.

    Attributes:
        mode (PreferenceShipmentsMode | None): Undocumented in the spec.
        free_shipping (bool | None): Undocumented in the spec.
        receiver_address (Address | None): Undocumented in the spec.
        cost (float | None): Undocumented in the spec.
    """

    mode: PreferenceShipmentsMode | None = None
    free_shipping: bool | None = None
    receiver_address: Address | None = None
    cost: float | None = None


class ProcessTransactionIntentBody(BaseSchema):
    """Schema generated for ProcessTransactionIntentBody.

    Attributes:
        external_reference (str | None): Undocumented in the spec.
        point_of_interaction (ProcessTransactionIntentBodyPointOfInteraction | None):
            Undocumented in the spec.
        transaction (ProcessTransactionIntentBodyTransaction | None): Undocumented in
            the spec.
    """

    external_reference: str | None = None
    point_of_interaction: ProcessTransactionIntentBodyPointOfInteraction | None = None
    transaction: ProcessTransactionIntentBodyTransaction | None = None


class Refund(BaseSchema):
    """A payment refund (partial or full).

    Attributes:
        id (int | None): Undocumented in the spec.
        payment_id (int | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        amount (float | None): Refunded amount (decimal)
        source (RefundSource | None): Undocumented in the spec.
        refund_mode (RefundRefundMode | None): Undocumented in the spec.
        status (RefundStatus | None): Undocumented in the spec.
    """

    id: int | None = Field(examples=[987654321], default=None)
    payment_id: int | None = None
    date_created: datetime | None = None
    amount: float | None = Field(description="Refunded amount (decimal)", default=None)
    source: RefundSource | None = None
    refund_mode: RefundRefundMode | None = None
    status: RefundStatus | None = None


class RefundListResponse(BaseSchema):
    """Schema generated for RefundListResponse.

    Attributes:
        data (list[RefundResponse]): List of refunds for the payment
        pagination (Pagination): Pagination metadata returned in list/search responses
    """

    data: list[RefundResponse] = Field(description="List of refunds for the payment")
    pagination: Pagination = Field(
        description="Pagination metadata returned in list/search responses",
    )


class ReportConfig(BaseSchema):
    """Configuration for scheduled report generation.

    Attributes:
        columns (list[ReportConfigColumnsItem]): Columns to include in the report
        file_name_prefix (str | None): Prefix for generated report filenames
        frequency (ReportConfigFrequency | None): Schedule frequency for automatic
            generation
        sftp_info (ReportConfigSftpInfo | None): SFTP destination for automatic delivery
            (optional)
        separator (ReportConfigSeparator | None): CSV column separator character
        display_timezone (str | None): Timezone for date columns (IANA format)
        notification_email_list (list[EmailStr]): Email addresses to notify when report
            is ready
    """

    model_config = ConfigDict(extra="allow")

    columns: list[ReportConfigColumnsItem] = Field(
        description="Columns to include in the report",
        default_factory=list,
    )
    file_name_prefix: str | None = Field(
        description="Prefix for generated report filenames",
        examples=["releases_report"],
        default=None,
    )
    frequency: ReportConfigFrequency | None = Field(
        description="Schedule frequency for automatic generation",
        default=None,
    )
    sftp_info: ReportConfigSftpInfo | None = Field(
        description="SFTP destination for automatic delivery (optional)",
        default=None,
    )
    separator: ReportConfigSeparator | None = Field(
        description="CSV column separator character",
        default=None,
    )
    display_timezone: str | None = Field(
        description="Timezone for date columns (IANA format)",
        examples=["America/Sao_Paulo"],
        default=None,
    )
    notification_email_list: list[EmailStr] = Field(
        description="Email addresses to notify when report is ready",
        default_factory=list,
    )


class ReportListResult(BaseSchema):
    """Schema generated for ReportListResult.

    Attributes:
        paging (Pagination | None): Pagination metadata returned in list/search
            responses
        results (list[ReportEntry]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    paging: Pagination | None = Field(
        description="Pagination metadata returned in list/search responses",
        default=None,
    )
    results: list[ReportEntry] = Field(default_factory=list)


class SearchMerchantOrdersResponse(BaseSchema):
    """Schema generated for SearchMerchantOrdersResponse.

    Attributes:
        id (int | None): Undocumented in the spec.
        status (MerchantOrderStatus | None): Undocumented in the spec.
        order_status (CreateMerchantOrderResponseOrderStatus | None): Undocumented in
            the spec.
        external_reference (str | None): Undocumented in the spec.
        preference_id (str | None): Undocumented in the spec.
        marketplace (str | None): Undocumented in the spec.
        application_id (str | None): Undocumented in the spec.
        site_id (CreateMerchantOrderBodySiteId | None): Undocumented in the spec.
        notification_url (str | None): Undocumented in the spec.
        sponsor_id (int | None): Undocumented in the spec.
        collector (SearchMerchantOrdersResponseCollector | None): Undocumented in the
            spec.
        payer (SearchMerchantOrdersResponsePayer | None): Undocumented in the spec.
        payments (list[dict[str, Any]]): Undocumented in the spec.
        shipments (list[dict[str, Any]]): Undocumented in the spec.
        payouts (list[dict[str, Any]]): Undocumented in the spec.
        items (list[dict[str, Any]]): Undocumented in the spec.
        additional_info (str | None): Undocumented in the spec.
        shipping_cost (float | None): Undocumented in the spec.
        total_amount (float | None): Undocumented in the spec.
        paid_amount (float | None): Undocumented in the spec.
        refunded_amount (float | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        last_updated (datetime | None): Undocumented in the spec.
        canceled (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    status: MerchantOrderStatus | None = None
    order_status: CreateMerchantOrderResponseOrderStatus | None = None
    external_reference: str | None = None
    preference_id: str | None = None
    marketplace: str | None = None
    application_id: str | None = None
    site_id: CreateMerchantOrderBodySiteId | None = None
    notification_url: str | None = None
    sponsor_id: int | None = None
    collector: SearchMerchantOrdersResponseCollector | None = None
    payer: SearchMerchantOrdersResponsePayer | None = None
    payments: list[dict[str, Any]] = Field(default_factory=list)
    shipments: list[dict[str, Any]] = Field(default_factory=list)
    payouts: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)
    additional_info: str | None = None
    shipping_cost: float | None = None
    total_amount: float | None = None
    paid_amount: float | None = None
    refunded_amount: float | None = None
    date_created: datetime | None = None
    last_updated: datetime | None = None
    canceled: bool | None = None


class SearchPosResponse(BaseSchema):
    """Schema generated for SearchPosResponse.

    Attributes:
        paging (Pagination | None): Pagination metadata returned in list/search
            responses
        results (list[Pos]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    paging: Pagination | None = Field(
        description="Pagination metadata returned in list/search responses",
        default=None,
    )
    results: list[Pos] = Field(default_factory=list)


class StoreBusinessHours(BaseSchema):
    """Schema generated for StoreBusinessHours.

    Attributes:
        monday (list[StoreBusinessHoursMondayItem]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    monday: list[StoreBusinessHoursMondayItem] = Field(default_factory=list)


class StoreRequestBusinessHours(BaseSchema):
    """Schema generated for StoreRequestBusinessHours.

    Attributes:
        monday (list[StoreRequestBusinessHoursMondayItem]): Undocumented in the spec.
    """

    monday: list[StoreRequestBusinessHoursMondayItem] = Field(default_factory=list)


class SubscriptionPlanRequestPaymentMethodsAllowed(BaseSchema):
    """Schema generated for SubscriptionPlanRequestPaymentMethodsAllowed.

    Attributes:
        payment_types (list[SubscriptionPlanRequestPaymentMethodsAllowedPaymentType]):
            Undocumented in the spec.
        payment_methods (list[SubscriptionPlanRequestPaymentMethodsAllowedPaymentMeth]):
            Undocumented in the spec.
    """

    payment_types: list[SubscriptionPlanRequestPaymentMethodsAllowedPaymentType] = (
        Field(
            default_factory=list,
        )
    )
    payment_methods: list[SubscriptionPlanRequestPaymentMethodsAllowedPaymentMeth] = (
        Field(
            default_factory=list,
        )
    )


class UpdateAdvancedPaymentBody(BaseSchema):
    """Schema generated for UpdateAdvancedPaymentBody.

    Attributes:
        capture (bool | None): Undocumented in the spec.
        status (PaymentUpdateRequestStatus | None): Undocumented in the spec.
        wallet_payment (UpdateAdvancedPaymentBodyWalletPayment | None): Undocumented in
            the spec.
    """

    capture: bool | None = None
    status: PaymentUpdateRequestStatus | None = None
    wallet_payment: UpdateAdvancedPaymentBodyWalletPayment | None = None


class UpdateAdvancedPaymentResponse(BaseSchema):
    """Schema generated for UpdateAdvancedPaymentResponse.

    Attributes:
        id (int | None): Undocumented in the spec.
        payments (list[dict[str, Any]]): Undocumented in the spec.
        wallet_payment (UpdateAdvancedPaymentResponseWalletPayment | None): Undocumented
            in the spec.
        disbursements (list[dict[str, Any]]): Undocumented in the spec.
        payer (UpdateAdvancedPaymentResponsePayer | None): Undocumented in the spec.
        site_id (str | None): Undocumented in the spec.
        binary_mode (bool | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        date_last_updated (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    payments: list[dict[str, Any]] = Field(default_factory=list)
    wallet_payment: UpdateAdvancedPaymentResponseWalletPayment | None = None
    disbursements: list[dict[str, Any]] = Field(default_factory=list)
    payer: UpdateAdvancedPaymentResponsePayer | None = None
    site_id: str | None = None
    binary_mode: bool | None = None
    date_created: datetime | None = None
    date_last_updated: datetime | None = None


class UpdateCardRequestCardholder(BaseSchema):
    """Schema generated for UpdateCardRequestCardholder.

    Attributes:
        name (str | None): Name as it appears on the card.
        identification (Identification | None): Payer identification document. Valid
            types vary by country.
    """

    name: str | None = Field(
        description="Name as it appears on the card.",
        default=None,
    )
    identification: Identification | None = Field(
        description="Payer identification document. Valid types vary by country.",
        default=None,
    )


class UpdateChargebackBody(BaseSchema):
    """Schema generated for UpdateChargebackBody.

    Attributes:
        files (list[UpdateChargebackBodyFilesItem]): Undocumented in the spec.
    """

    files: list[UpdateChargebackBodyFilesItem] = Field(default_factory=list)


class UpdateMerchantOrderBody(BaseSchema):
    """Schema generated for UpdateMerchantOrderBody.

    Attributes:
        external_reference (str | None): Undocumented in the spec.
        preference_id (str | None): Undocumented in the spec.
        marketplace (str | None): Undocumented in the spec.
        notification_url (str | None): Undocumented in the spec.
        sponsor_id (int | None): Undocumented in the spec.
        payer (UpdateMerchantOrderBodyPayer | None): Undocumented in the spec.
        site_id (CreateMerchantOrderBodySiteId | None): Undocumented in the spec.
        items (list[dict[str, Any]]): Undocumented in the spec.
        additional_info (str | None): Undocumented in the spec.
        application_id (str | None): Undocumented in the spec.
    """

    external_reference: str | None = None
    preference_id: str | None = None
    marketplace: str | None = None
    notification_url: str | None = None
    sponsor_id: int | None = None
    payer: UpdateMerchantOrderBodyPayer | None = None
    site_id: CreateMerchantOrderBodySiteId | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    additional_info: str | None = None
    application_id: str | None = None


class UpdateOrderTransactionBody(BaseSchema):
    """Schema generated for UpdateOrderTransactionBody.

    Attributes:
        payment_method (OrderPaymentMethod | None): Payment method for the transaction.
            Use GET /v1/payment_methods to list available methods for your site.
    """

    payment_method: OrderPaymentMethod | None = Field(
        description=(
            "Payment method for the transaction. Use GET /v1/payment_methods to list "
            "available methods for your site."
        ),
        default=None,
    )


class UpdateTerminalOperationModeBody(BaseSchema):
    """Schema generated for UpdateTerminalOperationModeBody.

    Attributes:
        terminals (list[UpdateTerminalOperationModeBodyTerminalsItem]): Undocumented in
            the spec.
    """

    terminals: list[UpdateTerminalOperationModeBodyTerminalsItem]


class ValidationResponse(BaseSchema):
    """Schema generated for ValidationResponse.

    Attributes:
        is_valid (bool): Whether the payment method is valid
        suggestions (list[str]): Suggestions for fixing validation issues
        errors (list[ValidationResponseErrorsItem]): List of validation errors
    """

    is_valid: bool = Field(
        description="Whether the payment method is valid",
        examples=[True],
    )
    suggestions: list[str] = Field(
        description="Suggestions for fixing validation issues",
        default_factory=list,
    )
    errors: list[ValidationResponseErrorsItem] = Field(
        description="List of validation errors",
        default_factory=list,
    )


class WebhookEventListResponse(BaseSchema):
    """Schema generated for WebhookEventListResponse.

    Attributes:
        data (list[WebhookEvent]): List of webhook events
        pagination (Pagination): Pagination metadata returned in list/search responses
    """

    data: list[WebhookEvent] = Field(description="List of webhook events")
    pagination: Pagination = Field(
        description="Pagination metadata returned in list/search responses",
    )


class WebhookNotification(BaseSchema):
    """Webhook notification payload sent to your notification URL when a resource
    changes. Validate the x-signature HMAC-SHA256 header before processing. After
    receiving, GET the resource URL to fetch the full updated object.

    Attributes:
        id (int): Notification unique ID (use for deduplication)
        type (WebhookNotificationType): Resource type that triggered the notification
        date_created (datetime | None): Undocumented in the spec.
        user_id (int | None): MP user ID that owns the resource
        api_version (str | None): Undocumented in the spec.
        action (WebhookNotificationAction | None): Undocumented in the spec.
        data (WebhookNotificationData): Undocumented in the spec.
    """

    id: int = Field(
        description="Notification unique ID (use for deduplication)",
        examples=[12345678],
    )
    type: WebhookNotificationType = Field(
        description="Resource type that triggered the notification",
        examples=["payment"],
    )
    date_created: datetime | None = None
    user_id: int | None = Field(
        description="MP user ID that owns the resource",
        default=None,
    )
    api_version: str | None = Field(examples=["v1"], default=None)
    action: WebhookNotificationAction | None = Field(
        examples=["payment.updated"],
        default=None,
    )
    data: WebhookNotificationData


class AddOrderTransactionBody(BaseSchema):
    """Schema generated for AddOrderTransactionBody.

    Attributes:
        payments (list[OrderPayment]): Undocumented in the spec.
    """

    payments: list[OrderPayment]


class AuthorizedPaymentSearchResult(BaseSchema):
    """Schema generated for AuthorizedPaymentSearchResult.

    Attributes:
        paging (Pagination | None): Pagination metadata returned in list/search
            responses
        results (list[AuthorizedPayment]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    paging: Pagination | None = Field(
        description="Pagination metadata returned in list/search responses",
        default=None,
    )
    results: list[AuthorizedPayment] = Field(default_factory=list)


class Card(BaseSchema):
    """A tokenized card saved to a customer (never contains full PAN or CVV).

    Attributes:
        id (str | None): Card unique identifier
        customer_id (str | None): Undocumented in the spec.
        first_six_digits (str | None): First 6 digits of card (BIN — identifies issuer
            and type)
        last_four_digits (str | None): Last 4 digits of card
        expiration_month (int | None): Undocumented in the spec.
        expiration_year (int | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        date_last_updated (datetime | None): Undocumented in the spec.
        cardholder (CardCardholder | None): Undocumented in the spec.
        payment_method (CardPaymentMethod | None): Undocumented in the spec.
        issuer (CardIssuer | None): Undocumented in the spec.
        security_code (CardSecurityCode | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(
        description="Card unique identifier",
        examples=["1234567890"],
        default=None,
    )
    customer_id: str | None = None
    first_six_digits: str | None = Field(
        description="First 6 digits of card (BIN — identifies issuer and type)",
        examples=["411111"],
        default=None,
    )
    last_four_digits: str | None = Field(
        description="Last 4 digits of card",
        examples=["4321"],
        default=None,
    )
    expiration_month: int | None = Field(examples=[12], ge=1, le=12, default=None)
    expiration_year: int | None = Field(examples=[2028], default=None)
    date_created: datetime | None = None
    date_last_updated: datetime | None = None
    cardholder: CardCardholder | None = None
    payment_method: CardPaymentMethod | None = None
    issuer: CardIssuer | None = None
    security_code: CardSecurityCode | None = None


class CardToken(BaseSchema):
    """A single-use card token representing a card. Expires in 7 days or after first
    use. Use this token in payment or order creation requests.

    Attributes:
        id (str | None): Token unique identifier — use as the `token` field in payments
        first_six_digits (str | None): Undocumented in the spec.
        last_four_digits (str | None): Undocumented in the spec.
        expiration_month (int | None): Undocumented in the spec.
        expiration_year (int | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        date_due (datetime | None): Token expiry (7 days after creation)
        luhn_validation (bool | None): Undocumented in the spec.
        status (CardTokenStatus | None): Undocumented in the spec.
        card_id (str | None): Linked saved card ID (if token was created from a saved
            card)
        cardholder (CardTokenCardholder | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(
        description="Token unique identifier — use as the `token` field in payments",
        examples=["YOUR_ACCESS_TOKEN"],
        default=None,
    )
    first_six_digits: str | None = Field(examples=["411111"], default=None)
    last_four_digits: str | None = Field(examples=["4321"], default=None)
    expiration_month: int | None = None
    expiration_year: int | None = None
    date_created: datetime | None = None
    date_due: datetime | None = Field(
        description="Token expiry (7 days after creation)",
        default=None,
    )
    luhn_validation: bool | None = None
    status: CardTokenStatus | None = None
    card_id: str | None = Field(
        description="Linked saved card ID (if token was created from a saved card)",
        default=None,
    )
    cardholder: CardTokenCardholder | None = None


class CardTokenRequest(BaseSchema):
    """Card tokenization request. This is a CLIENT-SIDE ONLY operation — use
    MercadoPago.js or MP Secure Fields to call this endpoint from the browser. Never
    send raw card data through your server.

    Attributes:
        card_number (str): Full card PAN — client-side only, never server-side
        expiration_month (int): Undocumented in the spec.
        expiration_year (int): Undocumented in the spec.
        security_code (str): CVV / CVC — client-side only
        cardholder (CardTokenRequestCardholder): Undocumented in the spec.
    """

    card_number: str = Field(
        description="Full card PAN — client-side only, never server-side",
        examples=["4111111111111111"],
    )
    expiration_month: int = Field(examples=[12], ge=1, le=12)
    expiration_year: int = Field(examples=[2028])
    security_code: str = Field(
        description="CVV / CVC — client-side only",
        examples=["123"],
    )
    cardholder: CardTokenRequestCardholder


class ClaimSearchResult(BaseSchema):
    """Schema generated for ClaimSearchResult.

    Attributes:
        paging (Pagination | None): Pagination metadata returned in list/search
            responses
        results (list[Claim]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    paging: Pagination | None = Field(
        description="Pagination metadata returned in list/search responses",
        default=None,
    )
    results: list[Claim] = Field(default_factory=list)


class MerchantListResponse(BaseSchema):
    """Schema generated for MerchantListResponse.

    Attributes:
        data (list[MerchantResponse]): List of merchants
        pagination (Pagination): Pagination metadata returned in list/search responses
    """

    data: list[MerchantResponse] = Field(description="List of merchants")
    pagination: Pagination = Field(
        description="Pagination metadata returned in list/search responses",
    )


class OrderConfig(BaseSchema):
    """Optional settings for the order.

    Attributes:
        online (OrderConfigOnline | None): Settings for online card transactions.
    """

    model_config = ConfigDict(extra="allow")

    online: OrderConfigOnline | None = Field(
        description="Settings for online card transactions.",
        default=None,
    )


class OrderTransactionPayment(BaseSchema):
    """Schema generated for OrderTransactionPayment.

    Attributes:
        id (str | None): Transaction ID, automatically generated by MercadoPago.
        amount (str | None): Transaction amount.
        paid_amount (str | None): Amount effectively paid.
        reference_id (str | None): External reference for this transaction.
        status (OrderTransactionPaymentStatus | None): created — transaction created;
            processed — transaction successfully processed; action_required — integrator
            must act (e.g. capture); processing — awaiting asynchronous result.
        status_detail (OrderTransactionPaymentStatusDetail | None): Undocumented in the
            spec.
        date_of_expiration (datetime | None): Undocumented in the spec.
        expiration_time (str | None): Expiration duration in ISO 8601 format.
        payment_method (OrderTransactionPaymentPaymentMethod | None): Undocumented in
            the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(
        description="Transaction ID, automatically generated by MercadoPago.",
        default=None,
    )
    amount: str | None = Field(
        description="Transaction amount.",
        examples=["100.00"],
        default=None,
    )
    paid_amount: str | None = Field(
        description="Amount effectively paid.",
        default=None,
    )
    reference_id: str | None = Field(
        description="External reference for this transaction.",
        default=None,
    )
    status: OrderTransactionPaymentStatus | None = Field(
        description=(
            "created — transaction created; processed — transaction successfully "
            "processed; action_required — integrator must act (e.g. capture); "
            "processing — awaiting asynchronous result."
        ),
        default=None,
    )
    status_detail: OrderTransactionPaymentStatusDetail | None = None
    date_of_expiration: datetime | None = None
    expiration_time: str | None = Field(
        description="Expiration duration in ISO 8601 format.",
        default=None,
    )
    payment_method: OrderTransactionPaymentPaymentMethod | None = None


class OrderTransactions(BaseSchema):
    """Payment transactions for this order. Currently supports one transaction.

    Attributes:
        payments (list[OrderPayment]): Payment transaction(s). Required when
            processing_mode=automatic. Must not be present when processing_mode=manual
            (add via POST /orders/{id}/transactions).
    """

    payments: list[OrderPayment] = Field(
        description=(
            "Payment transaction(s). Required when processing_mode=automatic. Must not "
            "be present when processing_mode=manual (add via POST "
            "/orders/{id}/transactions)."
        ),
        default_factory=list,
    )


class PaymentAdditionalInfo(BaseSchema):
    """Additional context for fraud scoring and installment calculation.

    Attributes:
        items (list[PaymentItem]): Undocumented in the spec.
        payer (PaymentAdditionalInfoPayer | None): Undocumented in the spec.
        shipments (PaymentAdditionalInfoShipments | None): Undocumented in the spec.
    """

    items: list[PaymentItem] = Field(default_factory=list)
    payer: PaymentAdditionalInfoPayer | None = None
    shipments: PaymentAdditionalInfoShipments | None = None


class PaymentCard(BaseSchema):
    """Card data (last 4 digits only — never full PAN).

    Attributes:
        last_four_digits (str | None): Undocumented in the spec.
        first_six_digits (str | None): Undocumented in the spec.
        expiration_year (int | None): Undocumented in the spec.
        expiration_month (int | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        date_last_updated (datetime | None): Undocumented in the spec.
        cardholder (PaymentCardCardholder | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    last_four_digits: str | None = Field(examples=["4321"], default=None)
    first_six_digits: str | None = Field(examples=["411111"], default=None)
    expiration_year: int | None = None
    expiration_month: int | None = None
    date_created: datetime | None = None
    date_last_updated: datetime | None = None
    cardholder: PaymentCardCardholder | None = None


class PaymentListResponse(BaseSchema):
    """Schema generated for PaymentListResponse.

    Attributes:
        pagination (Pagination): Pagination metadata returned in list/search responses
        data (list[PaymentResponse]): List of payments
    """

    pagination: Pagination = Field(
        description="Pagination metadata returned in list/search responses",
    )
    data: list[PaymentResponse] = Field(description="List of payments")


class PaymentMethodStoreRequest(BaseSchema):
    """Schema generated for PaymentMethodStoreRequest.

    Attributes:
        is_default (bool): Set as the default payment method for this customer
        payment_method (PaymentMethod): Undocumented in the spec.
    """

    is_default: bool = Field(
        description="Set as the default payment method for this customer",
        examples=[True],
    )
    payment_method: PaymentMethod


class Preference(BaseSchema):
    """A created Checkout Pro preference.

    Attributes:
        id (str | None): Preference unique identifier
        init_point (str | None): Production checkout URL. Redirect your payer here to
            complete payment.
        items (list[PreferenceItem]): Undocumented in the spec.
        payer (PreferencePayer | None): Undocumented in the spec.
        payment_methods (PreferencePaymentMethods | None): Undocumented in the spec.
        back_urls (PreferenceBackUrls | None): Redirect URLs after checkout completion.
            Configure auto_return to control when automatic redirects happen.
        external_reference (str | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        last_updated (datetime | None): Undocumented in the spec.
        total_amount (float | None): Undocumented in the spec.
        expires (bool | None): Undocumented in the spec.
        expiration_date_from (datetime | None): Undocumented in the spec.
        expiration_date_to (datetime | None): Undocumented in the spec.
        collector_id (int | None): Undocumented in the spec.
        client_id (str | None): Undocumented in the spec.
        marketplace (str | None): Undocumented in the spec.
        marketplace_fee (float | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(
        description="Preference unique identifier",
        examples=["123456789-abcd1234-ef56-7890-abcd-ef1234567890"],
        default=None,
    )
    init_point: str | None = Field(
        description=(
            "Production checkout URL. Redirect your payer here to complete payment."
        ),
        examples=["https://www.mercadopago.com.br/checkout/v1/redirect?pref_id=..."],
        default=None,
    )
    items: list[PreferenceItem] = Field(default_factory=list)
    payer: PreferencePayer | None = None
    payment_methods: PreferencePaymentMethods | None = None
    back_urls: PreferenceBackUrls | None = Field(
        description=(
            "Redirect URLs after checkout completion. Configure auto_return to control "
            "when automatic redirects happen."
        ),
        default=None,
    )
    external_reference: str | None = None
    date_created: datetime | None = None
    last_updated: datetime | None = None
    total_amount: float | None = None
    expires: bool | None = None
    expiration_date_from: datetime | None = None
    expiration_date_to: datetime | None = None
    collector_id: int | None = None
    client_id: str | None = None
    marketplace: str | None = None
    marketplace_fee: float | None = None


class PreferenceRequest(BaseSchema):
    """Request body to create a Checkout Pro preference. The preference generates a
    hosted checkout URL (init_point) where the payer is redirected to complete payment
    on MP's UI. Use this for simple integrations; use Orders API for custom checkout
    experiences.

    Attributes:
        items (list[PreferenceItem]): List of products/services being purchased
        payer (PreferencePayer | None): Undocumented in the spec.
        payment_methods (PreferencePaymentMethods | None): Undocumented in the spec.
        shipments (PreferenceShipments | None): Undocumented in the spec.
        back_urls (PreferenceBackUrls | None): Redirect URLs after checkout completion.
            Configure auto_return to control when automatic redirects happen.
        notification_url (str | None): URL for IPN notifications. DEPRECATED — use
            Webhooks instead.
        statement_descriptor (str | None): Text on the payer's card statement. Max 22
            chars.
        additional_info (str | None): Free-text additional info (returned in webhook
            notifications)
        auto_return (PreferenceRequestAutoReturn | None): approved — auto-redirect to
            success URL on approved payments; all — auto-redirect for any final status.
        external_reference (str | None): Your internal order reference. Returned in
            webhook payloads.
        expires (bool | None): Whether this preference has an expiry window
        expiration_date_from (datetime | None): Preference activation start time (ISO
            8601)
        expiration_date_to (datetime | None): Preference expiration time (ISO 8601)
        marketplace (str | None): Marketplace identifier
        marketplace_fee (float | None): Marketplace fee charged to the seller
        differential_pricing (PreferenceRequestDifferentialPricing | None): Undocumented
            in the spec.
        binary_mode (bool | None): Undocumented in the spec.
    """

    items: list[PreferenceItem] = Field(
        description="List of products/services being purchased",
        min_length=1,
    )
    payer: PreferencePayer | None = None
    payment_methods: PreferencePaymentMethods | None = None
    shipments: PreferenceShipments | None = None
    back_urls: PreferenceBackUrls | None = Field(
        description=(
            "Redirect URLs after checkout completion. Configure auto_return to control "
            "when automatic redirects happen."
        ),
        default=None,
    )
    notification_url: str | None = Field(
        description="URL for IPN notifications. DEPRECATED — use Webhooks instead.",
        default=None,
    )
    statement_descriptor: str | None = Field(
        description="Text on the payer's card statement. Max 22 chars.",
        max_length=22,
        default=None,
    )
    additional_info: str | None = Field(
        description="Free-text additional info (returned in webhook notifications)",
        default=None,
    )
    auto_return: PreferenceRequestAutoReturn | None = Field(
        description=(
            "approved — auto-redirect to success URL on approved payments; all — "
            "auto-redirect for any final status."
        ),
        default=None,
    )
    external_reference: str | None = Field(
        description="Your internal order reference. Returned in webhook payloads.",
        examples=["ORDER-2024-001234"],
        default=None,
    )
    expires: bool | None = Field(
        description="Whether this preference has an expiry window",
        default=None,
    )
    expiration_date_from: datetime | None = Field(
        description="Preference activation start time (ISO 8601)",
        default=None,
    )
    expiration_date_to: datetime | None = Field(
        description="Preference expiration time (ISO 8601)",
        default=None,
    )
    marketplace: str | None = Field(description="Marketplace identifier", default=None)
    marketplace_fee: float | None = Field(
        description="Marketplace fee charged to the seller",
        default=None,
    )
    differential_pricing: PreferenceRequestDifferentialPricing | None = None
    binary_mode: bool | None = None


class Store(BaseSchema):
    """Schema generated for Store.

    Attributes:
        name (str): Store display name
        external_id (str | None): Your internal store identifier
        business_hours (StoreBusinessHours | None): Undocumented in the spec.
        location (StoreLocation | None): Undocumented in the spec.
        id (str | None): Store unique identifier
        date_created (datetime | None): Undocumented in the spec.
        date_last_updated (datetime | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(
        description="Store display name",
        examples=["Main Branch — Paulista"],
    )
    external_id: str | None = Field(
        description="Your internal store identifier",
        default=None,
    )
    business_hours: StoreBusinessHours | None = None
    location: StoreLocation | None = None
    id: str | None = Field(description="Store unique identifier", default=None)
    date_created: datetime | None = None
    date_last_updated: datetime | None = None


class StoreRequest(BaseSchema):
    """Schema generated for StoreRequest.

    Attributes:
        name (str): Store display name
        external_id (str | None): Your internal store identifier
        business_hours (StoreRequestBusinessHours | None): Undocumented in the spec.
        location (StoreRequestLocation | None): Undocumented in the spec.
    """

    name: str = Field(
        description="Store display name",
        examples=["Main Branch — Paulista"],
    )
    external_id: str | None = Field(
        description="Your internal store identifier",
        default=None,
    )
    business_hours: StoreRequestBusinessHours | None = None
    location: StoreRequestLocation | None = None


class Subscription(BaseSchema):
    """A MercadoPago subscription (preapproval).

    Attributes:
        id (str | None): Undocumented in the spec.
        payer_id (int | None): Undocumented in the spec.
        payer_email (EmailStr | None): Undocumented in the spec.
        preapproval_plan_id (str | None): Undocumented in the spec.
        reason (str | None): Undocumented in the spec.
        external_reference (str | None): Undocumented in the spec.
        status (SubscriptionRequestStatus | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        last_modified (datetime | None): Undocumented in the spec.
        auto_recurring (AutoRecurring | None): Undocumented in the spec.
        summarized (SubscriptionSummarized | None): Subscription billing summary
        next_payment_date (datetime | None): Undocumented in the spec.
        payment_method_id (str | None): Undocumented in the spec.
        card_id (int | None): Undocumented in the spec.
        init_point (str | None): Authorization URL — send payer here to confirm
            subscription
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(examples=["2c9380847e9c5058017ea64ea8260b60"], default=None)
    payer_id: int | None = None
    payer_email: EmailStr | None = None
    preapproval_plan_id: str | None = None
    reason: str | None = None
    external_reference: str | None = None
    status: SubscriptionRequestStatus | None = None
    date_created: datetime | None = None
    last_modified: datetime | None = None
    auto_recurring: AutoRecurring | None = None
    summarized: SubscriptionSummarized | None = Field(
        description="Subscription billing summary",
        default=None,
    )
    next_payment_date: datetime | None = None
    payment_method_id: str | None = None
    card_id: int | None = None
    init_point: str | None = Field(
        description="Authorization URL — send payer here to confirm subscription",
        default=None,
    )


class SubscriptionPlan(BaseSchema):
    """A subscription plan (preapproval_plan).

    Attributes:
        id (str | None): Undocumented in the spec.
        reason (str | None): Undocumented in the spec.
        status (SubscriptionPlanStatus | None): Undocumented in the spec.
        auto_recurring (AutoRecurring | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        last_modified (datetime | None): Undocumented in the spec.
        init_point (str | None): URL to subscribe users to this plan
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(examples=["2c9380847e9c5058017ea64ea8260b60"], default=None)
    reason: str | None = None
    status: SubscriptionPlanStatus | None = None
    auto_recurring: AutoRecurring | None = None
    date_created: datetime | None = None
    last_modified: datetime | None = None
    init_point: str | None = Field(
        description="URL to subscribe users to this plan",
        default=None,
    )


class SubscriptionPlanRequest(BaseSchema):
    """Request body to create a subscription plan (preapproval_plan). A plan defines the
    recurring billing terms. Individual subscriptions (preapproval) are then created
    referencing this plan.

    Attributes:
        reason (str): Plan description shown to the payer
        auto_recurring (AutoRecurring): Undocumented in the spec.
        payment_methods_allowed (SubscriptionPlanRequestPaymentMethodsAllowed | None):
            Undocumented in the spec.
        back_url (str | None): Redirect URL after subscription setup
    """

    reason: str = Field(
        description="Plan description shown to the payer",
        examples=["Monthly Premium Subscription"],
    )
    auto_recurring: AutoRecurring
    payment_methods_allowed: SubscriptionPlanRequestPaymentMethodsAllowed | None = None
    back_url: str | None = Field(
        description="Redirect URL after subscription setup",
        default=None,
    )


class SubscriptionRequest(BaseSchema):
    """Request body to create a subscription (preapproval). Either reference a
    preapproval_plan or define auto_recurring inline.

    Attributes:
        preapproval_plan_id (str | None): Reference a previously created plan
            (recommended)
        reason (str): Subscription description shown to payer
        payer_email (EmailStr): Payer's email address
        card_token_id (str | None): Card token for credit card subscriptions
        auto_recurring (AutoRecurring): Undocumented in the spec.
        back_url (str | None): Undocumented in the spec.
        external_reference (str | None): Undocumented in the spec.
        status (SubscriptionRequestStatus | None): Initial status (default is pending
            until payer authorizes)
    """

    preapproval_plan_id: str | None = Field(
        description="Reference a previously created plan (recommended)",
        default=None,
    )
    reason: str = Field(
        description="Subscription description shown to payer",
        examples=["Monthly Premium — Acme Inc."],
    )
    payer_email: EmailStr = Field(description="Payer's email address")
    card_token_id: str | None = Field(
        description="Card token for credit card subscriptions",
        default=None,
    )
    auto_recurring: AutoRecurring
    back_url: str | None = None
    external_reference: str | None = Field(examples=["CUSTOMER-ID-12345"], default=None)
    status: SubscriptionRequestStatus | None = Field(
        description="Initial status (default is pending until payer authorizes)",
        default=None,
    )


class SubscriptionUpdateRequest(BaseSchema):
    """Request body to update a subscription.

    Attributes:
        reason (str | None): Undocumented in the spec.
        external_reference (str | None): Undocumented in the spec.
        back_url (str | None): Undocumented in the spec.
        auto_recurring (AutoRecurring | None): Undocumented in the spec.
        card_token_id (str | None): New card token to update the payment method.
        card_token_id_secondary (str | None): Token for secondary/fallback payment
            method card.
        payment_method_id_secondary (str | None): Secondary payment method identifier
            for fallback charges.
        status (SubscriptionRequestStatus | None): Undocumented in the spec.
    """

    reason: str | None = None
    external_reference: str | None = None
    back_url: str | None = None
    auto_recurring: AutoRecurring | None = None
    card_token_id: str | None = Field(
        description="New card token to update the payment method.",
        default=None,
    )
    card_token_id_secondary: str | None = Field(
        description="Token for secondary/fallback payment method card.",
        default=None,
    )
    payment_method_id_secondary: str | None = Field(
        description="Secondary payment method identifier for fallback charges.",
        default=None,
    )
    status: SubscriptionRequestStatus | None = None


class UpdateCardRequest(BaseSchema):
    """Request body to update a saved card's expiry or cardholder data.

    Attributes:
        expiration_month (int | None): Undocumented in the spec.
        expiration_year (int | None): Undocumented in the spec.
        cardholder (UpdateCardRequestCardholder | None): Undocumented in the spec.
    """

    expiration_month: int | None = Field(ge=1, le=12, default=None)
    expiration_year: int | None = None
    cardholder: UpdateCardRequestCardholder | None = None


class AddOrderTransactionResponse(BaseSchema):
    """Schema generated for AddOrderTransactionResponse.

    Attributes:
        payments (list[OrderTransactionPayment]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    payments: list[OrderTransactionPayment] = Field(default_factory=list)


class Customer(BaseSchema):
    """A stored MercadoPago customer.

    Attributes:
        id (str | None): Customer unique identifier
        email (EmailStr | None): Undocumented in the spec.
        first_name (str | None): Undocumented in the spec.
        last_name (str | None): Undocumented in the spec.
        phone (Phone | None): Undocumented in the spec.
        identification (Identification | None): Payer identification document. Valid
            types vary by country.
        address (Address | None): Undocumented in the spec.
        date_registered (datetime | None): Undocumented in the spec.
        date_created (datetime | None): Undocumented in the spec.
        date_last_updated (datetime | None): Undocumented in the spec.
        description (str | None): Undocumented in the spec.
        metadata (dict[str, Any] | None): Undocumented in the spec.
        cards (list[Card]): Saved cards for this customer
        default_card (str | None): ID of the customer's default card
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(
        description="Customer unique identifier",
        examples=["123456789-abcd"],
        default=None,
    )
    email: EmailStr | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: Phone | None = None
    identification: Identification | None = Field(
        description="Payer identification document. Valid types vary by country.",
        default=None,
    )
    address: Address | None = None
    date_registered: datetime | None = None
    date_created: datetime | None = None
    date_last_updated: datetime | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None
    cards: list[Card] = Field(
        description="Saved cards for this customer",
        default_factory=list,
    )
    default_card: str | None = Field(
        description="ID of the customer's default card",
        default=None,
    )


class OrderRequest(BaseSchema):
    """Request body to create an Order. Supports automatic (single-stage) and manual
    (multi-stage) processing modes. Use processing_mode=automatic for most integrations.

    Attributes:
        type (OrderRequestType): Order type. Only "online" is supported for online
            payments.
        processing_mode (OrderRequestProcessingMode | None): automatic — MP processes
            all transactions immediately in a single stage. manual — transactions are
            processed in configurable stages; use the /process endpoint to trigger
            processing after creation.
        capture_mode (OrderRequestCaptureMode | None): automatic — authorize and capture
            funds at the same time. manual — authorize only (reserve funds); capture
            later with /capture endpoint. automatic_async — order may remain in
            status=processing while awaiting async update; final status delivered via
            webhook.
        total_amount (str): Total amount to be paid as a decimal string. Must equal the
            sum of all payment transaction amounts. Example: "100.00".
        external_reference (str | None): Your internal order reference ID. Returned in
            all order responses.
        description (str | None): Description of the purchased product or service.
        payer (OrderPayer): Undocumented in the spec.
        transactions (OrderTransactions): Payment transactions for this order. Currently
            supports one transaction.
        config (OrderConfig | None): Optional settings for the order.
        items (list[OrderItem]): Items included in the order.
        shipment (OrderShipment | None): Undocumented in the spec.
        additional_info (OrderRequestAdditionalInfo | None): Additional information
            required for specific payment methods (e.g. PSE).
        integration_data (OrderRequestIntegrationData | None): Integration metadata used
            by MercadoPago internally.
    """

    type: OrderRequestType = Field(
        description='Order type. Only "online" is supported for online payments.',
        examples=["online"],
    )
    processing_mode: OrderRequestProcessingMode | None = Field(
        description=(
            "automatic — MP processes all transactions immediately in a single stage. "
            "manual — transactions are processed in configurable stages; use the "
            "/process endpoint to trigger processing after creation."
        ),
        examples=["automatic"],
        default=None,
    )
    capture_mode: OrderRequestCaptureMode | None = Field(
        description=(
            "automatic — authorize and capture funds at the same time. manual — "
            "authorize only (reserve funds); capture later with /capture endpoint. "
            "automatic_async — order may remain in status=processing while awaiting "
            "async update; final status delivered via webhook."
        ),
        examples=["automatic"],
        default=None,
    )
    total_amount: str = Field(
        description=(
            "Total amount to be paid as a decimal string. Must equal the sum of all "
            'payment transaction amounts. Example: "100.00".'
        ),
        examples=["100.00"],
    )
    external_reference: str | None = Field(
        description=(
            "Your internal order reference ID. Returned in all order responses."
        ),
        examples=["ORDER-2024-001234"],
        default=None,
    )
    description: str | None = Field(
        description="Description of the purchased product or service.",
        examples=["Premium subscription"],
        default=None,
    )
    payer: OrderPayer
    transactions: OrderTransactions = Field(
        description=(
            "Payment transactions for this order. Currently supports one transaction."
        ),
    )
    config: OrderConfig | None = Field(
        description="Optional settings for the order.",
        default=None,
    )
    items: list[OrderItem] = Field(
        description="Items included in the order.",
        default_factory=list,
    )
    shipment: OrderShipment | None = None
    additional_info: OrderRequestAdditionalInfo | None = Field(
        description=(
            "Additional information required for specific payment methods (e.g. PSE)."
        ),
        default=None,
    )
    integration_data: OrderRequestIntegrationData | None = Field(
        description="Integration metadata used by MercadoPago internally.",
        default=None,
    )


class OrderTransactions2(BaseSchema):
    """Schema generated for OrderTransactions2.

    Attributes:
        payments (list[OrderTransactionPayment]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    payments: list[OrderTransactionPayment] = Field(default_factory=list)


class Payment(BaseSchema):
    """A MercadoPago payment object.

    Attributes:
        id (int | None): Payment unique identifier
        date_created (datetime | None): Undocumented in the spec.
        date_approved (datetime | None): Undocumented in the spec.
        date_last_updated (datetime | None): Undocumented in the spec.
        date_of_expiration (datetime | None): Undocumented in the spec.
        money_release_date (datetime | None): Undocumented in the spec.
        operation_type (PaymentOperationType | None): Undocumented in the spec.
        issuer_id (str | None): Undocumented in the spec.
        payment_method_id (str | None): Undocumented in the spec.
        payment_type_id (PaymentPaymentTypeId | None): Undocumented in the spec.
        status (PaymentStatus | None): Current payment status. Key values: approved —
            payment completed successfully; pending — awaiting payer action (e.g.,
            Boleto payment); in_process — under review; rejected — payment declined.
        status_detail (str | None): Detailed reason for the current status
        currency_id (CurrencyId | None): ISO 4217 currency code for the applicable site
        description (str | None): Undocumented in the spec.
        payer (PaymentPayer2 | None): Undocumented in the spec.
        metadata (dict[str, Any] | None): Undocumented in the spec.
        additional_info (dict[str, Any] | None): Undocumented in the spec.
        external_reference (str | None): Undocumented in the spec.
        transaction_amount (float | None): Original payment amount (decimal, not cents)
        transaction_amount_refunded (float | None): Total amount refunded so far
        coupon_amount (float | None): Undocumented in the spec.
        transaction_details (PaymentTransactionDetails | None): Undocumented in the
            spec.
        captured (bool | None): Undocumented in the spec.
        binary_mode (bool | None): Undocumented in the spec.
        statement_descriptor (str | None): Undocumented in the spec.
        installments (int | None): Undocumented in the spec.
        card (PaymentCard | None): Card data (last 4 digits only — never full PAN)
        notification_url (str | None): Undocumented in the spec.
        processing_mode (PaymentProcessingMode | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = Field(
        description="Payment unique identifier",
        examples=[1234567890],
        default=None,
    )
    date_created: datetime | None = Field(
        examples=["2024-01-15T10:30:00.000-03:00"],
        default=None,
    )
    date_approved: datetime | None = None
    date_last_updated: datetime | None = None
    date_of_expiration: datetime | None = None
    money_release_date: datetime | None = None
    operation_type: PaymentOperationType | None = None
    issuer_id: str | None = None
    payment_method_id: str | None = Field(examples=["visa"], default=None)
    payment_type_id: PaymentPaymentTypeId | None = None
    status: PaymentStatus | None = Field(
        description=(
            "Current payment status. Key values: approved — payment completed "
            "successfully; pending — awaiting payer action (e.g., Boleto payment); "
            "in_process — under review; rejected — payment declined."
        ),
        examples=["approved"],
        default=None,
    )
    status_detail: str | None = Field(
        description="Detailed reason for the current status",
        examples=["accredited"],
        default=None,
    )
    currency_id: CurrencyId | None = Field(
        description="ISO 4217 currency code for the applicable site",
        examples=["BRL"],
        default=None,
    )
    description: str | None = None
    payer: PaymentPayer2 | None = None
    metadata: dict[str, Any] | None = None
    additional_info: dict[str, Any] | None = None
    external_reference: str | None = None
    transaction_amount: float | None = Field(
        description="Original payment amount (decimal, not cents)",
        default=None,
    )
    transaction_amount_refunded: float | None = Field(
        description="Total amount refunded so far",
        default=None,
    )
    coupon_amount: float | None = None
    transaction_details: PaymentTransactionDetails | None = None
    captured: bool | None = None
    binary_mode: bool | None = None
    statement_descriptor: str | None = None
    installments: int | None = None
    card: PaymentCard | None = Field(
        description="Card data (last 4 digits only — never full PAN)",
        default=None,
    )
    notification_url: str | None = None
    processing_mode: PaymentProcessingMode | None = None


class PaymentRequest(BaseSchema):
    """Request body to create a payment. For card payments, `token` and
    `payment_method_id` are required. For cash/offline methods, only `payment_method_id`
    is required.

    Attributes:
        transaction_amount (float): Payment amount as a decimal number. MercadoPago does
            NOT use integer cents — send 100.50 for R$100,50 (not 10050). CLP uses 0
            decimal places.
        token (str | None): Card token created client-side via MercadoPago.js / MP
            Secure Fields. Required for credit/debit card payments. Single-use; expires
            in 7 days.
        description (str | None): Description of the purchased product or service
        installments (int | None): Number of installments (1 = no installments)
        payment_method_id (str | None): Payment method identifier. Examples: visa,
            master, bolbradesco (Boleto), pix, oxxo, rapipago, pse. Use GET
            /v1/payment_methods to list available methods for a given site_id.
        issuer_id (str | None): Card issuer ID (required for some credit cards)
        payer (PaymentPayer): Undocumented in the spec.
        capture (bool | None): Two-step payment flow: set false to only authorize
            (reserve funds), then PUT /v1/payments/{id} with capture=true to capture.
            Debit cards do not support two-step capture.
        binary_mode (bool | None): When true, payments can only be in_process → approved
            or rejected — no pending state. Useful for in-store flows.
        external_reference (str | None): Your internal order or reference ID. Max 256
            chars.
        notification_url (str | None): URL to receive IPN notifications when payment
            status changes. DEPRECATED — use Webhooks instead.
        statement_descriptor (str | None): Text that appears on the payer's card
            statement. Max 22 chars.
        callback_url (str | None): Redirect URL after bank transfer (redirect-based
            methods only)
        date_of_expiration (datetime | None): Expiration date for cash/offline payment
            methods (boleto, OXXO, etc.). ISO 8601 format. Default varies by method.
        metadata (dict[str, Any] | None): Free key-value object for your own internal
            data (not used by MP)
        additional_info (PaymentAdditionalInfo | None): Additional context for fraud
            scoring and installment calculation
        application_fee (float | None): Marketplace fee charged to the seller
            (marketplace integrations only)
        coupon_code (str | None): Discount coupon code
        coupon_amount (float | None): Coupon discount value
    """

    transaction_amount: float = Field(
        description=(
            "Payment amount as a decimal number. MercadoPago does NOT use integer "
            "cents — send 100.50 for R$100,50 (not 10050). CLP uses 0 decimal places."
        ),
        examples=[100.5],
        ge=0.01,
    )
    token: str | None = Field(
        description=(
            "Card token created client-side via MercadoPago.js / MP Secure Fields. "
            "Required for credit/debit card payments. Single-use; expires in 7 days."
        ),
        examples=["YOUR_ACCESS_TOKEN"],
        default=None,
    )
    description: str | None = Field(
        description="Description of the purchased product or service",
        examples=["Premium subscription — 1 month"],
        default=None,
    )
    installments: int | None = Field(
        description="Number of installments (1 = no installments)",
        examples=[1],
        ge=1,
        default=None,
    )
    payment_method_id: str | None = Field(
        description=(
            "Payment method identifier. Examples: visa, master, bolbradesco (Boleto), "
            "pix, oxxo, rapipago, pse. Use GET /v1/payment_methods to list available "
            "methods for a given site_id."
        ),
        examples=["visa"],
        default=None,
    )
    issuer_id: str | None = Field(
        description="Card issuer ID (required for some credit cards)",
        examples=["310"],
        default=None,
    )
    payer: PaymentPayer
    capture: bool | None = Field(
        description=(
            "Two-step payment flow: set false to only authorize (reserve funds), then "
            "PUT /v1/payments/{id} with capture=true to capture. Debit cards do not "
            "support two-step capture."
        ),
        default=None,
    )
    binary_mode: bool | None = Field(
        description=(
            "When true, payments can only be in_process → approved or rejected — no "
            "pending state. Useful for in-store flows."
        ),
        default=None,
    )
    external_reference: str | None = Field(
        description="Your internal order or reference ID. Max 256 chars.",
        examples=["ORDER-2024-001234"],
        default=None,
    )
    notification_url: str | None = Field(
        description=(
            "URL to receive IPN notifications when payment status changes. DEPRECATED "
            "— use Webhooks instead."
        ),
        default=None,
    )
    statement_descriptor: str | None = Field(
        description="Text that appears on the payer's card statement. Max 22 chars.",
        examples=["MYSTORE.COM"],
        max_length=22,
        default=None,
    )
    callback_url: str | None = Field(
        description="Redirect URL after bank transfer (redirect-based methods only)",
        default=None,
    )
    date_of_expiration: datetime | None = Field(
        description=(
            "Expiration date for cash/offline payment methods (boleto, OXXO, etc.). "
            "ISO 8601 format. Default varies by method."
        ),
        examples=["2024-12-31T23:59:59.000-03:00"],
        default=None,
    )
    metadata: dict[str, Any] | None = Field(
        description="Free key-value object for your own internal data (not used by MP)",
        default=None,
    )
    additional_info: PaymentAdditionalInfo | None = Field(
        description="Additional context for fraud scoring and installment calculation",
        default=None,
    )
    application_fee: float | None = Field(
        description=(
            "Marketplace fee charged to the seller (marketplace integrations only)"
        ),
        examples=[1.5],
        default=None,
    )
    coupon_code: str | None = Field(description="Discount coupon code", default=None)
    coupon_amount: float | None = Field(
        description="Coupon discount value",
        default=None,
    )


class SearchPreferencesResponse(BaseSchema):
    """Schema generated for SearchPreferencesResponse.

    Attributes:
        elements (list[Preference]): Undocumented in the spec.
        paging (Pagination | None): Pagination metadata returned in list/search
            responses
    """

    model_config = ConfigDict(extra="allow")

    elements: list[Preference] = Field(default_factory=list)
    paging: Pagination | None = Field(
        description="Pagination metadata returned in list/search responses",
        default=None,
    )


class SearchStoresResponse(BaseSchema):
    """Schema generated for SearchStoresResponse.

    Attributes:
        paging (Pagination | None): Pagination metadata returned in list/search
            responses
        results (list[Store]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    paging: Pagination | None = Field(
        description="Pagination metadata returned in list/search responses",
        default=None,
    )
    results: list[Store] = Field(default_factory=list)


class SubscriptionSearchResult(BaseSchema):
    """Schema generated for SubscriptionSearchResult.

    Attributes:
        paging (Pagination | None): Pagination metadata returned in list/search
            responses
        results (list[Subscription]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    paging: Pagination | None = Field(
        description="Pagination metadata returned in list/search responses",
        default=None,
    )
    results: list[Subscription] = Field(default_factory=list)


class CustomerSearchResult(BaseSchema):
    """Schema generated for CustomerSearchResult.

    Attributes:
        paging (Pagination | None): Pagination metadata returned in list/search
            responses
        results (list[Customer]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    paging: Pagination | None = Field(
        description="Pagination metadata returned in list/search responses",
        default=None,
    )
    results: list[Customer] = Field(default_factory=list)


class Order(BaseSchema):
    """A MercadoPago Order.

    Attributes:
        id (str | None): Order unique identifier, automatically generated by
            MercadoPago.
        type (str | None): Undocumented in the spec.
        processing_mode (OrderRequestProcessingMode | None): Undocumented in the spec.
        capture_mode (OrderRequestCaptureMode | None): Undocumented in the spec.
        status (OrderStatus | None): created — order created, waiting for processing.
            processed — all transactions successfully processed. action_required —
            integrator action needed (e.g. capture an authorized payment). processing —
            being processed; no action needed from integrator. canceled — order
            canceled, will not be processed further.
        status_detail (OrderStatusDetail | None): accredited — payment credited;
            waiting_payment — waiting for payer to complete offline payment;
            waiting_capture — authorized payment awaiting capture; waiting_transfer —
            waiting for bank transfer.
        external_reference (str | None): Undocumented in the spec.
        total_amount (str | None): Total order amount as decimal string.
        total_paid_amount (str | None): Total amount effectively paid so far.
        description (str | None): Undocumented in the spec.
        country_code (str | None): site_id of the order (e.g. MLB, MLA).
        user_id (int | None): Collector (seller) user ID.
        created_date (datetime | None): Undocumented in the spec.
        last_updated_date (datetime | None): Undocumented in the spec.
        client_token (str | None): Token for client-side rendering of the payment UI
            (e.g. Bricks). Only returned when applicable.
        payer (OrderPayer | None): Undocumented in the spec.
        items (list[OrderItem]): Undocumented in the spec.
        shipment (OrderShipment | None): Undocumented in the spec.
        transactions (OrderTransactions2 | None): Undocumented in the spec.
        config (OrderConfig | None): Optional settings for the order.
        integration_data (dict[str, Any] | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(
        description="Order unique identifier, automatically generated by MercadoPago.",
        examples=["01JG28GRB9NYTG6VVS3R5N58YT"],
        default=None,
    )
    type: str | None = Field(examples=["online"], default=None)
    processing_mode: OrderRequestProcessingMode | None = None
    capture_mode: OrderRequestCaptureMode | None = None
    status: OrderStatus | None = Field(
        description=(
            "created — order created, waiting for processing. processed — all "
            "transactions successfully processed. action_required — integrator action "
            "needed (e.g. capture an authorized payment). processing — being "
            "processed; no action needed from integrator. canceled — order canceled, "
            "will not be processed further."
        ),
        examples=["processed"],
        default=None,
    )
    status_detail: OrderStatusDetail | None = Field(
        description=(
            "accredited — payment credited; waiting_payment — waiting for payer to "
            "complete offline payment; waiting_capture — authorized payment awaiting "
            "capture; waiting_transfer — waiting for bank transfer."
        ),
        examples=["accredited"],
        default=None,
    )
    external_reference: str | None = None
    total_amount: str | None = Field(
        description="Total order amount as decimal string.",
        examples=["100.00"],
        default=None,
    )
    total_paid_amount: str | None = Field(
        description="Total amount effectively paid so far.",
        examples=["100.00"],
        default=None,
    )
    description: str | None = None
    country_code: str | None = Field(
        description="site_id of the order (e.g. MLB, MLA).",
        examples=["MLB"],
        default=None,
    )
    user_id: int | None = Field(description="Collector (seller) user ID.", default=None)
    created_date: datetime | None = None
    last_updated_date: datetime | None = None
    client_token: str | None = Field(
        description=(
            "Token for client-side rendering of the payment UI (e.g. Bricks). Only "
            "returned when applicable."
        ),
        default=None,
    )
    payer: OrderPayer | None = None
    items: list[OrderItem] = Field(default_factory=list)
    shipment: OrderShipment | None = None
    transactions: OrderTransactions2 | None = None
    config: OrderConfig | None = Field(
        description="Optional settings for the order.",
        default=None,
    )
    integration_data: dict[str, Any] | None = None


class PaymentSearchResult(BaseSchema):
    """Schema generated for PaymentSearchResult.

    Attributes:
        results (list[Payment]): Undocumented in the spec.
        paging (Pagination | None): Pagination metadata returned in list/search
            responses
    """

    model_config = ConfigDict(extra="allow")

    results: list[Payment] = Field(default_factory=list)
    paging: Pagination | None = Field(
        description="Pagination metadata returned in list/search responses",
        default=None,
    )


class OrderSearchResult(BaseSchema):
    """Schema generated for OrderSearchResult.

    Attributes:
        data (list[Order]): Undocumented in the spec.
        paging (OrderSearchResultPaging | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    data: list[Order] = Field(default_factory=list)
    paging: OrderSearchResultPaging | None = None


__all__: list[str] = [
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
    "GetQrIntegratorConfigResponse",
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
]
