"""Response models for the Stripe objects a payments integration touches.

These are **thin on purpose**. Stripe's specification declares 1440
component schemas, and generating them costs 3.3 MB of Python and 492 MB
of RSS at import (measured; see ``scripts/regen_stripe.py``). What a
service actually reads off a charge is a dozen fields.

So every model here names the fields that carry decisions — status,
amount, currency, the ids that link objects — and sets ``extra="allow"``,
which keeps everything else reachable through
:attr:`~pydantic.BaseModel.model_extra` instead of discarding it. Nothing
is lost; only the type coverage is partial, and it is partial where the
API is widest.

Expandable fields (``customer``, ``payment_intent``, …) are typed ``str``:
without ``expand``, Stripe sends the id. Ask for the object with
``params={"expand": ["customer"]}`` and read it from ``model_extra``.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

_STRIPE_CONFIG = ConfigDict(
    extra="allow",
    populate_by_name=True,
    str_strip_whitespace=True,
)
"""Shared model config.

``extra="allow"`` is the load-bearing setting: a partial model that
*dropped* unknown fields would quietly lose the half of the object this
SDK does not name.
"""


class StripeObject(BaseModel):
    """Base for every Stripe resource.

    Attributes:
        id (str): The object id (``cus_``, ``pi_``, ``sub_``, …).
        object (str): Stripe's own type discriminator (``"customer"``,
            ``"payment_intent"``, …), useful when a field can hold more
            than one kind of object.
        livemode (bool): Whether the object belongs to live mode.
        metadata (dict[str, str]): The key/value map Stripe stores
            verbatim. String-to-string, because that is all Stripe keeps.
    """

    model_config = _STRIPE_CONFIG

    id: str = ""
    object: str = ""
    livemode: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)


class StripeCustomer(StripeObject):
    """A customer.

    Attributes:
        email (str | None): Billing email.
        name (str | None): Full name.
        phone (str | None): Phone number.
        description (str | None): Free-form internal description.
        currency (str | None): Default currency for invoices.
        created (int | None): Creation time, epoch seconds.
    """

    email: str | None = None
    name: str | None = None
    phone: str | None = None
    description: str | None = None
    currency: str | None = None
    created: int | None = None


class StripePaymentIntent(StripeObject):
    """A payment intent — the object a card charge actually is.

    Attributes:
        amount (int): Amount in the currency's smallest unit. Convert with
            :func:`~tempest_fastapi_sdk.integrations.payment.stripe.from_minor_units`,
            never by dividing by 100 — some currencies have no subunit.
        amount_received (int): How much has been captured so far.
        currency (str): Lower-case ISO-4217 code.
        status (str): ``requires_payment_method``, ``requires_action``,
            ``processing``, ``succeeded``, ``canceled``, …
        client_secret (str | None): The value the frontend confirms with.
            **Treat it as a credential**: it authorizes confirming this
            payment, so it belongs in the response to its own customer and
            nowhere else — not in a log line.
        customer (str | None): Customer id, or the expanded object in
            ``model_extra`` when ``expand`` asked for it.
        payment_method (str | None): Payment method id.
        latest_charge (str | None): Charge id, once there is one.
        created (int | None): Creation time, epoch seconds.
    """

    amount: int = 0
    amount_received: int = 0
    currency: str = ""
    status: str = ""
    client_secret: str | None = None
    customer: str | None = None
    payment_method: str | None = None
    latest_charge: str | None = None
    created: int | None = None


class StripeRefund(StripeObject):
    """A refund.

    Attributes:
        amount (int): Refunded amount in the smallest unit.
        currency (str): Lower-case ISO-4217 code.
        status (str): ``pending``, ``succeeded``, ``failed``, ``canceled``.
        reason (str | None): ``duplicate``, ``fraudulent``,
            ``requested_by_customer``, or ``None``.
        payment_intent (str | None): The payment being refunded.
        charge (str | None): The charge being refunded.
        created (int | None): Creation time, epoch seconds.
    """

    amount: int = 0
    currency: str = ""
    status: str = ""
    reason: str | None = None
    payment_intent: str | None = None
    charge: str | None = None
    created: int | None = None


class StripeProduct(StripeObject):
    """A product — what is being sold, independent of what it costs.

    Attributes:
        name (str): Display name.
        description (str | None): Product description.
        active (bool): Whether it can be used in new purchases.
        default_price (str | None): Price id, when one is set.
        created (int | None): Creation time, epoch seconds.
    """

    name: str = ""
    description: str | None = None
    active: bool = True
    default_price: str | None = None
    created: int | None = None


class StripePrice(StripeObject):
    """A price — how much a product costs, and how often.

    Attributes:
        product (str): Product id.
        active (bool): Whether the price can be used.
        currency (str): Lower-case ISO-4217 code.
        unit_amount (int | None): Amount in the smallest unit. ``None``
            for a tiered price, where the amount depends on quantity.
        recurring (dict[str, Any] | None): ``{"interval": "month", …}``
            for a subscription price, ``None`` for a one-off.
        nickname (str | None): Internal label.
        created (int | None): Creation time, epoch seconds.
    """

    product: str = ""
    active: bool = True
    currency: str = ""
    unit_amount: int | None = None
    recurring: dict[str, Any] | None = None
    nickname: str | None = None
    created: int | None = None


class StripeSubscription(StripeObject):
    """A subscription.

    Attributes:
        customer (str): Customer id.
        status (str): ``trialing``, ``active``, ``past_due``, ``canceled``,
            ``incomplete``, ``incomplete_expired``, ``unpaid``, ``paused``.
        currency (str): Lower-case ISO-4217 code.
        cancel_at_period_end (bool): Whether it stops at the end of the
            current period instead of renewing.
        canceled_at (int | None): When it was canceled, epoch seconds.
        latest_invoice (str | None): Invoice id, when there is one.
        created (int | None): Creation time, epoch seconds.
        items (dict[str, Any] | None): The subscription items list, as
            Stripe returns it.
    """

    customer: str = ""
    status: str = ""
    currency: str = ""
    cancel_at_period_end: bool = False
    canceled_at: int | None = None
    latest_invoice: str | None = None
    created: int | None = None
    items: dict[str, Any] | None = None


class StripeInvoice(StripeObject):
    """An invoice.

    Attributes:
        customer (str | None): Customer id.
        subscription (str | None): Subscription id, for a recurring
            invoice.
        status (str): ``draft``, ``open``, ``paid``, ``uncollectible``,
            ``void``.
        currency (str): Lower-case ISO-4217 code.
        amount_due (int): Amount still owed, smallest unit.
        amount_paid (int): Amount already paid, smallest unit.
        total (int): Invoice total, smallest unit.
        hosted_invoice_url (str | None): Stripe-hosted payment page.
        created (int | None): Creation time, epoch seconds.
    """

    customer: str | None = None
    subscription: str | None = None
    status: str = ""
    currency: str = ""
    amount_due: int = 0
    amount_paid: int = 0
    total: int = 0
    hosted_invoice_url: str | None = None
    created: int | None = None


class StripeCheckoutSession(StripeObject):
    """A Checkout session — the hosted payment page.

    Attributes:
        url (str | None): Where to send the customer. ``None`` once the
            session is completed or expired.
        status (str): ``open``, ``complete``, ``expired``.
        payment_status (str): ``paid``, ``unpaid``, ``no_payment_required``.
        mode (str): ``payment``, ``setup`` or ``subscription``.
        amount_total (int | None): Total in the smallest unit.
        currency (str | None): Lower-case ISO-4217 code.
        customer (str | None): Customer id, when one is attached.
        payment_intent (str | None): Payment intent id, in ``payment``
            mode.
        subscription (str | None): Subscription id, in ``subscription``
            mode.
        expires_at (int | None): When the session stops accepting payment.
    """

    url: str | None = None
    status: str = ""
    payment_status: str = ""
    mode: str = ""
    amount_total: int | None = None
    currency: str | None = None
    customer: str | None = None
    payment_intent: str | None = None
    subscription: str | None = None
    expires_at: int | None = None


class StripeEventObject(StripeObject):
    """An event, as read back from ``/v1/events``.

    The webhook path has its own type
    (:class:`~tempest_fastapi_sdk.integrations.payment.stripe.StripeWebhookEvent`),
    which also carries the verified raw body. This one is what a *poll* of
    the events endpoint returns.

    Attributes:
        type (str): The event type, e.g. ``"payment_intent.succeeded"``.
        api_version (str | None): The API version the payload is rendered
            in — not necessarily the one you pinned.
        created (int | None): Creation time, epoch seconds.
        data (dict[str, Any]): ``{"object": {...}}``, the resource the
            event is about.
    """

    type: str = ""
    api_version: str | None = None
    created: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class StripeDeleted(BaseModel):
    """What a delete returns: the id and a tombstone flag.

    Attributes:
        id (str): The deleted object's id.
        object (str): Stripe's type discriminator.
        deleted (bool): Always ``True`` on a successful delete.
    """

    model_config = _STRIPE_CONFIG

    id: str = ""
    object: str = ""
    deleted: bool = False


ResourceT = TypeVar("ResourceT", bound=StripeObject)


class StripeList(BaseModel, Generic[ResourceT]):
    """One page of a Stripe list response.

    Stripe paginates by cursor, not by page number: ask for the next page
    with ``starting_after=<id of the last item>``.
    :meth:`~tempest_fastapi_sdk.integrations.payment.stripe.StripeResource.auto_paginate`
    does that walk for you.

    Attributes:
        object (str): Always ``"list"``.
        data (list[ResourceT]): The page's items, newest first.
        has_more (bool): Whether another page exists after this one.
        url (str): The endpoint this page came from.
    """

    model_config = _STRIPE_CONFIG

    object: str = "list"
    data: list[ResourceT] = Field(default_factory=list)
    has_more: bool = False
    url: str = ""


__all__: list[str] = [
    "StripeCheckoutSession",
    "StripeCustomer",
    "StripeDeleted",
    "StripeEventObject",
    "StripeInvoice",
    "StripeList",
    "StripeObject",
    "StripePaymentIntent",
    "StripePrice",
    "StripeProduct",
    "StripeRefund",
    "StripeSubscription",
]
