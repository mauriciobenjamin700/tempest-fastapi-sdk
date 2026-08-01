# Migration guide

Breaking-change walkthroughs grouped by minor release. Stick to the version that matches what you're upgrading **from**. The release sections are listed newest-first, so on a multi-version jump read and apply them bottom-up.

## 0.173.0 — a token only works where it was meant to, and caches stop being shared

Three security fixes change default behavior. None requires a code change, but check whether you were relying on the old behavior.

### Refresh and MFA-pending tokens no longer authorize a route

`make_bearer_token_dependency`, `make_jwt_user_dependency`, `make_role_dependency`, `make_permission_dependency` and `UserAuthService.current_user_dependency()` now accept **only** `access`-type tokens.

Before, the three JWTs `UserAuthService` mints with one secret verified identically, so the refresh token and the step-one `mfa_token` worked as a bearer on any authenticated route — the second factor was bypassable with just the password.

You are affected if you **deliberately** sent a refresh token to a regular route:

```python
from tempest_fastapi_sdk import REFRESH_TOKEN_TYPE, make_bearer_token_dependency

# Take that type again, on that one route:
require_refresh = make_bearer_token_dependency(tokens, accepted_typ=(REFRESH_TOKEN_TYPE,))
```

A token hand-signed with `JWTUtils.encode()` and carrying **no** `typ` is still accepted — the upgrade does not log live sessions out. Only the markers the SDK itself stamped (`refresh: True`, `purpose: "mfa_pending"`) are now rejected as access.

### `ResponseCacheMiddleware`: `private` by default, credentials skip the store

Two defaults changed:

- The emitted `Cache-Control` went from `public, max-age=N` to `private, max-age=N`. If you served genuinely shared content and relied on CDN caching, declare it again: `cache_control="public, max-age=N"`.
- A request with `Authorization` or `Cookie` neither reads nor writes the shared store (`ETag`/`304` still apply). To get caching back on an authenticated route, pass `cache_credentialed=True` — the credential joins the key, so each caller gets its own entry.

The `X-Cache` header now only appears when a `store=` is configured; it used to report `MISS` even in ETag-only mode.

### `IdempotencyMiddleware`: key scoped to the caller

The key went from `(method, path, key)` to `(caller, method, path, key)`, the caller being a digest of `Authorization`/`Cookie`. Reusing someone else's key no longer returns their response.

If your client swaps credentials between the original request and the retry (a token rotation mid-backoff), the retry no longer hits the earlier entry. Point identity at something stable there:

```python
app.add_middleware(
    IdempotencyMiddleware,
    store=store,
    principal_resolver=lambda request: request.headers.get("x-api-key-id", ""),
)
```

Also changed: `5xx` is no longer cached (`cache_server_errors=True` restores it), `Set-Cookie` is left out of the stored copy, and concurrent requests sharing a key are serialized within a process.

## 0.138.1 — `BaseAppSettings` must be the **last** base

0.138.1 made **every settings mixin inherit `BaseAppSettings`** (they used to extend raw `pydantic_settings.BaseSettings`). That fixes `.env` silently not loading when a mixin was listed before the base — the canonical `model_config` is now materialized onto every mixin regardless of ordering.

In exchange, base ordering stopped being style and became a **hard rule**: because the mixins subclass `BaseAppSettings`, Python's C3 linearization forbids the base from preceding its own subclass.

```python
# docs-guard: skip — the first two examples are the mistake this section describes
# ❌ fails at import time
class Settings(DatabaseSettings, BaseAppSettings, RedisSettings): ...

# ❌ also fails
class Settings(BaseAppSettings, DatabaseSettings): ...

# ✅ BaseAppSettings last
class Settings(DatabaseSettings, RedisSettings, BaseAppSettings): ...
```

Before 0.159.1 the symptom was pydantic's raw `TypeError`, which never names the fix:

```text
TypeError: Cannot create a consistent method resolution order (MRO) for bases BaseAppSettings, RedisSettings
```

