# Settings: environment variables

Look up the flow's environment variables in typed groups: JWT, password policy, email, TTL, URLs, pages, language, token delivery and more.

Part of the [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md) recipe.

Every knob in the flow comes from settings mixins. Mix them into your `Settings` class
(the table of every mixin and the ordering rule are in [Settings](../settings.md)):

```python
# src/core/settings.py
from tempest_fastapi_sdk import (
    AuthSettings,
    BaseAppSettings,
    DatabaseSettings,
    EmailSettings,
    JWTSettings,
    ServerSettings,
)


class Settings(
    ServerSettings,
    DatabaseSettings,
    EmailSettings,
    JWTSettings,
    AuthSettings,
    BaseAppSettings,
):
    pass


settings: Settings = Settings()
```

!!! info "The attribute name **is** the env var name"
    Every attribute in the tables below is read from an environment variable of the **same name**, case-sensitive, **no prefix**. `AUTH_PASSWORD_MIN_LENGTH` in `.env` → `settings.AUTH_PASSWORD_MIN_LENGTH`. They all have defaults — you only set what you want to change.

The variables split across **two mixins** and **six concern groups**. They're separated on purpose: a password is not the same thing as an email, and authentication (JWT) is not the same thing as account activation.

## Group 1 — Authentication / JWT (`JWTSettings`)

Controls the signing and lifetime of the tokens login returns. **It's the same `JWT_SECRET` the `get_current_user` dependency uses to verify** (see [Getting the `current_user`](current-user.md)).

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `JWT_SECRET` | `str` (≥32 bytes) | `change-me-…-32` | HMAC secret that signs the JWT. **Must be overridden in production.** |
| `JWT_ALGORITHM` | `str` | `HS256` | JOSE algorithm. `HS256`/`HS512` (symmetric secret) or `RS256` (key pair). |
| `JWT_ACCESS_TTL_SECONDS` | `int` (≥1) | `3600` | **Access token** lifetime (1 h). Short by design — renew via refresh. |
| `JWT_REFRESH_TTL_SECONDS` | `int` (≥1) | `604800` | **Refresh token** lifetime (7 days). |
| `JWT_ISSUER` | ``str | None`` | `None` | `iss` claim. `None` omits the claim. |

!!! danger "The default `JWT_SECRET` leaks tokens"
    The default `change-me-change-me-change-me-32` exists only to boot locally. In production, **anyone** with the default can forge a valid JWT. Generate a strong secret (`openssl rand -base64 48`) and inject it via a secret manager — never commit it.

## Group 2 — Password policy (`AuthSettings`)

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_PASSWORD_MIN_LENGTH` | `int` (≥1) | `8` | Minimum length accepted on signup **and** reset. |
| `AUTH_PASSWORD_MAX_BYTES` | `int` (≥1) | `72` | Maximum length in UTF-8 **bytes**. This is bcrypt's hard limit. |
| `AUTH_PASSWORD_REQUIRE_COMPLEXITY` | `bool` | `false` | `true` = require 1 lowercase + 1 uppercase + 1 digit + 1 special character. |

These two interact — **this is where it usually gets confusing**. The exact rule:

- **`complexity=false` (default):** only length matters. Any password with `≥ AUTH_PASSWORD_MIN_LENGTH` characters passes, with no composition requirement.
- **`complexity=true`:** on top of the 4 character classes, the **effective** length floor becomes `max(AUTH_PASSWORD_MIN_LENGTH, 8)`. That is, an `AUTH_PASSWORD_MIN_LENGTH` below 8 is **ignored** while complexity is on.

Decision table:

| `MIN_LENGTH` | `REQUIRE_COMPLEXITY` | Password accepted when |
|--------------|----------------------|------------------------|
| `12` | `false` | `≥ 12` chars, any composition |
| `4` | `false` | `≥ 4` chars, any composition (low floor, dev-only) |
| `4` | `true` | `≥ 8` chars (floor 8 **overrides** the 4) **+** the 4 classes |
| `16` | `true` | `≥ 16` chars **+** the 4 classes |

!!! warning "The floor is the single source of truth"
    The request schemas (`SignupSchema`, `PasswordResetConfirmSchema`) impose **no** length bound of their own — they delegate to these two vars. Lowering `AUTH_PASSWORD_MIN_LENGTH` to `4` genuinely relaxes validation on the route too. There is no hidden second limit in the schema "protecting" you.

!!! danger "The ceiling counts bytes, not characters"
    `AUTH_PASSWORD_MAX_BYTES` exists because bcrypt **refuses** input over 72 bytes: `hashpw` raises `ValueError`, and without the ceiling that surfaced as a **500** on signup / reset / password change. Bytes is the unit the hash sees, and 72 bytes arrive well before 72 characters on non-ASCII text — an emoji costs 4 bytes, an accented letter 2, so `"🔒" * 19` (19 characters) is already over the limit and answers **422**. Only raise the value if you swap the hasher for one without the limit.

!!! info "Each violation has its own `message_key` (v0.305.0+)"
    The response `code` stays `VALIDATION_ERROR`, but the `message_key` names
    the rule that failed, and the default catalog translates each one:

    | `message_key` | When | `detail` in en-US (default catalog) |
    |---------------|------|-------------------------------------|
    | `PASSWORD_TOO_SHORT` | below the effective floor | `Password must be at least 8 characters` |
    | `PASSWORD_TOO_LONG` | above `AUTH_PASSWORD_MAX_BYTES` | `Password must be at most 72 bytes` |
    | `PASSWORD_TOO_WEAK` | missing a character class (complexity on) | `Password must contain a lowercase letter, an uppercase letter, a digit and a special character` |

    The same value is on `PasswordPolicyViolation.code`
    (`PasswordViolationCodeEnum`), for callers of `check_password_policy`. To
    change the `code` too, see
    [your product's error codes](errors.md).

## Group 3 — Email flow control (`AuthSettings`)

Decide **whether** and **how** the link reaches the user. They map directly to the [five operating modes](modes.md#five-operating-modes).

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_AUTO_ACTIVATE` | `bool` | `false` | `true` = user is born active, skips activation, signup returns the JWT pair directly (Mode D). `ENV=production` refuses to boot with it on. |
| `AUTH_RETURN_TOKEN_IN_RESPONSE` | `bool` | `false` | `true` = activation/reset link goes in the JSON body instead of the email (Mode C). |
| `AUTH_SEND_EMAILS_IN_BACKGROUND` | `bool` | `false` | `true` = the auth routes' emails go out **after** the response (`BackgroundTasks`); an SMTP failure is logged, not a `503`. See [In the background](email-failures.md#in-the-background-auth_send_emails_in_background). |

