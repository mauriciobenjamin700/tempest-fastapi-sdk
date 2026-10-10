# Mercado Pago: test accounts and credentials

Before charging for real, you want to see a Pix being created, a card being
approved and another declined, a refund going back. All of that can be done
in Mercado Pago's sandbox without moving money. The hard part is getting
there: the dashboard has several credentials with similar names, and most
combinations **do not work** for charging.

This page is the path that worked, step by step, with the error each detour
produces. At the end you will have two things:

1. a **test seller account** (who receives);
2. its **Access Token**, from an application of the right type.

That is all. The payer can be any valid e-mail — measured, in step 5.

!!! info "Measured, not deduced"
    Every answer quoted here was observed against the sandbox between
    2026-10-09 and 2026-10-10, on the Orders API, with the credential and
    body described. The record is in `vendor/mercadopago-evidence.md`,
    section 9. Mercado Pago's dashboard changes from time to time: if a menu
    is not where this page says, search for the option's name.

## Why not use your own account

The first temptation is the `TEST-...` token shown under "Test credentials"
of **your** application. It reads data, but it does not charge:

| Credential | Payer | What the Orders API answers |
| --- | --- | --- |
| `TEST-` from your account | anyone, or none | `403 At least one policy returned UNAUTHORIZED.` |
| test seller's `APP_USR-`, Checkout Pro application | any | `401 Unauthorized use of live credentials` (measured on the Payments API) |
| test seller's `APP_USR-`, **Checkout Transparente / Orders API** application | any valid e-mail | **`201`, charge created** |

Only the last row charges. The rest of this page is how to get there.

!!! warning "Never use your real account's production credentials"
    The `APP_USR-...` of **your** account moves real money. Everything here
    uses the `APP_USR-...` of a **test** account, which has the same prefix
    but moves nothing. How to check is in step 6.

## Step 1 — create the test seller account

Log in with your normal account at
<https://www.mercadopago.com.br/developers/panel/test-users> and create an
account of type **Seller**, country **Brazil**. Write down what the dashboard
shows: **username**, **password** and **User ID**.

!!! tip "What about the buyer account?"
    Charging through the API does not need one: the order's payer can be any
    valid e-mail (step 5). Create one only to test a screen where someone
    logs into Mercado Pago to pay.

## Step 2 — log in as the seller

Open a **private window** (so it does not mix with your real account) and
log in at <https://www.mercadopago.com.br/> with the **seller's** username
and password.

If Mercado Pago asks for a verification code, use the **last 6 digits of the
test account's User ID**.

## Step 3 — create an application of the right type

Still logged in as the seller, open
<https://www.mercadopago.com.br/developers/panel/app> and click **Create
application**:

1. **Solution**: choose **Checkout Transparente**. Do not choose Checkout
   Pro: its token creates checkout preferences but refuses direct charges
   with `401 Unauthorized use of live credentials`.
2. **API type**: choose **Orders API**. The Payments API shows the warning
   "This API will be discontinued soon", and this SDK talks to Orders.
3. **Name**: anything.

## Step 4 — copy the seller's Access Token

In the new application, open **Production credentials** and copy the
**Access Token** (`APP_USR-...`).

It is the **production token of a test account**, and it is exactly the one
you want. Two paths that look right and are not:

- **"Test credentials"** inside the test account: the dashboard answers
  *"Não é possível utilizar credenciais de teste em um ambiente de teste"*
  (test credentials cannot be used in a test environment).
- **"Activate credentials"**: in a test account the option does not show,
  and it is not needed.

Keep the token **outside** any repository, readable only by you:

```bash
mkdir -p ~/.config/my-service
printf 'MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN=APP_USR-paste-here\n' \
  > ~/.config/my-service/mercadopago-sandbox.env
chmod 600 ~/.config/my-service/mercadopago-sandbox.env
```

Open the file in your editor and replace the value. Never paste the token
into a chat, a commit or a log.

## Step 5 — the payer's e-mail

With the right token, an order accepts **any valid e-mail** as the payer:
`buyer@example.com`, a Gmail, a made-up `test_user_…@testuser.com` — Pix and
card, all `201`. What it refuses is not having one:

| `payer` sent | Answer |
| --- | --- |
| absent, or `{}` | `400 '$.payer' - minimum 1 properties allowed, but found 0 properties` |
| only `first_name` | `400 '$.payer.email' or '$.payer.customer_id' or '$.payer.id'` |
| the `TESTUSER…` username instead of an e-mail | `400 '$.payer.email' - does not match pattern` |
| any valid e-mail | `201` |

!!! note "If you saw other payer errors"
    `500 payer_cannot_be_nil`, `400 excludes_by_rule` and
    `403 Payer email forbidden` were measured on the **Payments API**
    (`/v1/payments`), which requires an actual test buyer. If they show up,
    the code is calling the discontinued API.

## Step 6 — check it worked

The script below does two things. First, it checks the token belongs to a
**test** account — if not, it stops without charging anything. Then it
creates a R$ 19.90 Pix through the Orders API and cancels it right away.

