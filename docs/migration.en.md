# Migration guide

Breaking-change walkthroughs grouped by minor release. Stick to the version that matches what you're upgrading **from**. The release sections are listed newest-first, so on a multi-version jump read and apply them bottom-up.

## 0.270.0 — `PixCharge.raw` uses the wire's spelling

No signature changes. What changes is **the keys of a dictionary** the
canonical contract hands you.

### What changes

`PixCharge.raw` always carried the provider's payload, but with a
different spelling depending on the path. Measured on v0.269.0, same body,
same two paths:

```text
API      raw['paymentLinkUrl']   -> None
API      raw['payment_link_url'] -> https://openpix.com.br/pay/pl_1
webhook  raw['paymentLinkUrl']   -> https://openpix.com.br/pay/pl_1
```

The API path dumped without `by_alias`, so a **declared** field came out in
`snake_case` while an **undeclared** one — which `extra="allow"` has kept
since v0.259/v0.260 — held on to the wire name. The API path's `raw` was
literally a mixture of the two spellings.

From v0.270.0 both paths use `by_alias=True`: **the spelling is the
provider's, always**.

### What to do

If your service reads `raw` for a field the specification **declares**,
switch to the wire name:

```python
from tempest_fastapi_sdk.integrations.payment import PixCharge


def payment_link(charge: PixCharge) -> object | None:
    """Read OpenPix's payment link out of the raw payload.

    Args:
        charge (PixCharge): The charge, in canonical shape.

    Returns:
        object | None: The link, when the provider sent one.
    """
    return charge.raw.get("paymentLinkUrl")
```

Anyone already reading an **undeclared** field (`paidAt`, say) changes
nothing: those already arrived in the wire's spelling.

!!! tip "Finding the call sites"
    ```bash
    grep -rn 'raw\[\|raw\.get(' <your-service>/
    ```

    Every `raw` lookup with a `snake_case` key is a candidate. The
    replacement is the name the provider's documentation uses.

!!! note "Why the wire's spelling and not Python's"
    `raw` exists for what the contract does **not** model. A consumer
    discovers that field by reading the provider's documentation, and
    there it is called `paymentLinkUrl`. A spelling that only exists after
    the SDK renames it is one nobody can predict.

## 0.270.0 — `PaymentStatus` gained `UNKNOWN`

A new enum member. **Additive**: nothing that existed changed value.

### What changes

A state the SDK does not classify now arrives as
`PaymentStatus.UNKNOWN`, with the provider's string preserved in
`provider_status`. Before, OpenPix's two paths answered opposite things
and neither was true:

```text
API      status 'CANCELLED' -> ValidationError -> 500 from your service
webhook  status 'CANCELLED' -> status=pending
```

### What to do

If you branch exhaustively on `PaymentStatus` — a `match` with no
`case _`, a dictionary indexed by member — add the case:

```python
from tempest_fastapi_sdk.integrations.payment import PaymentStatus, PixCharge


def describe(charge: PixCharge) -> str:
    """Describe a charge's state for a support screen.

    Args:
        charge (PixCharge): The charge, in canonical shape.

    Returns:
        str: A line a human can act on.
    """
    if charge.status is PaymentStatus.UNKNOWN:
        return f"unclassified state: {charge.provider_status}"
    return charge.status.value
```

If you branch only on `PAID`/`PENDING` with a default arm, nothing to do —
the new behaviour is strictly better: a charge that used to show up as
`PENDING` without being one, or that failed the request outright, now
identifies itself.

## 0.269.0 — `Charge.expires_in` became an `int`

No signature changes. What changes is **the type of one OpenPix response
field**.

### What changes

`Charge.expires_in` was `str | None` and is now `int | None`. Woovi's
document declares `expiresIn` as `string`, the API returns `3600`, and
validation blew up before any consumer saw the charge:

```text
charge.expiresIn
  Input should be a valid string [type=string_type, input_value=3600, input_type=int]
```

### What to do

**Nothing at runtime.** Since no charge response was ever constructed, the
old type never handed anyone a value — there is no code out there that read
a `str` from this field and worked.

**If you annotated the field**, change the type:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import Charge


def seconds_left(charge: Charge) -> int:
    """How many seconds are left before the charge expires."""
    return charge.expires_in or 0
```

!!! note "Why not `int | str`"
    A union would push the ambiguity onto every consumer, who would then
    need a defensive `int(charge.expires_in)` without ever knowing whether
    text arrives someday. The document contradicts itself about this field
    in three places (`Charge` says `string`, `ChargePayload` says `number`,
    `WebhookCharge` says `integer`) — the evidence is in
    `vendor/openpix-evidence.md`, section 7.

## 0.265.0 — an optional array in a request body became `| None`

No method signature changes. What changes is **the type of some fields** on
the generated body models, and **what goes on the wire**.

### What changes

An array the specification lists as optional, on a model the SDK only
**sends**, is no longer `list[T]` with `default_factory=list`; it is
`list[T] | None` with `default=None`. That is 21 fields: 9 on OpenPix, 12 on
Mercado Pago.

Alongside it, the generated `_dump` now passes `exclude_unset=True`, so **no**
field you did not touch reaches the body — including on the shared models,
which kept the list spelling.

**Response** models are untouched: `Charge.additional_info` is still `[]` when
the provider sends nothing, which is the SDK's convention.

### What to do

**Nothing, if you only build the payload and send it.** That case is exactly
what the fix repairs:

```text
before: {"correlationID": "...", "value": 1190, "additionalInfo": [], "splits": []}  → 400
now:    {"correlationID": "...", "value": 1190}                                      → 200
```

**If you read the field back off a payload you built**, it is now `None`
instead of `[]`. Before, `for split in payload.splits:` iterated an empty
list and did nothing; now it raises `TypeError`. The form that works on both
versions:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import ChargePayload

payload: ChargePayload = ChargePayload(correlation_id="abc", value=1190)

for split in payload.splits or []:
    print(split.pix_key)
```

!!! tip "Sending an empty list is still possible — and still a claim"
    `ChargePayload(..., splits=[])` **does** send `"splits": []`, and Woovi
    answers `400 O array de split precisa ter ao menos um item`. That is
    correct: you asserted something and the provider judged it. What changed
    is that the SDK stopped asserting it for you.

## 0.264.0 — the language belongs to the flow, not to each end

Breaks no signature. It changes **the language some pages come out in** —
to the correct one, but observably.

### What changes

Through v0.263.0 the email read `AUTH_DEFAULT_LOCALE` and the page
negotiated `Accept-Language`, each on its own. Both now call
`resolve_locale`, in the same order:

```text
1. ?lang= on the link (the language THIS email went out in)
2. user.locale        (the preference you stored on the row)
3. Accept-Language    (pages only)
4. AUTH_DEFAULT_LOCALE
```

Two observable consequences:

1. **A user with a stored `locale` on a differently-configured browser now
   sees the page in their language.** The header used to win.
2. **The emailed link gains `?lang=<locale>`.** The previous query string is
   preserved byte for byte — the token is never re-encoded — but the URL
   carries one more parameter.

