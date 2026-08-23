# Idempotency

`IdempotencyMiddleware` implements the `Idempotency-Key` pattern used by Stripe, AWS, GitHub and Plaid: the client sends a unique header and, **once the first request completes**, the server replays the same response on any retry — no duplicate row in the database, no double charge.

!!! warning "The in-progress lock is per process"
    Within one process, concurrent requests sharing a key are serialized: the second waits for the first to finish and replays its response. **Across replicas it does not hold** — the store deduplicates retries (the first has finished and written), but two simultaneous requests landing on different replicas both run the handler. Keep client timeouts generous relative to handler latency to avoid premature retries.

## How it works

1. Client sends `POST /charge` with `Idempotency-Key: chk_<uuid>`.
2. Middleware runs the handler, stores the complete response keyed by `(caller, method, path, key)`.
3. Client retries? Middleware returns the **same cached response**. Handler doesn't run again.

Only mutating verbs (`POST` / `PUT` / `PATCH` / `DELETE`) are eligible — `GET` is already idempotent.

!!! warning "Opt-in per request"
    Requests without the header pass straight through. Existing endpoints aren't disturbed — only callers that need the guarantee send the header.

## The key is scoped to the caller

The header value is chosen **by the client**. On its own it identifies nobody: two callers picking the same string on the same endpoint would share the entry, and the replay hands back the stored **response** — body and headers included.

So the middleware folds a digest of the request's credentials (`Authorization` / `Cookie`) into the key. An entry is only ever replayed to the credentials that created it.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import IdempotencyMiddleware, MemoryIdempotencyStore

app = FastAPI()


app.add_middleware(
    IdempotencyMiddleware,
    store=MemoryIdempotencyStore(),
    ttl_seconds=24 * 3600,
    principal_resolver=lambda request: request.headers.get("x-tenant-id", ""),
)
```

Use `principal_resolver=` when identity lives elsewhere — an API-key id, a tenant header. Returning a constant there restores the old behavior (one entry per key, shared across callers) and is **unsafe** on a multi-tenant endpoint.

!!! note "What is not replayed"
    A `Set-Cookie` from the original response is left **out** of the stored copy — re-issuing the first caller's session on a replay would hand that session over. The original caller still gets its cookie; only the replay does not.

!!! tip "5xx is not cached"
    By default responses `>= 500` do not enter the store, so the client's retry actually reaches the handler. A transient failure cached for `ttl_seconds` would pin that key to the error for the entry's whole life. Pass `cache_server_errors=True` if your case needs the opposite.

## Minimum setup (single-replica / dev)

```python
from fastapi import FastAPI
from tempest_fastapi_sdk import (
    IdempotencyMiddleware,
    MemoryIdempotencyStore,
)


app = FastAPI()
app.add_middleware(
    IdempotencyMiddleware,
    store=MemoryIdempotencyStore(),
    ttl_seconds=24 * 3600,
)
```

`MemoryIdempotencyStore` keeps entries in a local dict — works for one replica only. For production use Redis.

!!! note "Process-local, volatile state"
    `MemoryIdempotencyStore` lives in process memory: each replica has its own dict and it is wiped on every restart / redeploy. Keys stored before a restart stop deduplicating afterwards. Use it only in dev / single-replica; for persistence across restarts and across replicas, use `RedisIdempotencyStore`.

## Production setup (multi-replica via Redis)

!!! info "Installation"
    In-memory idempotency ships with `tempest-fastapi-sdk` — the minimum
    setup needs no extra. `RedisIdempotencyStore` needs the `[cache]`
    extra — `uv add "tempest-fastapi-sdk[cache]"` (pulls in `redis`).

```python
from fastapi import FastAPI
from redis.asyncio import Redis
from tempest_fastapi_sdk import (
    IdempotencyMiddleware,
    RedisIdempotencyStore,
)

from src.core.settings import settings


redis = Redis.from_url(settings.REDIS_URL)
app = FastAPI()
app.add_middleware(
    IdempotencyMiddleware,
    store=RedisIdempotencyStore(redis, prefix="idem:"),
    ttl_seconds=24 * 3600,
)
```

!!! note "Why `Redis.from_url` here, not `AsyncRedisManager`?"
    This client feeds a **middleware**, built in `create_app` (sync), before any
    async lifespan runs. `Redis.from_url()` is **lazy** — it constructs without
    opening a connection, so it fits here. `AsyncRedisManager` needs `await
    connect()` and fits where there's an async context: a client via
    `Depends(cache.client_dependency)`, or the `SSEBroker` built in the lifespan.
    Both need the `[cache]` extra (the `redis` package).

Stripe defaults to 24h — coherent with client-side exponential retry.

## Client

```python
import uuid
import httpx


async def create_charge(amount_cents: int) -> dict[str, object]:
    """Idempotent POST with automatic retry."""
    key = uuid.uuid4().hex
    async with httpx.AsyncClient() as c:
        for _ in range(3):
            try:
                r = await c.post(
                    "https://api/charge",
                    json={"amount_cents": amount_cents},
                    headers={"Idempotency-Key": key},
                    timeout=10,
                )
                return r.json()
            except httpx.ReadTimeout:
                continue
        raise RuntimeError("3 retries failed")
```

Whichever of the 3 attempts reaches the server, the end state is the same resource created exactly once — remaining replicas receive the cached response.

## When to use

- Payments / charges
- Webhook delivery (client retries with the same key)
- External side-effect operations (email send, SMS)
- Any `POST /create` whose retry could duplicate records

## When NOT to use

- `GET` (already idempotent)
- Trivially reentrant operations (`PATCH` rewriting the same value)
- When duplication has no consequence (logs, metrics)

## Custom backend

Implement the `IdempotencyStore` protocol:

```python
from tempest_fastapi_sdk import CachedResponse, IdempotencyStore


class DynamoIdempotencyStore:
    """Example DynamoDB-backed store."""

    async def get(self, key: str) -> CachedResponse | None:
        ...

    async def set(
        self,
        key: str,
        response: CachedResponse,
        *,
        ttl_seconds: int,
    ) -> None:
        ...


# Works with the middleware just like the built-in stores:
assert isinstance(DynamoIdempotencyStore(), IdempotencyStore)
```

## Recap

- The `Idempotency-Key` header makes the server replay the same response on any retry **once the first request completes** — no duplicate record.
- Only mutating verbs (`POST` / `PUT` / `PATCH` / `DELETE`) carrying the header are eligible; everything else passes straight through (opt-in per request).
- There **is** an in-progress lock, per process: inside one replica, concurrent requests with the same key are serialized and the second replays the first's response. Across replicas it does not hold — keep client timeouts generous.
- `MemoryIdempotencyStore` is process-local and volatile (dev / single-replica); `RedisIdempotencyStore` covers multi-replica and survives restarts.
- Implement the `IdempotencyStore` protocol to plug in any backend (e.g. DynamoDB).

Next step: combine it with [`@cached`](cache.en.md) to speed up reads, or with the [Outbox pattern](outbox.en.md) for reliable delivery of side-effects triggered by the handler.
