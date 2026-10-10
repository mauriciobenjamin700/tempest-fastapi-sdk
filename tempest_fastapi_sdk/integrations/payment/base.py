"""The canonical payment contract every bundled provider is mapped into.

A service that charges over Pix should depend on this module, never on the
shape of a provider. The shapes diverge more than the domain does: OpenPix
states the amount in **cents inside a float** and has three charge states;
Mercado Pago states it in **reais inside a float** and has nine payment
states, five order states and four transaction states, in three different
schemas. Writing ``charge.status == "COMPLETED"`` couples a service not to
Pix but to one provider — and, in Mercado Pago's case, to one endpoint of
one provider.

What lives here is only what every provider can answer. Everything a
provider says beyond that survives in :attr:`PixCharge.raw`, so nothing is
lost by going through the contract.

The translation lives in ``integrations/payment/adapters/``. The contract
knows no provider; an adapter knows both.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

from pydantic import ConfigDict, Field

from tempest_fastapi_sdk import BaseSchema, BaseStrEnum
from tempest_fastapi_sdk.utils.regex import PixKeyType


class PaymentStatus(BaseStrEnum):
    """The state of a charge, in the vocabulary the SDK guarantees.

    A provider's own string is never discarded — it travels alongside in
    :attr:`PixCharge.provider_status`. This enum is what a service branches
    on.

    :attr:`UNKNOWN` exists for the same reason
    :attr:`PixEventType.UNKNOWN` does: a provider can report a state this
    SDK version has never seen, and the two ways of hiding that are both
    worse than saying so. Falling through to :attr:`PENDING` reports a
    charge as awaiting payment when the provider just said it is not;
    refusing to read the charge at all turns a state the SDK does not
    recognize into a failed request, with the real state nowhere the
    caller can see it.

    Attributes:
        PENDING (str): Created and waiting for the payer.
        AUTHORIZED (str): Approved by the card issuer and held, not
            captured. The money has not moved: capture settles it,
            cancelling releases the hold. Distinct from :attr:`PENDING`,
            which waits on the payer, and from :attr:`PAID`, which is
            settled.
        PAID (str): Settled. The money is with the receiver.
        EXPIRED (str): The window closed before payment.
        CANCELLED (str): Withdrawn by the merchant or the provider.
        REFUNDED (str): Paid and then returned to the payer.
        CHARGED_BACK (str): Reversed by the payer's institution after
            settlement, which is not the same event as a refund and is not
            initiated by us.
        IN_ANALYSIS (str): Held by the provider for review — neither
            settled nor refused yet.
        FAILED (str): Refused. A terminal state distinct from
            :attr:`EXPIRED`, which is about time rather than refusal.
        UNKNOWN (str): A state this SDK version does not classify. The
            provider's own string stays in
            :attr:`PixCharge.provider_status`, so an unmapped state is
            visible rather than reported as something it is not.
    """

    PENDING = "pending"
    AUTHORIZED = "authorized"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    CHARGED_BACK = "charged_back"
    IN_ANALYSIS = "in_analysis"
    FAILED = "failed"
    UNKNOWN = "unknown"


class PixEventType(BaseStrEnum):
    """What a webhook delivery means, once the provider name is dropped.

    Attributes:
        CHARGE_CREATED (str): A charge came into existence.
        CHARGE_PAID (str): A charge settled.
        CHARGE_EXPIRED (str): A charge reached its deadline unpaid.
        CHARGE_CANCELLED (str): A charge was withdrawn.
        CHARGE_REFUNDED (str): Money went back to the payer.
        UNKNOWN (str): A delivery this SDK version does not classify. The
            provider's own name stays in
            :attr:`PixPaymentEvent.provider_event_name`, so an unmapped
            event is visible rather than swallowed.
    """

    CHARGE_CREATED = "charge_created"
    CHARGE_PAID = "charge_paid"
    CHARGE_EXPIRED = "charge_expired"
    CHARGE_CANCELLED = "charge_cancelled"
    CHARGE_REFUNDED = "charge_refunded"
    UNKNOWN = "unknown"


class _EnumSafeSchema(BaseSchema):
    """A :class:`BaseSchema` that keeps enum members as enum members.

    ``BaseSchema`` sets ``use_enum_values=True``, which stores the *value*
    instead of the member. A field annotated ``PaymentStatus`` would then
    hold a plain ``str`` at runtime while the type-checker still reads it
    as the enum, so ``charge.status is PaymentStatus.PAID`` is always
    ``False`` — silently, on every charge. ``BaseStrEnum`` mixes in ``str``,
    so ``==`` keeps working either way, which is exactly what makes the
    defect survive review.

    The same trap is why
    :class:`~tempest_fastapi_sdk.integrations.payment.openpix.OpenPixWebhookEvent`
    is a dataclass. Here the schemas are wire DTOs a service serializes in a
    response, so the fix is to keep Pydantic and turn the flag off:
    ``model_dump(mode="json")`` still renders ``"paid"``.

    Attributes:
        model_config (ConfigDict): ``BaseSchema``'s configuration with
            ``use_enum_values`` disabled.
    """

    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        use_enum_values=False,
        str_strip_whitespace=True,
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )


class PixPayer(BaseSchema):
    """Who is paying, to the extent a provider accepts being told.

    Every field is optional because a Pix charge is valid without any of
    them; providers use what they get to prefill their screens and to bind
    the charge to a tax ID when asked to.

    Attributes:
        name (str | None): The payer's full name.
        tax_id (str | None): CPF or CNPJ, digits or formatted — the adapter
            sends it in whatever shape its provider expects.
        email (str | None): The payer's e-mail.
        phone (str | None): The payer's phone number.
    """

    name: str | None = Field(default=None, description="Nome do pagador.")
    tax_id: str | None = Field(default=None, description="CPF ou CNPJ do pagador.")
    email: str | None = Field(default=None, description="E-mail do pagador.")
    phone: str | None = Field(default=None, description="Telefone do pagador.")


class PixChargeRequest(BaseSchema):
    """What a service asks for when it wants to charge over Pix.

    Attributes:
        amount_cents (int): The amount in cents. An ``int``, never a
            ``float``: both bundled providers type money as ``number`` in
            their specification, and a value that has been through a float
            is a value that can be wrong. The adapter converts to whatever
            unit its provider states.
        reference (str): The identifier **the service** owns. It becomes
            OpenPix's ``correlationID`` and Mercado Pago's
            ``external_reference``, and it is what lets a webhook that
            arrives before the HTTP response still be reconciled.
        description (str | None): Free text shown to the payer where the
            provider supports it.
        expires_in (timedelta | None): How long the charge stays payable.
            ``None`` leaves the provider's default in place.
        payer (PixPayer | None): Who is paying, when known.
    """

    amount_cents: int = Field(
        gt=0,
        description="Valor da cobrança em centavos.",
        examples=[1990],
    )
    reference: str = Field(
        min_length=1,
        description="Identificador da cobrança no lado do serviço.",
        examples=["order-1042"],
    )
    description: str | None = Field(
        default=None, description="Texto exibido ao pagador."
    )
    expires_in: timedelta | None = Field(
        default=None, description="Janela de pagamento da cobrança."
    )
    payer: PixPayer | None = Field(default=None, description="Dados do pagador.")


class PixCharge(_EnumSafeSchema):
    """A Pix charge, in the shape every provider is mapped into.

    Attributes:
        provider (str): The provider that owns this charge, matching
            :attr:`PixProvider.provider_name`.
        provider_charge_id (str): The charge's identifier at the provider,
            which is what a later read or cancellation is addressed to.
        reference (str): The identifier the service sent in
            :attr:`PixChargeRequest.reference`.
        amount_cents (int): The amount in cents.
        currency (str): ISO 4217 code. Pix is BRL-only, and the field
            exists so the contract does not have to change the day a
            provider settles in another currency.
        status (PaymentStatus): The canonical state.
        provider_status (str): The provider's own string, kept verbatim for
            logs and support — the canonical mapping loses detail on
            purpose, and this is where the detail stays.
        br_code (str | None): The EMV copy-and-paste string.
        qr_code_base64 (str | None): The QR code as a Base64 image, for
            providers that return the image inline.
        qr_code_image_url (str | None): The QR code as a URL, for providers
            that return a link instead. Both fields exist because the two
            bundled providers disagree: OpenPix returns a URL, Mercado Pago
            returns Base64. Filling the wrong one would make the contract
            lie about what the caller can render.
        end_to_end_id (str | None): The Pix end-to-end identifier, when the
            provider reports it. Deliberately **not** filled with a
            provider's internal transaction id: a txid is not an E2E id,
            and a plausible wrong value is worse than a missing one.
        expires_at (datetime | None): When the charge stops being payable.
        paid_at (datetime | None): When settlement happened.
        raw (dict[str, Any]): The provider's payload as decoded. Present
            because ``BaseSchema`` is ``extra="ignore"``: without it,
            everything a provider sends beyond this contract would be
            dropped in validation, with no error. Keys are spelled the way
            the provider spells them **on the wire**, whichever path
            produced the charge — an adapter that reads a response through
            a generated model dumps it ``by_alias``, so an API read and a
            webhook delivery answer to the same key.
    """

    provider: str = Field(description="Provedor dono desta cobrança.")
    provider_charge_id: str = Field(
        description="Identificador da cobrança no provedor."
    )
    reference: str = Field(description="Identificador no lado do serviço.")
    amount_cents: int = Field(description="Valor da cobrança em centavos.")
    currency: str = Field(default="BRL", description="Código ISO 4217.")
    status: PaymentStatus = Field(description="Estado canônico da cobrança.")
    provider_status: str = Field(description="Estado como o provedor o nomeia, cru.")
    br_code: str | None = Field(default=None, description="String copia-e-cola EMV.")
    qr_code_base64: str | None = Field(
        default=None, description="Imagem do QR code em Base64."
    )
    qr_code_image_url: str | None = Field(
        default=None, description="URL da imagem do QR code."
    )
    end_to_end_id: str | None = Field(
        default=None, description="Identificador fim-a-fim do Pix."
    )
    expires_at: datetime | None = Field(
        default=None, description="Quando a cobrança expira."
    )
    paid_at: datetime | None = Field(
        default=None, description="Quando a cobrança foi paga."
    )
    raw: dict[str, Any] = Field(
        default_factory=dict, description="Payload cru do provedor."
    )


class PixPaymentEvent(_EnumSafeSchema):
    """A webhook delivery, after the provider's verifier accepted it.

    Verification stays with the provider — RSA-1024 at OpenPix, HMAC at
    Stripe — because signing is the one thing that cannot be unified. What
    is unified is what comes out of it.

    Attributes:
        provider (str): The provider that delivered the event.
        type (PixEventType): The canonical meaning.
        provider_event_name (str): The provider's own event name, kept even
            when it maps to :attr:`PixEventType.UNKNOWN`.
        charge (PixCharge | None): The charge the event is about, when the
            payload carried enough to build one.
        raw (dict[str, Any]): The decoded delivery body.
    """

    provider: str = Field(description="Provedor que entregou o evento.")
    type: PixEventType = Field(description="Significado canônico do evento.")
    provider_event_name: str = Field(
        description="Nome do evento como o provedor o envia."
    )
    charge: PixCharge | None = Field(
        default=None, description="Cobrança a que o evento se refere."
    )
    raw: dict[str, Any] = Field(
        default_factory=dict, description="Corpo cru da entrega."
    )


class PixProvider(Protocol):
    """The contract every Pix integration implements.

    A ``Protocol`` rather than a base class, matching how the SDK models
    every other provider seam (``RateLimitStore``, ``QuotaStore``,
    ``ModerationBackend``, ``PushDispatcher``, ``TextBackend``). An adapter
    satisfies it by shape; nothing has to be inherited.

    Deliberately **not** ``runtime_checkable``: ``isinstance`` against a
    runtime-checkable protocol only checks that the attribute names exist,
    so an adapter whose ``create_pix_charge`` takes the wrong arguments
    would pass. ``tests/integrations/payment/test_contract.py`` compares
    ``inspect.signature`` instead, which is the check worth having.

    Attributes:
        provider_name (str): The provider's identifier, copied into
            :attr:`PixCharge.provider`.
    """

    provider_name: str

    async def create_pix_charge(self, request: PixChargeRequest) -> PixCharge:
        """Create a charge.

        Args:
            request (PixChargeRequest): What to charge, and for whom.

        Returns:
            PixCharge: The created charge, with the payable code filled in.
        """
        ...

    async def get_pix_charge(self, charge_id: str) -> PixCharge:
        """Read a charge back from the provider.

        Args:
            charge_id (str): :attr:`PixCharge.provider_charge_id`.

        Returns:
            PixCharge: The charge as the provider currently reports it.
        """
        ...

    async def cancel_pix_charge(self, charge_id: str) -> PixCharge:
        """Withdraw a charge that has not been paid.

        Args:
            charge_id (str): :attr:`PixCharge.provider_charge_id`.

        Returns:
            PixCharge: The charge after cancellation. Providers differ in
            how much they return here, so fields the provider does not
            report on this call come back ``None`` rather than being
            refetched behind the caller's back.
        """
        ...

    def parse_webhook(self, event: Any) -> PixPaymentEvent:
        """Turn a verified provider delivery into a canonical event.

        Args:
            event (Any): Whatever the provider's own verifier produces —
                an ``OpenPixWebhookEvent``, a ``StripeWebhookEvent``, and so
                on. Typed ``Any`` because the input type is the one part of
                this contract that is genuinely per-provider.

        Returns:
            PixPaymentEvent: The canonical event.
        """
        ...


class CardChargeRequest(BaseSchema):
    """What a service asks for when it charges a card.

    The card itself never reaches the service: it is tokenized in the
    browser or app with the provider's public key, and what arrives here is
    the token. Handling the card number on a server puts that server in PCI
    DSS scope.

    Attributes:
        amount_cents (int): The amount, in cents. With installments, the
            amount the provider's installment options quote for the choice
            the payer made.
        reference (str): The identifier the service owns, as in
            :attr:`PixChargeRequest.reference`.
        card_token (str): The single-use token the client-side tokenizer
            produced.
        payment_method_id (str): The card brand as the provider names it
            (``visa``, ``master``, ...), which the tokenizer reports.
        installments (int): How many installments the payer chose, from
            the options the provider offered for this card and amount.
        capture (bool): ``True`` charges at once. ``False`` only
            authorizes: the charge comes back
            :attr:`PaymentStatus.AUTHORIZED` and waits for
            :meth:`CardProvider.capture_card_charge` or
            :meth:`CardProvider.cancel_card_charge`.
        description (str | None): Free text, where the provider supports it.
        payer (PixPayer | None): Who is paying. The name is historical — the
            same payer block serves card and Pix.
    """

    amount_cents: int = Field(gt=0, description="Valor da cobrança em centavos.")
    reference: str = Field(
        min_length=1, description="Identificador da cobrança no lado do serviço."
    )
    card_token: str = Field(
        min_length=1, description="Token do cartão gerado no cliente."
    )
    payment_method_id: str = Field(
        min_length=1, description="Bandeira do cartão (visa, master, ...)."
    )
    installments: int = Field(default=1, ge=1, description="Número de parcelas.")
    capture: bool = Field(default=True, description="Capturar na hora.")
    description: str | None = Field(default=None, description="Texto da cobrança.")
    payer: PixPayer | None = Field(default=None, description="Dados do pagador.")


class CardCharge(_EnumSafeSchema):
    """A card charge, in the shape every provider is mapped into.

    Attributes:
        provider (str): The provider that owns this charge.
        provider_charge_id (str): The charge's identifier at the provider —
            what capture, cancel and refund are addressed to.
        reference (str): The identifier the service sent.
        amount_cents (int): The amount charged, in cents.
        refunded_cents (int): How much of it went back to the payer. A
            partial refund leaves :attr:`status` at
            :attr:`PaymentStatus.PAID` and shows here.
        currency (str): ISO 4217 code.
        status (PaymentStatus): The canonical state. A declined card is
            :attr:`PaymentStatus.FAILED`, returned, not raised: the charge
            exists at the provider, and the reason is in
            :attr:`status_detail`.
        provider_status (str): The provider's own status string.
        status_detail (str | None): The provider's reason —
            ``rejected_by_issuer``, ``waiting_capture``, ``accredited``.
            What a service shows the payer on a decline comes from here.
        payment_method_id (str | None): The card brand.
        installments (int | None): How many installments were charged.
        raw (dict[str, Any]): The provider's payload as decoded.
    """

    provider: str = Field(description="Provedor dono desta cobrança.")
    provider_charge_id: str = Field(
        description="Identificador da cobrança no provedor."
    )
    reference: str = Field(description="Identificador no lado do serviço.")
    amount_cents: int = Field(description="Valor cobrado em centavos.")
    refunded_cents: int = Field(default=0, description="Valor devolvido em centavos.")
    currency: str = Field(default="BRL", description="Código ISO 4217.")
    status: PaymentStatus = Field(description="Estado canônico da cobrança.")
    provider_status: str = Field(description="Estado como o provedor o nomeia, cru.")
    status_detail: str | None = Field(
        default=None, description="Motivo do estado, como o provedor o nomeia."
    )
    payment_method_id: str | None = Field(default=None, description="Bandeira.")
    installments: int | None = Field(default=None, description="Parcelas cobradas.")
    raw: dict[str, Any] = Field(
        default_factory=dict, description="Payload cru do provedor."
    )


class CardProvider(Protocol):
    """The contract a card integration implements.

    Separate from :class:`PixProvider` because a card charge has a lifecycle
    Pix does not: it can be authorized and captured later, refunded in part,
    and declined with a reason. Structural, like every provider seam here.

    A decline is a **result**, not an exception: ``create_card_charge``
    returns a :class:`CardCharge` with :attr:`PaymentStatus.FAILED` and the
    reason in :attr:`CardCharge.status_detail`. Exceptions are for the call
    itself failing — transport, an invalid request — where whether a charge
    exists is the open question.

    Attributes:
        provider_name (str): Copied into :attr:`CardCharge.provider`.
    """

    provider_name: str

    async def create_card_charge(self, request: CardChargeRequest) -> CardCharge:
        """Charge, or only authorize, a tokenized card.

        Args:
            request (CardChargeRequest): What to charge, and how.

        Returns:
            CardCharge: The charge — :attr:`PaymentStatus.PAID`,
            :attr:`PaymentStatus.AUTHORIZED` or :attr:`PaymentStatus.FAILED`.
        """
        ...

    async def get_card_charge(self, charge_id: str) -> CardCharge:
        """Read a charge back from the provider.

        Args:
            charge_id (str): :attr:`CardCharge.provider_charge_id`.

        Returns:
            CardCharge: The charge as the provider reports it now.
        """
        ...

    async def capture_card_charge(self, charge_id: str) -> CardCharge:
        """Settle an authorized charge.

        Args:
            charge_id (str): :attr:`CardCharge.provider_charge_id`.

        Returns:
            CardCharge: The charge after capture.
        """
        ...

    async def cancel_card_charge(self, charge_id: str) -> CardCharge:
        """Release an authorization that was not captured.

        Args:
            charge_id (str): :attr:`CardCharge.provider_charge_id`.

        Returns:
            CardCharge: The charge after cancellation.
        """
        ...

    async def refund_card_charge(
        self, charge_id: str, amount_cents: int | None = None
    ) -> CardCharge:
        """Give money back to the payer, in full or in part.

        Args:
            charge_id (str): :attr:`CardCharge.provider_charge_id`.
            amount_cents (int | None): How much to refund. ``None`` refunds
                whatever is left.

        Returns:
            CardCharge: The charge after the refund, with
            :attr:`CardCharge.refunded_cents` updated.
        """
        ...


class PayoutStatus(BaseStrEnum):
    """Where a payout stands right after the provider accepted it.

    A refusal is not a status here: it is
    :class:`~tempest_fastapi_sdk.exceptions.PayoutRejectedException`, raised,
    because the caller has to act on it (give the money back).

    Attributes:
        CONFIRMED: The provider reports the transfer done.
        PENDING: Accepted, not settled yet. Settlement or failure arrives
            later (for OpenPix, the ``MOVEMENT_*`` webhooks).
    """

    CONFIRMED = "confirmed"
    PENDING = "pending"


class PayoutRequest(BaseSchema):
    """A transfer from the platform account to someone's Pix key.

    Attributes:
        amount_cents (int): The amount, in cents.
        pix_key (str): The destination key, read from the payee's profile
            and never from the request that asked for the payout.
        pix_key_type (PixKeyType): The kind of key — the SDK's
            :class:`~tempest_fastapi_sdk.PixKeyType`, which
            :func:`~tempest_fastapi_sdk.detect_pix_key_type` can infer from
            the key itself.
        correlation_id (str): The service's id for this payout. Providers
            use it to make a retried request idempotent.
        comment (str | None): Text that travels with the transfer.
    """

    amount_cents: int = Field(gt=0, description="Valor do saque em centavos.")
    pix_key: str = Field(min_length=1, description="Chave Pix de destino.")
    pix_key_type: PixKeyType = Field(description="Tipo da chave Pix.")
    correlation_id: str = Field(
        min_length=1, description="Identificador do saque no lado do serviço."
    )
    comment: str | None = Field(default=None, description="Texto do saque.")


class PayoutResult(_EnumSafeSchema):
    """What the provider answered when it accepted a payout.

    Attributes:
        correlation_id (str): Echo of :attr:`PayoutRequest.correlation_id`.
        status (PayoutStatus): Confirmed or still pending.
        provider (str): Which provider handled it.
        provider_status (str | None): The provider's own status string,
            kept so a value the mapping did not anticipate is still visible.
    """

    correlation_id: str
    status: PayoutStatus
    provider: str
    provider_status: str | None = None


class PayoutProvider(Protocol):
    """The contract a provider that can send Pix out of the platform implements.

    Error contract, which the wallet's withdrawal depends on:

    * raise :class:`~tempest_fastapi_sdk.exceptions.PayoutRejectedException`
      **only** when the provider definitively refused — the money did not
      leave, so the debit is given back;
    * let anything else propagate (timeout, connection error, unreadable
      answer): the outcome is unknown and the debit must be kept.

    Attributes:
        provider_name (str): Copied into :attr:`PayoutResult.provider`.
    """

    provider_name: str

    async def transfer_to_pix_key(self, request: PayoutRequest, /) -> PayoutResult:
        """Send ``request.amount_cents`` to ``request.pix_key``.

        Args:
            request (PayoutRequest): The transfer.

        Returns:
            PayoutResult: The accepted transfer.
        """
        ...


__all__: list[str] = [
    "CardCharge",
    "CardChargeRequest",
    "CardProvider",
    "PaymentStatus",
    "PayoutProvider",
    "PayoutRequest",
    "PayoutResult",
    "PayoutStatus",
    "PixCharge",
    "PixChargeRequest",
    "PixEventType",
    "PixPayer",
    "PixPaymentEvent",
    "PixProvider",
]
