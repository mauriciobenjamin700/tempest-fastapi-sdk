# Mercado Pago: test accounts and credentials

Before charging for real, you want to see a Pix being created, a card being
approved and another declined, a refund going back. All of that can be done
in Mercado Pago's sandbox without moving money. The hard part is getting
there: the dashboard has several credentials with similar names, and most
combinations **do not work** for charging.

This page is the path that worked, step by step, with the error each detour
produces. At the end you will have four things:

1. a **test seller account** (who receives);
2. a **test buyer account** (who pays);
3. the seller's **Access Token**, from an application of the right type;
4. the buyer's **e-mail**.

!!! info "Measured, not deduced"
    Every error quoted here was observed against the sandbox on 2026-10-09,
    with the credential and body described. The record is in
    `vendor/mercadopago-evidence.md`, sections 8.5 and 9. Mercado Pago's
    dashboard changes from time to time: if a menu is not where this page
    says, search for the option's name.

## Why not use your own account

The first temptation is the `TEST-...` token shown under "Test credentials"
of **your** application. It reads data (`GET /v1/payments/search` answers
`200`), but it does not charge:

| Credential | Payer | What Mercado Pago answers |
| --- | --- | --- |
| `TEST-` from your account | no e-mail | `400 Params Error` (Pix: `500 payer_cannot_be_nil`) |
| `TEST-` from your account | any e-mail | `400 excludes_by_rule` (Pix: `500 not_found`) |
| `TEST-` from your account | a test buyer's e-mail | `403 Payer email forbidden` |
| test seller's `APP_USR-`, Checkout Pro application | any | `401 Unauthorized use of live credentials` |
| test seller's `APP_USR-`, **Checkout Transparente / Orders API** application | the test buyer's e-mail | **`201`, charge created** |

Only the last row charges. The rest of this page is how to get there.

!!! warning "Never use your real account's production credentials"
    The `APP_USR-...` of **your** account moves real money. Everything here
    uses the `APP_USR-...` of a **test** account, which has the same prefix
    but moves nothing. How to check is in step 7.

## Step 1 — create the two test accounts

Log in with your normal account at
<https://www.mercadopago.com.br/developers/panel/test-users> and create two
accounts:

- one of type **Seller**, country **Brazil**;
- one of type **Buyer**, country **Brazil**.

For each, write down three things the dashboard shows: **username**,
**password** and **User ID**.

!!! tip "The dashboard shows no e-mail"
    That is expected: the test accounts screen shows username and password,
    not the e-mail. The username looks like `TESTUSER123456789` and is
    **not** an e-mail — sent as `payer.email`, Mercado Pago answers
    `400 payer.email must be a valid email`. The e-mail comes in step 5.

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

## Step 5 — get the buyer's e-mail

Close the private window, open another and log in with the **buyer's**
username and password. Click your name in the top corner → **Your profile**
→ **Personal data**. The e-mail is there, in the form
`test_user_...@testuser.com`.

!!! danger "Do not make the e-mail up"
    An e-mail in the same format, but made up, does not work: with the
    `TEST-` token it came back `403 Payer email forbidden`, and an arbitrary
    e-mail (`@example.com`, Gmail) came back `400 excludes_by_rule`. Use the
    one from the buyer account you created.

## Step 6 — keep it outside the repository

Create a file **outside** any repository, readable only by you:

```bash
mkdir -p ~/.config/my-service
cat > ~/.config/my-service/mercadopago-sandbox.env <<'EOF'
MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN=APP_USR-paste-here
MERCADO_PAGO_TEST_BUYER_EMAIL=test_user_paste-here@testuser.com
EOF
chmod 600 ~/.config/my-service/mercadopago-sandbox.env
```

Open the file in your editor and replace both values. Never paste the token
into a chat, a commit or a log.

## Step 7 — check it worked

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
    buyer: str = os.environ["MERCADO_PAGO_TEST_BUYER_EMAIL"]
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
                "payer": {"email": buyer},
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

Run it with the step 6 file loaded:

```bash
set -a; . ~/.config/my-service/mercadopago-sandbox.env; set +a
python check_sandbox.py
```

Expected output (measured on 2026-10-09):

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
| Mastercard `5031 4332 1540 6351` | `11/2030` / `123` | `APRO` | `422 unprocessable_content` |

!!! note "A decline is HTTP 402, not a server error"
    A declined card comes back **402** with the reason in `errors` and the
    whole order in `data`. Treat it as an answer, not as a network failure:
    the order exists, it was declined, and the reason is right there.

!!! warning "Use the Visa"
    The Mastercard test card found in the documentation answered a generic
    `422` on the Orders API, with the same token that approved the Visa. The
    cause was not isolated.

## Common errors

| Message | Likely cause | What to do |
| --- | --- | --- |
| `401 Unauthorized use of live credentials` | Checkout Pro application, or a token from another application | step 3: Checkout Transparente / Orders API application |
| `403 Payer email forbidden` | your account's `TEST-` token with a test e-mail | steps 2 to 4: use the test seller's token |
| `400 excludes_by_rule` | an e-mail that is not a test buyer's | step 5 |
| `400 payer.email must be a valid email` | username (`TESTUSER...`) instead of the e-mail | step 5 |
| `500 payer_cannot_be_nil` | Pix without `payer.email` | send the buyer's e-mail |
| `422 unprocessable_content` | Mastercard test card | use the Visa test card |
| *"Não é possível utilizar credenciais de teste em um ambiente de teste"* | you opened "Test credentials" inside the test account | step 4: use the production ones |

## Recap

- Two test accounts: the **seller** receives, the **buyer** pays.
- Log into them in a private window; the verification code is the end of the
  User ID.
- Seller's application: **Checkout Transparente**, **Orders API**.
- Token: the test account's **Production credentials** (`APP_USR-...`).
- E-mail: the buyer's profile, never the `TESTUSER...` username and never a
  made-up one.
- Keep it outside the repository, `chmod 600`, and check with the step 7
  script before anything else.
- The test card that works: Visa, cardholder `APRO` approves and `OTHE`
  declines.

Next: the [Mercado Pago »](mercado-pago.md) recipe, now with credentials in
hand.