## Group 4 — Account token TTL (`AuthSettings`)

Lifetime of the **single-use** tokens (activation / reset) — distinct from the Group 1 JWTs.

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_ACTIVATION_TTL_SECONDS` | `int` (≥60) | `604800` | Activation token lifetime (7 days). |
| `AUTH_PASSWORD_RESET_TTL_SECONDS` | `int` (≥60) | `3600` | Reset token lifetime (1 h). Shorter is safer. |

## Group 5 — Email URLs and templates (`AuthSettings`)

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_ACTIVATION_URL_TEMPLATE` | `str` | `http://localhost:3000/activate?token={token}` | Activation link that goes in the email (or in the `signup` body, when there is no email); `{token}` is substituted. Points at **whoever will open the link** — frontend, backend or app. Per-mode table right below. |
| `AUTH_PASSWORD_RESET_URL_TEMPLATE` | `str` | `http://localhost:3000/reset-password?token={token}` | Same, for reset. |
| `AUTH_ACTIVATION_TEMPLATE` | `str` | `activation.html` | Jinja2 filename of the activation **email HTML**, resolved against `EmailUtils.template_dir`. |
| `AUTH_PASSWORD_RESET_TEMPLATE` | `str` | `password_reset.html` | Same, for reset. |
| `AUTH_EMAIL_BRAND_NAME` | `str` | `""` | Name in the emails' header; empty leaves the header out. See [The brand in the emails](emails.md#the-brand-in-the-emails). |
| `AUTH_EMAIL_BRAND_COLOR` | `str` | `#2563eb` | Color (`#rgb`/`#rrggbb`) of the button and accents; the button text darkens on its own on a light color. |
| `AUTH_EMAIL_BRAND_LOGO_URL` | `str` | `""` | Absolute `http(s)` URL of the header logo, instead of the name. |
| `AUTH_EMAIL_FOOTER_TEXT` | `str` | `""` | Extra footer line (company name, address). |

### Where `AUTH_ACTIVATION_URL_TEMPLATE` points

The template is just a `.format()` string: the SDK substitutes `{token}` and
does not know who will open the result. The mode decides:

