---
description: "Index of the local-account flow (signup, activation, login, reset) with `UserAuthService` and `make_auth_router`; maps each task to its subpage, plus security and recap."
---

# Bundled auth flow (signup / activate / login / reset)

Since v0.31.0 the SDK ships the full local-account lifecycle — email + password signup, link-based activation, JWT-pair login, password reset — via `UserAuthService` + `make_auth_router`. **Endpoints ready to mount** (including `POST /auth/refresh` since v0.65.0), Jinja2 templates bundled, settings flags decide whether the link is emailed or returned in the response body, and five pre-thought modes: production, dev with local SMTP, dev without SMTP, CI and backend-only.

!!! tip "Jump straight to your case"
    The table below maps **what the user wants to do** → the section and
    endpoints. Start at your case; come back to [setup](auth-flow/setup.md#minimum-setup)
    only when you need to wire the pieces.

    | I want to… | Logged in? | Section | Endpoints |
    | --- | --- | --- | --- |
    | Create account + activate by email | — | [Setup](auth-flow/setup.md#minimum-setup) | `signup` → `activate/{token}` |
    | **Test everything without a frontend** (curl, Swagger, mobile app) | — | **[Backend only](auth-flow/backend-only.md)** | `signup` → `activate/{token}` → `login` → protected route |
    | **The activation email never arrived** | ❌ no | **[Re-send the activation](auth-flow/recovery.md#re-send-the-activation)** | `activation/request` → `activate/{token}` |
    | Log in | — | [Endpoints](auth-flow/endpoints.md) | `login` |
    | **I forgot my password** | ❌ no | **[Password recovery](auth-flow/recovery.md#password-recovery)** | `password-reset/request` → `password-reset/confirm` |
    | Change my password | ✅ yes | [Change password logged in](auth-flow/recovery.md#change-your-password-logged-in) | `password-change` |
    | Change my email | ✅ yes | [Change email](auth-flow/email-change.md#change-email-logged-in) | `email-change/request` → `email-change/confirm` |
    | Re-verify my current email | ✅ yes | [Re-verify email](auth-flow/email-change.md#re-verify-the-current-email) | `email-verify/request` → `email-verify/confirm` |
    | **I lost access to my email** | ❌ no | **[Email recovery](auth-flow/email-change.md#email-recovery)** | `email-recovery/request` → `email-change/confirm` |
    | My session expired | — | [Refresh](auth-flow/email-change.md#renewing-the-session-with-the-refresh-token) | `refresh` |

    **The two "I forgot"**: lost password → [Password recovery](auth-flow/recovery.md#password-recovery); lost mailbox → [Email recovery](auth-flow/email-change.md#email-recovery).

## Where each task lives

Use this table to open only the subpage for the task at hand; they are listed in reading order.

| Subpage | What it solves |
| --- | --- |
| [Minimum setup and `UserTokenModel`](auth-flow/setup.md) | Use to wire the flow: extras, the four objects (`AsyncDatabaseManager`, `EmailUtils`, `UserAuthService`, `make_auth_router`) and the concrete `UserTokenModel`. |
| [Endpoints](auth-flow/endpoints.md) | Look up every endpoint with payload and behaviour: closed system, admin-created accounts, extra signup fields and attempt limits. |
| [Backend only: from signup to a protected route](auth-flow/backend-only.md) | Use to test signup → activation → login → protected route without a frontend (curl, Swagger, mobile app) and the same cycle in a test. |
| [Re-send activation and recover a password](auth-flow/recovery.md) | Use when the activation email never arrived, when the user forgot the password or wants to change it while logged in. |
| [Change and recover an email](auth-flow/email-change.md) | Use to change the email while logged in, re-verify the current one, recover the account when the inbox is lost and renew the session with the refresh token. |
| [Settings: environment variables](auth-flow/settings.md) | Look up the flow's environment variables in typed groups: JWT, password policy, email, TTL, URLs, pages, language, token delivery and more. |
| [Your product's error codes](auth-flow/errors.md) | Use to replace the flow's error codes with your product's own via `AuthExceptions`. |
| [Emails: anatomy, brand and templates](auth-flow/emails.md) | Use to understand how link, template and URL fit together, put your brand on the emails and override the Jinja2 templates. |
| [Language of emails, pages and `/docs`](auth-flow/i18n.md) | Use to choose the default language, store the user's preference, translate it yourself and set the language of the route docs. |
| [Five operating modes and local SMTP](auth-flow/modes.md) | Use to pick the mode (production, dev with local SMTP, dev without SMTP, CI, backend-only) and run Mailhog or smtp4dev. |
| [Email disabled and SMTP down](auth-flow/email-failures.md) | Use to know what each route answers with `EMAIL_ENABLED=false` or with SMTP failing, and when to send emails in the background. |
| [Token delivery: bearer, cookie or both](auth-flow/token-delivery.md) | Use to decide whether the JWT pair goes in the body, in a cookie or both. |
| [Get the `current_user` from the request](auth-flow/current-user.md) | Use to inject the authenticated user into your routes, with optional auth (`soft=True`) and guards inside the service. |

## Security

- **Token stored as SHA-256 hash.** Plaintext leaves via email only once; the database can never reproduce the original token. A leak of the `user_tokens` table does **not** enable retroactive activation.
- **One-shot.** `used_at` is stamped on consume; replay rejected with `UnauthorizedException`.
- **TTL-bounded.** `expires_at` computed from `AUTH_ACTIVATION_TTL_SECONDS` / `AUTH_PASSWORD_RESET_TTL_SECONDS`. Expired tokens rejected.
- **Anti-enumeration.** `POST /auth/password-reset/request` always returns HTTP 202 + a generic body, regardless of whether the email exists. `POST /auth/login` raises the same `UnauthorizedException` for wrong-email, wrong-password and inactive account, and all three pay one bcrypt verification (an unknown email is checked against a throwaway hash), so the response time does not separate them either. Measured on this machine (bcrypt cost 12, `UserAuthService.login` called directly, median of N=21): wrong password 153.8 ms, unknown email 153.8 ms; before the fix, 0.5 ms for the unknown email.
- **Password floor enforced twice.** `SignupSchema` validates on input; `UserAuthService` re-validates before hashing — defense in depth in case anyone bypasses the schema.

## Recap

- The bundled flow covers signup, activation, login, password reset and email
  change — and the concrete `UserTokenModel` is the only table your service has
  to ship.
- Password recovery and **email** recovery are different flows: one is for
  someone who forgot the password, the other for someone who lost the inbox.
- The five operating modes exist because "who renders the page" changes per
  project: pure API, backend-rendered, SPA, or a mix.
- `AUTH_TOKEN_DELIVERY` decides whether the JWT pair leaves in the body, in a
  cookie or both — and a cross-site cookie needs `SameSite=none` plus `Secure`.
- Templates and language are overridable, but the defaults work without you
  creating a single file.
- The router prefix and the template URL have to agree: mounting under `/api`
  with a template pointing at the root fails on the user's click, not at deploy
  time.

## Next steps

- **[Idempotency »](idempotency.en.md)** — protect `POST /auth/signup` from retries that would duplicate the row.
- **[MinIO/S3 Storage »](storage.en.md)** — attach avatar / profile picture during signup.
- **[Logging »](logging.en.md)** — `request_id` propagates automatically across every log line emitted during the flow.
- **[Metrics »](metrics.en.md)** — `PrometheusMiddleware` counts `/auth/*` separately with no extra config.
