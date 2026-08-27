# Migration guide

Breaking-change walkthroughs grouped by minor release. Stick to the version that matches what you're upgrading **from**. The release sections are listed newest-first, so on a multi-version jump read and apply them bottom-up.

## 0.257.0 — the agent fakes rename the parameter to `tools`

Breaks callers passing `chat_with_tools(..., specs=[...])` by keyword.

### What changes

`ScriptedBackend` and `FailingBackend` exist to fake the `ChatBackend` /
`ToolCallingBackend` protocols — and satisfied neither under mypy. The protocol
names the parameter `tools` and accepts `**kwargs`; the fakes named it `specs`
and accepted nothing else. A protocol member is only implemented by a signature
with the **same parameter name**, so the opening line of the testing recipe was
a type error:

```python
from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies

agent = Agent(ScriptedBackend([replies("ok")]))
# up to v0.256.0:
# Argument 1 to "Agent" has incompatible type "ScriptedBackend";
# expected "ChatBackend | ToolCallingBackend"  [arg-type]
```

### What to do

Nothing, if you call positionally — which is what `Agent` does internally and
what the recipe shows. If your test calls the fake directly, by keyword:

```diff
-decision = await backend.chat_with_tools(messages, specs=specs)
+decision = await backend.chat_with_tools(messages, tools=specs)
```

The `specs_seen` attribute — where the fake records the tool names offered each
turn — did **not** change name.

### What started compiling

Three annotations that rejected the argument the docs themselves told you to
pass:

- `EventStream.response(on_disconnect=task.cancel)`, because `Task.cancel`
  returns `bool` and the annotation asked for `None`;
- `RedisIdempotencyStore(Redis.from_url(...))`, `RedisResponseCacheStore(...)`
  and `RedisWebAuthnChallengeStore(...)`, because the protocols demanded the
  parameter name `key`/`name` and a `Coroutine` return, while redis-py returns
  an `Awaitable`;
- `require_authenticated(identity)` with a `FirebaseIdentity`, because the
  `TypeVar` was bound to `BaseUserModel`.

None of them changes runtime — they only stop demanding a workaround
(`# type: ignore`, `cast`) from anyone running a type checker.

## 0.256.0 — the `RateLimitMiddleware` 429 becomes JSON

Breaks clients that read the 429 body as text.

### What changes

Up to v0.255.0 the middleware answered `text/plain` with the raw `error_message`:

```text
HTTP/1.1 429 Too Many Requests
content-type: text/plain; charset=utf-8

Too many requests
```

It now answers the same envelope `register_exception_handlers` writes in every handler:

```text
HTTP/1.1 429 Too Many Requests
content-type: application/json
retry-after: 60

{"detail": "Too many requests",
 "code": "TOO_MANY_REQUESTS",
 "details": {"retry_after_seconds": 60, "limit": 15}}
```

The reason is a contradiction inside the SDK itself: `error_responses()` always pointed 429 at `ErrorResponseSchema`, so a client generated from the OpenAPI schema broke deserializing the text -- and anyone adopting `register_exception_handlers` alongside the middleware ended up with two error shapes in one API.

### What to do

- **A client branching on `status === 429`:** nothing. The status and `Retry-After` are unchanged.
- **A client reading the body as text:** read JSON instead, using `detail` to display and `code` to branch.

```typescript
// before
const message = await response.text();

// after
const { detail, code } = await response.json();
```

- **A service that rewrote the response** (a middleware subclass turning the text into an envelope) can delete the workaround: `error_message` plus the new `error_code` cover it.

### If you need the text back

There is no flag for it. The old body was incompatible with the schema the route itself documents, and keeping both shapes would keep the defect behind an option. A service that genuinely needs another format can subclass the middleware and override the response, the way it did before.

## 0.252.0 — SQLite `:memory:` gets one connection per session

No API break. It changes the connection topology of a `:memory:` database, so read this before upgrading if you rely on the old behaviour.

