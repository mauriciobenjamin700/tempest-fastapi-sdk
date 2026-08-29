"""OpenPix mapped into the canonical Pix contract.

Everything provider-specific about OpenPix that the contract hides is
decided here: cents already being cents, the three charge states, the QR
code arriving as a URL rather than Base64, and the cancellation that
answers with two fields instead of a charge.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from tempest_fastapi_sdk.integrations.payment.base import (
    PaymentStatus,
    PixCharge,
    PixChargeRequest,
    PixEventType,
    PixPaymentEvent,
)
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ChargePayload,
    ChargeStatus,
    CustomerPayload,
    OpenPixClient,
    OpenPixEvent,
    OpenPixWebhookEvent,
    to_cents,
)

PROVIDER_NAME: Final[str] = "openpix"
"""Value written into :attr:`PixCharge.provider` by this adapter."""

STATUS_MAP: Final[dict[ChargeStatus, PaymentStatus]] = {
    ChargeStatus.ACTIVE: PaymentStatus.PENDING,
    ChargeStatus.COMPLETED: PaymentStatus.PAID,
    ChargeStatus.EXPIRED: PaymentStatus.EXPIRED,
}
"""Every value of OpenPix's ``ChargeStatus``, mapped to the canonical one.

Exhaustive by construction and kept that way by
``tests/integrations/payment/test_contract.py``, which walks the generated
enum and fails on any member missing here.

What that guard cannot cover is the opposite direction: a state the
*provider* reports and the generated enum does not have. It happens —
the document declares three values on ``Charge.status`` and leaves
``WebhookCharge.status`` an unconstrained string, so OpenPix has not
committed to the list on the path it validates. Both readers below treat a
value missing from this map as :attr:`PaymentStatus.UNKNOWN`, keeping the
provider's own string in ``provider_status``. Falling through to
``PENDING`` would report a charge as awaiting payment on the strength of
not recognizing it.
"""

EVENT_MAP: Final[dict[OpenPixEvent, PixEventType]] = {
    OpenPixEvent.CHARGE_CREATED: PixEventType.CHARGE_CREATED,
    OpenPixEvent.CHARGE_COMPLETED: PixEventType.CHARGE_PAID,
    OpenPixEvent.CHARGE_COMPLETED_NOT_SAME_CUSTOMER_PAYER: (PixEventType.CHARGE_PAID),
    OpenPixEvent.CHARGE_EXPIRED: PixEventType.CHARGE_EXPIRED,
    OpenPixEvent.TRANSACTION_REFUND_RECEIVED: PixEventType.CHARGE_REFUNDED,
    OpenPixEvent.PIX_TRANSACTION_REFUND_RECEIVED_CONFIRMED: (
        PixEventType.CHARGE_REFUNDED
    ),
    OpenPixEvent.PIX_TRANSACTION_REFUND_SENT_CONFIRMED: (PixEventType.CHARGE_REFUNDED),
}
"""OpenPix event names that carry a canonical meaning.

Deliberately partial. OpenPix ships 27 event names covering disputes,
account registration, movements and Pix Automático; only the ones that
describe a charge's lifecycle map here, and everything else becomes
:attr:`PixEventType.UNKNOWN` with the original name preserved. Mapping an
account-registration event onto a charge event would be worse than not
mapping it.

``CHARGE_COMPLETED_NOT_SAME_CUSTOMER_PAYER`` is settlement too: the money
arrived, by someone other than the registered customer. Treating it as
anything but paid would leave a settled charge open.
"""


def _parse_datetime(value: object) -> datetime | None:
    """Read an ISO 8601 timestamp that OpenPix types as a bare string.

    Args:
        value (object): The field as the generated model produced it.

    Returns:
        datetime | None: The parsed timestamp, or ``None`` when the field
        is absent or does not parse. A malformed date is not worth raising
        over here — the charge is still a charge, and refusing it would
        turn a cosmetic field into a failed payment.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_status(raw: object) -> PaymentStatus:
    """Read OpenPix's status string into the canonical state.

    Args:
        raw (object): The ``status`` field, from the generated model or
            straight off a webhook body. Typed ``object`` because since
            the v0.270.0 overlay ``Charge.status`` is a plain ``str``: the
            document's three-value ``enum`` is not honoured by the API,
            and a closed enum on a *response* turns an unrecognized state
            into a refused read.

    Returns:
        PaymentStatus: The mapped state, or :attr:`PaymentStatus.UNKNOWN`
        for anything :data:`STATUS_MAP` does not name. The provider's own
        string is kept by the caller in ``provider_status``, so nothing is
        lost by not recognizing it.
    """
    if not isinstance(raw, str) or not raw:
        return PaymentStatus.UNKNOWN
    try:
        return STATUS_MAP[ChargeStatus(raw)]
    except (KeyError, ValueError):
        return PaymentStatus.UNKNOWN


