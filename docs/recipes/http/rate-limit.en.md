# Rate limiting (middleware and per route)

Use to limit requests by IP, user, tenant or plan, with state in Redis, token bucket, `RateLimit-*` headers and a limit on a single route.

Part of the [HTTP layer](../http.md) recipe.

## Rate limit middleware


`RateLimitMiddleware` is a sliding-window limiter — each unique key (client IP by default) is allowed at most `max_requests` requests inside every `window_seconds` window. Exceeded requests get a `429 Too Many Requests` with a `Retry-After` header and the SDK's canonical error envelope as the body (see below). Two axes are pluggable: the **store** (memory or Redis) and the **key** (IP, user, tenant, API key) — see below.

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import RateLimitMiddleware


def create_app() -> FastAPI:
    app = FastAPI(...)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=120,
        window_seconds=60.0,
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    ...
```

### Limit per user / tenant / API key

By default the key is the client IP. To limit **per principal** (authenticated user, tenant, API key), pass a `key_func`. The SDK ships ready-made factories:

| Factory | Key produced | Use |
| --- | --- | --- |
| `key_by_ip(trusted_header=...)` | `ip:<addr>` | Per IP (default). |
| `key_by_jwt_subject(jwt)` | `user:<sub>` | Per authenticated user (`sub` claim). |
| `key_by_jwt_claim(jwt, "tenant_id", scope="tenant")` | `tenant:<id>` | Per arbitrary token claim. |
| `key_by_header("x-api-key", scope="apikey", is_known=api_keys.__contains__)` | `apikey:<value>` | Per **verified** header value; an unknown value falls back to the IP. |

!!! warning "The middleware runs before dependencies"
    `RateLimitMiddleware` runs **before** FastAPI `Depends` resolve — so the user authenticated by your auth dependency does not exist yet when the key is computed. That is why the `key_by_jwt_*` factories decode the bearer **from the raw request** (via `JWTUtils.decode_or_none`, no exception raised). Anonymous traffic falls back to the IP, so it stays limited.

!!! note "`key_by_header` requires `is_known=`"
    The header value is whatever the client sent, and the middleware reads it before any auth. Unverified, sending a made-up value per request would get a fresh budget each time. So `is_known=` is required: only a value it accepts (a lookup in the set of issued API keys, a checksum on the key format) gets its own bucket; anything else is treated as a missing header and falls back to the IP. The check is synchronous and runs on every request — keep it cheap.

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import RateLimitMiddleware, key_by_jwt_subject

from src.api.dependencies.resources import get_jwt_utils


def create_app() -> FastAPI:
    app = FastAPI(...)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=600,
        window_seconds=60.0,
        key_func=key_by_jwt_subject(get_jwt_utils()),        # ← limit per user
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    return app
```

### Distributed state with Redis

The default store (`MemoryRateLimitStore`) counts **in-process** — correct for a single worker. For multi-replica deployments, pass `store=RedisRateLimitStore(redis)`: each key becomes a sorted set and a single Lua script prunes expired members, counts, and adds the new hit **atomically** (no race between count and add). On a Redis error, `fail_open=True` (default) allows the request rather than locking everyone out.

```python
# src/api/app.py

from fastapi import FastAPI
from redis.asyncio import Redis

from tempest_fastapi_sdk import (
    RateLimitMiddleware,
    RedisRateLimitStore,
    key_by_jwt_subject,
)

from src.api.dependencies.resources import get_jwt_utils
from src.core.settings import settings


def create_app() -> FastAPI:
    redis: Redis = Redis.from_url(settings.REDIS_URL)
    app = FastAPI(...)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=600,
        window_seconds=60.0,
        key_func=key_by_jwt_subject(get_jwt_utils()),
        store=RedisRateLimitStore(redis),                    # ← shared across replicas
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    return app
```

The sliding-window semantics are identical across both stores; only where the counters live changes. You can still push rate limiting to the edge (nginx / Cloudflare / AWS WAF) when you prefer.