### What changes

`AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")` now builds the engine over a **shared-cache** in-memory database (`file:<unique-name>?mode=memory&cache=shared&uri=true`) with a normal pool, instead of letting SQLAlchemy pick `StaticPool` with a single connection. The manager holds one connection open for its lifetime, because a shared-cache database dies with its last connection.

This fixes the error introduced in v0.200.0, when the explicit `BEGIN` started being emitted for every SQLite engine:

```text
sqlite3.OperationalError: cannot start a transaction within a transaction
[SQL: BEGIN]
```

Two overlapping sessions work again on `:memory:`, and `RELEASE SAVEPOINT` still is not a commit in disguise.

### If you rely on a single connection

Pass the pool explicitly — a pool the caller names is never overridden:

```python
from sqlalchemy.pool import StaticPool
from tempest_fastapi_sdk import AsyncDatabaseManager

db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
```

That restores the previous topology, including its failure on overlapping sessions.

### If you swapped `:memory:` for a temp file as a workaround

You can go back to `:memory:`. The workaround keeps working, so there is no rush.

## 0.251.0 — the Mercado Pago webhook signature now follows the provider's algorithm

Breaks anyone passing `manifest_template=` or unpacking the return of
`parse_signature_header`. If you only call `verify_signature`, **nothing in
your code changes** — it simply starts verifying deliveries it used to reject.

### The manifest omits absent pairs, so it is no longer a template

The previous implementation rendered
`"id:{data_id};request-id:{request_id};ts:{ts};"`. Mercado Pago's official
validator (`mercadopago/sdk-nodejs`, `src/utils/webhook/index.ts`, commit
`99857f33`) **omits** the pair whose value is absent. A delivery without
`data.id` signs `request-id:...;ts:...;`, while the fixed template signed
`id:;request-id:...;ts:...;` — a different hash, and verification that always
failed.

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import build_manifest

# before: DEFAULT_MANIFEST_TEMPLATE + str.format
# now: the rule, exported
build_manifest(data_id="", request_id="req-1", timestamp="1771891200")
# "request-id:req-1;ts:1771891200;"
```

`DEFAULT_MANIFEST_TEMPLATE` and the `manifest_template=` parameter were
**removed**: they existed because the algorithm was unknown, and a template
cannot express the omission rule. If you had measured a different manifest and
were passing your own, compare it against `build_manifest` and open an issue if
it still differs.

### `parse_signature_header` returns an object, not a tuple

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import parse_signature_header

# before
# timestamp, digest = parse_signature_header(header)

# now
parsed = parse_signature_header("ts=1771891200,v1=abc123")
parsed.timestamp        # "1771891200"
parsed.digest()         # "abc123" — the first supported version
parsed.hashes           # {"v1": "abc123"} — a header may carry v1 and v2
```

### What is new

- `versions=` on `verify_signature`, defaulting to `("v1",)`. A provider
  migration to `v2` becomes `versions=("v2", "v1")`, with no release to wait
  for.
- `tolerance_seconds=` (and `now=`, for tests), which is what makes the
  manifest's `ts` work against replay. Still opt-in, as upstream has it.
- Case-insensitive header keys, whitespace-only values treated as absent, and a
  non-numeric `ts` rejected as a malformed header — three upstream rules that
  were missing.

## 0.234.0 — a generated model is built with its Python names, and the type-checker agrees

Nothing changes at runtime. What changes is what pyright accepts.

### Construct with the field name, not the alias

Fields carrying a wire name moved from `Field(alias=...)` to
`Field(validation_alias=..., serialization_alias=...)`. Runtime is identical —
the models already set `populate_by_name=True`, so both spellings always
validated. What changes is the parameter a type-checker sees:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import ChargePayload

