# Misc utilities

Small stateless helpers from the base SDK (no extra). Each solves a
recurring pain so you don't rewrite it.

## UTC dates — `utcnow` / `to_utc`

`utcnow()` returns a **timezone-aware** "now" in UTC (never the naive
`datetime.utcnow()`, a classic bug source); `to_utc(value)` normalizes any
`datetime` to UTC (assuming UTC when the input is naive).

```python
from datetime import datetime

from tempest_fastapi_sdk import to_utc, utcnow

created_at: datetime = utcnow()                  # 2026-06-07T19:00:00+00:00
normalized: datetime = to_utc(some_naive_dt)     # becomes aware/UTC
```

## Filter/extend a dict — `modify_dict`

Drops keys and merges new ones in a single pass, returning a **new** dict
(it never mutates the original):

```python
from tempest_fastapi_sdk import modify_dict

payload = {"id": 1, "password": "x", "name": "Ana"}
safe = modify_dict(payload, exclude=["password"], include={"role": "user"})
# {"id": 1, "name": "Ana", "role": "user"}
```

## Client IP — `get_client_ip`

Resolves the IP from the request, optionally trusting **one** edge-set
header (proxy/LB). Without `trusted_header` it uses only the direct peer —
don't blindly trust a world-exposed `X-Forwarded-For`.

```python
from fastapi import APIRouter, Request

from tempest_fastapi_sdk import get_client_ip

router = APIRouter()


@router.get("/whoami")
async def whoami(request: Request) -> dict[str, str]:
    ip: str = get_client_ip(request, trusted_header="x-real-ip")
    return {"ip": ip}   # "unknown" when neither the header nor the peer exists
```

## Opaque tokens — `generate_opaque_token` / `verify_opaque_token`

For API keys, reset/invite tokens, etc.: generate a `(plaintext, hash)`
pair, **show the plaintext once** to the user, and persist only the hash
(SHA-256). Verification is constant-time.

```python
from tempest_fastapi_sdk import (
    generate_opaque_token,
    hash_opaque_token,
    verify_opaque_token,
)

plaintext, token_hash = generate_opaque_token()   # show plaintext once; store token_hash
# ... later, when the token comes back:
ok: bool = verify_opaque_token(submitted, token_hash)
# hash_opaque_token(x) to re-hash on demand
```

!!! tip "Why opaque, not JWT"
    Opaque tokens are revocable (just delete the hash) and carry no
    readable claims. Use them for long-lived secrets (API keys); use
    [JWT](http.md) for short-lived stateless sessions.

## Recap

- `utcnow()` / `to_utc()` — always timezone-aware UTC.
- `modify_dict(data, exclude=, include=)` — filter + extend without mutating.
- `get_client_ip(request, trusted_header=)` — client IP, with opt-in trusted header.
- `generate_opaque_token()` / `verify_opaque_token()` — hash-and-store secrets, constant-time verify.