and `mypy` (with the pydantic plugin) reported twice on the same line, the second one misleading — it suggests a metaclass conflict when the cause is just the position of one base:

```text
settings.py:4: error: Cannot determine consistent method resolution order (MRO) for "Settings"  [misc]
settings.py:4: error: Metaclass conflict: the metaclass of a derived class must be a (non-strict) subclass of the metaclasses of all its bases  [metaclass]
```

As of 0.159.1, `BaseAppSettings` uses the [`AppSettingsMeta`](reference.md) metaclass, which pre-checks base ordering and swaps the message for an instruction:

```text
TypeError: Settings: BaseAppSettings must be the LAST base — RedisSettings already subclasses it, so listing BaseAppSettings before it is an invalid method resolution order (MRO). Move BaseAppSettings to the end of the base list: class Settings(RedisSettings, BaseAppSettings).
```

### Check

```bash
# look for a Settings whose BaseAppSettings is not the last base
grep -rn "class Settings(" -A 12 src/core/settings.py
```

- Move `BaseAppSettings` to the **last** position in the base list.
- Ordering **among the mixins** stays free — only the base's position matters.
- No env var, field or value change: this is purely inheritance order.

## 0.92.0 — `payload` column on the user token

0.92.0 adds the **email change / re-verification / recovery** flow. To carry the pending email until confirmation, `BaseUserTokenModel` gained a new column:

```python
payload: Mapped[str | None] = mapped_column(String(320), nullable=True, default=None)
```

Since your `user_tokens` table inherits from `BaseUserTokenModel`, the column shows up on the model automatically — but the database needs a **migration**. It is additive and safe (nullable column, no required default):

```bash
# generate and apply
tempest db revision -m "add payload to user_tokens"
tempest db upgrade
```

Or by hand:

```sql
ALTER TABLE user_tokens ADD COLUMN payload VARCHAR(320) NULL;
```

!!! info "That's it"
    No renames, no default backfill. Existing flows (activation, password reset) keep writing `payload = NULL`. The new email flow is fully opt-in — recovery (`POST /auth/email-recovery/request`) is only mounted with `AUTH_EMAIL_RECOVERY_ENABLED=True`.

### Verify

- Run the migration before deploying 0.92.0 (the column must exist).
- If you hand-write `src/db/models/user_token.py` instead of using `make_user_token_model`, the column comes from the abstract base — no need to redeclare, just migrate.

## 0.63.0 — authenticated user loaded on the request session

Before 0.63.0, `UserAuthService.current_user_dependency()` loaded the authenticated user through `load_user`, which opened its **own** session (via `db.get_session_context()`) and closed it on exit. The `UserModel` handed to the route was therefore **detached**: mutating it and calling `commit`/`refresh` on the request session (the one your repositories use) raised
`InvalidRequestError: Instance is not persistent within this Session`.

From 0.63.0 the dependency loads the user on the **request session** (`db.session_dependency` by default) via `get_user(subject, session)`. The user is attached to the same session repositories use, so lazy-relationship reads and writes work without re-attaching anything.

!!! warning "Compatibility"
    The auth dependency and your repositories must share the **same** session callable for FastAPI's sub-dependency cache to deduplicate them. The recommended pattern is already covered:

    ```python
    # resources.py
    get_session = db.session_dependency          # one object, reused
    ```

    If you wrap the session in your own provider (`async def get_session(): ...`), pass it explicitly, otherwise the dependency opens a second session and the user is detached again:

    ```python
    get_current_user = auth.current_user_dependency(session_dependency=get_session)
    ```

!!! info "Extra safety net"
    `BaseRepository.resolve()` now re-attaches detached instances via `session.merge()`. Even if some flow still hands in a detached user, `resolve` brings it back into the active session instead of breaking — so services that worked around this (re-fetch by id before mutating) can drop the workaround.

### Verify

