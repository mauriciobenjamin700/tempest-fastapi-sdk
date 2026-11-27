# Users and secrets

Use to create and manage users (MFA included) and to generate, fill in and rotate the `.env` secrets (`tempest user`, `tempest secrets`).

Part of the [CLI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli.md) recipe.

## Users — `tempest user`

Seed and list users using the project's concrete ``UserModel`` (default ``src.db.models:UserModel``). Bootstraps the first admin without manual SQL.

```bash
# Create a regular user
tempest user create --email ana@example.com --password strong-pass-12 --no-admin

# Create an admin (can log into /admin)
tempest user create --email admin@local --password admin-pass-12 --admin

# Read the password interactively (never lands in shell history)
tempest user create --email admin@local --admin

# Custom model outside the scaffolded layout — MUST be a BaseUserModel subclass
tempest user create --email x@y --password pass-12-chars --model myapp.models.user:UserModel

# Columns YOUR UserModel adds — repeatable, one per column
tempest user create --email ana@example.com --password strong-pass-12 \
    --set display_name=Ana --set locale=pt-BR

# Promote / demote an existing user (toggles is_admin)
tempest user promote --email ana@example.com    # becomes admin
tempest user revoke  --email ana@example.com    # back to a regular account

# Replace an existing user's password (prompts twice) and revoke
# their sessions along with it
tempest user set-password --email ana@example.com

# Refresh-token table outside src.db.models
tempest user set-password --email ana@example.com \
    --refresh-token-model myapp.models:UserRefreshTokenModel

# Replace the password and leave the sessions alive (explicit opt-out)
tempest user set-password --email ana@example.com --keep-sessions

# Replace the password and keep the passkeys (explicit opt-out)
tempest user set-password --email ana@example.com --keep-passkeys

# First privileged account born with MFA + audit event in the same transaction
tempest user create --email staff@example.com --admin --require-mfa \
    --audit-model src.db.models:AuditLogModel --event staff.bootstrapped

# Account that lost the phone and the recovery codes: remove the second
# factor, end the sessions and audit it in the same transaction
tempest user mfa-reset --email staff@example.com \
    --audit-model src.db.models:AuditLogModel

# List
tempest user list                                # everyone
tempest user list --admin                        # admins only
```

