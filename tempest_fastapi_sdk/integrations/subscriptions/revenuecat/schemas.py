"""Models for the RevenueCat webhook body and the v1 customer info.

Hand-written, and **thin on purpose**. Two reasons, both measured:

* The webhook page describes the body in prose tables, not in a schema, and
  says to "parse defensively: treat **Sometimes** fields as optional keys,
  and don't assume **Always** fields are non-null"
  (https://www.revenuecat.com/docs/integrations/webhooks/event-types-and-fields).
  So every field here is optional except ``id`` and ``type``, which the
  inbox needs.
* The OpenAPI document the v1 reference links to
  (https://www.revenuecat.com/docs/redocusaurus/openapi-v1.yaml) was
  downloaded on 2026-10-15 and declares ``paths: {}`` — 4157 bytes, no
  operation and no schema to generate from. The customer info model below is
  copied from the "Customer Info Model" page instead
  (https://www.revenuecat.com/docs/api-v1/customer-info-model).

Every model sets ``extra="allow"``: RevenueCat "may add new fields ... without
changing the API version" (https://www.revenuecat.com/docs/integrations/webhooks),
and a partial model that dropped them would lose the half this SDK does not
name.

**``subscriber_attributes`` is excluded from ``model_dump()``.** It is the
map where an app puts ``$email``, ``$phoneNumber`` and whatever else it
attached to the customer (the sample ``INITIAL_PURCHASE`` carries an
``$email``). It is still readable as an attribute, but a payload built with
``model_dump()`` — which is what
:func:`~tempest_fastapi_sdk.integrations.subscriptions.revenuecat.make_revenuecat_webhook_router`
stores — leaves it out unless you put it back on purpose.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

_REVENUECAT_CONFIG = ConfigDict(extra="allow", str_strip_whitespace=True)
"""Shared config: keep unknown fields reachable through ``model_extra``."""


class RevenueCatExperiment(BaseModel):
    """One entry of an event's ``experiments`` array.

    Attributes:
        experiment_id (str | None): ID of the experiment.
        experiment_variant (str | None): Variant the subscriber was in.
        enrolled_at_ms (int | None): Enrollment time, epoch milliseconds.
    """

    model_config = _REVENUECAT_CONFIG

    experiment_id: str | None = None
    experiment_variant: str | None = None
    enrolled_at_ms: int | None = None


class RevenueCatEvent(BaseModel):
    """The ``event`` object of a RevenueCat webhook (format ``1.0``).

    Field names and meanings come from the documentation's field tables. Only
    the fields that drive a decision are named; the rest (``price``,
    ``tax_percentage``, ``adjustments``, ``transferred_from``, ...) stay
    readable through ``model_extra``.

    Attributes:
        id (str): Unique id of the event. "Retries reuse the same ``id``" —
            this is the inbox's idempotency key.
        type (str): The event type, as delivered. Compare with
            :class:`~tempest_fastapi_sdk.integrations.subscriptions.revenuecat.RevenueCatEventTypeEnum`.
        event_timestamp_ms (int | None): When the event was generated.
        app_id (str | None): Dashboard app the event belongs to.
        app_user_id (str | None): Last seen App User ID.
        original_app_user_id (str | None): First App User ID used.
        aliases (list[str]): Every App User ID the subscriber used. The
            documentation says to search both this and
            ``original_app_user_id`` when looking a user up.
        product_id (str | None): Product of the subscription.
        new_product_id (str | None): Product switched to, on some
            ``PRODUCT_CHANGE`` events.
        entitlement_ids (list[str]): Entitlements of the product. ``null`` in
            the payload becomes ``[]``.
        period_type (str | None): ``TRIAL``, ``INTRO``, ``NORMAL``, ...
        purchased_at_ms (int | None): Purchase time, epoch milliseconds.
        expiration_at_ms (int | None): End of the period; ``None`` for
            lifetime and non-subscription purchases.
        grace_period_expiration_at_ms (int | None): Only on
            ``BILLING_ISSUE``.
        auto_resume_at_ms (int | None): Only on ``SUBSCRIPTION_PAUSED``.
        environment (str | None): ``SANDBOX`` or ``PRODUCTION``.
        store (str | None): Store of the purchase.
        transaction_id (str | None): Store transaction id.
        original_transaction_id (str | None): First transaction of the
            subscription.
        cancel_reason (str | None): Only on ``CANCELLATION``.
        expiration_reason (str | None): Only on ``EXPIRATION``.
        is_trial_conversion (bool | None): Only on ``RENEWAL``.
        experiments (list[RevenueCatExperiment]): Experiments the subscriber
            was enrolled in.
        subscriber_attributes (dict[str, Any]): Customer attributes. Excluded
            from ``model_dump()``; see the module docstring.
    """

    model_config = _REVENUECAT_CONFIG

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    event_timestamp_ms: int | None = None
    app_id: str | None = None
    app_user_id: str | None = None
    original_app_user_id: str | None = None
    aliases: list[str] = Field(default_factory=list)
    product_id: str | None = None
    new_product_id: str | None = None
    entitlement_ids: list[str] = Field(default_factory=list)
    period_type: str | None = None
    purchased_at_ms: int | None = None
    expiration_at_ms: int | None = None
    grace_period_expiration_at_ms: int | None = None
    auto_resume_at_ms: int | None = None
    environment: str | None = None
    store: str | None = None
    transaction_id: str | None = None
    original_transaction_id: str | None = None
    cancel_reason: str | None = None
    expiration_reason: str | None = None
    is_trial_conversion: bool | None = None
    experiments: list[RevenueCatExperiment] = Field(default_factory=list)
    subscriber_attributes: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator(
        "aliases",
        "entitlement_ids",
        "experiments",
        "subscriber_attributes",
        mode="before",
    )
    @classmethod
    def _null_is_empty(cls, value: Any, info: ValidationInfo) -> Any:
        """Turn a documented ``null`` collection into an empty one.

        ``entitlement_ids`` "can be ``null`` if the ``product_id`` is not
        mapped to any entitlements" per the field tables; a handler iterating
        it should not need a ``None`` check, and an absent key and a ``null``
        mean the same thing to it.

        Args:
            value (Any): The raw value.
            info (ValidationInfo): Which field is being validated.

        Returns:
            Any: ``{}`` for a ``None`` ``subscriber_attributes``, ``[]`` for
            the other ``None`` fields, the value otherwise.
        """
        if value is not None:
            return value
        return {} if info.field_name == "subscriber_attributes" else []


class RevenueCatWebhook(BaseModel):
    """The whole body RevenueCat POSTs: ``api_version`` plus ``event``.

    Attributes:
        api_version (str): Version of the webhook format (``"1.0"``).
        event (RevenueCatEvent): The event.
    """

    model_config = _REVENUECAT_CONFIG

    api_version: str = ""
    event: RevenueCatEvent


class RevenueCatEntitlement(BaseModel):
    """One entry of ``subscriber.entitlements`` (keyed by entitlement id).

    Attributes:
        expires_date (datetime | None): When access ends; ``None`` for
            lifetime.
        grace_period_expires_date (datetime | None): End of the grace period.
        product_identifier (str | None): Product granting the entitlement.
        purchase_date (datetime | None): Latest purchase date.
    """

    model_config = _REVENUECAT_CONFIG

    expires_date: datetime | None = None
    grace_period_expires_date: datetime | None = None
    product_identifier: str | None = None
    purchase_date: datetime | None = None


class RevenueCatSubscription(BaseModel):
    """One entry of ``subscriber.subscriptions`` (keyed by product id).

    ``store_transaction_id`` is typed ``str | int`` because the
    documentation's own sample carries both shapes (a ``GPA.…`` string and
    ``1000000652379790`` as a number).

    Attributes:
        expires_date (datetime | None): End of the current period.
        purchase_date (datetime | None): Start of the current period.
        original_purchase_date (datetime | None): First purchase.
        period_type (str | None): ``normal``, ``trial``, ``intro``
            (lowercase in this API, unlike the webhook).
        store (str | None): ``app_store``, ``play_store``, ... (lowercase).
        is_sandbox (bool | None): Whether the purchase is a sandbox one.
        unsubscribe_detected_at (datetime | None): When auto-renew was
            turned off.
        billing_issues_detected_at (datetime | None): When a charge failed.
        grace_period_expires_date (datetime | None): End of the grace period.
        refunded_at (datetime | None): When it was refunded.
        auto_resume_date (datetime | None): When a paused subscription
            resumes.
        ownership_type (str | None): ``PURCHASED`` or ``FAMILY_SHARED``.
        store_transaction_id (str | int | None): The store's transaction id.
    """

    model_config = _REVENUECAT_CONFIG

    expires_date: datetime | None = None
    purchase_date: datetime | None = None
    original_purchase_date: datetime | None = None
    period_type: str | None = None
    store: str | None = None
    is_sandbox: bool | None = None
    unsubscribe_detected_at: datetime | None = None
    billing_issues_detected_at: datetime | None = None
    grace_period_expires_date: datetime | None = None
    refunded_at: datetime | None = None
    auto_resume_date: datetime | None = None
    ownership_type: str | None = None
    store_transaction_id: str | int | None = None


class RevenueCatSubscriber(BaseModel):
    """``subscriber`` of the v1 customer info.

    Attributes:
        original_app_user_id (str | None): App User ID the customer was first
            known by.
        first_seen (datetime | None): When RevenueCat first saw the customer.
        last_seen (datetime | None): When RevenueCat last saw the customer.
        management_url (str | None): Where the customer manages the active
            subscription; ``None`` when there is none.
        entitlements (dict[str, RevenueCatEntitlement]): Every entitlement,
            expired ones included.
        subscriptions (dict[str, RevenueCatSubscription]): Subscriptions by
            product id.
        non_subscriptions (dict[str, list[dict[str, Any]]]): One-off
            purchases by product id.
        subscriber_attributes (dict[str, Any]): Only sent to a secret key.
            Excluded from ``model_dump()``.
    """

    model_config = _REVENUECAT_CONFIG

    original_app_user_id: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    management_url: str | None = None
    entitlements: dict[str, RevenueCatEntitlement] = Field(default_factory=dict)
    subscriptions: dict[str, RevenueCatSubscription] = Field(default_factory=dict)
    non_subscriptions: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    subscriber_attributes: dict[str, Any] = Field(default_factory=dict, exclude=True)


class RevenueCatCustomerInfo(BaseModel):
    """Body of ``GET /v1/subscribers/{app_user_id}``.

    Attributes:
        request_date (datetime | None): When RevenueCat answered.
        request_date_ms (int | None): The same, epoch milliseconds.
        subscriber (RevenueCatSubscriber): The customer.
    """

    model_config = _REVENUECAT_CONFIG

    request_date: datetime | None = None
    request_date_ms: int | None = None
    subscriber: RevenueCatSubscriber = Field(default_factory=RevenueCatSubscriber)


__all__: list[str] = [
    "RevenueCatCustomerInfo",
    "RevenueCatEntitlement",
    "RevenueCatEvent",
    "RevenueCatExperiment",
    "RevenueCatSubscriber",
    "RevenueCatSubscription",
    "RevenueCatWebhook",
]