def _webhook_cents(payload: dict[str, Any], value: Any) -> int:
    """Read a webhook charge's ``value`` into cents, or refuse the delivery.

    Args:
        payload (dict[str, Any]): The ``charge`` object, for the error
            message — a rejected delivery is worth naming the charge it
            was about.
        value (Any): The ``value`` field exactly as JSON parsed it.

    Returns:
        int: The amount in cents.

    The message keeps ``to_cents``'s own reason verbatim. "Not a whole
    number of cents" and "cannot be negative" are different mistakes with
    different fixes, and a wrapper that replaces both with one sentence
    tells an operator only that something was wrong.

    Raises:
        ValueError: If the value cannot be read as a whole, non-negative
            number of cents. The guard here used to be
            ``isinstance(value, (int, float))`` with ``else 0`` — narrower
            than :func:`to_cents`, which accepts ``str`` on purpose
            because its documented input is a raw payload. The document
            types ``value`` as a string in two schemas, so ``"1990"`` is a
            body OpenPix can legitimately send, and it arrived as a charge
            *paid for nothing*: no exception, no log, and a divergence
            that surfaces at reconciliation rather than at the call.
            Zero is a number a service credits; "unreadable" is not zero.

    A ``value`` the delivery omits is refused with the rest rather than
    read as ``0``: every example body in the vendored document that
    carries a ``charge`` object carries the field too — 10 of 10,
    measured — so its absence is not a shape the provider documents.

    Refusing makes the webhook route answer non-2xx, which OpenPix
    retries. That is the point: a delivery this SDK cannot read is worth
    retrying, and is not worth booking as a settlement of nothing.
    """
    try:
        return to_cents(value)
    except (ValueError, TypeError) as error:
        reference = payload.get("correlationID") or payload.get("identifier")
        raise ValueError(
            f"OpenPix delivered a charge whose value cannot be read as cents: "
            f"{value!r} (correlationID={reference!r}) — {error}"
        ) from error


def _as_optional_str(value: object) -> str | None:
    """Narrow a field the specification types as ``Any`` to a string.

    Args:
        value (object): The field as the generated model produced it.
            OpenPix's specification types ``qrCodeImage``, ``globalID`` and
            ``transactionID`` as untyped, so the generated model carries
            ``Any``.

    Returns:
        str | None: The value when it is a non-empty string, else ``None``.
    """
    return value if isinstance(value, str) and value else None


