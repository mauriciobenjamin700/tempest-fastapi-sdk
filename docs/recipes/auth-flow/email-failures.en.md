---
description: "Use to know what each auth route answers with `EMAIL_ENABLED=false` or SMTP down, and when to use `AUTH_SEND_EMAILS_IN_BACKGROUND`."
---

# Email disabled and SMTP down

Use to know what each route answers with `EMAIL_ENABLED=false` or with SMTP failing, and when to send emails in the background.

Part of the [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md) recipe.

## Email disabled

In a phased deploy SMTP may arrive after everything else
(`EMAIL_ENABLED=false`, see
[Switching integrations on and off](../system-checks.md#switching-integrations-on-and-off-phased-deploys)).
The risk here is concrete: with `AUTH_AUTO_ACTIVATE=false`, a signup
creates the inactive account and mails the activation link — which never
arrives. The account is stuck, and its address stays "taken" forever.

That is why `UserAuthService` checks the mailer **before** writing
anything. With `EmailUtils(**settings.email_kwargs())` built under
`EMAIL_ENABLED=false`, every route that would mail a link answers `503
INTEGRATION_DISABLED`:

```python
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuthSettings,
    BaseUserModel,
    EmailSettings,
    EmailUtils,
    JWTSettings,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)


class Settings(EmailSettings, JWTSettings, AuthSettings):
    """Settings with e-mail not configured yet."""


class UserModel(BaseUserModel):
    """The accounts table."""

    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")

settings: Settings = Settings(
    EMAIL_ENABLED=False,
    AUTH_AUTO_ACTIVATE=False,
    JWT_SECRET="replace-with-a-real-long-secret-0123456789abcdef",
)
db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
auth_service: UserAuthService = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=EmailUtils(**settings.email_kwargs()),
)

app: FastAPI = FastAPI()
register_exception_handlers(app)
app.include_router(make_auth_router(auth_service, session_factory=db.session_dependency))


async def main() -> None:
    """Try a signup with e-mail switched off."""
    await db.connect()
    await db.create_tables()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/signup",
            json={"email": "ana@acme.com.br", "password": "strong-pass-123"},
        )
    print(response.status_code, response.json())
    await db.disconnect()


asyncio.run(main())
```

```text
503 {'detail': 'The email integration is disabled in this environment', 'code': 'INTEGRATION_DISABLED', 'details': {'integration': 'email'}}
```

No row was written: neither the account nor the token. The refused routes
are the ones that send mail — `/signup` (only with
`AUTH_AUTO_ACTIVATE=false`), `/activation/request`,
`/password-reset/request`, `/email-change/request`,
`/email-verify/request`, `/email-recovery/request` and, with
`AUTH_EMAIL_CHANGE_NOTIFY_OLD=true`, `/email-change/confirm` (before the
token is consumed, so it still works once e-mail is back). Login, refresh,
logout and MFA are unaffected.

!!! info "When disabled e-mail refuses nothing"
    With no mailer (`email=None`) or `AUTH_RETURN_TOKEN_IN_RESPONSE=true`
    the link travels in the response body, so there is no mail to send
    and the flow runs as always. Same for a signup with
    `AUTH_AUTO_ACTIVATE=true`: the account is born active and needs no
    link.

!!! tip "The refusal comes before the lookup"
    On `/password-reset/request` and `/email-recovery/request` the `503`
    is raised before the account is looked up, so a known and an unknown
    address get the same answer — the switch never becomes an enumeration
    oracle.

## SMTP server down

`EMAIL_ENABLED=false` is choosing to run without e-mail. The opposite case
is e-mail that is **configured and failing**: the host does not resolve,
the connection is refused, the TLS handshake breaks (`SMTP_USE_SSL=true`
on port `587` answers `[SSL: WRONG_VERSION_NUMBER]` on every send), the
login is rejected. Before, that surfaced as an `aiosmtplib` error and the
route answered a bare `500`, with the traceback logged as an unhandled
exception.

Now `EmailUtils.send` turns every transport failure into
`EmailDeliveryException` — `503`, `code="EMAIL_UNAVAILABLE"`, no
`details` — and each auth flow decides what to do with it:

```python
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuthSettings,
    BaseUserModel,
    EmailSettings,
    EmailUtils,
    JWTSettings,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)


class Settings(EmailSettings, JWTSettings, AuthSettings):
    """Settings with an SMTP server that does not answer."""


class UserModel(BaseUserModel):
    """The accounts table."""

    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")

settings: Settings = Settings(
    SMTP_HOST="127.0.0.1",
    SMTP_PORT=2525,
    SMTP_TIMEOUT_SECONDS=5.0,
    AUTH_AUTO_ACTIVATE=False,
    JWT_SECRET="replace-with-a-real-long-secret-0123456789",
)
db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
auth_service: UserAuthService = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=EmailUtils(**settings.email_kwargs()),
)

app: FastAPI = FastAPI()
register_exception_handlers(app)
app.include_router(make_auth_router(auth_service, session_factory=db.session_dependency))


async def main() -> None:
    """Try a signup with the SMTP server down."""
    await db.connect()
    await db.create_tables()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/signup",
            json={"email": "ana@acme.com.br", "password": "strong-pass-123"},
        )
    print(response.status_code, response.json())
    await db.disconnect()


asyncio.run(main())
```

```text
503 {'detail': 'The e-mail server could not be reached; try again later', 'code': 'EMAIL_UNAVAILABLE', 'details': {}}
```

No row was written: the flush of the account and its token never reached
the commit, and the request session discarded both. The same address can
sign up again once the server is back.

### What each route answers

| Route | With SMTP failing | What is stored |
| --- | --- | --- |
| `/signup` | `503 EMAIL_UNAVAILABLE` | nothing: no account, no token |
| `/email-change/request` | `503 EMAIL_UNAVAILABLE` | nothing; an earlier pending link keeps working |
| `/email-change/confirm` (with `AUTH_EMAIL_CHANGE_NOTIFY_OLD=true`) | `503 EMAIL_UNAVAILABLE` | nothing: the address does not change and the same link confirms later |
| `/email-verify/request` | `503 EMAIL_UNAVAILABLE` | nothing |
| `/email-recovery/request` (password and MFA correct) | `503 EMAIL_UNAVAILABLE` | nothing |
| `/activation/request` | `202`, the same answer as an unknown address | nothing; the earlier link keeps working |
| `/password-reset/request` | `202`, the same answer as an unknown address | nothing; the earlier link keeps working |

The rule is what the response already reveals. Signup already answers
`409` for an address in use; e-mail change and verification require a
login; e-mail recovery reaches the send only after the password (and MFA)
matched — the same point where a taken `new_email` answers `409`. On
those routes the `503` tells the caller nothing new.

!!! warning "Reset and activation re-send keep answering `202`"
    These two routes answer the same for a registered and an unknown
    address, on purpose. If the send failure became a `503`, it would show
    up only for accounts that exist — and the route would become an
    enumeration oracle. So the failure is **logged at `ERROR`** (without
    the address) and the answer stays `202`. The token is stored only
    **after** the mail went out, so the failure writes nothing and the
    earlier pending link is not invalidated.

!!! info "What reaches the log"
    `EmailUtils` logs once, at `ERROR`, with `smtp_host` and `smtp_port`
    — never the recipient nor the credential. A network or TLS failure
    carries the traceback (that is where the diagnosis lives); a refusal
    the server answered (a `550` for one recipient, say) does not,
    because the reply text can quote the address. For the same reason,
    only a network or TLS failure is chained as `__cause__`; a refusal is
    raised `from None`, so the handler that logs every 5xx has nothing to
    print.

When you call `UserAuthService` outside `make_auth_router`, the rule is
the one for any error after a flush: roll the session back before reusing
it. The router does that on its own, because it never reaches the commit.

### In the background (`AUTH_SEND_EMAILS_IN_BACKGROUND`)

*(v0.332.0+)*

By default the route waits for SMTP: a slow server makes signup and reset
slow. With `AUTH_SEND_EMAILS_IN_BACKGROUND=true`, the `make_auth_router`
routes hand the send to the request's `BackgroundTasks` — the response goes
first and the email after. Measured in `tests/auth/test_email_background.py`,
with a mailer taking 2 s per send served by real uvicorn: `POST /auth/signup`
answered after the 2 s by default and before the send finished in the
background — which is what the test asserts. In one run with `--durations`
(which also counts the server start-up), the default took 2.2 s and the
background 0.18 s on an idle machine; under the parallel suite the background
reached 0.67 s.

```bash
# .env
AUTH_SEND_EMAILS_IN_BACKGROUND=true
```

What changes, and the price:

- **The response time stops depending on the send.** A
  `/password-reset/request` for a registered address no longer takes longer
  than one for an unknown address, a difference you could measure from
  outside.
- **An SMTP failure does not answer `503`.** The response is gone; the
  failure goes to `ERROR` on the `tempest_fastapi_sdk.auth.email_delivery`
  logger, with the kind of email and only the recipient's **domain**.
- **There is no rollback on a failed send.** The signup stays stored without
  the activation email (the user asks for a resend), and on reset and
  activation resend the new token has already replaced the pending one when
  the send fails — the earlier link stops working and the new one never
  arrived. Asking again fixes it.
- **The email only goes out if the request succeeded.** `make_auth_router`
  commits before answering, and an exception after scheduling swaps the
  response for an error one, which does not carry the tasks: no link points
  at a token that was not stored.

Outside a request — `UserAuthService` called from a command, a task or a
test — there is no `BackgroundTasks`, and the send is awaited as by default,
with the same `EmailDeliveryException`.

!!! tip "Shutdown"
    Starlette runs a response's background tasks before the request ends, so
    the server's graceful shutdown waits for them like any in-flight request.
    A loose `asyncio.create_task` would not have that guarantee.
