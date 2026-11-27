# Change and recover an email

Use to change the email while logged in, re-verify the current one, recover the account when the inbox is lost and renew the session with the refresh token.

Part of the [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md) recipe.

The mirror of the password flow, but for the account **email** (v0.92.0+).
Three distinct cases — start at yours.

## Change email (logged in)

The base case: the user **is logged in** and wants to change their own
email. They confirm `current_password` and supply `new_email`; a
confirmation link goes to the **new** address. The email only changes
once that link is confirmed.

```bash
# Step 1 — request the change (authenticated). Always 202.
curl -X POST localhost:8000/auth/email-change/request \
  -H "Authorization: Bearer eyJhbGciOi…" \
  -H "Content-Type: application/json" \
  -d '{"new_email": "new@example.com", "current_password": "current-pass"}'

# Step 2 — confirm (the front reads the token from the link sent to the new email).
curl -X POST localhost:8000/auth/email-change/confirm \
  -H "Content-Type: application/json" \
  -d '{"token": "abc123…"}'
```

On confirmation, if `AUTH_EMAIL_CHANGE_NOTIFY_OLD=True` (default) a
**security notice goes to the old address** — the banks/Google pattern,
so a hijacked account still alerts its owner. Confirming also **revokes
the user's sessions** (refresh tokens and the pages' cookies), like the
password change — `confirm_email_change(..., revoke_sessions=False)` is the
explicit opt-out on the service. `email_verified_at` now applies to the new
address: stamped when the link reached it only by mail, cleared when it came
back in the response.

!!! check "Errors"
    - Wrong current password → **401**.
    - `new_email` already in use (at request **or** confirm — a race) → **409**.
    - Invalid / expired / used token → **401** with `code: "INVALID_TOKEN"`.

## Re-verify the current email

Re-sends a verification link to the **current address** — useful when the
activation email was lost. Authenticated, changes nothing; confirming the
link marks the account active.

```bash
curl -X POST localhost:8000/auth/email-verify/request \
  -H "Authorization: Bearer eyJhbGciOi…"
# then: POST /auth/email-verify/confirm  {"token": "…"}
```

It is also how an account activated from the admin site, or created before the
`email_verified_at` column existed, proves its address.

## What counts as a verified email

`BaseUserModel` records **when** the account holder proved they read the
current address in `email_verified_at` (`null` when they never did), and
`GET /auth/me` returns the column. `is_active` cannot answer that: an account
becomes active from the admin site, through `tempest user activate`, with
`AUTH_AUTO_ACTIVATE=true` or through a link returned in the response — and none
of those paths went through the mailbox.

The SDK stamps the column only when confirming an activation or verification
link that was **mailed to that address** (with
`AUTH_RETURN_TOKEN_IN_RESPONSE=false`, `AUTH_AUTO_ACTIVATE=false` and
`EmailUtils` wired), when confirming an email change under the same
conditions, and when creating an account through [social login](../oauth.md) with
`email_verified is True`. Changing `email` through any other ORM path (admin,
script) clears the column; a bulk `UPDATE` bypasses the ORM and must clear it
itself. Today the reader of the column is social login's link by email
(`AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL`).

## Email recovery

The "I forgot / lost access to my email". An **unauthenticated** and
**opt-in** endpoint — only mounted with `AUTH_EMAIL_RECOVERY_ENABLED=True`.
The user proves identity with their **password** (and an **MFA code** when
TOTP is enrolled) and supplies the new address; the confirmation link goes
to the **new** email and reuses the same `email-change/confirm`.

```bash
# Only exists if AUTH_EMAIL_RECOVERY_ENABLED=True. Always 202 (anti-enumeration).
curl -X POST localhost:8000/auth/email-recovery/request \
  -H "Content-Type: application/json" \
  -d '{
        "email": "old@example.com",
        "new_email": "new@example.com",
        "current_password": "current-pass",
        "mfa_code": "123456"
      }'
# then: POST /auth/email-change/confirm  {"token": "…"}  (link went to the new email)
```

!!! danger "Recovery is sensitive — enable it deliberately"
    This endpoint lets whoever has the **password** move the account to
    another email **without** accessing the old mailbox — an account-takeover
    vector if the password leaked. Rules:

    - **Opt-in**: off until `AUTH_EMAIL_RECOVERY_ENABLED=True`.
    - Always a generic **202** for every soft failure (unknown email,
      wrong password, missing/invalid `mfa_code`) — no account enumeration.
    - When TOTP is enrolled, a valid `mfa_code` is required.
    - It spends the **same budgets** as `/auth/login` (per email and, when
      configured, per IP) and `/auth/mfa/verify` (per account): once they
      are spent, not even the right password or code issues a link — the
      answer stays the generic `202`.
    - Keep `AUTH_EMAIL_CHANGE_NOTIFY_OLD=True` so the old email is always
      alerted of the change.

!!! info "Where the new email is stored"
    The pending address travels in the token's `payload` column
    (`EMAIL_CHANGE`), not on the `UserModel`. Confirmation re-reads the
    `payload`, re-checks the address is still free (a request→confirm race
    → **409**) and only then writes it. See the 0.92.0 migration note.

## Renewing the session with the refresh token

The `access_token` is short-lived by design (`JWT_ACCESS_TTL_SECONDS`,
1 h default). When it expires, **don't force the user to log in again** —
exchange the `refresh_token` (long-lived, 7 days default) for a fresh
pair at `POST /auth/refresh`:

```bash
curl -X POST localhost:8000/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "eyJhbGciOi…"}'
```

```json
{
  "user_id": "7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab",
  "access_token": "eyJhbGciOi…(new)",
  "refresh_token": "eyJhbGciOi…(new)",
  "mfa_required": false,
  "mfa_token": null
}
```

The endpoint decodes the token, requires it to actually carry the
`refresh` claim (a replayed **access** token is rejected with **401**),
resolves the `sub` to an **active** user and mints a new pair.

!!! warning "Both tokens rotate"
    The response carries a **new** `refresh_token`. Persist that one and
    discard the token you sent. In **stateless** mode (the default) the
    SDK issues JWTs, so the old pair is not revoked — it stays valid
    until its own `exp`.

!!! tip "Need real revocation? The SDK already ships it"
    Don't write your own refresh-token table — since **v0.66.0** the SDK
    offers **opaque DB-backed** refresh tokens with single-use rotation,
    **reuse detection** (a stolen token revokes the whole family) and
    `POST /auth/logout`. It is opt-in: pass a `refresh_token_model` to
    `UserAuthService`. See the
    [Refresh tokens (rotation/revocation)](../refresh-tokens.md) recipe.

!!! tip "When the refresh token expires too"
    Then no renewal is possible — the **401** is final and the client
    falls back to `POST /auth/login` with email + password.

No frontend? The service method behind the endpoint is public —
`await service.refresh_tokens(session, refresh_token=...)` returns
`(user, access_token, refresh_token)`.