### What to do

**Nothing, if you store no user language and don't validate the link's
query.** With no signal at all the behaviour is what it was:
`Accept-Language`, then `AUTH_DEFAULT_LOCALE`.

**If your front-end route rejects unknown query parameters**, turn the stamp
off:

```env
AUTH_STAMP_LOCALE_IN_LINK=false
```

**If you want the user's preference to win**, store it — the SDK reads the
attribute with `getattr`, so it just has to exist:

```python
from tempest_fastapi_sdk import BaseUserModel, LocaleColumnMixin


class UserModel(LocaleColumnMixin, BaseUserModel):
    """The BCP-47 `locale` column (nullable) comes from the mixin."""

    __tablename__ = "users"
```

!!! note "A locale the SDK doesn't ship is not an answer"
    The SDK ships `pt-BR` and `en-US`. A row saying `locale="fr-FR"` does
    **not** stop the search: it falls through to the next signal instead of
    rendering in a language that does not exist.

## 0.263.0 — `AdminAuthBackend` became generic

Breaks no runtime at all. It breaks **the type-check** for anyone who
subclasses `AdminAuthBackend` and runs `mypy --strict` — which the
`pyproject.toml` written by `tempest new` turns on by default.

### What changes

`AdminAuthBackend` is now `Generic[PrincipalT]`, parameterized by the
*principal* — the value `authenticate` returns and that `load_principal`,
`principal_id`, `display_name`, `mfa_enabled` and `verify_mfa` consume.
Before, all six passed that value around as `Any`.

Subclassing without a parameter is still **valid at runtime** and resolves to
`AdminAuthBackend[Any]`, the behaviour through v0.262.0. What changes is that
`mypy --strict` enables `disallow_any_generics`, and then:

```text
src/admin/backend.py:12: error: Missing type arguments for generic type
"AdminAuthBackend"  [type-arg]
```

Measured: basedpyright in `standard` mode does not complain; mypy strict does.

### What to do

**1. Name your principal in the parameter.** This is the option that buys you
something — it was `class LdapAuthBackend(AdminAuthBackend)` with
`principal: Any` on every method; it becomes:

```python
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AdminAuthBackend, AdminAuthError


@dataclass
class LdapEntry:
    """The principal your directory hands back."""

    dn: str
    common_name: str


DIRECTORY: dict[str, LdapEntry] = {
    "admin@example.com": LdapEntry(dn="cn=admin", common_name="Admin"),
}


class LdapAuthBackend(AdminAuthBackend[LdapEntry]):
    """Authenticate against the directory instead of the user table."""

    async def authenticate(
        self,
        session: AsyncSession,
        *,
        identifier: str,
        password: str,
    ) -> LdapEntry:
        """Return the directory entry, or reject the login."""
        entry = DIRECTORY.get(identifier)
        if entry is None:
            raise AdminAuthError("Invalid credentials")
        return entry

    async def load_principal(
        self,
        session: AsyncSession,
        principal_id: str,
    ) -> LdapEntry | None:
        """Reload the entry on every request."""
        for entry in DIRECTORY.values():
            if entry.dn == principal_id:
                return entry
        return None

    def principal_id(self, principal: LdapEntry) -> str:
        """Return what gets serialized into the cookie."""
        return principal.dn

    def display_name(self, principal: LdapEntry) -> str:
        """Return what the admin header shows."""
        return principal.common_name
```

With the parameter written down, a `display_name` reading a different type
than `authenticate` returned becomes an override error — in mypy (which names
Liskov outright) and in basedpyright. That was exactly the defect `Any` let
through.

**2. Or preserve the previous behaviour, literally.** `AdminAuthBackend[Any]`
is what the unparameterized class already resolved to:

```python
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AdminAuthBackend, AdminAuthError


class LegacyAuthBackend(AdminAuthBackend[Any]):
    """Exactly the behaviour through v0.262.0."""

    async def authenticate(
        self,
        session: AsyncSession,
        *,
        identifier: str,
        password: str,
    ) -> Any:
        """Return whatever principal your IdP hands back."""
        raise AdminAuthError("Invalid credentials")

    async def load_principal(
        self,
        session: AsyncSession,
        principal_id: str,
    ) -> Any | None:
        """Reload the principal, or invalidate the session."""
        return None

    def principal_id(self, principal: Any) -> str:
        """Return the cookie identifier."""
        return str(principal)
```

No difference from what you had; it only silences `disallow_any_generics`.

!!! note "`UserModelAuthBackend` asks for nothing"

    The implementation the SDK ships already declares
    `AdminAuthBackend[BaseUserModel]`. If you use the default backend —
    `auth_backend=UserModelAuthBackend(user_model=UserModel)` — nothing
    changes for you.

!!! tip "The Redis protocols only got easier to satisfy"

    The same release made every required parameter of `ThrottleBackend`,
    `_RedisHashClient` and `RedisLike` positional-only, and swapped
    `Awaitable[Any]` for the concrete type. That **widens** what is accepted:
    a backend of yours that already passed still passes, and
    `redis.asyncio.Redis`/`fakeredis` — which basedpyright rejected — pass
    now. The only way to feel it is to have a call site that declared the
    wrong return type on the back of `Any`; there, the type-checker will
    point at the line.

## 0.261.0 — the WebSocket router sends a `hello` first

Breaks a client that reads the **first** frame positionally. A client that
dispatches on `type` and ignores what it does not know is unaffected.

### What changes

The first frame `make_websocket_router` sends is now:

```json
{"type": "hello", "data": {"heartbeat_seconds": 30}, "request_id": null}
```

Before, the server's first frame was the `ping`, or whatever your handler
sent.

### What to do

**1. If your client reads the first frame positionally, consume the `hello`.**

```javascript
// up to v0.260.0
socket.onmessage = (event) => {
  const first = JSON.parse(event.data);   // was a ping, or application data
};

// from v0.261.0 — dispatch on type, which is stable
socket.onmessage = (event) => {
  const frame = JSON.parse(event.data);
  if (frame.type === "hello") {
    calibrateWatchdog(frame.data.heartbeat_seconds);
    return;
  }
  if (frame.type === "ping") {
    socket.send(JSON.stringify({ type: "pong", data: {} }));
    return;
  }
  handle(frame);
};
```

**2. Use the number.** A browser socket only reports a connection that closes
cleanly; a link that dies in flight leaves `readyState` at `OPEN` with nothing
ever arriving again. The client's silence watchdog is not optional, and
calibrating it from the `hello` means retuning `WS_HEARTBEAT_SECONDS` on the
server no longer turns every client's normal interval into a perceived drop.

### What you get for free

- **Any inbound frame counts as proof of life.** Only `pong` used to, so a
  busy client answering something else could cross the deadline and be closed
  with `4408`. The `pong` is still consumed before the handler.
- **`heartbeat` is public**, usable on an endpoint with no auth and no hub.
  Recipe in `docs/recipes/websocket.md`, section "Heartbeat without the
  router".