!!! note "Why `Redis.from_url` here, not `AsyncRedisManager`?"
    This client feeds a **middleware**, built in `create_app` (sync), before any
    async lifespan runs. `Redis.from_url()` is **lazy** — it constructs without
    opening a connection, so it fits here. `AsyncRedisManager` needs `await
    connect()` and fits where there's an async context: a client via
    `Depends(cache.client_dependency)`, or the `SSEBroker` built in the lifespan.
    Both need the `[cache]` extra (the `redis` package).

### Tolerating bursts: the token bucket

A sliding window answers one question: *did this key exceed N requests in the
last W seconds?* A client that fires 20 requests at once and then idles for a
minute is well-behaved on average — and a strict window rejects it anyway.

A **token bucket** separates the two: the sustained rate (tokens refilled per
second) and the burst it absorbs (bucket capacity). A `RateLimitRule` with
`burst` is a token bucket; without it, it stays a sliding window.

```python
from tempest_fastapi_sdk import RateLimitRule

# 10 req/s sustained (600 per minute), absorbing a burst of 100.
rule = RateLimitRule(max_requests=600, window_seconds=60.0, burst=100)
```

Rules reach the middleware through a **policy**. For a single-tier service,
`StaticRateLimitPolicy` applies one list to everyone:

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    RateLimitMiddleware,
    RateLimitRule,
    StaticRateLimitPolicy,
)


def create_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        policy=StaticRateLimitPolicy(
            [RateLimitRule(max_requests=600, window_seconds=60.0, burst=100)],
        ),
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    return app
```

!!! info "`policy=` and `store=` are alternatives"
    `max_requests` / `window_seconds` / `store` describe **one** sliding
    window. With `policy=` you pass a *list* of limits, and the counters live
    in `quota_store=` (`MemoryQuotaStore` by default, `RedisQuotaStore` for
    multi-replica). Passing both raises `ValueError` when Starlette builds the
    middleware stack — at lifespan startup, not on the `add_middleware` line,
    which returns without error. Honoring one would silently ignore the
    other.

### Per-plan quotas

One rule list per tier, resolved per request. A plan's rules are checked
**together**: a per-minute limit stacked under a daily ceiling.

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    PlanRateLimitPolicy,
    RateLimitMiddleware,
    RateLimitRule,
    key_by_jwt_subject,
    key_by_plan_principal,
    plan_by_jwt_claim,
)

from src.api.dependencies.resources import get_jwt_utils


def create_app() -> FastAPI:
    jwt = get_jwt_utils()
    policy = PlanRateLimitPolicy(
        {
            "free": [
                RateLimitRule(60, 60.0, scope="minute"),
                RateLimitRule(1_000, 86_400.0, scope="day"),
            ],
            "pro": [
                RateLimitRule(600, 60.0, burst=100, scope="minute"),
                RateLimitRule(100_000, 86_400.0, scope="day"),
            ],
        },
        resolve=plan_by_jwt_claim(jwt, "plan"),
        default_plan="free",
    )
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        policy=policy,
        key_func=key_by_plan_principal(policy, key_by_jwt_subject(jwt)),
    )
    return app
```

Three decisions worth understanding:

- **An unknown plan falls back to `default_plan`.** A tier the configuration
  does not know must be limited, not served unlimited — and a request is the
  wrong place to discover a typo. What *is* validated at construction: an
  empty `plans`, a `default_plan` outside the mapping, and a plan with no
  rules. All three would only show up in production as unlimited traffic.
- **A rejection spends nothing.** A request blocked by the daily ceiling must
  not burn a token from the per-minute limit — otherwise the minute drains
  without a single request being served. Both stores decide every rule before
  writing any of them; `RedisQuotaStore` does it inside **one** Lua script,
  which is the only way to keep the whole list atomic.