# before: it ran, but pyright reported "No parameter named correlation_id"
# now: both accept it
payload = ChargePayload(correlation_id="order-1", value=1990)
```

If your code used the alias purely to silence the checker
(`ChargePayload(correlationID="order-1")`), it **still runs** — validation takes
the alias — but the checker now objects. Switch to the Python name.

Reading and writing are unchanged: `model_validate({"correlationID": ...})`
still accepts the provider's spelling, and `model_dump(by_alias=True)` still
emits it.

## 0.233.0 — two generated OpenPix enums were renamed

One change, and it only breaks code importing the two payment enums by name.

### `PaymentType` and `PaymentDestinationAliasType` were renamed

The generator now emits the variants of `PaymentCreatePayload` (a `oneOf` with four shapes: Pix key, QR Code, Manual, Boleto), and those are what register the enums first. The name now comes from the variant:

| Before | Now |
| --- | --- |
| `PaymentType` | `PaymentCreatePayloadPixKeyType` |
| `PaymentDestinationAliasType` | `PaymentCreatePayloadPixKeyDestinationAliasType` |

Same members, same values — only the class name changed:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    PaymentCreatePayloadPixKeyType,
)

assert PaymentCreatePayloadPixKeyType.PIX_KEY.value == "PIX_KEY"
```

If you compared the value instead of importing the class (`payment.type == "PIX_KEY"`), there is nothing to do.

### What did **not** break

`PaymentCreatePayload` and `PostApiV1PaymentBody` are still importable: they became union aliases over the variants, so an annotation like `body: PostApiV1PaymentBody` stays valid. What changed is that they now carry the payment's fields — before they were models with **no properties at all**, and `extra="ignore"` silently dropped everything you passed.

## 0.229.0 — Ollama structured output moves from `/api/generate` to `/api/chat`

One change, and it only breaks **tests**, not runtime.

### `generate_structured` now talks to `/api/chat`

`OllamaGenerator.generate_structured` posted to `/api/generate` with the schema in the `format` field. That is broken on a reasoning model: against `gpt-oss:20b` the daemon answers `200 OK` with a non-zero `eval_count` and an **empty** `response`, because the reply lands in a channel that endpoint does not surface. On `/api/chat` the JSON arrives in `message.content`, and a non-reasoning model behaves identically on either.

There is nothing to adjust at runtime — the call that returned junk (or nothing) now returns the instance. What breaks is **a test whose mock is pinned to the old endpoint**:

```python
import httpx
from pydantic import BaseModel

from tempest_fastapi_sdk.genai import OllamaGenerator
from tempest_fastapi_sdk.utils import HTTPClient


class Person(BaseModel):
    name: str


async def before() -> Person:
    """A mock that matched /api/generate — it stops matching."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": '{"name": "Ana"}', "done": True})

    client = HTTPClient(transport=httpx.MockTransport(handler))
    gen = OllamaGenerator("llama3.2", http_client=client)
    return await gen.generate_structured("Any person.", Person)


async def after() -> Person:
    """The reply now comes in message.content."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": '{"name": "Ana"}'}, "done": True},
        )

    client = HTTPClient(transport=httpx.MockTransport(handler))
    gen = OllamaGenerator("llama3.2", http_client=client)
    return await gen.generate_structured("Any person.", Person)
```

Two behaviour changes come with it:

- **Empty content raises `ValueError`** instead of returning nothing. If you had a `try/except` treating an empty result as "the model said nothing", switch it to `except ValueError`.
- **`system=` is a new optional parameter.** Use it for the instruction when `prompt` is a long document: an instruction glued above the document is ignored — measured, 0 items extracted against 20 with the instruction in its own `system` turn.

## 0.174.0 — crashes become 422s, and `order_by` is validated

Robustness fixes. Each trades a crash for a correct answer; none requires a code change, but four change the status or the exception your service sees.

### A long password is now a 422