```python
import asyncio
import os
import uuid
from typing import Any

from tempest_fastapi_sdk import HTTPClient

BASE_URL: str = "https://api.mercadopago.com"


async def main() -> None:
    """Check the token is a test account, then open and cancel a Pix order."""
    token: str = os.environ["MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN"]
    async with HTTPClient(
        base_url=BASE_URL,
        default_headers={"Authorization": f"Bearer {token}"},
    ) as http:
        me: dict[str, Any] = (await http.request("GET", "/users/me")).json()
        if "test_user" not in (me.get("tags") or []):
            raise SystemExit("This token is not a test account. Stopping.")

        created = await http.request(
            "POST",
            "/v1/orders",
            headers={"X-Idempotency-Key": str(uuid.uuid4())},
            json={
                "type": "online",
                "processing_mode": "automatic",
                "total_amount": "19.90",
                "external_reference": "sandbox-check-1",
                "payer": {"email": "buyer@example.com"},
                "transactions": {
                    "payments": [
                        {
                            "amount": "19.90",
                            "expiration_time": "PT30M",
                            "payment_method": {"id": "pix", "type": "bank_transfer"},
                        }
                    ]
                },
            },
        )
        order: dict[str, Any] = created.json()
        print(created.status_code, order.get("status"), order.get("status_detail"))

        cancelled = await http.request(
            "POST",
            f"/v1/orders/{order['id']}/cancel",
            headers={"X-Idempotency-Key": str(uuid.uuid4())},
        )
        print(cancelled.status_code, cancelled.json().get("status"))


asyncio.run(main())
```

Run it with the step 4 file loaded:

```bash
set -a; . ~/.config/my-service/mercadopago-sandbox.env; set +a
python check_sandbox.py
```

Expected output (measured on 2026-10-10):

```text
201 action_required waiting_transfer
200 canceled
```

`action_required` / `waiting_transfer` is a Pix waiting for payment. If you
saw that, you are ready. 🎉

## Test cards

To test cards, tokenize a test card and send the token in the order. The
cardholder name written on the card decides the outcome:

| Card | Expiry / CVV | Cardholder | Measured outcome |
| --- | --- | --- | --- |
| Visa `4235 6477 2802 5682` | `11/2030` / `123` | `APRO` | `201`, `processed` / `accredited` |
| Visa `4235 6477 2802 5682` | `11/2030` / `123` | `OTHE` | `402`, `rejected_by_issuer` |
| Mastercard `5474 9254 3267 0366` | `11/2030` / `123` | `APRO` | `201`, `processed` / `accredited` |
| Mastercard `5031 4332 1540 6351` | `11/2030` / `123` | `APRO` | `422 unprocessable_content` |

!!! note "A decline is HTTP 402, not a server error"
    A declined card comes back **402** with the reason in `errors` and the
    whole order in `data`. Treat it as an answer, not as a network failure:
    the order exists, it was declined, and the reason is right there.

!!! warning "Do not use the Mastercard `5031 4332 1540 6351`"
    It circulates in the documentation, but Mercado Pago does not recognise
    its BIN in Brazil: `GET /v1/payment_methods/search?bins=503143&site_id=MLB`
    comes back empty, and the order answers a generic `422`. The
    `5474 9254 3267 0366` is recognised as `master` and approves (measured on
    2026-10-10).

## A paid Pix and an expired Pix

In the sandbox nobody scans the QR. To see a **paid** Pix, send
`"first_name": "APRO"` in the payer, alongside the e-mail:

```json
"payer": {"email": "buyer@example.com", "first_name": "APRO"}
```

The order is born `action_required` / `waiting_transfer`, like any Pix, and
within 2 to 4 seconds moves to `processed` / `accredited`, with a test
`e2e_id`. With `first_name` `OTHE`, or without it, the Pix keeps waiting
(observed for 16 seconds). Once paid, it accepts partial and full refunds;
unpaid, a refund answers `409 cannot_refund_order`.

To see an **expired** Pix, create it with `"expiration_time": "PT60S"` (the
sandbox accepted 60 seconds) and read it after the deadline: the order comes
back `canceled` / `expired`, and the payment `expired` / `expired`.

## Common errors

| Message | Likely cause | What to do |
| --- | --- | --- |
| `403 At least one policy returned UNAUTHORIZED.` | your account's `TEST-` token | steps 1 to 4: use the test seller's token |
| `401 Unauthorized use of live credentials` | Checkout Pro application, or a token from another application | step 3: Checkout Transparente / Orders API application |
| `400 '$.payer' - minimum 1 properties allowed` | an order without a payer | step 5: send an e-mail |
| `400 '$.payer.email' - does not match pattern` | username (`TESTUSER...`) instead of the e-mail | step 5 |
| `422 unprocessable_content` | Mastercard `5031 4332 1540 6351` | use the `5474 9254 3267 0366` or the Visa |
| `409 cannot_refund_order` | refunding a Pix not yet paid | cancel instead; to test a refund, pay it with `APRO` |
| *"Não é possível utilizar credenciais de teste em um ambiente de teste"* | you opened "Test credentials" inside the test account | step 4: use the production ones |

## Recap

- One **test seller** account; the buyer one is optional for the API.
- Log into it in a private window; the verification code is the end of the
  User ID.
- Seller's application: **Checkout Transparente**, **Orders API**.
- Token: the test account's **Production credentials** (`APP_USR-...`).
- Payer: any valid e-mail; without one, `400`.
- Keep it outside the repository, `chmod 600`, and check with the step 6
  script before anything else.
- Test cards that work: Visa `4235…5682` and Mastercard `5474…0366`;
  cardholder `APRO` approves and `OTHE` declines.
- A paid Pix in the sandbox: `first_name` `APRO` in the payer.
- An expired Pix: `expiration_time` `PT60S`, then wait out the deadline.

Next: the [Mercado Pago »](mercado-pago.md) recipe, now with the credential
in hand.
