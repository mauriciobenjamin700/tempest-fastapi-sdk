# Server-side login and signup

Your service serves its own HTML — a panel, a portal, an internal tool — and needs login, signup and account activation **without** a separate frontend. `make_auth_router` speaks JSON to an SPA; here you get the same rules as **pages**: the browser receives a form, posts a `<form>` without JavaScript, and the session is a cookie.

`make_auth_pages_router` ships the pages ready-made on top of the same `UserAuthService` and an opaque, revocable session (`SessionAuth`). The default templates come in pt-BR and en-US, and you can override any of them.

## What you will build

- `GET/POST /login` — e-mail and password; an account with MFA goes through `/login/mfa`.
- `GET/POST /signup` — a form generated from the `signup_schema`.
- `GET /activate/{token}` — the e-mailed link activates the account **and opens the session**.
- `GET/POST /activation/request` — resend the activation link.
- `POST /logout` — revokes the session on the server.
- A dependency that returns the session's **user** to your routes.

## Installation

```bash
uv add "tempest-fastapi-sdk[auth,email,sqlite]"
```

`[auth]` brings bcrypt and PyJWT; `[email]` brings the Jinja2 that renders the pages; `[sqlite]`, the example's driver.

## The complete example

```python
# app.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseUserModel,
    MemorySessionStore,
    SessionAuth,
    UserAuthService,
    make_auth_pages_router,
    make_session_user_dependency,
    make_user_token_model,
    redirect_to_login,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings import AuthSettings, JWTSettings, SessionSettings


class UserModel(BaseUserModel):
    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")

db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")

auth_service = UserAuthService(
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=AuthSettings(
        AUTH_ACTIVATION_URL_TEMPLATE="http://localhost:8000/activate/{token}",
    ),
    jwt_settings=JWTSettings(JWT_SECRET="change-me-to-32-or-more-characters"),
    email=None,
)

session_auth = SessionAuth(
    user_model=UserModel,
    store=MemorySessionStore(),
    settings=SessionSettings(SESSION_COOKIE_SECURE=False),
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the tables on startup and close the connection on shutdown."""
    await db.connect()
    await db.create_tables()
    yield
    await db.disconnect()


app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)
app.include_router(
    make_auth_pages_router(
        auth_service,
        db.session_dependency,
        session_auth=session_auth,
    )
)

current_user = Depends(
    make_session_user_dependency(
        UserModel,
        db.session_dependency,
        session_auth=session_auth,
        on_missing=redirect_to_login("/login"),
    )
)


@app.get("/")
async def home(user: UserModel = current_user) -> dict[str, str]:
    """Only someone with a session gets here."""
    return {"hello": user.email}
```

Run it with `uv run uvicorn app:app` and open `http://localhost:8000/`. What happens, measured with `TestClient`:

```text
GET  /                      → 303 /login?next=/
GET  /signup                → 200 (the form)
POST /signup                → 200 "Confira seu e-mail" (with email=None, the link shows on the page)
GET  /activate/{token}      → 303 /   + session cookie
GET  /                      → 200 {"hello": "ana@example.com"}
POST /logout                → 303 /login
GET  /                      → 303 /login?next=/
POST /login (no token)      → 403
```

The page language follows `?lang=`, then `Accept-Language`, then `AUTH_DEFAULT_LOCALE` (`pt-BR` by default), which is why the run above shows Portuguese.

## Piece by piece

### The session: `SessionAuth`

The pages open an **opaque** session: the cookie carries a random id, and the store keeps only its hash. That is what lets logout really revoke — a copied cookie stops working the moment `POST /logout` runs. `MemorySessionStore` is for dev and tests; in production, with more than one replica, use `RedisSessionStore`. The cookie follows the `SessionSettings` `SESSION_*` fields (`HttpOnly` always, `Secure` and `SameSite` configurable).

!!! warning "`SESSION_COOKIE_SECURE=False` only on local HTTP"
    The example turns `Secure` off because it runs on `http://localhost`. In production, behind HTTPS, keep the default (`True`). A `Secure` cookie does not come back over plain HTTP, and then every form answers `403` (the CSRF token is a cookie too).

### The pages router

`make_auth_pages_router(service, session_factory, session_auth=...)` mounts the routes at the site root (`AUTH_PAGES_PREFIX=""`). To put them under another path, change `AUTH_PAGES_PREFIX` (for instance `/account`). Do not reuse the `make_auth_router` prefix (`/auth`): both would answer `POST /auth/login`.

No page route shows up in OpenAPI (`include_in_schema=False`).

