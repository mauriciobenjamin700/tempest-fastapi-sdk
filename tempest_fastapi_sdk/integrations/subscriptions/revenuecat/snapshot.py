"""Translate a RevenueCat event into one normalized subscription state.

The service decides what a state **means** — which plan unlocks what, how
long a grace period lasts in the product, what a refund takes away. What it
should not have to re-derive from twenty-one event types is the state
itself. :func:`normalize` does that one step, and nothing past it: it
returns a :class:`SubscriptionSnapshot`, not an entitlement.

Every mapping below cites the line of RevenueCat's "Event Types and Fields"
page (https://www.revenuecat.com/docs/integrations/webhooks/event-types-and-fields)
that justifies it:

* ``SUBSCRIPTION_PAUSED`` — "Don't revoke access on this event. Revoke access
  only on ``EXPIRATION`` with expiration reason ``SUBSCRIPTION_PAUSED``." So
  it maps to :attr:`SubscriptionStatusEnum.PAUSE_SCHEDULED`, and the
  ``EXPIRATION`` with that reason maps to ``PAUSED``.
* ``BILLING_ISSUE`` — "This doesn't mean the subscription has expired", and
  "You can safely ignore this event if you listen for ``CANCELLATION`` with
  ``cancel_reason=BILLING_ERROR``". Both map to ``BILLING_ISSUE``.
* ``CANCELLATION`` with ``CUSTOMER_SUPPORT`` — the reasons table says the
  customer received a refund. It maps to ``REFUNDED``; any other reason maps
  to ``CANCELLED`` (renewal turned off, the period runs to its end).
* ``PRODUCT_CHANGE`` — "This doesn't mean the new subscription is in effect
  immediately." It maps to ``ACTIVE`` with ``new_product_id`` set; the
  subscription itself did not stop.
* ``TEMPORARY_ENTITLEMENT_GRANT`` — a short-term grant (at most 24 hours)
  during a store outage; expect an ``INITIAL_PURCHASE`` or an ``EXPIRATION``
  afterwards. It maps to ``TEMPORARY_GRANT``.
* Events that do not change a subscription's state (``TEST``, ``TRANSFER``,
  ``EXPERIMENT_ENROLLMENT``, ``VIRTUAL_CURRENCY_TRANSACTION``,
  ``SUBSCRIBER_ALIAS``, ``PURCHASE_REDEEMED``, ``INVOICE_ISSUANCE``, the two
  ``PRICE_INCREASE_CONSENT_*``) map to ``NO_CHANGE``. ``TRANSFER`` moves
  purchases between App User IDs; the webhooks page recommends re-reading
  the customer through ``GET /subscribers`` after any webhook, which is the
  right answer for it.
* A type this release does not know maps to ``UNKNOWN`` instead of raising:
  RevenueCat documents that new event types may appear without an API
  version change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.events import (
    RevenueCatEventTypeEnum,
    RevenueCatPeriodTypeEnum,
    RevenueCatReasonEnum,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas import (
    RevenueCatEvent,
)


class SubscriptionStatusEnum(BaseStrEnum):
    """Normalized state of a subscription after one event.

    * ``TRIAL`` — active, in a free trial period.
    * ``ACTIVE`` — active and paid (or a one-off purchase).
    * ``CANCELLED`` — renewal turned off; access normally lasts until
      ``period_end``.
    * ``BILLING_ISSUE`` — a charge failed; not expired. ``grace_period_end``
      says how long the store keeps trying, when it has a grace period.
    * ``PAUSE_SCHEDULED`` — a Google Play pause starts at the end of the
      period; access continues until then.
    * ``PAUSED`` — the pause started; access is gone until it resumes.
    * ``EXPIRED`` — access should be removed.
    * ``REFUNDED`` — the latest period was refunded.
    * ``TEMPORARY_GRANT`` — RevenueCat granted access for at most 24 hours
      while it could not validate the purchase.
    * ``NO_CHANGE`` — a documented event that does not move the state.
    * ``UNKNOWN`` — an event type this release does not know.
    """

    TRIAL = "trial"
    ACTIVE = "active"
    CANCELLED = "cancelled"
    BILLING_ISSUE = "billing_issue"
    PAUSE_SCHEDULED = "pause_scheduled"
    PAUSED = "paused"
    EXPIRED = "expired"
    REFUNDED = "refunded"
    TEMPORARY_GRANT = "temporary_grant"
    NO_CHANGE = "no_change"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SubscriptionSnapshot:
    """The subscription state one event leaves behind.

    A dataclass rather than a schema: it is a value the service reads and
    branches on, and keeping :attr:`status` an enum member (not a bare
    string) keeps ``snapshot.status is SubscriptionStatusEnum.EXPIRED``
    true.

    Attributes:
        status (SubscriptionStatusEnum): The normalized state.
        event_id (str): The event it came from.
        event_type (str): The event type, as delivered.
        app_user_id (str | None): Last seen App User ID.
        original_app_user_id (str | None): First App User ID.
        aliases (tuple[str, ...]): Every App User ID of the subscriber.
        product_id (str | None): Product of the subscription.
        new_product_id (str | None): Product switched to, on a
            ``PRODUCT_CHANGE``.
        entitlement_ids (tuple[str, ...]): Entitlements of the product.
        period_type (str | None): ``TRIAL``, ``INTRO``, ``NORMAL``, ...
        store (str | None): Store of the purchase.
        environment (str | None): ``SANDBOX`` or ``PRODUCTION``.
        purchased_at (datetime | None): Start of the period, UTC.
        period_end (datetime | None): End of the period, UTC; ``None`` for a
            lifetime or one-off purchase.
        grace_period_end (datetime | None): End of the grace period, UTC.
        reason (str | None): ``cancel_reason`` or ``expiration_reason``.
        event_at (datetime | None): When RevenueCat generated the event, UTC.
            Order by this to discard a late delivery of an older event.
    """

    status: SubscriptionStatusEnum
    event_id: str
    event_type: str
    app_user_id: str | None = None
    original_app_user_id: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    product_id: str | None = None
    new_product_id: str | None = None
    entitlement_ids: tuple[str, ...] = field(default_factory=tuple)
    period_type: str | None = None
    store: str | None = None
    environment: str | None = None
    purchased_at: datetime | None = None
    period_end: datetime | None = None
    grace_period_end: datetime | None = None
    reason: str | None = None
    event_at: datetime | None = None


_E = RevenueCatEventTypeEnum

_NO_CHANGE_EVENTS: frozenset[str] = frozenset(
    {
        _E.TEST.value,
        _E.TRANSFER.value,
        _E.EXPERIMENT_ENROLLMENT.value,
        _E.VIRTUAL_CURRENCY_TRANSACTION.value,
        _E.SUBSCRIBER_ALIAS.value,
        _E.PURCHASE_REDEEMED.value,
        _E.INVOICE_ISSUANCE.value,
        _E.PRICE_INCREASE_CONSENT_REQUIRED.value,
        _E.PRICE_INCREASE_CONSENT_APPROVED.value,
    }
)
"""Documented events that do not move a subscription's state."""