- **`CompactPaginationSchema`/`CompactPaginationFilterSchema`**, for services
  publishing `size` rather than `page_size`. Nothing moves if you do not use
  them.

## 0.260.0 — the OpenPix spec was refreshed, and every method was renamed

Breaks **every** `OpenPixClient` caller: all 125 methods changed name. Also
breaks anyone importing an operation-derived schema class, reading
`OpenPixEnvironment.PRODUCTION`, or relying on `extra="allow"` on a model that
is also a payload.

### Why

The vendored document was two versions behind the published one — `3.0.3`
titled "OpenPix" against the `3.1.0` "Woovi" — and nothing in the repository
measured that. The new document carries an `operationId` on **125 of 125**
operations where the old one had none: the generator used to derive names from
the path, and now uses the name the provider gave.

This is upside wearing a break's clothes. `post_api_v1_charge` was the name you
get when there is no name; `create_charge` is the operation's name.

### What to do

**1. Rename the calls.** Each row is a direct substitution. Of the 103, only
three changed signature:

| Method | Change |
| --- | --- |
| `update_customer` | the path parameter went from `correlation_id` to `id` — positional callers are fine, keyword callers are not |
| `get_statement` | gained `company_bank_account`, optional |
| `get_transaction` | gained `company_bank_account`, optional |

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ChargePayload,
    OpenPixClient,
)


async def charge(client: OpenPixClient, payload: ChargePayload) -> None:
    """Create one charge with the renamed method."""
    # up to v0.259.0: await client.post_api_v1_charge(body=payload)
    await client.create_charge(body=payload)
```

| Before (v0.259.0) | Now (v0.260.0) |
| --- | --- |
| `get_api_image_qrcode_base64_by_id` | `get_charge_qr_code_base64` |
| `post_api_v1_account` | `duplicate_account` |
| `delete_api_v1_account_register_by_id` | `delete_account_register` |
| `get_api_v1_account` | `list_accounts` |
| `delete_api_v1_account_by_account_id` | `close_account` |
| `get_api_v1_account_by_account_id` | `get_account` |
| `post_api_v1_account_by_account_id_withdraw` | `withdraw_from_account` |
| `delete_api_v1_application` | `delete_application` |
| `post_api_v1_application` | `create_application` |
| `post_api_v1_boleto_validate` | `validate_boleto` |
| `post_api_v1_cashback_fidelity` | `create_cashback_fidelity` |
| `get_api_v1_cashback_fidelity_balance_by_tax_id` | `get_cashback_fidelity_balance` |
| `get_api_v1_charge` | `list_charges` |
| `post_api_v1_charge` | `create_charge` |
| `delete_api_v1_charge_by_id` | `delete_charge` |
| `get_api_v1_charge_by_id` | `get_charge` |
| `patch_api_v1_charge_by_id` | `update_charge` |
| `get_api_v1_charge_by_id_refund` | `list_charge_refunds` |
| `post_api_v1_charge_by_id_refund` | `refund_charge` |
| `get_api_v1_company` | `get_company` |
| `get_api_v1_customer` | `list_customers` |
| `post_api_v1_customer` | `create_customer` |
| `patch_api_v1_customer_by_correlation_id` | `update_customer` |
| `get_api_v1_customer_by_id` | `get_customer` |
| `post_api_v1_decode_emv` | `decode_emv` |
| `get_api_v1_dispute` | `list_disputes` |
| `get_api_v1_dispute_by_id` | `get_dispute` |
| `post_api_v1_funds_recovery` | `create_funds_recovery` |
| `get_api_v1_funds_recovery_by_id` | `get_funds_recovery` |
| `post_api_v1_funds_recovery_by_id_cancel` | `cancel_funds_recovery` |
| `get_api_v1_installments_by_id` | `get_installment` |
| `post_api_v1_installments_by_id_cobr` | `create_installment_cobr` |
| `post_api_v1_installments_by_id_cobr_retry` | `retry_installment_cobr` |
| `get_api_v1_invoice` | `list_invoices` |
| `post_api_v1_invoice` | `create_invoice` |
| `get_api_v1_invoice_integration` | `get_invoice_integration` |
| `patch_api_v1_invoice_integration` | `set_invoice_integration_status` |
| `post_api_v1_invoice_integration` | `upsert_invoice_integration` |
| `put_api_v1_invoice_integration` | `update_invoice_integration_tax_fields` |
| `post_api_v1_invoice_integration_certificate` | `upload_invoice_integration_certificate` |
| `post_api_v1_invoice_integration_test` | `test_invoice_integration` |
| `post_api_v1_invoice_by_correlation_id_cancel` | `cancel_invoice` |
| `get_api_v1_invoice_by_correlation_id_pdf` | `get_invoice_pdf` |
| `get_api_v1_invoice_by_correlation_id_xml` | `get_invoice_xml` |
| `post_api_v1_kyc_onboarding` | `create_kyc_onboarding` |
| `get_api_v1_limits_by_account_id` | `get_account_limits` |
| `get_api_v1_partner_affiliate` | `list_partner_affiliates` |
| `post_api_v1_partner_application` | `create_partner_application` |
| `get_api_v1_partner_company` | `list_partner_companies` |
| `post_api_v1_partner_company` | `create_partner_company` |
| `get_api_v1_partner_company_by_tax_id` | `get_partner_company` |
| `get_api_v1_payment` | `list_payments` |
| `post_api_v1_payment` | `create_payment` |
| `post_api_v1_payment_approve` | `approve_payment` |
| `get_api_v1_payment_by_id` | `get_payment` |
| `get_api_v1_pix_keys` | `list_pix_keys` |
| `post_api_v1_pix_keys` | `create_pix_key` |
| `post_api_v1_pix_keys_check` | `check_pix_key` |
| `get_api_v1_pix_keys_tokens` | `list_pix_key_tokens` |
| `get_api_v1_pix_keys_tokens_logs` | `list_pix_key_token_logs` |
| `delete_api_v1_pix_keys_by_pix_key` | `delete_pix_key` |
| `get_api_v1_pix_keys_by_pix_key_check` | `check_pix_key_by_key` |
| `put_api_v1_pix_keys_by_pix_key_default` | `set_default_pix_key` |
| `get_api_v1_psp` | `list_psps` |
| `get_api_v1_qrcode_static` | `list_static_qr_codes` |
| `post_api_v1_qrcode_static` | `create_static_qr_code` |
| `delete_api_v1_qrcode_static_by_id` | `delete_static_qr_code` |
| `get_api_v1_qrcode_static_by_id` | `get_static_qr_code` |
| `get_api_v1_receipt_by_receipt_type_by_end_to_end_id` | `get_receipt` |
| `get_api_v1_refund` | `list_refunds` |
| `post_api_v1_refund` | `create_refund` |
| `get_api_v1_refund_by_id` | `get_refund` |
| `post_api_v1_stablecoin_deposit` | `create_stablecoin_deposit` |
| `post_api_v1_stablecoin_deposit_approve` | `approve_stablecoin_deposit` |
| `get_api_v1_stablecoin_quote` | `get_stablecoin_quote` |
| `get_api_v1_stablecoin_subaccount` | `list_stablecoin_subaccounts` |
| `post_api_v1_stablecoin_subaccount` | `create_stablecoin_subaccount` |
| `get_api_v1_stablecoin_subaccount_by_sub_account_id` | `get_stablecoin_subaccount` |
| `get_api_v1_statement` | `get_statement` |
| `get_api_v1_subaccount` | `list_subaccounts` |
| `post_api_v1_subaccount` | `create_subaccount` |
| `post_api_v1_subaccount_transfer` | `transfer_between_subaccounts` |
| `delete_api_v1_subaccount_by_id` | `delete_subaccount` |
| `get_api_v1_subaccount_by_id` | `get_subaccount` |
| `post_api_v1_subaccount_by_id_credit` | `credit_subaccount` |
| `post_api_v1_subaccount_by_id_debit` | `debit_subaccount` |
| `get_api_v1_subaccount_by_id_statement` | `get_subaccount_statement` |
| `post_api_v1_subaccount_by_id_withdraw` | `withdraw_from_subaccount` |
| `get_api_v1_subscriptions` | `list_subscriptions` |
| `post_api_v1_subscriptions` | `create_subscription` |
| `get_api_v1_subscriptions_by_id` | `get_subscription` |
| `put_api_v1_subscriptions_by_id_cancel` | `cancel_subscription` |
| `get_api_v1_subscriptions_by_id_installments` | `list_subscription_installments` |
| `put_api_v1_subscriptions_by_id_value` | `update_subscription_value` |
| `get_api_v1_transaction` | `list_transactions` |
| `get_api_v1_transaction_by_id` | `get_transaction` |
| `post_api_v1_transfer` | `create_transfer` |
| `get_api_v1_webhook` | `list_webhooks` |
| `post_api_v1_webhook` | `create_webhook` |
| `get_api_v1_webhook_events` | `list_webhook_events` |
| `get_api_v1_webhook_ips` | `list_webhook_ips` |
| `delete_api_v1_webhook_by_id` | `delete_webhook` |
| `get_openpix_charge_brcode_image_id_png` | `get_charge_qr_code_image` |

**2. Three methods have no direct replacement.**

| Before | Now | What was wrong |
| --- | --- | --- |
| `post_api_v1_dispute_id_evidence(body=...)` | `upload_dispute_evidence(id, *, body=...)` | The path was `/api/v1/dispute/:id/evidence`, colon and all, and no argument named the dispute |
| `get_api_v1_account_register()` | `get_account_register(id)` | The docstring said "by CorrelationID" and the method took nothing |
| `delete_api_v1_payment_by_id(id)` | *removed* | The endpoint does not exist: the published document has only `get` on that path, and the DELETE Woovi documents is on `/api/v1/charge/{id}` |

**3. Operation-derived schema classes were renamed.** The `components` ones
were **not** — `Charge`, `ChargePayload`, `ChargeStatus`, `Transaction`,
`Customer` are unchanged. The inline ones moved:

The name up to v0.259.0 was `GetApiV1ChargeResponsePageInfo`. From v0.260.0:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ListChargesResponsePageInfo,
)
```