!!! warning "Also mounted `make_auth_router`? Share the budget"
    Each router builds its own in-memory `AttemptThrottle` when you pass none. With both mounted, an e-mail would get the attempts of `/auth/login` **and** those of `/login`. Pass both the same instance (and, with more than one worker, a Redis backend):

    ```python
    from fastapi import FastAPI

    from tempest_fastapi_sdk import (
        AttemptThrottle,
        InMemoryThrottleBackend,
        make_auth_pages_router,
        make_auth_router,
    )

    from app import auth_service, db, session_auth

    app = FastAPI()

    login_throttle = AttemptThrottle(
        InMemoryThrottleBackend(), max_attempts=5, window_seconds=900
    )

    app.include_router(
        make_auth_router(
            auth_service,
            session_factory=db.session_dependency,
            login_throttle=login_throttle,
        )
    )
    app.include_router(
        make_auth_pages_router(
            auth_service,
            db.session_dependency,
            session_auth=session_auth,
            login_throttle=login_throttle,
        )
    )
    ```

### The activation link points at the page

`AUTH_ACTIVATION_URL_TEMPLATE` is the URL the e-mail carries. Point it at the pages' `GET /activate/{token}`, on the service's public domain:

```dotenv
AUTH_ACTIVATION_URL_TEMPLATE=https://app.example.com/activate/{token}
```

Whoever clicks it is activated **and signed in** (`amr=["email"]`). An account with MFA goes to `/login/mfa` before the session opens.