- **`key_by_plan_principal` prefixes the plan onto the key.** An upgrade
  starts writing to fresh counters instead of inheriting the exhausted ones.
  The price is that a caller who *chooses* their own plan (a client-supplied
  header) could reset their counters by switching — pair it with a resolver
  your edge controls.

### The 429 body is the SDK's error envelope

The 429 comes out in the same shape `register_exception_handlers` writes in
every handler, so a client parses **one** envelope for every failure:

```json
{
    "detail": "Too many requests",
    "code": "TOO_MANY_REQUESTS",
    "details": {"retry_after_seconds": 60, "limit": 15}
}
```

`code` is what a client branches on -- never `detail`, which is prose and
changes with the negotiated locale once a `MessageCatalog` is registered. Swap
both for whatever your API uses:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import RateLimitMiddleware


app = FastAPI()

app.add_middleware(
    RateLimitMiddleware,
    max_requests=15,
    window_seconds=1.0,
    error_message="Slow down, you are sending too many requests.",
    error_code="TOO_MANY_REQUESTS",
)
```

`details` carries what only headers had before: `retry_after_seconds` (the same
number as `Retry-After`) and the `limit` of the rule that rejected the request
-- in policy mode, the tightest one.

!!! warning "Changed in v0.256.0"
    Up to v0.255.0 the 429 was `text/plain` with the raw `error_message` as the
    body. Clients reading that body as text have to read JSON and take `detail`
    instead; clients branching on `status === 429` are unaffected.

    The swap closes a contradiction inside the SDK itself: `error_responses()`
    always pointed 429 at `ErrorResponseSchema`, so a client generated from the
    OpenAPI schema broke deserializing the text.

!!! tip "Documenting the 429 in OpenAPI"
    `error_responses(TooManyRequestsException)` describes exactly what the
    middleware sends -- the default `error_code` is that exception's own
    `code`. Change `error_code` and you should change the exception documenting
    the route too, or the schema drifts from the body again.

!!! info "Why the middleware does not raise the exception"
    That would be the obvious route -- raise `TooManyRequestsException` and let
    the registered handler format it. It does not work: a `BaseHTTPMiddleware`
    added through `add_middleware` sits **outside** Starlette's
    `ExceptionMiddleware`, so an exception raised in `dispatch` finds no handler
    and turns into a 500. The middleware therefore builds the response itself,
    reading the default `code` off that same exception so the two cannot drift.

### `RateLimit-*` headers

Every response carries `RateLimit-Limit` and `RateLimit-Remaining` (turn them
off with `limit_headers=False`), describing the **tightest** rule — the one a
client should pace itself against. `RateLimit-Reset` is emitted only when the
number is known: always in policy mode, and only on 429 in single-window mode.
The sliding-window store reports no reset for an accepted request, and a
guessed number is worse than an absent header.

### Multi-replica

```python
# src/api/app.py

from fastapi import FastAPI
from redis.asyncio import Redis

from tempest_fastapi_sdk import (
    RateLimitMiddleware,
    RateLimitRule,
    RedisQuotaStore,
    StaticRateLimitPolicy,
)

from src.core.settings import settings


def create_app() -> FastAPI:
    redis: Redis = Redis.from_url(settings.REDIS_URL)
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        policy=StaticRateLimitPolicy(
            [RateLimitRule(600, 60.0, burst=100)],
        ),
        quota_store=RedisQuotaStore(redis),             # ← shared across replicas
    )
    return app