!!! info "Accounts with MFA: `--require-mfa`, `set-password` and `mfa-reset`"
    `create --require-mfa` enrolls the second factor **before** the commit:
    it prints the `otpauth://` URI and the secret, asks for a code from the
    Authenticator app (up to 3 tries) and writes only with a valid one —
    without it, it exits with code 1 and no row is written. It needs
    `MFAMixin` on the `UserModel` and the recovery-code table
    (`--recovery-code-model`, default `src.db.models:UserRecoveryCodeModel`);
    the recovery codes are printed once, after the commit. `--audit-model` (a
    `BaseAuditLogModel` with `AuditRequestMixin`) writes `record_event` in the
    same transaction, named by `--event` (default `user.created`); without it,
    the command says on stderr that no event was recorded.

    `set-password` on an account with MFA active (and `AUTH_MFA_ENABLED` on,
    as at login) asks for the code — TOTP or recovery code — and changes the
    password through `UserAuthService.change_password_with_mfa`. Step by step
    in the [MFA recipe](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/mfa.md#bootstrapping-the-first-privileged-account).

    Whoever lost the phone **and** the recovery codes has no code to give:
    `mfa-reset` is the way back — see
    [Recovering an account that lost its second factor](#mfa-reset) below.

!!! tip "A `UserModel` with a required column of its own: use `--set`"
    `create` fills `email`, `hashed_password`, `is_admin` and
    `is_active`. Every **other** column of your `UserModel` goes in via
    `--set <column>=<value>`, validated against the mapped columns and
    converted to the column type (bool, int, float, `Decimal`, UUID,
    ISO-8601 date/time, enum by value, and JSON).

    ```bash
    tempest user create -e newbie@example.com -p 'strong-pass-12' --no-admin
    # error: UserModel requires a value for: display_name.
    #        Pass each one as --set <column>=<value>.
    ```

    A `NOT NULL` column with no default that you did not pass is
    **prompted for** in an interactive terminal (one prompt per column)
    and becomes an exit-code-2 error without a TTY — instead of the raw
    `IntegrityError` the database used to raise. An unknown key also
    exits 2, listing the accepted columns; `--set email=` /
    `--set hashed_password=` / `--set is_admin=` are refused, pointing at
    the flag that owns them (`--email`, `--password`,
    `--admin/--no-admin`).

    An insert the database refuses (a duplicate email, most often) exits
    with code 1 and the database's own message —
    `error: could not insert user: …` — instead of a traceback.
    The insert's values do **not** show up in it: every database the CLI
    opens uses `hide_parameters=True`, so the hash of the password you just
    typed never reaches the terminal or a CI log:

    ```text
    error: could not insert user: (sqlite3.IntegrityError) UNIQUE constraint failed: users.email
    [SQL: INSERT INTO users (email, hashed_password, ...) VALUES (?, ?, ...)]
    [SQL parameters hidden due to hide_parameters=True]
    ```

    A database that was never migrated is caught before the insert, with
    code 1 and nothing written:

    ```text
    error: table(s) users not found in the database. Run `tempest db upgrade` first; nothing was written.
    ```

!!! tip "Without `--admin`/`--no-admin`, create asks"
    When you pass **neither** `--admin` nor `--no-admin` in an
    interactive terminal, `tempest user create` prompts
    `Should this user be an administrator? [y/N]`. Non-interactive runs
    (CI, pipes, scripts) skip the prompt and create a regular user
    (`is_admin=False`) — pass `--admin` explicitly to create an admin
    without a TTY.

`tempest user promote` / `tempest user revoke` find the user by email (case-insensitive) and only flip `is_admin`. When no user matches the email they exit with code 1 and a `no user found` message.

### Changing a password — `tempest user set-password`

The operator's half of the reset flow: the account nobody can mail a link to, the first admin locked out of a fresh environment, the credential rotated after a report. It finds the user by email (case-insensitive), hashes the new plaintext with the model's own `set_password` — the very hash the login endpoint verifies — and exits with code 1 and `no user found` when nobody matches.

```bash
tempest user set-password --email ana@example.com
# Password:
# Confirm:
# Password updated for ana@example.com (id=6f0c...)
# Revoked 2 active refresh token(s).
# Deleted 1 passkey(s).
```

!!! check "Changing the password revokes the sessions — by default"
    Changing the hash does not, on its own, end any session: an access
    token already issued stays valid until it expires, and a DB-backed
    refresh token can still be exchanged for a fresh one — exactly the
    window the reset exists to close. So the revocation is **not a flag
    you have to remember**: the command resolves
    `src.db.models:UserRefreshTokenModel` (the conventional name of the
    opt-in refresh-token table — `tempest new` does **not** scaffold it;
    whoever turns the table on writes the model under that name in
    `src/db/models/`) and revokes the user's refresh tokens **in the same
    transaction** that writes the new hash.

    - Table somewhere else? `--refresh-token-model myapp.models:UserRefreshTokenModel`.
      A spec you typed that does not resolve is an exit-code-2 error — it
      never degrades silently into "revoked nothing".
    - Do not want the revocation? `--keep-sessions`, which is refused
      with code 2 when it comes together with `--refresh-token-model`
      (the two flags contradict each other).
    - A project that never wired the opt-in refresh-token table has
      nothing to revoke, and the command says so on `stderr`:
      `note: no refresh-token model at 'src.db.models:UserRefreshTokenModel', so no session was revoked. Pass --refresh-token-model if yours lives elsewhere.`

    The count is of the sessions **this** run killed — an already
    revoked row is not counted again, so running it twice prints
    `Revoked 0 active refresh token(s).`

!!! check "And it deletes the passkeys — by default too"
    The operator's password change is account recovery, like
    `mfa-reset`: a passkey signs in without the password, and one
    registered by whoever held the account would survive the new hash. So
    the command deletes **every** passkey of the user in the same
    transaction (`UserAuthService.revoke_user_passkeys`), by the same rules
    as the sessions: `--webauthn-credential-model` (default
    `src.db.models:UserWebAuthnCredentialModel`; without a table, it says
    on stderr that no passkey was deleted), `--keep-passkeys` as the
    explicit opt-out, and both together refused with code 2. The owner
    registers the passkey again afterwards.

!!! info "Auditing the rotation: `--audit-model`"
    Like `mfa-reset`, `set-password` records an event when given
    `--audit-model` (a `BaseAuditLogModel` with `AuditRequestMixin`):
    `user.password_set` by default, or the `--event` name, with
    `actor="cli"` and `changes={"password_changed": true,
    "sessions_revoked": N, "passkeys_revoked": M}`, **in the same
    transaction** as the new hash — if the audit write fails, the rotation
    is undone. `--event` without `--audit-model` exits with code 2.

    ```bash
    tempest user set-password --email ana@example.com \
        --audit-model src.db.models:AuditLogModel
    ```

!!! tip "The same thing, from code"
    `await service.revoke_user_sessions(session, user_id=user.id)` is the
    administrative counterpart of `revoke_refresh_token`:
    logout-everywhere addressed by id, for when you do not hold the token
    (a "sign out everywhere" endpoint, a support agent locking a
    compromised account). It returns how many rows it revoked, and `0`
    when the project does not use `refresh_token_model` — stateless JWTs
    carry no row to flip.

    ```python
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk import UserAuthService


    async def force_logout(
        user_id: UUID,
        session: AsyncSession,
        service: UserAuthService,
    ) -> int:
        """Revoke every session of a user. Returns how many fell."""
        killed = await service.revoke_user_sessions(session, user_id=user_id)
        await session.commit()
        return killed
    ```

!!! info "The password policy is your project's, not the CLI's"
    `create` and `set-password` both check the plaintext with
    `check_password_policy` against the three `AUTH_PASSWORD_*` fields of
    the project's `src.core.settings` — and against the `PasswordPolicy`
    defaults (minimum 8 characters, maximum 72 **bytes**) when the
    project does not compose `AuthSettings`. It is the same ruler
    `signup` and the admin panel use, so a password the CLI accepts is
    always one the account owner can log in with.

    The ceiling is measured in **UTF-8 bytes**, the unit bcrypt counts:
    40 accented characters are 80 bytes and exit with code 2 and
    `error: password must be at most 72 bytes.` — before anything is
    written to the database.

``DATABASE_URL`` resolves the same way as ``tempest db`` (env var > settings instance or factory > ``.env`` > ``alembic.ini``).

### Recovering an account that lost its second factor — `tempest user mfa-reset` { #mfa-reset }

An account with MFA active whose owner lost the phone **and** the recovery codes: `set-password` asks for the code, and there is no code to give. `mfa-reset` removes the factor **without proof** — so it asks for confirmation and records who did it.

```bash
tempest user mfa-reset --email staff@example.com \
    --audit-model src.db.models:AuditLogModel
# Remove the second factor of staff@example.com? It will log in with the password alone until it enrolls again. [y/N]: y
# MFA reset for staff@example.com (id=2ced...)
# Revoked 2 active refresh token(s).
# Deleted 1 passkey(s).

# Outside a terminal (CI, a script): --yes is required
tempest user mfa-reset --email staff@example.com --yes \
    --audit-model src.db.models:AuditLogModel --event support.mfa_recovered

# Remove the factor and leave the sessions alive (explicit opt-out)
tempest user mfa-reset --email staff@example.com --yes --keep-sessions
```

- It runs `UserAuthService.mfa_reset`: clears `totp_secret`, `totp_enabled_at` and `totp_last_step` and deletes the recovery codes (`--recovery-code-model`, default `src.db.models:UserRecoveryCodeModel`; without a table, it exits with code 2 naming the flag).
- **It revokes the sessions by default**, in the same transaction, with the same rules as `set-password`: `--refresh-token-model` (default `src.db.models:UserRefreshTokenModel`), `--keep-sessions` as the explicit opt-out, and both together refused with code 2.
- **It deletes the passkeys by default**, in the same transaction, by the same rules: `--webauthn-credential-model` (default `src.db.models:UserWebAuthnCredentialModel`; without a table, it says on stderr that no passkey was deleted), `--keep-passkeys` as the explicit opt-out, and both together refused with code 2.
- `--audit-model` (with `AuditRequestMixin`) writes `record_event` in the **same transaction**, named by `--event` (default `user.mfa_reset`); if the audit write fails, the reset rolls back with it. Without `--audit-model`, the command says on stderr that no event was recorded. `--event` without `--audit-model` exits with code 2.
- Without confirmation nothing changes: outside a terminal, without `--yes`, it exits with code 1; in a terminal, answering `n` exits with 1 too.
- An account **without MFA active** exits with code 2 (`has no MFA active, nothing to reset.`); a `UserModel` without `MFAMixin` does too, before the database is opened. An unknown email exits with 1 and `no user found`.

After the reset the account logs in with the password alone until it enrolls again, and `set-password` stops asking for a code. The MFA recipe walks the path, including the direct call from code: [An account that lost its second factor](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/mfa.md#mfa-reset).

### Inspect, deactivate and delete

`list` answers "who exists". The four below answer the rest.

```bash
# The whole row, including the columns YOUR model adds
tempest user show --email ana@example.com
tempest user show --email ana@example.com --json

# Turn access off (reversible) and drop the sessions with it
tempest user deactivate --email ana@example.com
tempest user activate --email ana@example.com

# That user's sessions (refresh tokens)
tempest user sessions --email ana@example.com
tempest user sessions --email ana@example.com --revoke

# Delete the row (irreversible)
tempest user delete --email ana@example.com --yes
```

`show` **redacts `hashed_password`**: no question this command answers needs
the hash, and printing it puts a credential in terminal scrollback and in CI
logs.

!!! warning "`deactivate` revokes sessions, and that is not optional"
    Flipping `is_active=False` ends nothing on its own: a refresh token
    already issued stays exchangeable, and the account you just turned off
    keeps minting fresh access tokens. So the revocation runs in the **same
    transaction** as the flag — the rule `set-password` already follows.

`sessions` without `--revoke` prints one line per token (id, family, created,
expires, state). A user with no session prints an empty table and exits 0 —
"not logged in anywhere" is an answer, not a failure. The `--revoke` count is
the number of sessions **that run** killed, so a second run prints
`Revoked 0`.

!!! danger "`delete` is irreversible, and takes the tokens with it"
    The scaffolded refresh-token table carries a `ForeignKey` with no
    `ON DELETE CASCADE`, so deleting a user with a live token would fail with
    an integrity error naming a constraint rather than the cause. The command
    removes the tokens in the same transaction. Outside an interactive
    terminal, `--yes` is required. When what you mean is "this person should
    not be able to log in any more", the command is `deactivate`.

!!! note "`deactivate` blocks login, it is not a ban"
    A deactivated account cannot log in, but its holder can reactivate it on
    their own: `POST /auth/activation/request` mails a fresh activation link
    to the account's address, and opening it makes the account active again.
    That is intended — whoever holds the mailbox gets back in. To close the
    door for good, pass `refuses_email_link` to `UserAuthService` with a
    predicate matching that account: activation and reset by e-mail are then
    refused for it (see the [MFA recipe](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/mfa.md#email-link-refusal)).

## Secrets — `tempest secrets`

Four commands, ordered by how much they touch your `.env`:

| Command | Writes to `.env`? | When to use it |
| --- | --- | --- |
| `generate` | never | grab a value to paste into a secret manager |
| `init` | only what is empty or a placeholder | right after `tempest new` |
| `rotate` | always, with a backup | the secret leaked or went stale |
| `vapid` | the Web Push key pair | turn on browser notifications |

### Generate a loose value — `tempest secrets generate`

Touches no file at all: it prints and stops.

```bash
# One secret, bare value
tempest secrets generate

# Three at once
tempest secrets generate --count 3

# Labelled, ready to paste into a .env or a deploy form
tempest secrets generate --keys JWT_SECRET,TOKEN_SECRET

# JSON, to pipe into a secret manager
tempest secrets generate --keys JWT_SECRET --json
```

Output of the third one:

```text
JWT_SECRET=_Ptx1Cq4lUAK4EJX7GAnXMp115d6i92I_rrTDJ3sS59noFLm3N1wTWL-F0OhCkbr
TOKEN_SECRET=qNsIyp8hp4BZll_BzcDS7omceBNB4a4EI9JVWvIQEg_R5VveTY0IuLBiogp0uRBa
```

!!! tip
    `--count` and `--keys` say the same thing (how many secrets come out), so
    passing both is a usage error rather than one of them silently winning.

### Fill a freshly scaffolded `.env` — `tempest secrets init`

`tempest new` writes `JWT_SECRET=change-me-change-me-change-me-32` and an
empty `TOKEN_SECRET=` — exactly the two cases `tempest check-config`
reports as `security.W001` / `security.W004`. `init` replaces only those:

```bash
tempest secrets init
```

```text
set JWT_SECRET (was placeholder)
set TOKEN_SECRET (was empty)
```

A key that already holds a real value is **kept**, so running it again does
nothing:

```text
kept JWT_SECRET (already set)
kept TOKEN_SECRET (already set)
Nothing to do.
```

That is what separates `init` from `rotate`: `init` is safe to run at any
moment — including from a setup script — because it never destroys a secret
in use. Replacing a real value is explicit opt-in, and then it backs the
file up first:

```bash
tempest secrets init --force
```

### Rotate — `tempest secrets rotate`

Generates and rewrites the matching `.env` lines **in place** — backing up
the old file first — and leaves every other line untouched.

```bash
# Rotate JWT_SECRET and TOKEN_SECRET in .env (writes .env.bak)
tempest secrets rotate

# Just print the new values (writes nothing)
tempest secrets rotate --print

# Custom keys and file
tempest secrets rotate --keys JWT_SECRET,SESSION_SECRET --env .env.prod

# More entropy, no backup
tempest secrets rotate --length 64 --no-backup
```

!!! warning
    Rotating `JWT_SECRET` invalidates every token signed with the old value: users are logged out and pending reset/activation links stop working. Rotate during a maintenance window and restart the service to load the new values.

### Web Push keys — `tempest secrets vapid`

Web Push does not use a random string: it uses a P-256 key pair. The browser
subscribes with the public key and the server signs every push with the
private one, so the two values are born together and cannot come out of
`generate`.

```bash
# Writes VAPID_PUBLIC_KEY + VAPID_PRIVATE_KEY into .env
tempest secrets vapid

# With the contact carried in the VAPID JWT's `sub` claim
tempest secrets vapid --subject mailto:ops@example.com

# Print only
tempest secrets vapid --print
```

These are exactly the fields `WebPushSettings` reads, in the shape
`pywebpush` reads back: the private key is the 32-byte scalar and the public
key is the 65-byte uncompressed point, both base64url without padding.

!!! danger
    Replacing a pair that already exists **invalidates every active
    subscription**: a browser that subscribed with the old public key will not
    accept a push signed with the new private one, and each client has to
    subscribe again. That is why the command refuses to overwrite without
    `--force`.

### Recap

- `generate` prints, `init` fills what is missing, `rotate` replaces
  everything, `vapid` mints the Web Push pair.
- `init` is idempotent and never loses a secret in use — it is the command
  for a project's first day.
- `rotate` and `vapid --force` are destructive in different ways: one logs
  users out, the other drops push subscriptions.
- Every file these commands write ends up `0600`.