The pattern follows the method: `GetApiV1ChargeResponse…` becomes
`ListChargesResponse…`, `PostApiV1PaymentBody…` becomes `CreatePaymentBody…`.

**4. `OpenPixEnvironment.PRODUCTION` is now `https://api.woovi.com`.** That is
`servers[0]` of the refreshed document. The old host is still up — measured
2026-08-28, `GET /api/v1/charge` answers `401` on `api.openpix.com.br`,
`api.woovi.com` and `api.woovi-sandbox.com` alike — so a service pinned to the
old URL keeps working. If you assert the value in a test, update it.

**5. If you read `model_extra` from a model that is also a payload.** A class
reachable from a response **and** from a request body goes back to
`extra="ignore"`. That is **4 classes on OpenPix** — `PreRegistrationPayloadObject`,
which is the body and the `200` of the same operation, plus the three it
reaches — and **25 on Mercado Pago**. The reason is what v0.259.0 already
stated and could not enforce: an unexpected key on a payload is the caller's
own typo, and carrying it to the provider is worse than dropping it.

### What you get for free

- 24 new operations: `anticipation` (7), `stablecoin` payout and wallets (7),
  `boleto-transaction` (2), `kyc-validation` (2), `files`, `webhook/public-keys`.
- `Transaction.webhook_sent[].status` becomes an `int` — it is an HTTP code.
- `to_cents` and `reais_to_cents` refuse with `ValueError`, always. Before,
  `"abc"` raised `decimal.InvalidOperation`, `None` raised `TypeError` and
  `float("inf")` raised `OverflowError`.

## 0.259.0 — OpenPix money becomes `int`

Breaks anyone who annotated a variable with the generated model's type, or who
compares a dump against an expected JSON string.

### What changes

154 fields of the `integrations/payment/openpix` package stop being `float`:

- **monetary values** — `Charge.value`, `ChargePayload.value`,
  `ChargeRefundPayload.value`, `Transaction.value`, `SubAccount.balance`, every
  `pix*Limit`, and the rest of the document's 50 `value` fields;
- **counts and day offsets** — `skip` and `limit`, `installmentsCount`,
  `dayDue`, `daysForDueDate`, `expiresIn`. The pagination pair appeared 27
  times as a **response field** (`pageInfo`, `Pagination`) and on 6
  operations as a query parameter.

Woovi settles in whole centavos — the specification says so in the field's own
description on 58 numeric schemas — and typed all of it `number`. The visible effect is on the
wire:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import ChargePayload

ChargePayload(correlation_id="abc-1", value=1000).model_dump(
    by_alias=True, mode="json", exclude_none=True
)
# up to v0.258.0: {"correlationID": "abc-1", "value": 1000.0, ...}
# from v0.259.0:  {"correlationID": "abc-1", "value": 1000, ...}
```

**19 fields stay `float`**: `basePrice` (an exchange rate),
`inputAmount`/`outputAmount` on the stablecoin quote (the first is documented
as *"currency unit, not cents"*), `rate`, the rate-limit token bucket,
`annualRevenue` and `Installment.expiration` — the last two with no unit stated
in the document, which is why they were left alone.

!!! warning "Corrected in v0.260.0"
    This section originally said "18 fields", and that they were "the only ones
    where the fraction is real". There were 19, and one of them was
    `Transaction.webhookSent[].status`, described in the document itself as
    *"HTTP response status code of the webhook delivery attempt"* — an HTTP
    status code is never fractional. It became an `int` in v0.260.0.

### What to do

Nothing, in most cases — an `int` satisfies at runtime wherever a `float` was
expected. Check three things:

- **Your own annotations.** `amount: float = charge.value` is now a type error.
  Change it to `int`.
- **Dump comparisons in tests.** `assert dumped == {"value": 1000.0}` still
  passes (`1000 == 1000.0`), but a comparison against a JSON **string**, or a
  snapshot of `model_dump_json()`, does not.
- **`to_cents` over a generated model.** It still works and still validates
  (refusing negatives and fractions), it simply narrows nothing any more. For a
  raw payload — the dictionary out of the JSON, a webhook — it is still the
  right call.

### The other half: your fields stop disappearing

Generated response models now use `extra="allow"`. A field the provider returns
and the specification does not declare lands in `model_extra` instead of being
dropped during validation:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import Charge

charge = Charge.model_validate({"value": 1000, "wooviAddedThis": "later"})
(charge.model_extra or {})["wooviAddedThis"]   # "later"
```