There is a ceiling: `AUTH_PASSWORD_MAX_BYTES`, default `72` — bcrypt's hard limit, counted in UTF-8 **bytes**. A password past it used to raise `ValueError` from `hashpw` and surface as a **500** on signup / reset / change. It is now a `ValidationException` (**422**).

If your frontend does not validate length, it starts receiving 422 where it received 500. If you swapped the hasher for one without the limit, raise the value.

### An invalid `order_by` is now a `ValidationException`

`BaseRepository.paginate` and `cursor_paginate` resolve `order_by` through the model's mapper. A name that is not a mapped column raises `ValidationException` (**422**) instead of `AttributeError` (**500**).

Contract change in `cursor_paginate`: it used to raise `ValueError` there. Code catching `ValueError` around it needs updating:

```python
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.exceptions import ValidationException

from src.db.models import UserModel

# In a service the session comes from `db.get_session_context()`; here, SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

repo = BaseRepository(session, model=UserModel)


async def main() -> None:
    """Run this example."""
    try:
        page = await repo.cursor_paginate(order_by="not_a_column")
    except ValidationException:
        ...


asyncio.run(main())
```

`ValueError` still signals a malformed cursor.

### `BodySizeLimitMiddleware`: a streaming oversize body answers 413

The 413 is now emitted the moment the count is exceeded, and whatever the app sends afterwards is dropped. It used to go out in a `finally`, after the app had answered — and FastAPI does answer, converting the guard's `ClientDisconnect` into a **400**. The second `http.response.start` made uvicorn raise `RuntimeError: Response already started`.

In practice: a streaming upload over the limit answers **413** where it recently answered **400** (with a `RuntimeError` in the log). A handler that never reads the body still answers whatever it answered before — a sent response cannot be retracted.

### `make_csrf_token_dependency` sets the cookie

It used to only return the token, so the cookie stayed absent and the following `POST` was rejected with a 403. It now sets it (`Secure` + `SameSite=Lax`, and not `HttpOnly` — the client must read it to echo the header).

If you were already setting the cookie by hand in the handler, the value is the same (`request.state.csrf_token`) and nothing changes: the dependency does not overwrite an existing cookie. On a plain-HTTP dev server pass `secure=False`, or the browser will not send it back.

### `OAuthUser.email_verified`

A new field (default `None`), so nothing breaks. But **read the note**: if you link a social login to an existing account by email, require `profile.email_verified is True`. On GitHub the value is always `None` — `GET /user` carries no verification field, and the email it returns is the public profile one, which GitHub does not require verifying.

### `GET /logs` reads at most 20,000 records per file

Tune it with `make_logs_router(max_records_per_file=...)`. They are the newest ones; the endpoint sorts newest-first and paginates, so what was left out was unreachable. A `WARNING` is logged when the cap bites.

## 0.173.0 — a token only works where it was meant to, and caches stop being shared

Three security fixes change default behavior. None requires a code change, but check whether you were relying on the old behavior.

### Refresh and MFA-pending tokens no longer authorize a route

`make_bearer_token_dependency`, `make_jwt_user_dependency`, `make_role_dependency`, `make_permission_dependency` and `UserAuthService.current_user_dependency()` now accept **only** `access`-type tokens.

Before, the three JWTs `UserAuthService` mints with one secret verified identically, so the refresh token and the step-one `mfa_token` worked as a bearer on any authenticated route — the second factor was bypassable with just the password.

You are affected if you **deliberately** sent a refresh token to a regular route:

```python
from tempest_fastapi_sdk import (
    JWTUtils,
    REFRESH_TOKEN_TYPE,
    make_bearer_token_dependency,
)

from src.core.settings import settings

tokens = JWTUtils(settings)


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
from fastapi import FastAPI

from tempest_fastapi_sdk import IdempotencyMiddleware, MemoryIdempotencyStore

store = MemoryIdempotencyStore()

app = FastAPI()


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

from tempest_fastapi_sdk import BaseAppSettings, DatabaseSettings, RedisSettings


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
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column


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