| Mode | Who opens the link | Template | Route that consumes the token |
| --- | --- | --- | --- |
| [A](modes.md#mode-a-production), [B](modes.md#mode-b-dev-with-local-smtp-mailhog-or-smtp4dev), [C](modes.md#mode-c-dev-without-smtp-link-in-body) | a **frontend** page, which reads `?token=` and calls the API | `https://app.example.com/activate?token={token}` | `POST /auth/activate/{token}` |
| [E — backend-only](modes.md#mode-e-backend-only-v0320) | the **backend** itself: `AUTH_BACKEND_LINKS=true` mounts an HTML page at `GET /auth/activate/{token}` that activates the account on click | `https://api.example.com/auth/activate/{token}` (with the router prefix, if any) | `GET /auth/activate/{token}` |
| Mobile app | the **app**, through a deep link | `meuapp://ativar?token={token}` | `POST /auth/activate/{token}` |
| [D](modes.md#mode-d-ci-tests-skip-everything) | nobody — the account is born active | irrelevant | none |

**Dev without a frontend:** the default points at `localhost:3000`, a page
that does not exist in your case. Turn on Mode E with the template on the
backend itself (`http://127.0.0.1:8000/auth/activate/{token}`), or leave the
template as is and send the token to `POST /auth/activate/{token}`. The step
by step, with measured responses, is in
[Backend only](backend-only.md).

!!! warning "URL template ≠ Jinja2 template"
    `*_URL_TEMPLATE` is a `.format()` string with `{token}` — it's the **link**. `*_TEMPLATE` is the name of an `.html` file — it's the **email that wraps the link**. Confusing the two is the #1 mistake. Full detail in [Email anatomy](emails.md#email-anatomy).

## Group 6 — Backend-rendered pages (Mode E, `AuthSettings`)

Only relevant when `AUTH_BACKEND_LINKS=true`. See [Mode E](modes.md#mode-e-backend-only-v0320) for the full flow.

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_BACKEND_LINKS` | `bool` | `false` | `true` = mounts 5 extra HTML endpoints; the email link points at the **backend**, not the frontend. |
| `AUTH_LOGIN_URL` | ``str | None`` | `None` | Login URL on the "go to login" button of success pages. `None` hides the button. |
| `AUTH_ACTIVATION_SUCCESS_TEMPLATE` | `str` | `activation_success.html` | Activation OK HTML page. |
| `AUTH_ACTIVATION_ERROR_TEMPLATE` | `str` | `activation_error.html` | Activation error HTML page. |
| `AUTH_PASSWORD_RESET_FORM_TEMPLATE` | `str` | `password_reset_form.html` | New-password HTML form. |
| `AUTH_PASSWORD_RESET_SUCCESS_TEMPLATE` | `str` | `password_reset_success.html` | Reset OK HTML page. |
| `AUTH_PASSWORD_RESET_ERROR_TEMPLATE` | `str` | `password_reset_error.html` | Reset error HTML page. |

## Group 7 — Language of emails and pages (`AuthSettings`)

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_DEFAULT_LOCALE` | `str` | `pt-BR` | Language of the bundled **emails** and **HTML pages** when no other signal exists. Accepts `pt-BR` and `en-US` (normalized: `PT-BR`, `pt_br`, `ptbr` → `pt-BR`). |
| `AUTH_STAMP_LOCALE_IN_LINK` | `bool` | `True` | Stamps `?lang=<locale>` onto the emailed link, so the page opens in the language of the email that produced it. |

There's a whole section dedicated to this, explained step by step:
[Email and page language (i18n)](i18n.md#email-and-page-language-i18n).

## Group 8 — Token delivery: bearer / cookie / both (`AuthSettings`) *(v0.87.0+)*

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_TOKEN_DELIVERY` | ``"bearer" | "cookie" | "both"`` | `bearer` | How login/refresh return the JWT pair. See [Token delivery](token-delivery.md). |
| `AUTH_COOKIE_SECURE` | `bool` | `true` | Flag cookies as `Secure` (HTTPS only). **Turn off only on plain HTTP** — otherwise the browser drops the cookie. |
| `AUTH_COOKIE_SAMESITE` | ``"lax" | "strict" | "none"`` | `lax` | A cross-site SPA needs `none` (+ `Secure=true`). |
| `AUTH_COOKIE_DOMAIN` | ``str | None`` | `None` | Cookie `Domain`. `None` = exact host. Use `.example.com` to share across subdomains. |
| `AUTH_ACCESS_COOKIE_NAME` | `str` | `access_token` | Access-token cookie name. |
| `AUTH_REFRESH_COOKIE_NAME` | `str` | `refresh_token` | Refresh-token cookie name (scoped to the refresh endpoint path). |

## Group 9 — Closed system (`AuthSettings`) *(v0.272.0+)*

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_SIGNUP_ENABLED` | `bool` | `true` | `false` = `make_auth_router` does not mount `POST /auth/signup`; the route is gone from the application and from the OpenAPI schema. Activation, reset and everything else stay intact. See [Closed system](endpoints.md#closed-system-no-registration-door-v02720). |

## Group 10 — Social login (`AuthSettings` + `OAuthSettings`) *(v0.273.0+)*

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_OAUTH_ENABLED` | `bool` | `false` | `true` mounts the five `/auth/oauth/*` routes (`{provider}/login`, `{provider}/callback`, `{provider}/token`, `accounts` and `accounts/unlink`). Requires a client in `oauth_clients=`, an `oauth_account_model` on the service and a `name` column on the user model — each missing piece raises `RuntimeError` at router construction. |
| `AUTH_OAUTH_STATE_COOKIE_NAME` | `str` | `oauth_state` | Cookie carrying the CSRF `state` between the redirect and the callback. Always `HttpOnly` and always `SameSite=Lax`. |
| `AUTH_OAUTH_STATE_TTL_SECONDS` | `int` | `600` | How long the user has to finish consenting at the provider. |
| `AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL` | `bool` | `false` | `true` attaches a new identity to an existing account whose email matches — **only** when `email_verified is True` and the local account has `email_verified_at`. Off by default because this is the knob that turns a provider's word about an email into control of an account. |
| `AUTH_OAUTH_ALLOW_ACCOUNT_CREATION` | `bool` or empty | empty | Whether the callback may create an account. Empty inherits `AUTH_SIGNUP_ENABLED`, so closing signup closes this door with it. |
| `OAUTH_REDIRECT_BASE_URL` | `str` | `""` | Public origin of the service. The redirect URI is derived from it by `oauth_redirect_uri(provider)`. |
| `OAUTH_GOOGLE_CLIENT_ID` / `OAUTH_GOOGLE_CLIENT_SECRET` | `str` | `""` | Credentials from the Google console. `google_kwargs()` builds the client. |
| `OAUTH_GOOGLE_EXTRA_AUDIENCES` | `list[str]` | `[]` | **This** project's other client ids (Android, iOS). Without them every mobile login through `POST /auth/oauth/google/token` is refused as another application's token. |
| `OAUTH_GITHUB_CLIENT_ID` / `OAUTH_GITHUB_CLIENT_SECRET` | `str` | `""` | Credentials of the GitHub OAuth app. `github_kwargs()` builds the client. |

## Group 11 — One link at a time (`AuthSettings`) *(v0.274.0+)*

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_SINGLE_ACTIVE_TOKEN` | `bool` | `true` | Issuing an account token spends that user's other unused tokens **of the same purpose**, so only the newest activation / reset / email-change link opens the account. `false` restores the pre-v0.274.0 behaviour. See the [migration guide](../../migration.en.md#02740-only-the-newest-link-opens-the-account). |

!!! danger "Without this, the user's correct reaction does not close the window"
    An attacker fires `POST /auth/password-reset/request` at a victim. The
    victim receives a recovery email they never asked for, gets suspicious,
    and **on their own** requests a reset and completes it — exactly the right
    reaction. With several links alive, the attacker's link stays valid until
    `AUTH_PASSWORD_RESET_TTL_SECONDS`, and a token leaked through any side
    channel (a proxy log, a browser extension, a forwarded email, a shared
    device) still resets the password after the incident looked handled.

    The scope is narrow in both directions: same purpose only (requesting a
    reset does not kill a pending email change) and same user only. The old row
    is marked `used_at`, not deleted, so the audit trail stays.

## Group 12 — Deactivated account (`AuthSettings`) *(v0.305.0+)*

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_REVEAL_INACTIVE_ACCOUNT` | `bool` | `false` | `true` = the **right** password on a deactivated account answers `403` `ACCOUNT_INACTIVE` instead of the generic `401`. A wrong password and an unknown email stay `401`. |

By default a deactivated account answers the same `401` as a wrong password, so
the response does not tell which emails have an account. But the owner, not
knowing why they cannot get in, tries "forgot password", resets it, and still
cannot get in. Turned on, whoever **proved the password** is told — and that
enumerates nothing, because whoever has the password already owns the account:

```console
$ curl -s -X POST localhost:8000/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"ana@example.com","password":"strong-pass-12-chars"}'
{"detail":"Esta conta está desativada — reative-a pelo link de ativação","code":"ACCOUNT_INACTIVE","details":{}}
```

(With the default catalog in `register_exception_handlers(app, catalog=default_message_catalog())`,
which negotiates pt-BR here; without a catalog, `detail` is `account is not active`.)

!!! check "The cost is the same in all three branches"
    The password is verified **before** `is_active` is read, on or off. An
    unknown email pays one bcrypt against a throwaway hash. Measured in
    `tests/auth/test_refusal_codes.py`: exactly one verification per refused
    login, in all three branches, with the flag on and off.

!!! note "MFA / TOTP has its own vars"
    When `AUTH_MFA_ENABLED=true`, `AuthSettings` also exposes `AUTH_MFA_ISSUER`, `AUTH_MFA_RECOVERY_CODES_COUNT`, `AUTH_MFA_TOKEN_TTL_SECONDS` and `AUTH_MFA_VERIFY_WINDOW`. They're out of scope for this recipe (signup/activate/login/reset) — covered in the MFA recipe.
