# Body size limit and HTTP cache (ETag / 304)

Use to refuse a large body with 413 before parsing and answer 304 with ETag without mixing authenticated requests.

Part of the [HTTP layer](../http.md) recipe.

## Request body size limit (`BodySizeLimitMiddleware`)

A 2 GB upload against an endpoint that expects JSON kills the worker before
Pydantic validates anything. `BodySizeLimitMiddleware` is pure ASGI and stops it
at the door:

```python
# src/api/app.py
from fastapi import FastAPI
from tempest_fastapi_sdk import BodySizeLimitMiddleware

app: FastAPI = FastAPI()

app.add_middleware(
    BodySizeLimitMiddleware,
    max_bytes=2 * 1024 * 1024,             # 2 MiB for any request
    exclude_paths=("/api/files/upload",),  # the upload route caps itself
)
```

There are **two** checks:

1. **Header** — a `Content-Length` above the cap answers `413` immediately,
   without reading a single body byte. This covers the common case where the
   client knows the size.
2. **Streaming** — on a `chunked` upload (no `Content-Length`), the middleware
   counts the bytes of the `http.request` messages and aborts once the cap is
   crossed.

The response is the SDK's canonical envelope:
`{"detail": "Request body too large.", "code": "REQUEST_BODY_TOO_LARGE", "details": {"max_bytes": 1048576}}`
with HTTP 413.

!!! info "`exclude_paths` matches by prefix"
    The match is `startswith`, so the more specific the prefix the better —
    `("/api/files",)` exempts everything under `/api/files`. Use it for the
    route that deliberately accepts a large file and enforces its **own** limit
    (via `UploadUtils(max_size=...)`, say). `max_bytes=0` disables the check —
    never ship that to production.

## HTTP response cache (ETag / 304)

`ResponseCacheMiddleware` delivers two performance wins, layered.

**ETag + conditional GET (always on).** Every cacheable response gets a strong `ETag` (a hash of the body) and a `Cache-Control`. When the client sends a matching `If-None-Match`, the middleware answers `304 Not Modified` with no body — the handler still runs, but the bytes never go over the wire.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import ResponseCacheMiddleware


def create_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ResponseCacheMiddleware, ttl_seconds=30)   # ETag/304 only
    return app
```

**Server-side cache (opt-in via `store=`).** With a store wired, a cacheable `GET`/`HEAD` response is stored for `ttl_seconds`; a later matching request is served **without running the handler** (`X-Cache: HIT`), and an `If-None-Match` hit against the stored ETag still short-circuits to `304`.

```python
from typing import Any

from fastapi import FastAPI

from tempest_fastapi_sdk import RedisResponseCacheStore, ResponseCacheMiddleware


def create_app(redis: Any) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        ResponseCacheMiddleware,
        store=RedisResponseCacheStore(redis),   # shared across replicas
        ttl_seconds=60,
        vary=("Accept-Encoding",),               # varies the key and emits Vary
    )
    return app
```

Only safe methods (`GET`/`HEAD`) and successful responses (`200` by default) are cached. Responses that opt out — `Cache-Control: no-store`/`private` or a `Set-Cookie` (personalized) — are **never** stored.

!!! tip "Cache key"
    The key is `method|path|query` plus the headers listed in `vary=`. Pass `cacheable=<predicate>` to exclude specific requests, or `exempt_paths=(...)` to skip exact paths. The store mirrors the idempotency one (memory or Redis with a raw client), so it composes with the service's existing Redis.

### A stream is never drained (and `exempt_paths` is equality)

Caching means **reading the body to the end** to hash and store it. In a
`text/event-stream` the body only ends when the client disconnects, so
through 0.285.0 an SSE route behind this middleware never answered at all:
the request hung until nginx's `proxy_read_timeout` and became a `504`.

Since 0.286.0 the middleware **passes a stream straight through**. There
is nothing to configure: a response with `Content-Type:
text/event-stream`, or with no `Content-Length`, is neither cached nor
given an `ETag`.

!!! warning "The same was true of `IdempotencyMiddleware`"
    It drains the body so it can replay the response on a retry, and it
    had no exemption list at all. A `POST` that answers with a stream —
    such as `make_genai_router`'s `/generate/stream` — hung the same way
    whenever the client sent an `Idempotency-Key`. It passes through now
    too: a stream is not replayable from any store.

**`exempt_paths` changed meaning in two middlewares.** It was equality in
`ResponseCacheMiddleware`, `RateLimitMiddleware` and
`GracefulShutdownMiddleware`, and prefix in `AccessLogMiddleware` and
`HoneypotBanMiddleware` — one argument name, two behaviours. A service
exempting its SSE route from both passed **the same tuple** to each and
got an exemption in one and a no-op in the other.

Now `exempt_paths` is **equality in all five**, and prefix matching has a
name of its own:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import AccessLogMiddleware, ResponseCacheMiddleware


def create_app() -> FastAPI:
    """Build an app whose SSE subtree is exempt from both middlewares."""
    app: FastAPI = FastAPI()
    app.add_middleware(
        ResponseCacheMiddleware,
        ttl_seconds=30,
        exempt_paths=("/health/liveness",),      # this path, and only it
        exempt_prefixes=("/api/sse",),           # and everything under /api/sse
    )
    app.add_middleware(
        AccessLogMiddleware,
        exempt_prefixes=("/api/sse",),           # was: exempt_paths
    )
    return app
```

!!! danger "Migration: a prefix `exempt_paths` becomes `exempt_prefixes`"
    Only `AccessLogMiddleware` and `HoneypotBanMiddleware` change
    behaviour. If you passed `exempt_paths=("/api/sse",)` to either of
    them expecting it to cover `/api/sse/stream`, **rename it to
    `exempt_prefixes`** — otherwise the exemption stops applying to child
    routes.

    Widening the other way — making everything a prefix — was rejected on
    purpose: in `RateLimitMiddleware` an exemption is a **hole in the rate
    limit**, and `exempt_paths=("/health",)` would start exempting
    `/health-admin` too, without anyone asking.


### A credentialed request does not share a cache entry

Notice what the key does **not** contain: who asked. If two people's `GET /api/me` land on the same key, the first stored response is served to the second — and to any anonymous caller hitting the same path.

So a request carrying `Authorization`, `Cookie`, `X-Token` or `X-API-Key` **bypasses the shared store**. It still gets an `ETag` and a `304` (both per-response, and safe); it just never enters a cache someone else can read. If your auth reads another header, pass the list in `credential_headers=`. A route gated by `make_token_dependency` also answers `Cache-Control: no-store`, so it stays out of the store even with a `header_name=` the list does not know.

```python
from typing import Any

from fastapi import FastAPI

from tempest_fastapi_sdk import RedisResponseCacheStore, ResponseCacheMiddleware


def create_app(redis: Any) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        ResponseCacheMiddleware,
        store=RedisResponseCacheStore(redis),
        ttl_seconds=60,
        cache_credentialed=True,   # opt in: the key now includes the credential
    )
    return app
```

With `cache_credentialed=True` a digest of the credential headers joins the key, so each caller gets its own entry and caching works for authenticated routes again. Only turn it on after checking that the cached routes do not carry a **second** identity the credential does not stand for (a tenant header, a session independent of the path) — there, credentials alone do not separate enough.

!!! warning "`Cache-Control` defaults to `private`"
    The emitted default is `private, max-age=<max_age>`. The middleware cannot know whether a route's body is personalized, and a wrong `public` there makes a shared proxy serve one user's response to another. Pass `cache_control="public, max-age=…"` **deliberately**, on a router that only serves shared content.
