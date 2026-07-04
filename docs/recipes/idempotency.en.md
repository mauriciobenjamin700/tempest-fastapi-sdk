# Idempotency

`IdempotencyMiddleware` implements the `Idempotency-Key` pattern used by Stripe, AWS, GitHub and Plaid: the client sends a unique header and, **once the first request completes**, the server replays the same response on any retry — no duplicate row in the database, no double charge.

!!! warning "No in-progress lock"
    Deduplication only kicks in **after** the first request finishes and its response is cached. Concurrent retries that arrive **while the original is still in-flight** are NOT deduplicated — the middleware has no in-progress lock (unlike Stripe's 409-while-in-progress), so both run the handler. Keep client timeouts generous relative to handler latency to avoid premature retries.

## How it works

1. Client sends `POST /charge` with `Idempotency-Key: chk_<uuid>`.
2. Middleware runs the handler, stores the complete response keyed by `(method, path, key)`.
3. Client retries? Middleware returns the **same cached response**. Handler doesn't run again.

Only mutating verbs (`POST` / `PUT` / `PATCH` / `DELETE`) are eligible — `GET` is already idempotent.

!!! warning "Opt-in per request"
    Requests without the header pass straight through. Existing endpoints aren't disturbed — only callers that need the guarantee send the header.

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
- There is no in-progress lock: concurrent retries during the original request run the handler — keep client timeouts generous.
- `MemoryIdempotencyStore` is process-local and volatile (dev / single-replica); `RedisIdempotencyStore` covers multi-replica and survives restarts.
- Implement the `IdempotencyStore` protocol to plug in any backend (e.g. DynamoDB).

Next step: combine it with [`@cached`](cache.en.md) to speed up reads, or with the [Outbox pattern](outbox.en.md) for reliable delivery of side-effects triggered by the handler.