**Payload** models are unchanged: they keep `extra="ignore"`, because there an
unexpected key is the caller's own typo.

Three fields that were in exactly that situation are now declared and typed:
`Charge.fee`, `Charge.discount` and `Charge.value_with_discount`. If you were
reading any of them through the raw `HTTPClient` because of this, the generated
method is now enough.

## 0.258.0 — log files now rotate

No signature breaks. What changes is what sits **on disk**: anyone reading
`logs/info.log` from outside the SDK now sees a window, not the whole history.

### What changes

The per-level handlers were `logging.FileHandler` — unbounded growth. They are
now `RotatingFileHandler` with `max_bytes=10_000_000` and `backup_count=5`, so
each level stops at ~60 MB (five rotated files plus the one being written), and
`info.log.1`, `info.log.2`, … appear next to the current file.

The reason already closed the reader side of this pair: the `/logs` router caps
reads at `DEFAULT_MAX_RECORDS_PER_FILE = 20_000` records per file, added after
a service whose log directory had grown to gigabytes answered with a dead
worker. On a service that logs one line per request, running on a long-lived
host, `info.log` is what fills the disk — and a full disk takes down the
service along with whatever else shares the partition.

### What to do

Nothing, in most cases. Check two things:

- **A collector that follows the file by name.** Filebeat, Promtail, Fluent Bit
  and friends handle rotation, but a hand-rolled `tail -F`, or a script that
  opens the file once at boot, misses the turnover. Point the pattern at
  `info.log*` if you need the rotated files.
- **`GET /logs` shows the current window.** The endpoint reads the exact names
  (`info.log`, `error.log`, …), so rotated files are not in the response.
  Longer retention is a collector's job, not the endpoint's.

### If you want the old behavior

`max_bytes=0` restores the plain `FileHandler` — for a host where `logrotate`
or a sidecar already owns retention:

```python
from tempest_fastapi_sdk import configure_logging

configure_logging(level="INFO", max_bytes=0)
```

Both knobs are keyword-only and independent: `backup_count` is only
read when rotation is on.

## 0.257.0 — the agent fakes rename the parameter to `tools`

Breaks callers passing `chat_with_tools(..., specs=[...])` by keyword.

### What changes

`ScriptedBackend` and `FailingBackend` exist to fake the `ChatBackend` /
`ToolCallingBackend` protocols — and satisfied neither under mypy. The protocol
names the parameter `tools` and accepts `**kwargs`; the fakes named it `specs`
and accepted nothing else. A protocol member is only implemented by a signature
with the **same parameter name**, so the opening line of the testing recipe was
a type error:

```python
from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies

agent = Agent(ScriptedBackend([replies("ok")]))
# up to v0.256.0:
# Argument 1 to "Agent" has incompatible type "ScriptedBackend";
# expected "ChatBackend | ToolCallingBackend"  [arg-type]
```

### What to do

Nothing, if you call positionally — which is what `Agent` does internally and
what the recipe shows. If your test calls the fake directly, by keyword:

```diff
-decision = await backend.chat_with_tools(messages, specs=specs)
+decision = await backend.chat_with_tools(messages, tools=specs)
```

The `specs_seen` attribute — where the fake records the tool names offered each
turn — did **not** change name.

### What started compiling

Three annotations that rejected the argument the docs themselves told you to
pass:

- `EventStream.response(on_disconnect=task.cancel)`, because `Task.cancel`
  returns `bool` and the annotation asked for `None`;
- `RedisIdempotencyStore(Redis.from_url(...))`, `RedisResponseCacheStore(...)`
  and `RedisWebAuthnChallengeStore(...)`, because the protocols demanded the
  parameter name `key`/`name` and a `Coroutine` return, while redis-py returns
  an `Awaitable`;
- `require_authenticated(identity)` with a `FirebaseIdentity`, because the
  `TypeVar` was bound to `BaseUserModel`.

None of them changes runtime — they only stop demanding a workaround
(`# type: ignore`, `cast`) from anyone running a type checker.

## 0.256.0 — the `RateLimitMiddleware` 429 becomes JSON

Breaks clients that read the 429 body as text.

### What changes

Up to v0.255.0 the middleware answered `text/plain` with the raw `error_message`:

```text
HTTP/1.1 429 Too Many Requests
content-type: text/plain; charset=utf-8

Too many requests
```

It now answers the same envelope `register_exception_handlers` writes in every handler:

```text
HTTP/1.1 429 Too Many Requests
content-type: application/json
retry-after: 60

{"detail": "Too many requests",
 "code": "TOO_MANY_REQUESTS",
 "details": {"retry_after_seconds": 60, "limit": 15}}
```

The reason is a contradiction inside the SDK itself: `error_responses()` always pointed 429 at `ErrorResponseSchema`, so a client generated from the OpenAPI schema broke deserializing the text -- and anyone adopting `register_exception_handlers` alongside the middleware ended up with two error shapes in one API.

### What to do

- **A client branching on `status === 429`:** nothing. The status and `Retry-After` are unchanged.
- **A client reading the body as text:** read JSON instead, using `detail` to display and `code` to branch.

```typescript
// before
const message = await response.text();

// after
const { detail, code } = await response.json();
```

- **A service that rewrote the response** (a middleware subclass turning the text into an envelope) can delete the workaround: `error_message` plus the new `error_code` cover it.

### If you need the text back

There is no flag for it. The old body was incompatible with the schema the route itself documents, and keeping both shapes would keep the defect behind an option. A service that genuinely needs another format can subclass the middleware and override the response, the way it did before.

## 0.252.0 — SQLite `:memory:` gets one connection per session

No API break. It changes the connection topology of a `:memory:` database, so read this before upgrading if you rely on the old behaviour.

### What changes

`AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")` now builds the engine over a **shared-cache** in-memory database (`file:<unique-name>?mode=memory&cache=shared&uri=true`) with a normal pool, instead of letting SQLAlchemy pick `StaticPool` with a single connection. The manager holds one connection open for its lifetime, because a shared-cache database dies with its last connection.

This fixes the error introduced in v0.200.0, when the explicit `BEGIN` started being emitted for every SQLite engine:

```text
sqlite3.OperationalError: cannot start a transaction within a transaction
[SQL: BEGIN]
```

Two overlapping sessions work again on `:memory:`, and `RELEASE SAVEPOINT` still is not a commit in disguise.

### If you rely on a single connection

Pass the pool explicitly — a pool the caller names is never overridden:

```python
from sqlalchemy.pool import StaticPool
from tempest_fastapi_sdk import AsyncDatabaseManager

db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
```

