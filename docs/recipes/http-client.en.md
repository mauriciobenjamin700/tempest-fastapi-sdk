# HTTP client (outbound)

`HTTPClient` is a typed wrapper over `httpx.AsyncClient` for **calling
external services** with retries + exponential backoff, a circuit-breaker,
default timeouts, and `X-Request-ID` propagation. It's the outbound
counterpart of the [HTTP middleware](http.md) (which handles inbound
traffic). Requires the `[http]` extra (`httpx`).

## Basic usage

The client is safe to share across requests on the same event loop — it
reuses the internal connection pool. Use it as an async context manager (or
keep a singleton in [`resources.py`](../architecture.md) and close it on the
lifespan).

```python
from typing import Any

from tempest_fastapi_sdk import HTTPClient


async def fetch_user(user_id: str) -> dict[str, Any]:
    """GET /users/{id} on the external service."""
    async with HTTPClient(base_url="https://api.example.com", timeout=10.0) as client:
        response = await client.get(f"/users/{user_id}")
        response.raise_for_status()
        return response.json()
```

!!! warning "Don't reuse a client that's already closed"
    Leaving the `async with` block calls `__aexit__`, which closes the
    connection pool (`aclose()`). For one-off calls, build the client inside the
    `async with` as above. To reuse it across requests, keep a singleton and
    close it **once** on the lifespan (see the circuit-breaker example below) —
    never wrap each call of a shared singleton in `async with`.

Methods: `get` / `post` / `put` / `patch` / `delete` (plus a generic
`request`), all forwarding kwargs to httpx (`json=`, `params=`, `headers=`,
...) and returning an `httpx.Response`.

## Retry + backoff + circuit-breaker

Pass a `RetryPolicy` and tune the breaker thresholds at construction:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk import CircuitOpenError, HTTPClient, RetryPolicy

client = HTTPClient(
    base_url="https://api.example.com",
    timeout=5.0,
    retry_policy=RetryPolicy(
        max_attempts=3,                # 1 try + 2 retries
        backoff_initial_seconds=0.5,   # 0.5s, 1s, 2s... (exponential)
        backoff_max_seconds=8.0,       # cap per wait
    ),
    failure_threshold=5,               # open the circuit after 5 straight failures
    recovery_seconds=30.0,             # half-open after 30s
    default_headers={"X-Api-Key": "..."},
    propagate_request_id=True,         # forward the current request's X-Request-ID
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Close the shared connection pool on shutdown."""
    yield
    await client.aclose()


async def call() -> None:
    # Shared singleton — call it directly, no per-call `async with`
    # (that would close the pool). The pool is closed once in the lifespan above.
    try:
        await client.post("/charge", json={"amount": 100})
    except CircuitOpenError:
        # The circuit is open — don't hammer the downed upstream.
        ...
```

- **Retry**: retried on transient errors (timeouts, 5xx, connection
  failures) up to `max_attempts`, with exponential backoff capped by
  `backoff_max_seconds`.
- **Circuit-breaker**: after `failure_threshold` consecutive failures the
  circuit **opens** and calls raise `CircuitOpenError` immediately (without
  touching the network) until `recovery_seconds` elapse, then it half-opens
  to probe.
- **Request-ID**: with `propagate_request_id=True`, the in-flight request's
  `X-Request-ID` (from `RequestIDMiddleware`) is forwarded to the upstream,
  stitching logs end-to-end.

!!! tip "Keep it as a singleton in resources.py"
    Build the `HTTPClient` once (in `src/api/dependencies/resources.py`),
    expose a `get_http_client`, and close it on the lifespan with
    `await client.aclose()` — so the connection pool is reused across
    requests.

## Recap

- `HTTPClient` = typed `httpx.AsyncClient` + retry/backoff/circuit-breaker + X-Request-ID.
- `[http]` extra. Methods `get/post/put/patch/delete/request` → `httpx.Response`.
- `RetryPolicy(max_attempts, backoff_initial_seconds, backoff_max_seconds)` controls retries.
- `failure_threshold` / `recovery_seconds` control the breaker; `CircuitOpenError` when open.
- Share a singleton and close it with `aclose()` on shutdown.