```

A window rule becomes a sorted set of timestamps; a bucket rule becomes a hash
holding `tokens` + `ts`. The bucket key's TTL covers the time to **fill the
bucket**, not the window: a slow bucket with a large burst (10 tokens/minute,
`burst=1000`) takes over an hour to fill, and expiring before that would hand
back a full bucket for free. On a Redis error, `fail_open=True` (default)
allows the request.

### Clock: Redis's, not the replica's

Bucket refill and window pruning depend on "how much time passed". With several
replicas, the question is: **by whose clock?**

Both Redis stores (`RedisQuotaStore` and `RedisRateLimitStore`) answer "by
Redis's": the Lua script reads `TIME` from the server itself, and no replica
sends a timestamp. Replicas with out-of-sync wall clocks — or a VM that just
took an NTP step — still see **one** bucket and **one** window.

!!! info "What this prevents, measured"
    Up to v0.301.0 each replica sent its own `time.time()`. Against real Redis 4
    and 7, with two clients — one exhausting the limit, the other probing:

    - a client **one hour ahead** got through a bucket (1 token/hour,
      `burst=3`) and a window (3/hour) the other one had just exhausted;
    - a client **one hour behind** wrote timestamps that let the on-time client
      through the same way.

    With the server's `TIME`, all eight cases are denied. They live in
    `tests/api/test_quota_redis_live.py` (`make test-docker`).

Two practical consequences:

- **The clock that matters is the Redis host's.** A step there still moves
  every key at once — but it is one clock, instead of one per replica.
- **Redis 4 works**, because the script switches to effects replication
  (`redis.replicate_commands()`) before writing. Without it Redis 4.0.14
  refuses the write after `TIME`, and under `fail_open=True` a one-request
  limit let 10 of 10 through.

!!! warning "redis-py 8 and Redis before 6"
    `redis-py` 8.1.0 — the `[cache]` extra's floor — opens the connection
    with `HELLO 3`, which Redis 4 and 5 reject as an unknown command. Against those servers, build the client with
    `protocol=2`: `Redis.from_url(settings.REDIS_URL, protocol=2)`.

### Testing without depending on the machine's speed

`MemoryQuotaStore` accepts `clock=`: any callable returning seconds. With a
clock that only moves when the test moves it, "the burst runs out" and "the
token comes back after an hour" become exact assertions — not a race against
the refill rate:

```python
import asyncio

from tempest_fastapi_sdk import MemoryQuotaStore, RateLimitRule


class ManualClock:
    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now


async def main() -> None:
    clock = ManualClock()
    store = MemoryQuotaStore(clock=clock)
    rules = [RateLimitRule(max_requests=1, window_seconds=3600.0, burst=2)]

    print([(await store.consume("k", rules)).allowed for _ in range(3)])
    clock.now += 3600.0
    print((await store.consume("k", rules)).allowed)


asyncio.run(main())
```

Output:

```text
[True, True, False]
True
```

!!! tip "Why not `sleep`?"
    A 100-per-second rule refills one token every 10 ms. Four sequential calls
    on a loaded machine take longer than that, the token comes back, and the
    "burst runs out" test fails: that is what happened to the SDK's own suite
    (#339). A negligible rate over the test's span (1 per hour) or an injected
    clock takes the machine's speed out of the assertion.


## Per-route rate limit (`make_rate_limit_dependency`)

`RateLimitMiddleware` limits the whole app, and its scope only shrinks by
subtraction (`exempt_paths`). Two questions fall outside it:

- **"Only `POST /api/invites`."** An expensive route deserves a ceiling the
  rest of the app does not have.
- **"Per invited e-mail, not just per IP."** The key lives in the **body**, and
  the middleware's `key_func` only receives the `Request`, synchronously,
  before the body is read.

`make_rate_limit_dependency` answers both: it is a dependency you hang on one
route, it counts in the same `RateLimitStore` as the middleware (memory or
Redis) and it answers the same 429.

### Limit one endpoint by IP and by a body field

```python
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from tempest_fastapi_sdk import (
    MemoryRateLimitStore,
    key_by_body_field,
    key_by_ip,
    make_rate_limit_dependency,
    register_exception_handlers,
)


class InviteCreateSchema(BaseModel):
    email: str


invite_limit = make_rate_limit_dependency(
    MemoryRateLimitStore(),
    max_requests=3,
    window_seconds=3600.0,
    key=[
        key_by_ip(trusted_header="x-real-ip"),
        key_by_body_field("email"),
    ],
)

