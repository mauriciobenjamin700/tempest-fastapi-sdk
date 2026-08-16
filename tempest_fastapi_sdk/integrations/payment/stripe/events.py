"""Stripe webhook event types, generated from the API specification.

Do not edit by hand: ``scripts/regen_stripe.py`` writes this file from
``vendor/stripe-api-facts.yaml``, and
``tests/integrations/payment/stripe/test_generated_drift.py`` fails when
the two disagree.

The specification does not enumerate ``event.type`` on the event object
itself — the authoritative list is the ``enabled_events`` parameter of
``POST /v1/webhook_endpoints``, which is where these values come from.
"""

from __future__ import annotations

from tempest_fastapi_sdk.core.enums import BaseStrEnum


class StripeEvent(BaseStrEnum):
    """A Stripe webhook event type.

    Members are named after the event string with dots and dashes turned
    into underscores, upper-cased. Use :meth:`BaseStrEnum.from_value` to
    parse a delivery, and :meth:`BaseStrEnum.has_value` to tell a known
    event from one Stripe added after this SDK release.
    """

    ACCOUNT_APPLICATION_AUTHORIZED = "account.application.authorized"
    ACCOUNT_APPLICATION_DEAUTHORIZED = "account.application.deauthorized"
    ACCOUNT_EXTERNAL_ACCOUNT_CREATED = "account.external_account.created"
    ACCOUNT_EXTERNAL_ACCOUNT_DELETED = "account.external_account.deleted"
    ACCOUNT_EXTERNAL_ACCOUNT_UPDATED = "account.external_account.updated"
    ACCOUNT_UPDATED = "account.updated"
    APPLICATION_FEE_CREATED = "application_fee.created"
    APPLICATION_FEE_REFUND_UPDATED = "application_fee.refund.updated"
    APPLICATION_FEE_REFUNDED = "application_fee.refunded"
    BALANCE_AVAILABLE = "balance.available"
    BALANCE_SETTINGS_UPDATED = "balance_settings.updated"
    BILLING_ALERT_TRIGGERED = "billing.alert.triggered"
    BILLING_CREDIT_BALANCE_TRANSACTION_CREATED = (
        "billing.credit_balance_transaction.created"
    )
    BILLING_CREDIT_GRANT_CREATED = "billing.credit_grant.created"
    BILLING_CREDIT_GRANT_UPDATED = "billing.credit_grant.updated"
    BILLING_METER_CREATED = "billing.meter.created"
    BILLING_METER_DEACTIVATED = "billing.meter.deactivated"
    BILLING_METER_REACTIVATED = "billing.meter.reactivated"
    BILLING_METER_UPDATED = "billing.meter.updated"
    BILLING_PORTAL_CONFIGURATION_CREATED = "billing_portal.configuration.created"
    BILLING_PORTAL_CONFIGURATION_UPDATED = "billing_portal.configuration.updated"
    BILLING_PORTAL_SESSION_CREATED = "billing_portal.session.created"
    CAPABILITY_UPDATED = "capability.updated"
    CASH_BALANCE_FUNDS_AVAILABLE = "cash_balance.funds_available"
    CHARGE_CAPTURED = "charge.captured"
    CHARGE_DISPUTE_CLOSED = "charge.dispute.closed"
    CHARGE_DISPUTE_CREATED = "charge.dispute.created"
    CHARGE_DISPUTE_FUNDS_REINSTATED = "charge.dispute.funds_reinstated"
    CHARGE_DISPUTE_FUNDS_WITHDRAWN = "charge.dispute.funds_withdrawn"
    CHARGE_DISPUTE_UPDATED = "charge.dispute.updated"
    CHARGE_EXPIRED = "charge.expired"
    CHARGE_FAILED = "charge.failed"
    CHARGE_PENDING = "charge.pending"
    CHARGE_REFUND_UPDATED = "charge.refund.updated"
    CHARGE_REFUNDED = "charge.refunded"
    CHARGE_SUCCEEDED = "charge.succeeded"
    CHARGE_UPDATED = "charge.updated"
    CHECKOUT_SESSION_ASYNC_PAYMENT_FAILED = "checkout.session.async_payment_failed"
    CHECKOUT_SESSION_ASYNC_PAYMENT_SUCCEEDED = (
        "checkout.session.async_payment_succeeded"
    )
    CHECKOUT_SESSION_COMPLETED = "checkout.session.completed"
    CHECKOUT_SESSION_EXPIRED = "checkout.session.expired"
    CLIMATE_ORDER_CANCELED = "climate.order.canceled"
    CLIMATE_ORDER_CREATED = "climate.order.created"
    CLIMATE_ORDER_DELAYED = "climate.order.delayed"
    CLIMATE_ORDER_DELIVERED = "climate.order.delivered"
    CLIMATE_ORDER_PRODUCT_SUBSTITUTED = "climate.order.product_substituted"
    CLIMATE_PRODUCT_CREATED = "climate.product.created"
    CLIMATE_PRODUCT_PRICING_UPDATED = "climate.product.pricing_updated"
    COUPON_CREATED = "coupon.created"
    COUPON_DELETED = "coupon.deleted"
    COUPON_UPDATED = "coupon.updated"
    CREDIT_NOTE_CREATED = "credit_note.created"
    CREDIT_NOTE_UPDATED = "credit_note.updated"
    CREDIT_NOTE_VOIDED = "credit_note.voided"
    CUSTOMER_CREATED = "customer.created"
    CUSTOMER_DELETED = "customer.deleted"
    CUSTOMER_DISCOUNT_CREATED = "customer.discount.created"
    CUSTOMER_DISCOUNT_DELETED = "customer.discount.deleted"
    CUSTOMER_DISCOUNT_UPDATED = "customer.discount.updated"
    CUSTOMER_SOURCE_CREATED = "customer.source.created"
    CUSTOMER_SOURCE_DELETED = "customer.source.deleted"
    CUSTOMER_SOURCE_EXPIRING = "customer.source.expiring"
    CUSTOMER_SOURCE_UPDATED = "customer.source.updated"
    CUSTOMER_SUBSCRIPTION_CREATED = "customer.subscription.created"
    CUSTOMER_SUBSCRIPTION_DELETED = "customer.subscription.deleted"
    CUSTOMER_SUBSCRIPTION_PAUSED = "customer.subscription.paused"
    CUSTOMER_SUBSCRIPTION_PENDING_UPDATE_APPLIED = (
        "customer.subscription.pending_update_applied"
    )
    CUSTOMER_SUBSCRIPTION_PENDING_UPDATE_EXPIRED = (
        "customer.subscription.pending_update_expired"
    )
    CUSTOMER_SUBSCRIPTION_RESUMED = "customer.subscription.resumed"
    CUSTOMER_SUBSCRIPTION_TRIAL_WILL_END = "customer.subscription.trial_will_end"
    CUSTOMER_SUBSCRIPTION_UPDATED = "customer.subscription.updated"
    CUSTOMER_TAX_ID_CREATED = "customer.tax_id.created"
    CUSTOMER_TAX_ID_DELETED = "customer.tax_id.deleted"
    CUSTOMER_TAX_ID_UPDATED = "customer.tax_id.updated"
    CUSTOMER_UPDATED = "customer.updated"
    CUSTOMER_CASH_BALANCE_TRANSACTION_CREATED = (
        "customer_cash_balance_transaction.created"
    )
    ENTITLEMENTS_ACTIVE_ENTITLEMENT_SUMMARY_UPDATED = (
        "entitlements.active_entitlement_summary.updated"
    )
    FILE_CREATED = "file.created"
    FINANCIAL_CONNECTIONS_ACCOUNT_ACCOUNT_NUMBERS_UPDATED = (
        "financial_connections.account.account_numbers_updated"
    )
    FINANCIAL_CONNECTIONS_ACCOUNT_CREATED = "financial_connections.account.created"
    FINANCIAL_CONNECTIONS_ACCOUNT_DEACTIVATED = (
        "financial_connections.account.deactivated"
    )
    FINANCIAL_CONNECTIONS_ACCOUNT_DISCONNECTED = (
        "financial_connections.account.disconnected"
    )
    FINANCIAL_CONNECTIONS_ACCOUNT_EXPECTED_DEACTIVATION_DATE_UPDATED = (
        "financial_connections.account.expected_deactivation_date_updated"
    )
    FINANCIAL_CONNECTIONS_ACCOUNT_REACTIVATED = (
        "financial_connections.account.reactivated"
    )
    FINANCIAL_CONNECTIONS_ACCOUNT_REFRESHED_BALANCE = (
        "financial_connections.account.refreshed_balance"
    )
    FINANCIAL_CONNECTIONS_ACCOUNT_REFRESHED_OWNERSHIP = (
        "financial_connections.account.refreshed_ownership"
    )
    FINANCIAL_CONNECTIONS_ACCOUNT_REFRESHED_TRANSACTIONS = (
        "financial_connections.account.refreshed_transactions"
    )
    FINANCIAL_CONNECTIONS_ACCOUNT_SUPPORTED_PAYMENT_METHOD_TYPES_UPDATED = (
        "financial_connections.account.supported_payment_method_types_updated"
    )
    FINANCIAL_CONNECTIONS_ACCOUNT_UPCOMING_ACCOUNT_NUMBER_EXPIRY = (
        "financial_connections.account.upcoming_account_number_expiry"
    )
    FINANCIAL_CONNECTIONS_ACCOUNT_UPCOMING_DEACTIVATION = (
        "financial_connections.account.upcoming_deactivation"
    )
    FINANCIAL_CONNECTIONS_AUTHORIZATION_EXPECTED_DEACTIVATION_DATE_UPDATED = (
        "financial_connections.authorization.expected_deactivation_date_updated"
    )
    FINANCIAL_CONNECTIONS_AUTHORIZATION_UPCOMING_DEACTIVATION = (
        "financial_connections.authorization.upcoming_deactivation"
    )
    IDENTITY_VERIFICATION_SESSION_CANCELED = "identity.verification_session.canceled"
    IDENTITY_VERIFICATION_SESSION_CREATED = "identity.verification_session.created"
    IDENTITY_VERIFICATION_SESSION_PROCESSING = (
        "identity.verification_session.processing"
    )
    IDENTITY_VERIFICATION_SESSION_REDACTED = "identity.verification_session.redacted"
    IDENTITY_VERIFICATION_SESSION_REQUIRES_INPUT = (
        "identity.verification_session.requires_input"
    )
    IDENTITY_VERIFICATION_SESSION_VERIFIED = "identity.verification_session.verified"
    INVOICE_CREATED = "invoice.created"
    INVOICE_DELETED = "invoice.deleted"
    INVOICE_FINALIZATION_FAILED = "invoice.finalization_failed"
    INVOICE_FINALIZED = "invoice.finalized"
    INVOICE_MARKED_UNCOLLECTIBLE = "invoice.marked_uncollectible"
    INVOICE_OVERDUE = "invoice.overdue"
    INVOICE_OVERPAID = "invoice.overpaid"
    INVOICE_PAID = "invoice.paid"
    INVOICE_PAYMENT_ACTION_REQUIRED = "invoice.payment_action_required"
    INVOICE_PAYMENT_ATTEMPT_REQUIRED = "invoice.payment_attempt_required"
    INVOICE_PAYMENT_FAILED = "invoice.payment_failed"
    INVOICE_PAYMENT_SUCCEEDED = "invoice.payment_succeeded"
    INVOICE_SENT = "invoice.sent"
    INVOICE_UPCOMING = "invoice.upcoming"
    INVOICE_UPDATED = "invoice.updated"
    INVOICE_VOIDED = "invoice.voided"
    INVOICE_WILL_BE_DUE = "invoice.will_be_due"
    INVOICE_PAYMENT_PAID = "invoice_payment.paid"
    INVOICEITEM_CREATED = "invoiceitem.created"
    INVOICEITEM_DELETED = "invoiceitem.deleted"
    ISSUING_AUTHORIZATION_CREATED = "issuing_authorization.created"
    ISSUING_AUTHORIZATION_REQUEST = "issuing_authorization.request"
    ISSUING_AUTHORIZATION_UPDATED = "issuing_authorization.updated"
    ISSUING_CARD_CREATED = "issuing_card.created"
    ISSUING_CARD_UPDATED = "issuing_card.updated"
    ISSUING_CARDHOLDER_CREATED = "issuing_cardholder.created"
    ISSUING_CARDHOLDER_UPDATED = "issuing_cardholder.updated"
    ISSUING_DISPUTE_CLOSED = "issuing_dispute.closed"
    ISSUING_DISPUTE_CREATED = "issuing_dispute.created"
    ISSUING_DISPUTE_FUNDS_REINSTATED = "issuing_dispute.funds_reinstated"
    ISSUING_DISPUTE_FUNDS_RESCINDED = "issuing_dispute.funds_rescinded"
    ISSUING_DISPUTE_SUBMITTED = "issuing_dispute.submitted"
    ISSUING_DISPUTE_UPDATED = "issuing_dispute.updated"
    ISSUING_PERSONALIZATION_DESIGN_ACTIVATED = (
        "issuing_personalization_design.activated"
    )
    ISSUING_PERSONALIZATION_DESIGN_DEACTIVATED = (
        "issuing_personalization_design.deactivated"
    )
    ISSUING_PERSONALIZATION_DESIGN_REJECTED = "issuing_personalization_design.rejected"
    ISSUING_PERSONALIZATION_DESIGN_UPDATED = "issuing_personalization_design.updated"
    ISSUING_TOKEN_CREATED = "issuing_token.created"
    ISSUING_TOKEN_UPDATED = "issuing_token.updated"
    ISSUING_TRANSACTION_CREATED = "issuing_transaction.created"
    ISSUING_TRANSACTION_PURCHASE_DETAILS_RECEIPT_UPDATED = (
        "issuing_transaction.purchase_details_receipt_updated"
    )
    ISSUING_TRANSACTION_UPDATED = "issuing_transaction.updated"
    MANDATE_UPDATED = "mandate.updated"
    PAYMENT_INTENT_AMOUNT_CAPTURABLE_UPDATED = (
        "payment_intent.amount_capturable_updated"
    )
    PAYMENT_INTENT_CANCELED = "payment_intent.canceled"
    PAYMENT_INTENT_CREATED = "payment_intent.created"
    PAYMENT_INTENT_PARTIALLY_FUNDED = "payment_intent.partially_funded"
    PAYMENT_INTENT_PAYMENT_FAILED = "payment_intent.payment_failed"
    PAYMENT_INTENT_PROCESSING = "payment_intent.processing"
    PAYMENT_INTENT_REQUIRES_ACTION = "payment_intent.requires_action"
    PAYMENT_INTENT_SUCCEEDED = "payment_intent.succeeded"
    PAYMENT_LINK_CREATED = "payment_link.created"
    PAYMENT_LINK_UPDATED = "payment_link.updated"
    PAYMENT_METHOD_ATTACHED = "payment_method.attached"
    PAYMENT_METHOD_AUTOMATICALLY_UPDATED = "payment_method.automatically_updated"
    PAYMENT_METHOD_DETACHED = "payment_method.detached"
    PAYMENT_METHOD_UPDATED = "payment_method.updated"
    PAYOUT_CANCELED = "payout.canceled"
    PAYOUT_CREATED = "payout.created"
    PAYOUT_FAILED = "payout.failed"
    PAYOUT_PAID = "payout.paid"
    PAYOUT_RECONCILIATION_COMPLETED = "payout.reconciliation_completed"
    PAYOUT_UPDATED = "payout.updated"
    PERSON_CREATED = "person.created"
    PERSON_DELETED = "person.deleted"
    PERSON_UPDATED = "person.updated"
    PLAN_CREATED = "plan.created"
    PLAN_DELETED = "plan.deleted"
    PLAN_UPDATED = "plan.updated"
    PRICE_CREATED = "price.created"
    PRICE_DELETED = "price.deleted"
    PRICE_UPDATED = "price.updated"
    PRODUCT_CREATED = "product.created"
    PRODUCT_DELETED = "product.deleted"
    PRODUCT_UPDATED = "product.updated"
    PROMOTION_CODE_CREATED = "promotion_code.created"
    PROMOTION_CODE_UPDATED = "promotion_code.updated"
    QUOTE_ACCEPTED = "quote.accepted"
    QUOTE_CANCELED = "quote.canceled"
    QUOTE_CREATED = "quote.created"
    QUOTE_FINALIZED = "quote.finalized"
    RADAR_EARLY_FRAUD_WARNING_CREATED = "radar.early_fraud_warning.created"
    RADAR_EARLY_FRAUD_WARNING_UPDATED = "radar.early_fraud_warning.updated"
    REFUND_CREATED = "refund.created"
    REFUND_FAILED = "refund.failed"
    REFUND_UPDATED = "refund.updated"
    REPORTING_REPORT_RUN_FAILED = "reporting.report_run.failed"
    REPORTING_REPORT_RUN_SUCCEEDED = "reporting.report_run.succeeded"
    REPORTING_REPORT_TYPE_UPDATED = "reporting.report_type.updated"
    RESERVE_HOLD_CREATED = "reserve.hold.created"
    RESERVE_HOLD_UPDATED = "reserve.hold.updated"
    RESERVE_PLAN_CREATED = "reserve.plan.created"
    RESERVE_PLAN_DISABLED = "reserve.plan.disabled"
    RESERVE_PLAN_EXPIRED = "reserve.plan.expired"
    RESERVE_PLAN_UPDATED = "reserve.plan.updated"
    RESERVE_RELEASE_CREATED = "reserve.release.created"
    REVIEW_CLOSED = "review.closed"
    REVIEW_OPENED = "review.opened"
    SETUP_INTENT_CANCELED = "setup_intent.canceled"
    SETUP_INTENT_CREATED = "setup_intent.created"
    SETUP_INTENT_REQUIRES_ACTION = "setup_intent.requires_action"
    SETUP_INTENT_SETUP_FAILED = "setup_intent.setup_failed"
    SETUP_INTENT_SUCCEEDED = "setup_intent.succeeded"
    SIGMA_SCHEDULED_QUERY_RUN_CREATED = "sigma.scheduled_query_run.created"
    SOURCE_CANCELED = "source.canceled"
    SOURCE_CHARGEABLE = "source.chargeable"
    SOURCE_FAILED = "source.failed"
    SOURCE_MANDATE_NOTIFICATION = "source.mandate_notification"
    SOURCE_REFUND_ATTRIBUTES_REQUIRED = "source.refund_attributes_required"
    SOURCE_TRANSACTION_CREATED = "source.transaction.created"
    SOURCE_TRANSACTION_UPDATED = "source.transaction.updated"
    SUBSCRIPTION_SCHEDULE_ABORTED = "subscription_schedule.aborted"
    SUBSCRIPTION_SCHEDULE_CANCELED = "subscription_schedule.canceled"
    SUBSCRIPTION_SCHEDULE_COMPLETED = "subscription_schedule.completed"
    SUBSCRIPTION_SCHEDULE_CREATED = "subscription_schedule.created"
    SUBSCRIPTION_SCHEDULE_EXPIRING = "subscription_schedule.expiring"
    SUBSCRIPTION_SCHEDULE_RELEASED = "subscription_schedule.released"
    SUBSCRIPTION_SCHEDULE_UPDATED = "subscription_schedule.updated"
    TAX_SETTINGS_UPDATED = "tax.settings.updated"
    TAX_RATE_CREATED = "tax_rate.created"
    TAX_RATE_UPDATED = "tax_rate.updated"
    TERMINAL_READER_ACTION_FAILED = "terminal.reader.action_failed"
    TERMINAL_READER_ACTION_SUCCEEDED = "terminal.reader.action_succeeded"
    TERMINAL_READER_ACTION_UPDATED = "terminal.reader.action_updated"
    TEST_HELPERS_TEST_CLOCK_ADVANCING = "test_helpers.test_clock.advancing"
    TEST_HELPERS_TEST_CLOCK_CREATED = "test_helpers.test_clock.created"
    TEST_HELPERS_TEST_CLOCK_DELETED = "test_helpers.test_clock.deleted"
    TEST_HELPERS_TEST_CLOCK_INTERNAL_FAILURE = (
        "test_helpers.test_clock.internal_failure"
    )
    TEST_HELPERS_TEST_CLOCK_READY = "test_helpers.test_clock.ready"
    TOPUP_CANCELED = "topup.canceled"
    TOPUP_CREATED = "topup.created"
    TOPUP_FAILED = "topup.failed"
    TOPUP_REVERSED = "topup.reversed"
    TOPUP_SUCCEEDED = "topup.succeeded"
    TRANSFER_CREATED = "transfer.created"
    TRANSFER_REVERSED = "transfer.reversed"
    TRANSFER_UPDATED = "transfer.updated"
    TREASURY_CREDIT_REVERSAL_CREATED = "treasury.credit_reversal.created"
    TREASURY_CREDIT_REVERSAL_POSTED = "treasury.credit_reversal.posted"
    TREASURY_DEBIT_REVERSAL_COMPLETED = "treasury.debit_reversal.completed"
    TREASURY_DEBIT_REVERSAL_CREATED = "treasury.debit_reversal.created"
    TREASURY_DEBIT_REVERSAL_INITIAL_CREDIT_GRANTED = (
        "treasury.debit_reversal.initial_credit_granted"
    )
    TREASURY_FINANCIAL_ACCOUNT_CLOSED = "treasury.financial_account.closed"
    TREASURY_FINANCIAL_ACCOUNT_CREATED = "treasury.financial_account.created"
    TREASURY_FINANCIAL_ACCOUNT_FEATURES_STATUS_UPDATED = (
        "treasury.financial_account.features_status_updated"
    )
    TREASURY_INBOUND_TRANSFER_CANCELED = "treasury.inbound_transfer.canceled"
    TREASURY_INBOUND_TRANSFER_CREATED = "treasury.inbound_transfer.created"
    TREASURY_INBOUND_TRANSFER_FAILED = "treasury.inbound_transfer.failed"
    TREASURY_INBOUND_TRANSFER_SUCCEEDED = "treasury.inbound_transfer.succeeded"
    TREASURY_OUTBOUND_PAYMENT_CANCELED = "treasury.outbound_payment.canceled"
    TREASURY_OUTBOUND_PAYMENT_CREATED = "treasury.outbound_payment.created"
    TREASURY_OUTBOUND_PAYMENT_EXPECTED_ARRIVAL_DATE_UPDATED = (
        "treasury.outbound_payment.expected_arrival_date_updated"
    )
    TREASURY_OUTBOUND_PAYMENT_FAILED = "treasury.outbound_payment.failed"
    TREASURY_OUTBOUND_PAYMENT_POSTED = "treasury.outbound_payment.posted"
    TREASURY_OUTBOUND_PAYMENT_RETURNED = "treasury.outbound_payment.returned"
    TREASURY_OUTBOUND_PAYMENT_TRACKING_DETAILS_UPDATED = (
        "treasury.outbound_payment.tracking_details_updated"
    )
    TREASURY_OUTBOUND_TRANSFER_CANCELED = "treasury.outbound_transfer.canceled"
    TREASURY_OUTBOUND_TRANSFER_CREATED = "treasury.outbound_transfer.created"
    TREASURY_OUTBOUND_TRANSFER_EXPECTED_ARRIVAL_DATE_UPDATED = (
        "treasury.outbound_transfer.expected_arrival_date_updated"
    )
    TREASURY_OUTBOUND_TRANSFER_FAILED = "treasury.outbound_transfer.failed"
    TREASURY_OUTBOUND_TRANSFER_POSTED = "treasury.outbound_transfer.posted"
    TREASURY_OUTBOUND_TRANSFER_RETURNED = "treasury.outbound_transfer.returned"
    TREASURY_OUTBOUND_TRANSFER_TRACKING_DETAILS_UPDATED = (
        "treasury.outbound_transfer.tracking_details_updated"
    )
    TREASURY_RECEIVED_CREDIT_CREATED = "treasury.received_credit.created"
    TREASURY_RECEIVED_CREDIT_FAILED = "treasury.received_credit.failed"
    TREASURY_RECEIVED_CREDIT_SUCCEEDED = "treasury.received_credit.succeeded"
    TREASURY_RECEIVED_DEBIT_CREATED = "treasury.received_debit.created"


__all__: list[str] = [
    "StripeEvent",
]