That restores the previous topology, including its failure on overlapping sessions.

### If you swapped `:memory:` for a temp file as a workaround

You can go back to `:memory:`. The workaround keeps working, so there is no rush.

## 0.251.0 — the Mercado Pago webhook signature now follows the provider's algorithm

Breaks anyone passing `manifest_template=` or unpacking the return of
`parse_signature_header`. If you only call `verify_signature`, **nothing in
your code changes** — it simply starts verifying deliveries it used to reject.

### The manifest omits absent pairs, so it is no longer a template

The previous implementation rendered
`"id:{data_id};request-id:{request_id};ts:{ts};"`. Mercado Pago's official
validator (`mercadopago/sdk-nodejs`, `src/utils/webhook/index.ts`, commit
`99857f33`) **omits** the pair whose value is absent. A delivery without
`data.id` signs `request-id:...;ts:...;`, while the fixed template signed
`id:;request-id:...;ts:...;` — a different hash, and verification that always
failed.

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import build_manifest

# before: DEFAULT_MANIFEST_TEMPLATE + str.format
# now: the rule, exported
build_manifest(data_id="", request_id="req-1", timestamp="1771891200")
# "request-id:req-1;ts:1771891200;"
```

`DEFAULT_MANIFEST_TEMPLATE` and the `manifest_template=` parameter were
**removed**: they existed because the algorithm was unknown, and a template
cannot express the omission rule. If you had measured a different manifest and
were passing your own, compare it against `build_manifest` and open an issue if
it still differs.

### `parse_signature_header` returns an object, not a tuple

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import parse_signature_header

# before
# timestamp, digest = parse_signature_header(header)

# now
parsed = parse_signature_header("ts=1771891200,v1=abc123")
parsed.timestamp        # "1771891200"
parsed.digest()         # "abc123" — the first supported version
parsed.hashes           # {"v1": "abc123"} — a header may carry v1 and v2
```

### What is new

- `versions=` on `verify_signature`, defaulting to `("v1",)`. A provider
  migration to `v2` becomes `versions=("v2", "v1")`, with no release to wait
  for.
- `tolerance_seconds=` (and `now=`, for tests), which is what makes the
  manifest's `ts` work against replay. Still opt-in, as upstream has it.
- Case-insensitive header keys, whitespace-only values treated as absent, and a
  non-numeric `ts` rejected as a malformed header — three upstream rules that
  were missing.

## 0.234.0 — a generated model is built with its Python names, and the type-checker agrees

Nothing changes at runtime. What changes is what pyright accepts.

### Construct with the field name, not the alias

Fields carrying a wire name moved from `Field(alias=...)` to
`Field(validation_alias=..., serialization_alias=...)`. Runtime is identical —
the models already set `populate_by_name=True`, so both spellings always
validated. What changes is the parameter a type-checker sees:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import ChargePayload

# before: it ran, but pyright reported "No parameter named correlation_id"
# now: both accept it
payload = ChargePayload(correlation_id="order-1", value=1990)
```

If your code used the alias purely to silence the checker
(`ChargePayload(correlationID="order-1")`), it **still runs** — validation takes
the alias — but the checker now objects. Switch to the Python name.

Reading and writing are unchanged: `model_validate({"correlationID": ...})`
still accepts the provider's spelling, and `model_dump(by_alias=True)` still
emits it.

## 0.233.0 — two generated OpenPix enums were renamed

One change, and it only breaks code importing the two payment enums by name.

### `PaymentType` and `PaymentDestinationAliasType` were renamed

The generator now emits the variants of `PaymentCreatePayload` (a `oneOf` with four shapes: Pix key, QR Code, Manual, Boleto), and those are what register the enums first. The name now comes from the variant:

| Before | Now |
| --- | --- |
| `PaymentType` | `PaymentCreatePayloadPixKeyType` |
| `PaymentDestinationAliasType` | `PaymentCreatePayloadPixKeyDestinationAliasType` |

Same members, same values — only the class name changed:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    PaymentCreatePayloadPixKeyType,
)

assert PaymentCreatePayloadPixKeyType.PIX_KEY.value == "PIX_KEY"
```

If you compared the value instead of importing the class (`payment.type == "PIX_KEY"`), there is nothing to do.

### What did **not** break

`PaymentCreatePayload` and `PostApiV1PaymentBody` are still importable: they became union aliases over the variants, so an annotation like `body: PostApiV1PaymentBody` stays valid. What changed is that they now carry the payment's fields — before they were models with **no properties at all**, and `extra="ignore"` silently dropped everything you passed.

## 0.229.0 — Ollama structured output moves from `/api/generate` to `/api/chat`

One change, and it only breaks **tests**, not runtime.

### `generate_structured` now talks to `/api/chat`

`OllamaGenerator.generate_structured` posted to `/api/generate` with the schema in the `format` field. That is broken on a reasoning model: against `gpt-oss:20b` the daemon answers `200 OK` with a non-zero `eval_count` and an **empty** `response`, because the reply lands in a channel that endpoint does not surface. On `/api/chat` the JSON arrives in `message.content`, and a non-reasoning model behaves identically on either.

There is nothing to adjust at runtime — the call that returned junk (or nothing) now returns the instance. What breaks is **a test whose mock is pinned to the old endpoint**:

```python
import httpx
from pydantic import BaseModel

from tempest_fastapi_sdk.genai import OllamaGenerator
from tempest_fastapi_sdk.utils import HTTPClient


class Person(BaseModel):
    name: str


async def before() -> Person:
    """A mock that matched /api/generate — it stops matching."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": '{"name": "Ana"}', "done": True})

    client = HTTPClient(transport=httpx.MockTransport(handler))
    gen = OllamaGenerator("llama3.2", http_client=client)
    return await gen.generate_structured("Any person.", Person)


async def after() -> Person:
    """The reply now comes in message.content."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": '{"name": "Ana"}'}, "done": True},
        )

    client = HTTPClient(transport=httpx.MockTransport(handler))
    gen = OllamaGenerator("llama3.2", http_client=client)
    return await gen.generate_structured("Any person.", Person)
```

Two behaviour changes come with it:

- **Empty content raises `ValueError`** instead of returning nothing. If you had a `try/except` treating an empty result as "the model said nothing", switch it to `except ValueError`.
- **`system=` is a new optional parameter.** Use it for the instruction when `prompt` is a long document: an instruction glued above the document is ignored — measured, 0 items extracted against 20 with the instruction in its own `system` turn.

## 0.174.0 — crashes become 422s, and `order_by` is validated

Robustness fixes. Each trades a crash for a correct answer; none requires a code change, but four change the status or the exception your service sees.

### A long password is now a 422

There is a ceiling: `AUTH_PASSWORD_MAX_BYTES`, default `72` — bcrypt's hard limit, counted in UTF-8 **bytes**. A password past it used to raise `ValueError` from `hashpw` and surface as a **500** on signup / reset / change. It is now a `ValidationException` (**422**).

If your frontend does not validate length, it starts receiving 422 where it received 500. If you swapped the hasher for one without the limit, raise the value.