!!! info "Why the link signs in"
    It saves a step: the e-mail just proved the person controls that inbox. The trade-off is the one of every *magic link*, both ways. Someone who gets another person's link gets into their account. And an attacker can sign up an account, leave it inactive and send **their own** link to someone: whoever clicks it is signed in to the attacker's account and may type data there the attacker reads later (the form's *login CSRF*, by another route). The token is single-use and expires after `AUTH_ACTIVATION_TTL_SECONDS`. If that risk matters to your product, show which account it is right after activation.

### Protecting your routes

`make_session_user_dependency(UserModel, session_factory, session_auth=...)` returns the **user**, not the session. It reloads the account on every request: an account deactivated after the login loses access on its next click, without waiting for the session to expire.

- `on_missing=redirect_to_login("/login")` sends the browser to the login with `?next=` (the path and query it was on), and the login sends it back there.
- `require_mfa=True` requires the session to have passed the second factor (`otp` in its `amr`); without it, `403`.

## MFA

With `AUTH_MFA_ENABLED=true`, pass the recovery-code model:

```python
from tempest_fastapi_sdk import make_auth_pages_router, make_user_recovery_code_model

from app import auth_service, db, session_auth

UserRecoveryCodeModel = make_user_recovery_code_model(user_table="users")

router = make_auth_pages_router(
    auth_service,
    db.session_dependency,
    session_auth=session_auth,
    recovery_code_model=UserRecoveryCodeModel,
)
```

An account with TOTP confirmed leaves `POST /login` for `/login/mfa` **without a session**. The pending step lives in a short-lived `HttpOnly` cookie, never in the URL. The code goes through the same `mfa_verify` as the JSON API: a TOTP code is single-use and has a per-account budget. The session opened afterwards records `amr=["pwd", "otp"]`.

## Signup with your own fields

The `/signup` form is generated from the `signup_schema`, the same one `make_auth_router` takes. An extra field that is also a user column is set before the insert; `on_signup` runs after it, in the same transaction:

```python
from pydantic import Field

from tempest_fastapi_sdk import SignupSchema, make_auth_pages_router

from app import auth_service, db, session_auth


class ProductSignupSchema(SignupSchema):
    """E-mail, password, name — and the phone number."""

    phone: str = Field(title="Phone", max_length=20)


router = make_auth_pages_router(
    auth_service,
    db.session_dependency,
    session_auth=session_auth,
    signup_schema=ProductSignupSchema,
)
```

The field's `title` becomes the label. A validation error re-renders the form with `422`, a message per field and what was typed — except the password. With `AUTH_SIGNUP_ENABLED=false`, `/signup` is not mounted at all (`404`).

## Overriding the templates

Pass `template_dir` and put a file with the same name in it. The lookup is: `template_dir/<locale>/<name>`, `template_dir/<name>` and, last, the SDK's copy.

| Template | Setting | Context |
|---|---|---|
| `login.html` | `AUTH_LOGIN_PAGE_TEMPLATE` | `email`, `next`, `error` |
| `login_mfa.html` | `AUTH_LOGIN_MFA_PAGE_TEMPLATE` | `next`, `error` |
| `signup.html` | `AUTH_SIGNUP_PAGE_TEMPLATE` | `fields`, `values`, `errors`, `error`, `min_length` |
| `signup_done.html` | `AUTH_SIGNUP_DONE_TEMPLATE` | `email`, `activation_url` |
| `activation_request.html` | `AUTH_ACTIVATION_REQUEST_PAGE_TEMPLATE` | `email`, `error` |
| `activation_request_done.html` | `AUTH_ACTIVATION_REQUEST_DONE_TEMPLATE` | `email` |

Every template also gets `csrf_field`, `csrf_token`, `prefix` and `signup_enabled`. **Every `<form method="post">` needs the hidden field**, or the submit comes back `403`:

```html
<input type="hidden" name="{{ csrf_field }}" value="{{ csrf_token }}">
```

To change only the look, override `_auth_layout.html`: every page extends it, and the CSS lives in its `styles` block.

!!! tip "Prefer the typed `ui` layer?"
    The default pages are Jinja2 so they do not require the `[ssr]` extra. In a service with the `ui` layer, you can write your own with `form_for`/`parse_form` and call `UserAuthService` and `SessionAuth` from the router. What must not go missing is what this router already does: CSRF per form field (`tempest_fastapi_sdk.auth.FormCSRF`), a `next` limited to this site's paths (`is_local_path`) and the same attempt budgets.

## Security: what comes built in

Each item below has a test in `tests/auth/test_auth_pages.py`.

- **CSRF on every POST, login included.** A random token in an `HttpOnly` cookie, repeated in a hidden field, compared in constant time (*double-submit*). Without it, a page on another site could sign the victim's browser in to the attacker's account (*login CSRF*). The refusal comes before anything else and spends no attempt. With a `Secure` cookie, the name carries the `__Host-` prefix, which a browser only accepts without `Domain`: a sibling subdomain cannot plant the cookie (and the field) to pass the check.
- **`next` without open redirect.** Only a path on this site (`/x`). `https://...`, `//host` and `/\host` are ignored, and the redirect goes to `AUTH_PAGES_LOGIN_REDIRECT`. The settings' redirects are validated at boot.
- **Generic refusal.** A wrong password, an unknown account and an inactive account all get the same `401` page ("E-mail ou senha inválidos." / "Invalid e-mail or password.").
- **Budgets.** Login per e-mail (the same rule as the JSON API, through the same code) and per IP (`login_ip_throttle`), signup and resend per IP, MFA per account. The `429` re-renders the page with `Retry-After`.
- **Resend without enumeration by content.** `/activation/request` shows the same page with or without an account. The **timing** differs: only a pending account waits on the mail being sent — the JSON API has the same property.
- **Signup reveals an e-mail in use.** An existing e-mail comes back `409` on the field, as in the JSON API. If that matters to your product, turn public signup off (`AUTH_SIGNUP_ENABLED=false`).
- **Session.** Rotated on login (`SESSION_ROTATE_ON_LOGIN`), really revoked on logout.

!!! note "What about the password-reset form?"
    The reset pages stay with the `make_auth_router` link pages (`AUTH_BACKEND_LINKS=true`). They do not ask for a CSRF token: the POST only works with the secret token from the e-mailed URL, and whoever has that token can already reset the password without forging anything.

## Testing

`TestClient` does what the browser does: loads the form, reads the hidden field and submits it.

```python
import re

from fastapi.testclient import TestClient

from app import app


def test_signup_and_activation_sign_in() -> None:
    """Signup, activation link and session — no JavaScript."""
    with TestClient(app, base_url="http://localhost:8000") as client:
        page = client.get("/signup")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        assert token is not None
        done = client.post(
            "/signup",
            data={
                "csrf_token": token.group(1),
                "email": "ana@example.com",
                "password": "strong-pass-12",
            },
        )
        link = re.search(r'href="http://localhost:8000(/activate/[^"]+)"', done.text)
        assert link is not None
        client.get(link.group(1))
        assert client.get("/").json() == {"hello": "ana@example.com"}
```

## Recap

- `make_auth_pages_router` mounts login, MFA, signup, activation, resend and logout as HTML, on top of the same `UserAuthService`.
- The session is `SessionAuth`: opaque, revocable, configured by the `SESSION_*` fields.
- `make_session_user_dependency` protects your routes and returns the user, reloaded on every request; `redirect_to_login` brings the user back with `next`.
- Every POST carries the `csrf_token`; a template of your own needs the hidden field.
- Point `AUTH_ACTIVATION_URL_TEMPLATE` at `/activate/{token}`, and the e-mailed link signs the user in.