class OpenPixPixProvider:
    """OpenPix as a :class:`~...payment.base.PixProvider`.

    Satisfies the protocol structurally — nothing is inherited, matching how
    every other provider seam in the SDK works.

    Attributes:
        provider_name (str): Always ``"openpix"``.
    """

    provider_name: str = PROVIDER_NAME

    def __init__(self, client: OpenPixClient) -> None:
        """Wrap an already-configured OpenPix client.

        Args:
            client (OpenPixClient): The generated client, built over an
                ``HTTPClient`` that already carries the AppID in
                ``Authorization`` and points at the right environment.
        """
        self._client: OpenPixClient = client

    async def create_pix_charge(self, request: PixChargeRequest) -> PixCharge:
        """Create a charge at OpenPix.

        ``amount_cents`` goes across unchanged: OpenPix states ``value`` in
        cents already, and since v0.259.0 the generated model types it as
        ``int``. The response still goes back through ``to_cents``, which
        now narrows nothing and validates everything — a value that is
        negative or not whole means the assumption about the field is
        wrong, and that is worth an error rather than a plausible number.

        ``expires_in`` is sent in seconds, truncated to a whole one: the
        field is an integer count, and ``timedelta.total_seconds()``
        answers a ``float``. OpenPix refuses anything under five minutes
        with a 400; that floor is not re-checked here, because duplicating
        a provider's validation is how the copy drifts from the original.

        Args:
            request (PixChargeRequest): What to charge, and for whom.

        Returns:
            PixCharge: The created charge, carrying the copy-and-paste code.

        Raises:
            httpx.HTTPStatusError: For any non-2xx answer from OpenPix.
            ValueError: If OpenPix answers 2xx without a charge body, which
                would otherwise surface later as an attribute error far
                from the call that caused it.
        """
        payload = ChargePayload(
            value=request.amount_cents,
            correlation_id=request.reference,
            comment=request.description,
            expires_in=(
                int(request.expires_in.total_seconds())
                if request.expires_in is not None
                else None
            ),
            customer=self._customer(request),
        )
        response = await self._client.create_charge(body=payload)
        if response.charge is None:
            raise ValueError("OpenPix accepted the charge but returned no charge body.")
        charge = self._to_pix_charge(response.charge)
        if charge.br_code is None and response.br_code is not None:
            return charge.model_copy(update={"br_code": response.br_code})
        return charge

    async def get_pix_charge(self, charge_id: str) -> PixCharge:
        """Read a charge back from OpenPix.

        Args:
            charge_id (str): The charge's identifier. OpenPix accepts its
                own id or the ``correlationID`` on this route, so either
                works.

        Returns:
            PixCharge: The charge as OpenPix currently reports it.

        Raises:
            httpx.HTTPStatusError: For any non-2xx answer from OpenPix.
            ValueError: If the answer carries no charge body.
        """
        response = await self._client.get_charge(charge_id)
        if response.charge is None:
            raise ValueError(f"OpenPix returned no charge for {charge_id!r}.")
        return self._to_pix_charge(response.charge)

    async def cancel_pix_charge(self, charge_id: str) -> PixCharge:
        """Withdraw an unpaid charge.

        OpenPix answers this route with ``{"status": ..., "id": ...}`` and
        nothing else — no amount, no code, no timestamps. The charge that
        comes back therefore has those fields empty rather than refetched:
        a second round-trip the caller did not ask for would hide latency
        and could fail on its own after the cancellation already happened.

        Args:
            charge_id (str): The charge's identifier.

        Returns:
            PixCharge: The cancelled charge, with only what OpenPix
            reported plus the amount the caller cannot recover from this
            call, which is ``0``.

        Raises:
            httpx.HTTPStatusError: For any non-2xx answer from OpenPix.
        """
        response = await self._client.delete_charge(charge_id)
        return PixCharge(
            provider=PROVIDER_NAME,
            provider_charge_id=response.id or charge_id,
            reference="",
            amount_cents=0,
            status=PaymentStatus.CANCELLED,
            provider_status=response.status or "",
            raw=response.model_dump(mode="json"),
        )

    def parse_webhook(self, event: Any) -> PixPaymentEvent:
        """Turn a verified OpenPix delivery into a canonical event.

        Args:
            event (Any): An
                :class:`~...payment.openpix.OpenPixWebhookEvent`, as
                produced by ``make_openpix_webhook_dependency``. Typed
                ``Any`` because the protocol cannot name a per-provider
                type.

        Returns:
            PixPaymentEvent: The canonical event. Its ``charge`` is filled
            only when the delivery carried a ``charge`` object; the raw body
            is always kept.

        Raises:
            TypeError: If handed something other than an
                ``OpenPixWebhookEvent``. Signature verification is not
                optional, so accepting a bare dict here would let an
                unverified body through the front door.
        """
        if not isinstance(event, OpenPixWebhookEvent):
            raise TypeError(
                "parse_webhook expects the verified OpenPixWebhookEvent, "
                f"got {type(event).__name__}."
            )
        payload = event.payload.get("charge")
        charge: PixCharge | None = None
        if isinstance(payload, dict):
            charge = self._from_webhook_payload(payload)
        return PixPaymentEvent(
            provider=PROVIDER_NAME,
            type=(
                EVENT_MAP.get(event.event, PixEventType.UNKNOWN)
                if event.event is not None
                else PixEventType.UNKNOWN
            ),
            provider_event_name=event.event_name,
            charge=charge,
            raw=event.payload,
        )

    @staticmethod
    def _customer(request: PixChargeRequest) -> CustomerPayload | None:
        """Build OpenPix's customer block, when there is enough to build it.

        Args:
            request (PixChargeRequest): The canonical request.

        Returns:
            CustomerPayload | None: The customer, or ``None`` when the
            payer does not satisfy any variant the provider accepts.

        ``CustomerPayload`` is a ``oneOf`` of three variants, and the
        document requires ``name`` in all three plus one of ``taxID``,
        ``email`` or ``phone``::

            CustomerPayload.oneOf[0] required = ['name', 'taxID']
            CustomerPayload.oneOf[1] required = ['name', 'email']
            CustomerPayload.oneOf[2] required = ['name', 'phone']

        So ``name`` is necessary in all three and sufficient in none, and
        a block carrying only a name is a claim no variant accepts. The
        generated model cannot refuse it — the emitter records that it
        merges ``oneOf`` into one model and does not enforce "exactly one
        variant" — and every field of :class:`PixPayer` is optional, so
        the check has to live here.

        A payer who does not reach any variant is omitted rather than
        padded: inventing a contact detail to satisfy the schema would put
        made-up data on the payer's receipt, and the charge is valid
        without a customer block at all.
        """
        payer = request.payer
        if payer is None or not payer.name:
            return None
        if not (payer.tax_id or payer.email or payer.phone):
            return None
        return CustomerPayload(
            name=payer.name,
            email=payer.email,
            phone=payer.phone,
            tax_id=payer.tax_id,
        )

    @staticmethod
    def _to_pix_charge(charge: Any) -> PixCharge:
        """Map a generated ``Charge`` onto the canonical shape.

        ``raw`` is dumped ``by_alias=True``, so it carries the wire's own
        spelling — ``paymentLinkUrl``, not ``payment_link_url``. The two
        paths used to disagree: this one dumped declared fields in
        ``snake_case`` while the webhook path kept the raw dictionary, so
        ``raw["paymentLinkUrl"]`` — the very lookup the recipe teaches —
        answered the link on a delivery and ``None`` on every charge read
        back from the API. Worse, this path produced a *mixture*: a field
        the specification does not declare survives under ``extra="allow"``
        and kept the wire spelling while its declared neighbours did not.

        The provider's spelling is the one a consumer can predict from the
        provider's own documentation, so it is the one ``raw`` uses.

        Args:
            charge (Any): The generated ``Charge`` model.

        Returns:
            PixCharge: The canonical charge.
        """
        status = charge.status
        return PixCharge(
            provider=PROVIDER_NAME,
            provider_charge_id=(
                _as_optional_str(charge.global_id)
                or charge.identifier
                or charge.correlation_id
                or ""
            ),
            reference=charge.correlation_id or "",
            amount_cents=to_cents(charge.value) if charge.value else 0,
            status=_to_status(status),
            provider_status=status if isinstance(status, str) else "",
            br_code=charge.br_code,
            qr_code_image_url=_as_optional_str(charge.qr_code_image),
            expires_at=_parse_datetime(charge.expires_date),
            raw=charge.model_dump(mode="json", by_alias=True),
        )

    @staticmethod
    def _from_webhook_payload(payload: dict[str, Any]) -> PixCharge:
        """Map the ``charge`` object of a webhook delivery.

        Read straight from the dictionary rather than through the generated
        model: the delivery is the one place where OpenPix's undocumented
        fields survive, and validating here would drop them before ``raw``
        could keep them. ``paidAt`` is exactly such a field — it appears in
        the specification's webhook examples but not in the ``Charge``
        schema, so the API path can never fill :attr:`PixCharge.paid_at` and
        this one can.

        Args:
            payload (dict[str, Any]): The ``charge`` object as delivered.

        Returns:
            PixCharge: The canonical charge.

        Raises:
            ValueError: If ``value`` cannot be read as cents. A delivery
                is the settlement notice a service credits from, so an
                unreadable amount is refused rather than reported as zero
                — see :func:`_webhook_cents`. This is deliberately a
                different posture from :func:`_parse_datetime`, which
                swallows a malformed timestamp: a wrong date is cosmetic
                and a wrong amount is money.
        """
        raw_status = payload.get("status")
        return PixCharge(
            provider=PROVIDER_NAME,
            provider_charge_id=(
                _as_optional_str(payload.get("globalID"))
                or _as_optional_str(payload.get("identifier"))
                or _as_optional_str(payload.get("correlationID"))
                or ""
            ),
            reference=_as_optional_str(payload.get("correlationID")) or "",
            amount_cents=_webhook_cents(payload, payload.get("value")),
            status=_to_status(raw_status),
            provider_status=raw_status if isinstance(raw_status, str) else "",
            br_code=_as_optional_str(payload.get("brCode")),
            qr_code_image_url=_as_optional_str(payload.get("qrCodeImage")),
            expires_at=_parse_datetime(payload.get("expiresDate")),
            paid_at=_parse_datetime(payload.get("paidAt")),
            raw=payload,
        )


__all__: list[str] = [
    "EVENT_MAP",
    "PROVIDER_NAME",
    "STATUS_MAP",
    "OpenPixPixProvider",
]