### An invalid `order_by` is now a `ValidationException`

`BaseRepository.paginate` and `cursor_paginate` resolve `order_by` through the model's mapper. A name that is not a mapped column raises `ValidationException` (**422**) instead of `AttributeError` (**500**).

Contract change in `cursor_paginate`: it used to raise `ValueError` there. Code catching `ValueError` around it needs updating:

```python
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.exceptions import ValidationException

from src.db.models import UserModel

# In a service the session comes from `db.get_session_context()`; here, SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

repo = BaseRepository(session, model=UserModel)


async def main() -> None:
    """Run this example."""
    try:
        page = await repo.cursor_paginate(order_by="not_a_column")
    except ValidationException:
        ...


asyncio.run(main())
```

`ValueError` still signals a malformed cursor.

### `BodySizeLimitMiddleware`: a streaming oversize body answers 413

The 413 is now emitted the moment the count is exceeded, and whatever the app sends afterwards is dropped. It used to go out in a `finally`, after the app had answered — and FastAPI does answer, converting the guard's `ClientDisconnect` into a **400**. The second `http.response.start` made uvicorn raise `RuntimeError: Response already started`.

In practice: a streaming upload over the limit answers **413** where it recently answered **400** (with a `RuntimeError` in the log). A handler that never reads the body still answers whatever it answered before — a sent response cannot be retracted.

### `make_csrf_token_dependency` sets the cookie

It used to only return the token, so the cookie stayed absent and the following `POST` was rejected with a 403. It now sets it (`Secure` + `SameSite=Lax`, and not `HttpOnly` — the client must read it to echo the header).

If you were already setting the cookie by hand in the handler, the value is the same (`request.state.csrf_token`) and nothing changes: the dependency does not overwrite an existing cookie. On a plain-HTTP dev server pass `secure=False`, or the browser will not send it back.

### `OAuthUser.email_verified`

A new field (default `None`), so nothing breaks. But **read the note**: if you link a social login to an existing account by email, require `profile.email_verified is True`. On GitHub the value is always `None` — `GET /user` carries no verification field, and the email it returns is the public profile one, which GitHub does not require verifying.

### `GET /logs` reads at most 20,000 records per file

Tune it with `make_logs_router(max_records_per_file=...)`. They are the newest ones; the endpoint sorts newest-first and paginates, so what was left out was unreachable. A `WARNING` is logged when the cap bites.

## 0.173.0 — a token only works where it was meant to, and caches stop being shared

Three security fixes change default behavior. None requires a code change, but check whether you were relying on the old behavior.

### Refresh and MFA-pending tokens no longer authorize a route

`make_bearer_token_dependency`, `make_jwt_user_dependency`, `make_role_dependency`, `make_permission_dependency` and `UserAuthService.current_user_dependency()` now accept **only** `access`-type tokens.

Before, the three JWTs `UserAuthService` mints with one secret verified identically, so the refresh token and the step-one `mfa_token` worked as a bearer on any authenticated route — the second factor was bypassable with just the password.

You are affected if you **deliberately** sent a refresh token to a regular route:

```python
from tempest_fastapi_sdk import (
    JWTUtils,
    REFRESH_TOKEN_TYPE,
    make_bearer_token_dependency,
)

from src.core.settings import settings

tokens = JWTUtils(settings)


# Take that type again, on that one route:
require_refresh = make_bearer_token_dependency(tokens, accepted_typ=(REFRESH_TOKEN_TYPE,))
```

A token hand-signed with `JWTUtils.encode()` and carrying **no** `typ` is still accepted — the upgrade does not log live sessions out. Only the markers the SDK itself stamped (`refresh: True`, `purpose: "mfa_pending"`) are now rejected as access.

### `ResponseCacheMiddleware`: `private` by default, credentials skip the store

Two defaults changed:

- The emitted `Cache-Control` went from `public, max-age=N` to `private, max-age=N`. If you served genuinely shared content and relied on CDN caching, declare it again: `cache_control="public, max-age=N"`.
- A request with `Authorization` or `Cookie` neither reads nor writes the shared store (`ETag`/`304` still apply). To get caching back on an authenticated route, pass `cache_credentialed=True` — the credential joins the key, so each caller gets its own entry.

The `X-Cache` header now only appears when a `store=` is configured; it used to report `MISS` even in ETag-only mode.

### `IdempotencyMiddleware`: key scoped to the caller

The key went from `(method, path, key)` to `(caller, method, path, key)`, the caller being a digest of `Authorization`/`Cookie`. Reusing someone else's key no longer returns their response.

If your client swaps credentials between the original request and the retry (a token rotation mid-backoff), the retry no longer hits the earlier entry. Point identity at something stable there:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import IdempotencyMiddleware, MemoryIdempotencyStore

store = MemoryIdempotencyStore()

app = FastAPI()


app.add_middleware(
    IdempotencyMiddleware,
    store=store,
    principal_resolver=lambda request: request.headers.get("x-api-key-id", ""),
)
```

Also changed: `5xx` is no longer cached (`cache_server_errors=True` restores it), `Set-Cookie` is left out of the stored copy, and concurrent requests sharing a key are serialized within a process.


## 0.138.1 — `BaseAppSettings` must be the **last** base

0.138.1 made **every settings mixin inherit `BaseAppSettings`** (they used to extend raw `pydantic_settings.BaseSettings`). That fixes `.env` silently not loading when a mixin was listed before the base — the canonical `model_config` is now materialized onto every mixin regardless of ordering.

In exchange, base ordering stopped being style and became a **hard rule**: because the mixins subclass `BaseAppSettings`, Python's C3 linearization forbids the base from preceding its own subclass.

```python
# docs-guard: skip — the first two examples are the mistake this section describes
# ❌ fails at import time

from tempest_fastapi_sdk import BaseAppSettings, DatabaseSettings, RedisSettings


class Settings(DatabaseSettings, BaseAppSettings, RedisSettings): ...

# ❌ also fails
class Settings(BaseAppSettings, DatabaseSettings): ...

# ✅ BaseAppSettings last
class Settings(DatabaseSettings, RedisSettings, BaseAppSettings): ...
```

Before 0.159.1 the symptom was pydantic's raw `TypeError`, which never names the fix:

```text
TypeError: Cannot create a consistent method resolution order (MRO) for bases BaseAppSettings, RedisSettings
```

and `mypy` (with the pydantic plugin) reported twice on the same line, the second one misleading — it suggests a metaclass conflict when the cause is just the position of one base:

```text
settings.py:4: error: Cannot determine consistent method resolution order (MRO) for "Settings"  [misc]
settings.py:4: error: Metaclass conflict: the metaclass of a derived class must be a (non-strict) subclass of the metaclasses of all its bases  [metaclass]
```

As of 0.159.1, `BaseAppSettings` uses the [`AppSettingsMeta`](reference.md) metaclass, which pre-checks base ordering and swaps the message for an instruction:

```text
TypeError: Settings: BaseAppSettings must be the LAST base — RedisSettings already subclasses it, so listing BaseAppSettings before it is an invalid method resolution order (MRO). Move BaseAppSettings to the end of the base list: class Settings(RedisSettings, BaseAppSettings).
```

### Check

```bash
# look for a Settings whose BaseAppSettings is not the last base
grep -rn "class Settings(" -A 12 src/core/settings.py
```

- Move `BaseAppSettings` to the **last** position in the base list.
- Ordering **among the mixins** stays free — only the base's position matters.
- No env var, field or value change: this is purely inheritance order.

## 0.92.0 — `payload` column on the user token

0.92.0 adds the **email change / re-verification / recovery** flow. To carry the pending email until confirmation, `BaseUserTokenModel` gained a new column:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column


payload: Mapped[str | None] = mapped_column(String(320), nullable=True, default=None)
```

