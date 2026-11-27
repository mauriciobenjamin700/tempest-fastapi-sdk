---
description: "Use to decide whether the JWT pair goes in the body (bearer), in a cookie or both with `AUTH_TOKEN_DELIVERY`; covers cookie mode and both mode."
---

# Token delivery: bearer, cookie or both

Use to decide whether the JWT pair goes in the body, in a cookie or both.

Part of the [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md) recipe.

*(v0.87.0+)*

By default login returns `access_token` / `refresh_token` **in the body** and the client replays them as `Authorization: Bearer <token>`. Great for mobile/API clients, but a browser SPA has to stash the token somewhere JS can reach — exposed to XSS. `AUTH_TOKEN_DELIVERY` lets you choose.

| Mode | What changes | For whom |
|------|--------------|----------|
| `bearer` *(default)* | Tokens **in the body only**. Historical, backward-compatible behaviour. | Mobile, APIs, clients that send `Authorization`. |
| `cookie` | Tokens set as **`HttpOnly`** cookies on the same paths (`/auth/login`, `/auth/refresh`, `/auth/logout`); the body returns the tokens as `null`. | Browser SPAs — the token is never visible to JS (XSS defense). |
| `both` | Bearer endpoints stay at `/auth/*` **and** a parallel cookie set is mounted at `/auth/cookie/*`. | One backend serving web (cookie) **and** mobile (bearer) at once. |

!!! danger "The cookie `Secure` flag requires HTTPS"
    With `AUTH_COOKIE_SECURE=true` (default) the browser **only** sends the cookie back over HTTPS. If the backend is served over **plain HTTP**, the cookie is dropped and the session never persists (login looks like it works but nothing stays logged in). In production, put TLS in front and keep it `true`; on local HTTP dev use `AUTH_COOKIE_SECURE=false`.

## Cookie mode

```bash
# .env
AUTH_TOKEN_DELIVERY=cookie
AUTH_COOKIE_SECURE=true          # false only on plain HTTP
AUTH_COOKIE_SAMESITE=lax         # "none" (+Secure) if the SPA is cross-site
```

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import UserAuthService, make_auth_router

from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel

auth_service = UserAuthService(
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
)
app = FastAPI()


app.include_router(make_auth_router(auth_service, session_factory=db.session_dependency))
```

Frontend flow — **stores no token at all**, just calls the endpoints with `credentials: "include"`:

```javascript
// login: the browser stores the HttpOnly cookies itself
await fetch("/auth/login", {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, password }),
});

// authenticated requests: the access cookie rides along automatically
await fetch("/api/me", { credentials: "include" });

// renew the session: the refresh cookie is read on the backend, no body
await fetch("/auth/refresh", { method: "POST", credentials: "include" });

// logout: clears the cookies (and revokes the refresh family if a refresh_token_model is wired)
await fetch("/auth/logout", { method: "POST", credentials: "include" });
```

The `current_user` dependency (see [Getting the `current_user`](current-user.md)) now **reads the access token from the cookie** automatically whenever delivery involves cookies — the `Authorization` header still wins if present.

## Both mode

```bash
AUTH_TOKEN_DELIVERY=both
```

Mounts both sets, no route collision:

```text
# bearer (body):
POST /auth/login
POST /auth/refresh
POST /auth/logout

# cookie (HttpOnly):
POST /auth/cookie/login
POST /auth/cookie/refresh
POST /auth/cookie/logout
```

!!! info "What stays in the body"
    Cookie delivery covers the **login / refresh / logout** lifecycle. Activation (`POST /auth/activate/{token}`), signup auto-login (`AUTH_AUTO_ACTIVATE`) and `POST /auth/mfa/verify` still return the JWT pair in the body, regardless of `AUTH_TOKEN_DELIVERY`.

!!! tip "Fine-tuning the cookies: `cookie_config=`"
    The `AUTH_COOKIE_*` settings cover the common case. When you need
    per-attribute control — name, `max_age`, refresh path, `Domain` — pass an
    `AuthCookieConfig` to `make_auth_router`; it wins over the settings:

    ```python
    from tempest_fastapi_sdk import AuthCookieConfig, make_auth_router

    app.include_router(
        make_auth_router(
            auth_service,
            session_factory=db.session_dependency,
            token_delivery="cookie",
            cookie_config=AuthCookieConfig(
                access_name="session",
                refresh_name="session_refresh",
                access_max_age=900,
                refresh_max_age=1209600,
                samesite="none",
                secure=True,
                domain=".example.com",
            ),
        ),
    )
    ```

    Mirror `access_max_age` on `JWT_ACCESS_TTL_SECONDS` and `refresh_max_age` on
    `JWT_REFRESH_TTL_SECONDS`: a cookie outliving its token leaves the browser
    sending a dead credential, and the reverse logs the user out early. The
    router narrows `refresh_path` to the refresh endpoint, so the long-lived
    token never rides on an ordinary call. `http_only=True` is the whole point
    of cookie mode — do not turn it off.

    Outside `make_auth_router` the same rules apply through
    `apply_auth_cookies(response, access_token=..., refresh_token=..., config=...)`
    and `clear_auth_cookies(response, config=...)` — handy in a login route of
    your own (the OAuth callback in the [social login recipe](../oauth.md), say).

!!! tip "CORS with credentials"
    For a cross-origin SPA to send/receive cookies, the backend needs `allow_credentials=True` in CORS **and** `AUTH_COOKIE_SAMESITE=none` + `AUTH_COOKIE_SECURE=true` (hence HTTPS). Same-origin (frontend served from the API's domain) works with the default `lax`.