app = FastAPI()
register_exception_handlers(app)


@app.post("/api/invites", status_code=202, dependencies=[Depends(invite_limit)])
async def create_invite(payload: InviteCreateSchema) -> dict[str, str]:
    return {"invited": payload.email}


@app.get("/api/invites")
async def list_invites() -> list[str]:
    return []


client = TestClient(app)
for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"):
    response = client.post(
        "/api/invites",
        json={"email": "ana@example.com"},
        headers={"x-real-ip": ip},
    )
    print(response.status_code, response.json())
print(response.headers["retry-after"], response.headers["ratelimit-limit"])
print(client.get("/api/invites").status_code)
```

Output:

```text
202 {'invited': 'ana@example.com'}
202 {'invited': 'ana@example.com'}
202 {'invited': 'ana@example.com'}
429 {'detail': 'Too many requests', 'code': 'TOO_MANY_REQUESTS', 'details': {'retry_after_seconds': 3600, 'limit': 3}}
3600 3
200
```

Four different IPs, one e-mail: the fourth request falls on the e-mail key,
and a `GET` on the same URL stays free. Piece by piece:

- **`key=[...]`** — each function returns one key (or several, or none), and
  every key counts in the same store. The first one over budget refuses the
  request; the ones after it are not counted, so a refused request does not
  spend their budget. Sync functions (`key_by_ip`, `key_by_header`,
  `key_by_jwt_*`) and async ones work alike.
- **`key_by_body_field("email")`** — reads the field off the JSON body.
  FastAPI reads the body **before** resolving dependencies and Starlette keeps
  the bytes on the `Request`, so the endpoint still receives its validated
  `payload` — the output above is that proof. The value is normalized (`strip`
  + lowercase) and stored as SHA-256, so Redis does not pile up e-mails in
  clear text (`normalize=False`, `hash_value=False` turn that off). A body that
  is not JSON, or lacks the field, yields no key: the endpoint's own 422
  answers that request.
- **`trusted_ip_header`** — without `key`, the default key is the IP resolved
  from that header
  (`make_rate_limit_dependency(store, ..., trusted_ip_header="x-real-ip")`).
  With `key`, pass the header to the `key_by_ip` inside the list; passing both
  raises `ValueError` at construction instead of silently ignoring the header.
- **The bucket belongs to the route.** Each key is prefixed by default with the
  method and the path template (`"POST /api/invites"`), so the same dependency
  on two routes gives each its own budget, `/users/1` and `/users/2` share the
  one of `/users/{user_id}`, and the counter never collides with the
  middleware's in the same Redis. Pass `scope="invites"` to make several routes
  share one budget.

!!! info "The same 429 as the middleware"
    The body (`detail`, `code`, `details.retry_after_seconds`,
    `details.limit`) and the headers (`Retry-After`, `RateLimit-Limit`,
    `RateLimit-Remaining`, `RateLimit-Reset`) come from the same helpers
    `RateLimitMiddleware` uses — `tests/api/test_rate_limit_dependency.py`
    compares the two. The difference is the path: the dependency **raises**
    `TooManyRequestsException`, so the app needs `register_exception_handlers`,
    and with a `MessageCatalog` registered there `detail` is translated by
    `code` (`"Requisições em excesso"` under `pt-BR`), which the middleware —
    answering outside the handlers — does not do.

!!! tip "A store built in `create_app`"
    Pass a function taking the `Request` instead of the store —
    `make_rate_limit_dependency(store_of, ...)`, with `store_of(request)`
    returning `request.app.state.rate_limit_store` — and the dependency
    declared at import time counts in the store of the app serving the
    request. Measured with two apps and one dependency: each counts in its
    own.

!!! tip "Multi-replica"
    Swap `MemoryRateLimitStore()` for `RedisRateLimitStore(redis)` and every
    replica shares the counters, exactly as with the middleware.