Since your `user_tokens` table inherits from `BaseUserTokenModel`, the column shows up on the model automatically — but the database needs a **migration**. It is additive and safe (nullable column, no required default):

```bash
# generate and apply
tempest db revision -m "add payload to user_tokens"
tempest db upgrade
```

Or by hand:

```sql
ALTER TABLE user_tokens ADD COLUMN payload VARCHAR(320) NULL;
```

!!! info "That's it"
    No renames, no default backfill. Existing flows (activation, password reset) keep writing `payload = NULL`. The new email flow is fully opt-in — recovery (`POST /auth/email-recovery/request`) is only mounted with `AUTH_EMAIL_RECOVERY_ENABLED=True`.

### Verify

- Run the migration before deploying 0.92.0 (the column must exist).
- If you hand-write `src/db/models/user_token.py` instead of using `make_user_token_model`, the column comes from the abstract base — no need to redeclare, just migrate.

## 0.63.0 — authenticated user loaded on the request session

Before 0.63.0, `UserAuthService.current_user_dependency()` loaded the authenticated user through `load_user`, which opened its **own** session (via `db.get_session_context()`) and closed it on exit. The `UserModel` handed to the route was therefore **detached**: mutating it and calling `commit`/`refresh` on the request session (the one your repositories use) raised
`InvalidRequestError: Instance is not persistent within this Session`.

From 0.63.0 the dependency loads the user on the **request session** (`db.session_dependency` by default) via `get_user(subject, session)`. The user is attached to the same session repositories use, so lazy-relationship reads and writes work without re-attaching anything.

!!! warning "Compatibility"
    The auth dependency and your repositories must share the **same** session callable for FastAPI's sub-dependency cache to deduplicate them. The recommended pattern is already covered:

    ```python
    # resources.py
    get_session = db.session_dependency          # one object, reused
    ```

    If you wrap the session in your own provider (`async def get_session(): ...`), pass it explicitly, otherwise the dependency opens a second session and the user is detached again:

    ```python
    get_current_user = auth.current_user_dependency(session_dependency=get_session)
    ```

!!! info "Extra safety net"
    `BaseRepository.resolve()` now re-attaches detached instances via `session.merge()`. Even if some flow still hands in a detached user, `resolve` brings it back into the active session instead of breaking — so services that worked around this (re-fetch by id before mutating) can drop the workaround.

### Verify

- Drop any "re-fetch by id before mutating the authenticated user" workaround — it's no longer needed.
- A single-argument `user_loader` passed to `make_jwt_user_dependency` keeps working. To share the request session, pass `session_dependency=` and use a two-argument loader `(subject, session)`.

## 0.8.0 — `ServerSettings` rename

0.8.0 renames every field on `ServerSettings`, extracts log fields to a new `LogSettings` mixin, and adds eleven other primitives. The renames are the only **breaking** changes — every new primitive is opt-in.

#### 1. Rename env vars

| Old | New | Mixin |
| --- | --- | --- |
| `HOST` | `SERVER_HOST` | `ServerSettings` |
| `PORT` | `SERVER_PORT` | `ServerSettings` |
| `DEBUG` | `SERVER_DEBUG` | `ServerSettings` |
| *(new)* | `SERVER_RELOAD` | `ServerSettings` |
| `LOG_LEVEL` | `LOG_LEVEL` | **moved to** `LogSettings` |
| `LOG_JSON` | `LOG_JSON` | **moved to** `LogSettings` |

Mechanical `sed` on every `.env` / `docker-compose.yml` / deployment manifest:

```bash
sed -i \
  -e 's/^HOST=/SERVER_HOST=/' \
  -e 's/^PORT=/SERVER_PORT=/' \
  -e 's/^DEBUG=/SERVER_DEBUG=/' \
  .env .env.example .env.test
```

`LOG_LEVEL` and `LOG_JSON` keep their names — only the mixin moves.

#### 2. Rename code references

```bash
# `settings.HOST` → `settings.SERVER_HOST`, same for PORT/DEBUG
grep -rn "settings\.\(HOST\|PORT\|DEBUG\)\b" src/ tests/
```

Replace each match with the `SERVER_*` form. If a service was using the
old `settings.DEBUG` flag for application-level debug behavior, switch
to `settings.SERVER_DEBUG`; if it was only being read for uvicorn
auto-reload, switch to `settings.SERVER_RELOAD`.

#### 3. Mix `LogSettings` into the project `Settings`

```diff
 from tempest_fastapi_sdk import (
     BaseAppSettings,
     CORSSettings,
     DatabaseSettings,
     JWTSettings,
+    LogSettings,
     RabbitMQSettings,
     RedisSettings,
     ServerSettings,
 )


 class Settings(
     ServerSettings,
+    LogSettings,
     DatabaseSettings,
     RedisSettings,
     RabbitMQSettings,
     JWTSettings,
     CORSSettings,
     BaseAppSettings,
 ):
     ...
```

Skip this step if the service never read `settings.LOG_LEVEL` /
`settings.LOG_JSON` — `configure_logging` accepts the values as
keyword arguments directly.

#### 4. (Optional) Adopt the new primitives

Pick what fits. None of these are required.

- Replace the hand-written `src/server.py` `uvicorn.run(...)` with
  [`run_server(...)`](recipes/http.md#programmatic-server-entry-point).
- Replace the hand-written `get_current_user` with
  [`make_jwt_user_dependency(tokens, load_user)`](recipes/http.md#jwt-bearer-current-user-role-dependencies).
- Move `SMTP_*` / `UPLOAD_*` / `TOKEN_SECRET` / `VAPID_*` /
  `TASKIQ_*` fields out of the project's `Settings` and onto the
  matching SDK mixin ([Settings mixins composition](recipes/http.md#settings-mixins-composition)).
- Adopt the
  [`Outbox`](recipes/outbox.md) if
  you already write side-effects from the same transaction as your
  domain rows.

#### 5. Verify

```bash
uv sync                      # picks up new pyproject deps
uv run pytest -q             # full suite
uv run ruff check src tests  # confirm no `HOST`/`PORT`/`DEBUG` references slipped
```

If `pytest` fails with a Pydantic `ValidationError` referencing
`HOST` / `PORT` / `DEBUG`, an env var was not renamed (look at the
process environment or `.env`).

---

