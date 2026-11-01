# Utilities

Small, stateless helpers from the base SDK: no extra, no configuration. Each
one solves a problem every service ends up rewriting, and nearly always
rewrites with the same defect: a `datetime` with no time zone, an IP read
from a header the client controls, a temporary password the service's own
policy rejects.

This page covers the general-purpose helpers. The larger families in `utils`
(Brazilian documents, validated fields, upload, download, retry, metrics)
have their own recipes, listed under [Where the rest lives](#where-the-rest-lives).

## Dates in UTC: `utcnow` / `to_utc`

`datetime.utcnow()` returns a `datetime` **with no time zone**, which Python
treats as local time in any later arithmetic. `utcnow()` returns the current
instant with `tzinfo=UTC` already set. `to_utc(value)` converts any
`datetime` to UTC, and reads a naive value as if it were already UTC.

```python
from datetime import datetime, timedelta, timezone

from tempest_fastapi_sdk import to_utc, utcnow

created_at: datetime = utcnow()
naive: datetime = to_utc(datetime(2026, 8, 2, 10, 30))
brasilia: datetime = to_utc(
    datetime(2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=-3)))
)

print(created_at)
print(naive)
print(brasilia)
```

```text
2026-11-01 12:25:28.636506+00:00
2026-08-02 10:30:00+00:00
2026-08-02 13:30:00+00:00
```

The naive value is **not** shifted: `10:30` stays `10:30` and only gains
`+00:00`. The Brasília time becomes `13:30` in UTC.

## Filtering and extending a dict: `modify_dict`

Removes keys and adds others in one step, returning a **new** dict:

```python
from typing import Any

from tempest_fastapi_sdk import modify_dict

payload: dict[str, Any] = {"id": 1, "password": "x", "name": "Ana"}
safe: dict[str, Any] = modify_dict(payload, exclude=["password"], include={"role": "user"})

print(safe)
print(payload)
```

```text
{'id': 1, 'name': 'Ana', 'role': 'user'}
{'id': 1, 'password': 'x', 'name': 'Ana'}
```

The original is left untouched.

## Client IP: `get_client_ip`

Rate limiting, auditing and IP blocking all depend on knowing who called.
Behind a proxy, the connection peer is the proxy, and the real IP arrives in
a header. The problem is that the client can send that header too.

`get_client_ip` trusts **one** header only, the one you name in
`trusted_header`, and only when its value is exactly **one** IP. Without
`trusted_header`, it uses the connection peer.

```python
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import get_client_ip

app: FastAPI = FastAPI()


@app.get("/whoami")
async def whoami(request: Request) -> dict[str, str]:
    return {
        "trusted": get_client_ip(request, trusted_header="x-real-ip"),
        "peer": get_client_ip(request),
    }


client: TestClient = TestClient(app)
print(client.get("/whoami", headers={"X-Real-IP": "203.0.113.7"}).json())
print(client.get("/whoami", headers={"X-Real-IP": "203.0.113.7, 10.0.0.1"}).json())
```

```text
{'trusted': '203.0.113.7', 'peer': 'testclient'}
{'trusted': 'testclient', 'peer': 'testclient'}
```

On the second call the header carries a list, not an IP, so it is ignored
and the answer falls back to the peer. When neither the header nor the peer
is available, the result is `"unknown"`.

!!! warning "Name the header **your edge** writes"
    `trusted_header` is only safe when the proxy in front of the service
    overwrites that header on every request. If nothing overwrites it, the
    client picks the IP you are going to record. When in doubt, leave it
    `None`.

### In ASGI middleware: `get_client_ip_from_scope`

Pure ASGI middleware has no `Request`, it has the `scope`. Same rule:

```python
from typing import Any

from tempest_fastapi_sdk import get_client_ip_from_scope

scope: dict[str, Any] = {
    "type": "http",
    "headers": [(b"x-real-ip", b"198.51.100.9")],
    "client": ("10.0.0.2", 5000),
}

print(get_client_ip_from_scope(scope, trusted_header="x-real-ip"))
print(get_client_ip_from_scope({"type": "http", "headers": [], "client": None}))
```

```text
198.51.100.9
unknown
```

### Validating a value: `parse_client_ip`

This is the piece both functions above use to decide whether the header
counts. It returns the IP in canonical form, or `None`:

```python
from tempest_fastapi_sdk import parse_client_ip

for value in ["203.0.113.7", " 203.0.113.7 ", "203.0.113.7, 10.0.0.1", "::ffff:1.2.3.4", "nao-e-ip"]:
    print(repr(value), parse_client_ip(value))
```

```text
'203.0.113.7' 203.0.113.7
' 203.0.113.7 ' 203.0.113.7
'203.0.113.7, 10.0.0.1' None
'::ffff:1.2.3.4' ::ffff:1.2.3.4
'nao-e-ip' None
```

??? info "Why the canonical form matters"
    Python 3.12 writes the mapped IPv6 `::ffff:1.2.3.4` as `::ffff:102:304`;
    3.13 onwards, as `::ffff:1.2.3.4`. Two replicas on different versions
    would count the same client in two rate-limit buckets. `parse_client_ip`
    returns the dotted form on both.

## Opaque tokens: `generate_opaque_token` / `verify_opaque_token`

For an API key, an invite link, a reset token: generate the
`(plaintext, hash)` pair, **show the plaintext once** and store only the
hash (SHA-256). Verification runs in constant time.

```python
from tempest_fastapi_sdk import (
    generate_opaque_token,
    hash_opaque_token,
    verify_opaque_token,
)

plaintext, token_hash = generate_opaque_token()

print(len(plaintext), len(token_hash))
print(verify_opaque_token(plaintext, token_hash))
print(verify_opaque_token("chute", token_hash))
print(hash_opaque_token(plaintext) == token_hash)
```

```text
43 64
True
False
True
```

The plaintext is 43 characters with the default `nbytes=32`; the hash is the
64-character hex that goes into the column. `hash_opaque_token` is what you
use to look the row up from the token you received.

!!! tip "Why opaque and not JWT"
    An opaque token is revocable (delete the hash) and carries no readable
    claims. Use it for long-lived secrets such as API keys. For short,
    stateless sessions, use [JWT](security.md).

## Generated password: `generate_password`

An account created through social login, or provisioned by an admin, has no
typed password, but the column is usually `NOT NULL`. The generated password
has to be unguessable **and** accepted by the service's password policy.

```python
from tempest_fastapi_sdk import generate_password

password: str = generate_password()
longer: str = generate_password(min_length=30)

print(password, len(password))
print(len(longer))
```

```text
yUzj1w1z82o04gfhKnK?LxIE 24
30
```

The default is 24 characters, always with a lowercase letter, an uppercase
letter, a digit and a special character. The parameters mirror the policy
(`min_length`, `max_bytes`, `require_complexity`), so pass the same values
as your `Settings`.

!!! info "Why not `secrets.token_urlsafe`"
    Against the policy with complexity on, sampled 200 000 times,
    `secrets.token_urlsafe(32)` was rejected 26.54% of the time and
    `secrets.token_hex(32)` 100% (it has no uppercase and no special
    character). A generator that passes three times in four fails
    intermittently, in production, inside an OAuth callback.
    `generate_password` draws one character from each class first, so it
    always passes.

An impossible policy fails right away, not at signup:

```python
from tempest_fastapi_sdk import generate_password

try:
    generate_password(min_length=12, max_bytes=6)
except ValueError as exc:
    print(exc)
```

```text
password policy is unsatisfiable: max_bytes=6 is below the effective minimum length of 12
```

## Signed URL: `sign_path` / `verify_path`

A download link that expires, with no token table: `sign_path` appends
`expires` and `signature` to the path, and `verify_path` checks both in the
route.

```python
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from tempest_fastapi_sdk import sign_path, verify_path
from tempest_fastapi_sdk.exceptions import AppException

now: datetime = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
path: str = "/api/files/relatorio 2026.pdf"
url: str = sign_path(
    path,
    secret="s3cr3t",
    expires_in=timedelta(minutes=10),
    purpose="files",
    now=now,
)
print(url)

query: dict[str, list[str]] = parse_qs(urlsplit(url).query)
expires: int = int(query["expires"][0])
signature: str = query["signature"][0]

verify_path(path, expires=expires, signature=signature, secret="s3cr3t", purpose="files", now=now)
print("valid")

for purpose, instant in [("files", now + timedelta(minutes=11)), ("email-link", now)]:
    try:
        verify_path(
            path,
            expires=expires,
            signature=signature,
            secret="s3cr3t",
            purpose=purpose,
            now=instant,
        )
    except AppException as exc:
        print(exc.status_code, exc.code)
```

```text
/api/files/relatorio%202026.pdf?expires=1793535000&signature=nozJSjnE1ujH8np8VTXahFq6JFcMwod0p5p1M9lJJiI
valid
403 SIGNED_URL_EXPIRED
403 SIGNED_URL_INVALID
```

- **`path` is the decoded path**, as the route sees it in
  `request.scope["path"]`. The space becomes `%20` in the URL, but you sign
  and verify `relatorio 2026.pdf`.
- **`purpose` binds the signature to one use.** A URL signed for `files` is
  not valid for `email-link`, even with the same secret.
- **`now=`** exists for tests; omit it in production.

Serving files from disk with signed URLs already wired is covered in
[Local disk storage](local-storage.md).

## Form payload: `form_encode`

Some APIs (Stripe is the best known) take `application/x-www-form-urlencoded`
with nested objects in bracket notation. `form_encode` flattens the dict:

```python
from typing import Any

from tempest_fastapi_sdk import form_encode

payload: dict[str, Any] = {
    "amount": 1000,
    "metadata": {"user_id": 42},
    "items": [{"price": "price_123", "quantity": 2}],
    "paid": True,
    "note": None,
}

print(form_encode(payload))
```

```text
{'amount': '1000', 'metadata[user_id]': '42', 'items[0][price]': 'price_123', 'items[0][quantity]': '2', 'paid': 'true'}
```

`None` is **dropped** instead of becoming `""`: on these APIs an empty
string clears the field, so sending `""` would erase data you never meant to
touch. The result goes straight into `httpx` as `data=`.

## Color contrast: `is_light` / `relative_luminance`

A brand color the customer picked, text on top: black or white? `is_light`
answers from the WCAG relative luminance.

```python
from tempest_fastapi_sdk.utils import is_light, relative_luminance

for color in ["#ffffff", "#000", "#2563eb", "#facc15", "rebeccapurple"]:
    print(color, relative_luminance(color), is_light(color))
```

```text
#ffffff 1.0 True
#000 0.0 False
#2563eb 0.15315153582201535 False
#facc15 0.6356386777636567 True
rebeccapurple None False
```

A light background (`True`) needs dark text. A color that is not hex
(`rebeccapurple`, `rgb(...)`) returns `None` for luminance and is treated as
dark.

!!! note "Import from `tempest_fastapi_sdk.utils`"
    These two are not re-exported at the package top level, only from
    `tempest_fastapi_sdk.utils`.

## Where the rest lives

| Family | Examples | Recipe |
| --- | --- | --- |
| Brazilian documents, phone, CEP, currency | `is_valid_cpf`, `normalize_phone_br`, `format_currency_br` | [Brazilian helpers](br-helpers.md) |
| Ready-made Pydantic fields | `CPFField`, `PriceField`, `PixKeyField`, `SlugField` | [Validated fields](fields.md) |
| Upload and file type | `UploadUtils`, `read_upload_capped`, `sniff_mime` | [Uploads](uploads.md) |
| Download and `Content-Disposition` | `DownloadUtils`, `build_content_disposition` | [Downloads](downloads.md) |
| Retry and HTTP client | `async_retry`, `RetryPolicy`, `HTTPClient` | [HTTP client](http-client.md) |
| Password, JWT, login throttling | `PasswordUtils`, `check_password_policy`, `AttemptThrottle` | [Auth flow](auth-flow.md) and [Security](security.md) |
| TOTP | `TOTPHelper` | [MFA](mfa.md) |
| Email | `EmailUtils` | [Transactional email](email.md) |
| System metrics | `MetricsUtils` | [Metrics](metrics.md) |
| Logging | `LogUtils` | [Logging](logging.md) |
| Files in storage | `FileStoreUtils` | [File store](file-store.md) |

## Recap

- `utcnow()` / `to_utc()`: always zone-aware, in UTC; a naive value is read
  as UTC.
- `modify_dict(data, exclude=, include=)`: filters and extends without
  mutating.
- `get_client_ip` / `get_client_ip_from_scope`: one trusted header, opt-in,
  and only when it carries exactly one IP; `parse_client_ip` is the rule.
- `generate_opaque_token()`: plaintext once, hash in the database,
  constant-time verification.
- `generate_password()`: a password the policy always accepts, by
  construction.
- `sign_path` / `verify_path`: a link that expires, bound to one `purpose`.
- `form_encode`: nested dict in bracket notation, never sending `None`.
- `is_light`: dark or light text over any color.
