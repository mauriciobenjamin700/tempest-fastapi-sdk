# Re-send activation and recover a password

Use when the activation email never arrived, when the user forgot the password or wants to change it while logged in.

Part of the [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md) recipe.

## Re-send the activation

The activation email goes out **once**, at `signup`. When it does not
arrive — a spam filter, a bounced relay, a tab closed before the click —
the account has no way forward:

- signing up again answers **409**, the email is already on file;
- logging in answers **401**, because `is_active` is still `False`;
- `email-verify/request` exists for exactly this case, but it requires a
  **bearer token** — which only a successful login hands out.

That is a dead end nothing but database access could open.
`POST /auth/activation/request` is the way out, and it asks for **no
credentials at all**, by necessity:

```bash
curl -X POST localhost:8000/auth/activation/request \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@example.com"}'
```

```json
{
  "message": "If the email matches a pending account, a link was sent.",
  "activation_url": "http://localhost:3000/activate?token=..."
}
```

`activation_url` shows up only when you deliver the link yourself —
`AUTH_RETURN_TOKEN_IN_RESPONSE=True`, or no `EmailUtils` wired. In
production it comes back `null` and the link travels by email, in whatever
language [i18n](i18n.md#email-and-page-language-i18n) resolves.

!!! info "Always 202, always the same sentence"
    An unknown address, an **already active** account and a real re-send
    answer with the same status and the same body. It is the same
    anti-enumeration care as `password-reset/request`: probing the route
    tells nobody which emails have an account here.

!!! warning "An active account is not emailed"
    Re-issuing for someone who already activated would send a link that
    authorizes nothing — and would tell a stranger that the address has an
    account. So the path stops there, quietly.

The new token follows the original's rules: it lives for
`AUTH_ACTIVATION_TTL_SECONDS` and, with `AUTH_SINGLE_ACTIVE_TOKEN=True`
(the default), the previous request is burned — only the newest link works.

---

## Password recovery

The classic "I forgot my password". The user is **not logged in** and
proves identity with a **single-use token** delivered by email. Two steps.

### Base case (SPA / JSON)

**Step 1 — request the link.** The user types their email; the backend
always answers **202** with the same generic message (it never leaks
whether the email exists):

```bash
curl -X POST localhost:8000/auth/password-reset/request \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@example.com"}'
```

```json
{"message": "If the email matches an account, a reset link was sent.", "reset_url": null}
```

**Step 2 — set the new password.** The user opens the emailed link; the
front-end reads the `token` from the URL and sends it with the new
password. On success the password is replaced **and the user is logged
in** (a JWT pair comes back):

```bash
curl -X POST localhost:8000/auth/password-reset/confirm \
  -H "Content-Type: application/json" \
  -d '{"token": "abc123…", "new_password": "new-strong-pass-12"}'
```

```json
{
  "user_id": "7d8e4d5a-…",
  "access_token": "eyJhbGciOi…",
  "refresh_token": "eyJhbGciOi…",
  "mfa_required": false,
  "mfa_token": null
}
```

!!! warning "An account with MFA ends the reset at `/auth/mfa/verify`"
    When the account has TOTP enrolled (and `AUTH_MFA_ENABLED=True`), the
    `confirm` does **not** return the pair — it returns `mfa_required=true`
    + `mfa_token`, exactly like `/login`. That is what keeps a compromised
    mailbox from skipping the second factor: the password is replaced, but
    the session is only born after the code. Finish the login by sending
    the `mfa_token` and the TOTP code:

    ```bash
    curl -X POST localhost:8000/auth/mfa/verify \
      -H "Content-Type: application/json" \
      -d '{"mfa_token": "eyJ…", "code": "123456"}'
    ```

    The pair that comes back carries `amr == ["email", "otp"]`; without MFA,
    the pair from `confirm` itself carries `["email"]` *(v0.314.0+)*. See the
    [MFA recipe](../mfa.md).

!!! note "The reset drops sessions already open"
    Rotating a credential ends what was opened with the old one: changing
    the password this way **revokes** the refresh tokens that already
    existed, in the same transaction that writes the hash — a session
    opened before the reset can no longer exchange its refresh token.
    (Access tokens already issued live until their short TTL.) This is
    the same default as `/auth/mfa/confirm` and `tempest user
    set-password`; a caller that passes `revoke_sessions=False` to the
    service gives that up on purpose.

!!! note "The reset also deletes the passkeys"
    With [WebAuthn](../webauthn.md) mounted, the reset deletes **every**
    passkey of the user in the same transaction: a passkey signs in without
    the password, and one registered by whoever held the account would keep
    signing in after the recovery. The owner's own go too, and they register
    again. `confirm_password_reset(..., revoke_passkeys=False)` is the
    explicit opt-out. See
    [Password and MFA resets delete the passkeys](../webauthn.md#password-and-mfa-resets-delete-the-passkeys).

!!! check "Errors you'll see"
    - Unknown / already-used / expired token → **401** with `code: "INVALID_TOKEN"` and a `detail` naming the reason (unknown token: `token not recognized`).
    - A `token` shorter than 16 characters → **422** from FastAPI itself (`string_too_short`), before it reaches the service.
    - `new_password` violates `AUTH_PASSWORD_MIN_LENGTH` / complexity → **422**.
    - `request` **never** errors on a missing email — always **202**.

!!! note "Dev mode: link in the body (no SMTP)"
    Without the `[email]` extra, or with `AUTH_RETURN_TOKEN_IN_RESPONSE=True`,
    the `reset_url` comes back **in the `request` body** instead of by
    email — you can drive the whole flow with no inbox. In production it
    stays `null`: under `ENV=production` (`EnvironmentSettings` composed)
    the flag refuses to boot, and a `UserAuthService` without `email=`
    answers **503** `INTEGRATION_DISABLED` to every flow that would carry
    a link — the link never goes back to whoever only typed the address.

!!! tip "Accounts that do not use emailed links *(v0.316.0+)*"
    `UserAuthService(refuses_email_link=...)` closes this flow (and the
    activation one) for accounts chosen by a predicate — an operator, a
    staff member. The `request` answers the same `202` and sends no email;
    confirmation returns **403** `EMAIL_LINK_FORBIDDEN` without changing the
    password; and `signup` creates the account inactive without persisting
    or sending an activation link, with the same response as an ordinary
    pending signup. See the [MFA recipe](../mfa.md#email-link-refusal).

??? note "No front-end? Backend-rendered HTML pages (Mode E)"
    With `AUTH_BACKEND_LINKS=True` the SDK mounts **GET/POST**
    `/auth/password-reset/{token}`: the emailed link points at the
    backend, which renders an HTML form, validates it and shows a
    success/error page — no front-end route at all. See
    [Five operating modes](modes.md#five-operating-modes) (Mode E).

### Change your password (logged in)

Different from a reset: here the user **remembers** their password and
**is logged in**. No token, no email — they send the `access_token` in
the header and re-confirm their `current_password`:

```bash
curl -X POST localhost:8000/auth/password-change \
  -H "Authorization: Bearer eyJhbGciOi…" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "current-pass", "new_password": "new-strong-pass-12"}'
```

It **revokes the user's sessions** in the same transaction that writes
the new password, like the reset: a refresh token issued before the
change — the caller's included — can no longer be exchanged, and the
cookie session of the [auth pages](../auth-pages.md) ends too. So the device
that changed the password stays signed in, the response is **200** with a
**fresh pair** (through 0.335.0 it was `204` with no body — see the
[migration guide](../../migration.md)), issued after the revocation — it is the only
refresh token left:

```json
{
  "user_id": "7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab",
  "access_token": "eyJhbGciOi…",
  "refresh_token": "Zm9vYmFy…",
  "mfa_required": false,
  "mfa_token": null
}
```

Store both and drop the old ones. A caller that authenticated with the
access cookie gets the pair in cookies, and the body carries only the
`user_id`. Wrong `current_password` → **401**, no pair.

The rule: the route **never** returns a stronger session than `/auth/login`
would for the same proofs. The current password proves the first factor
only, so on an account **with MFA**:

| Body | What happens |
| --- | --- |
| no `code` | The password changes and the sessions end; the response is the login challenge (`mfa_required: true`, `mfa_token`), with no pair. Finish at `POST /auth/mfa/verify` as after login |
| valid `code` (TOTP or recovery code) | The code is spent before the change (the same TOTP step does not work twice); the password changes and a pair with `amr == ["pwd", "otp"]` comes back |
| wrong `code` | **401**: nothing changes, neither the password nor the sessions |

```bash
curl -X POST localhost:8000/auth/password-change \
  -H "Authorization: Bearer eyJhbGciOi…" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "current-pass", "new_password": "new-strong-pass-12", "code": "123456"}'
```

The current access token's `amr` is **not** copied into the new pair: on an
account without MFA the pair carries `["pwd"]` (or the token's methods that
are not a second factor), and the second factor only shows up when a `code`
was proved in this request. Calling the
service directly, `change_password(..., revoke_sessions=False)` is the
explicit opt-out. The passkeys stay: the caller just proved the current
password, so this is not account recovery.

!!! tip "Reset vs change — which is which?"
    - **Forgot** the password, not logged in → **[Password recovery](#password-recovery)** (`password-reset/request` → `confirm`, prove with an emailed token).
    - **Remembers** the password, logged in → **change password** (`password-change`, prove with the current password in the header).