_ACTIVE_EVENTS: frozenset[str] = frozenset(
    {
        _E.INITIAL_PURCHASE.value,
        _E.RENEWAL.value,
        _E.UNCANCELLATION.value,
        _E.NON_RENEWING_PURCHASE.value,
        _E.PRODUCT_CHANGE.value,
        _E.SUBSCRIPTION_EXTENDED.value,
        _E.REFUND_REVERSED.value,
    }
)
"""Events after which the subscription is running (trial or paid)."""


def _from_ms(value: int | None) -> datetime | None:
    """Convert RevenueCat's epoch milliseconds into an aware UTC datetime.

    Args:
        value (int | None): Milliseconds since the epoch.

    Returns:
        datetime | None: The instant, or ``None`` when absent.
    """
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _status(event: RevenueCatEvent) -> SubscriptionStatusEnum:
    """Pick the normalized status for one event.

    Args:
        event (RevenueCatEvent): The event.

    Returns:
        SubscriptionStatusEnum: See the module docstring for each rule.
    """
    kind = event.type
    if kind in _ACTIVE_EVENTS:
        if event.period_type == RevenueCatPeriodTypeEnum.TRIAL:
            return SubscriptionStatusEnum.TRIAL
        return SubscriptionStatusEnum.ACTIVE
    if kind == _E.CANCELLATION:
        if event.cancel_reason == RevenueCatReasonEnum.CUSTOMER_SUPPORT:
            return SubscriptionStatusEnum.REFUNDED
        if event.cancel_reason == RevenueCatReasonEnum.BILLING_ERROR:
            return SubscriptionStatusEnum.BILLING_ISSUE
        return SubscriptionStatusEnum.CANCELLED
    if kind == _E.BILLING_ISSUE:
        return SubscriptionStatusEnum.BILLING_ISSUE
    if kind == _E.SUBSCRIPTION_PAUSED:
        return SubscriptionStatusEnum.PAUSE_SCHEDULED
    if kind == _E.EXPIRATION:
        if event.expiration_reason == RevenueCatReasonEnum.SUBSCRIPTION_PAUSED:
            return SubscriptionStatusEnum.PAUSED
        return SubscriptionStatusEnum.EXPIRED
    if kind == _E.TEMPORARY_ENTITLEMENT_GRANT:
        return SubscriptionStatusEnum.TEMPORARY_GRANT
    if kind in _NO_CHANGE_EVENTS:
        return SubscriptionStatusEnum.NO_CHANGE
    return SubscriptionStatusEnum.UNKNOWN


def normalize(event: RevenueCatEvent) -> SubscriptionSnapshot:
    """Reduce one RevenueCat event to a :class:`SubscriptionSnapshot`.

    Never raises on an event type: an unknown one gives
    :attr:`SubscriptionStatusEnum.UNKNOWN` with the identity fields filled,
    so the service can log it and move on.

    Args:
        event (RevenueCatEvent): The parsed event.

    Returns:
        SubscriptionSnapshot: The state and the fields that go with it.
    """
    return SubscriptionSnapshot(
        status=_status(event),
        event_id=event.id,
        event_type=event.type,
        app_user_id=event.app_user_id,
        original_app_user_id=event.original_app_user_id,
        aliases=tuple(event.aliases),
        product_id=event.product_id,
        new_product_id=event.new_product_id,
        entitlement_ids=tuple(event.entitlement_ids),
        period_type=event.period_type,
        store=event.store,
        environment=event.environment,
        purchased_at=_from_ms(event.purchased_at_ms),
        period_end=_from_ms(event.expiration_at_ms),
        grace_period_end=_from_ms(event.grace_period_expiration_at_ms),
        reason=event.cancel_reason or event.expiration_reason,
        event_at=_from_ms(event.event_timestamp_ms),
    )


__all__: list[str] = [
    "SubscriptionSnapshot",
    "SubscriptionStatusEnum",
    "normalize",
]