- Drop any "re-fetch by id before mutating the authenticated user" workaround — it's no longer needed.
- A single-argument `user_loader` passed to `make_jwt_user_dependency` keeps working. To share the request session, pass `session_dependency=` and use a two-argument loader `(subject, session)`.

## 0.8.0 — `ServerSettings` rename

0.8.0 renames every field on `ServerSettings`, extracts log fields to a new `LogSettings` mixin, and adds eleven other primitives. The renames are the only **breaking** changes — every new primitive is opt-in.

#### 1. Rename env vars

| Old | New | Mixin |
| --- | --- | --- |
| `HOST` | `SERVER_HOST` | `ServerSettings` |
| `PORT` | `SERVER_PORT` | `ServerSettings` |
| `DEBUG` | `SERVER_DEBUG` | `ServerSettings` |
| *(new)* | `SERVER_RELOAD` | `ServerSettings` |
| `LOG_LEVEL` | `LOG_LEVEL` | **moved to** `LogSettings` |
| `LOG_JSON` | `LOG_JSON` | **moved to** `LogSettings` |

Mechanical `sed` on every `.env` / `docker-compose.yml` / deployment manifest:

```bash
sed -i \
  -e 's/^HOST=/SERVER_HOST=/' \
  -e 's/^PORT=/SERVER_PORT=/' \
  -e 's/^DEBUG=/SERVER_DEBUG=/' \
  .env .env.example .env.test
```

`LOG_LEVEL` and `LOG_JSON` keep their names — only the mixin moves.

#### 2. Rename code references

```bash
# `settings.HOST` → `settings.SERVER_HOST`, same for PORT/DEBUG
grep -rn "settings\.\(HOST\|PORT\|DEBUG\)\b" src/ tests/
```

Replace each match with the `SERVER_*` form. If a service was using the
old `settings.DEBUG` flag for application-level debug behavior, switch
to `settings.SERVER_DEBUG`; if it was only being read for uvicorn
auto-reload, switch to `settings.SERVER_RELOAD`.

#### 3. Mix `LogSettings` into the project `Settings`

```diff
 from tempest_fastapi_sdk import (
     BaseAppSettings,
     CORSSettings,
     DatabaseSettings,
     JWTSettings,
+    LogSettings,
     RabbitMQSettings,
     RedisSettings,
     ServerSettings,
 )


 class Settings(
     ServerSettings,
+    LogSettings,
     DatabaseSettings,
     RedisSettings,
     RabbitMQSettings,
     JWTSettings,
     CORSSettings,
     BaseAppSettings,
 ):
     ...
```

Skip this step if the service never read `settings.LOG_LEVEL` /
`settings.LOG_JSON` — `configure_logging` accepts the values as
keyword arguments directly.

#### 4. (Optional) Adopt the new primitives

Pick what fits. None of these are required.

- Replace the hand-written `src/server.py` `uvicorn.run(...)` with
  [`run_server(...)`](recipes/http.md#programmatic-server-entry-point).
- Replace the hand-written `get_current_user` with
  [`make_jwt_user_dependency(tokens, load_user)`](recipes/http.md#jwt-bearer-current-user-role-dependencies).
- Move `SMTP_*` / `UPLOAD_*` / `TOKEN_SECRET` / `VAPID_*` /
  `TASKIQ_*` fields out of the project's `Settings` and onto the
  matching SDK mixin ([Settings mixins composition](recipes/http.md#settings-mixins-composition)).
- Adopt the
  [`Outbox`](recipes/outbox.md) if
  you already write side-effects from the same transaction as your
  domain rows.

#### 5. Verify

```bash
uv sync                      # picks up new pyproject deps
uv run pytest -q             # full suite
uv run ruff check src tests  # confirm no `HOST`/`PORT`/`DEBUG` references slipped
```

If `pytest` fails with a Pydantic `ValidationError` referencing
`HOST` / `PORT` / `DEBUG`, an env var was not renamed (look at the
process environment or `.env`).

---

