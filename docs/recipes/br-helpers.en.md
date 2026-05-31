# Brazilian helpers

Document validators (CPF, CNPJ, CEP) and phone number normalizer/validator for BR formats. Pure stdlib — no extra deps.

## CPF / CNPJ / phone


`tempest_fastapi_sdk.utils.regex` ships ready-to-use regex patterns, validators, normalizers and Pydantic types for the identity/contact fields that show up in almost every Brazilian API. No extra required — pure stdlib + Pydantic (already a core dependency).

| Symbol | Kind | Purpose |
| --- | --- | --- |
| `CPF_PATTERN`, `CNPJ_PATTERN`, `CPF_CNPJ_PATTERN`, `PHONE_BR_PATTERN` | `re.Pattern[str]` | Compiled regex (masked or raw input). |
| `is_valid_cpf`, `is_valid_cnpj`, `is_valid_cpf_cnpj` | `(str) -> bool` | Format match **+** check-digit math. All-same-digit sequences rejected. |
| `is_valid_phone_br` | `(str) -> bool` | BR phone shape: optional `+55`, optional DDD, optional 9th digit. |
| `normalize_cpf`, `normalize_cnpj`, `normalize_cpf_cnpj`, `normalize_phone_br` | `(str) -> str` | Strip mask to digits-only; raise `ValueError` if invalid. |
| `only_digits` | `(str) -> str` | Strip every non-digit character. |
| `CPF`, `CNPJ`, `CPFOrCNPJ`, `PhoneBR` | `Annotated[str, AfterValidator(...)]` | Drop-in Pydantic field types — validate + normalize automatically. |

#### Schema usage

```python
from pydantic import EmailStr, Field

from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CPF, CPFOrCNPJ, PhoneBR


class CustomerCreateSchema(BaseSchema):
    """Payload for POST /customers.

    `document` accepts CPF or CNPJ in masked or raw form and is
    stored digits-only after validation. `phone` is normalized the
    same way. Invalid values surface as a Pydantic `ValidationError`
    (HTTP 422 via the SDK exception handler).
    """

    name: str = Field(min_length=1, max_length=128)
    email: EmailStr
    document: CPFOrCNPJ
    phone: PhoneBR
```

Valid input:

```json
{
    "name": "Ana",
    "email": "ana@example.com",
    "document": "529.982.247-25",
    "phone": "+55 (11) 98888-7777"
}
```

After validation:

```python
CustomerCreateSchema(...).document  # "52998224725"
CustomerCreateSchema(...).phone     # "5511988887777"
```

#### Manual validation (services, controllers, queue handlers)

```python
from tempest_fastapi_sdk.utils import (
    is_valid_cpf_cnpj,
    normalize_cpf_cnpj,
    only_digits,
)

if not is_valid_cpf_cnpj(raw_document):
    raise ValidationException(message="Documento inválido")

document_digits = normalize_cpf_cnpj(raw_document)
```

#### Filtering by stored digits

The normalizers strip masks before saving, so repository filters and unique constraints all work on the canonical digits-only form:

```python
await repo.get({"document": normalize_cpf_cnpj(query)})
```

---


## CEP (zipcode)


`CEP` is an `Annotated[str, AfterValidator(normalize_cep)]` type — drop it into a Pydantic schema and inbound values are accepted as `"01310-100"` or `"01310100"`, normalized to 8 digits, and rejected (`ValidationError` → HTTP 422 envelope) when they don't match the shape. CEPs have no check digits, so validation is format-only.

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CEP


class AddressCreateSchema(BaseSchema):
    cep: CEP
    street: str
    number: str
```

Imperative variants: `is_valid_cep(value)`, `normalize_cep(value)`, plus `CEP_PATTERN` for raw regex use. Use them inside services / queue handlers where you don't want a Pydantic round-trip.


## Utility helpers (utcnow, to_utc, modify_dict)


Small stateless helpers from `tempest_fastapi_sdk.utils` that the SDK itself relies on and that show up across every service. Available without any extra.

| Helper | Signature | Purpose |
| --- | --- | --- |
| `utcnow()` | `() -> datetime` | Current time as a timezone-aware UTC datetime — the SDK uses this for `created_at` / `updated_at` defaults. |
| `to_utc(value)` | `(datetime) -> datetime` | Coerce naive datetimes to UTC (assumed UTC) and aware datetimes to UTC via `astimezone`. Used by `BaseResponseSchema` field validators. |
| `modify_dict(data, exclude=None, include=None)` | `(dict, list[str] \| None, dict \| None) -> dict` | Single-pass filter + merge. Drop sensitive keys before logging or merge computed fields when mapping payloads to ORM models. |

#### Timestamps the same way everywhere

`utcnow` is the canonical "now" for the SDK. Use it for soft-delete timestamps, JWT `iat` / `exp`, audit trails — anything where mixing naive and aware datetimes would burn you later.

```python
from datetime import timedelta

from tempest_fastapi_sdk import to_utc, utcnow


now = utcnow()                      # timezone-aware UTC
expires_at = now + timedelta(hours=1)

# Normalize whatever the caller gave you
incoming = request.json()["scheduled_for"]              # naive or aware
scheduled_for = to_utc(datetime.fromisoformat(incoming))
```

A naive datetime is tagged with UTC (not converted from local time) so it's predictable in headless workers and Docker containers where `time.timezone` is anyone's guess.

#### Drop sensitive keys before logging / mapping

`modify_dict` is the tiny utility that powers `BaseSchema.to_dict(exclude=..., include=...)` and `BaseModel.update_from_dict(...)`. Use it directly when you don't want to call into Pydantic round-trips:

```python
from tempest_fastapi_sdk import LogUtils, modify_dict

log = LogUtils("app.users")

payload = {"email": "ana@example.com", "password": "s3cr3t", "name": "Ana"}

# Strip password before logging
log.info("user_signup", **modify_dict(payload, exclude=["password"]))

# Merge a computed hash before persisting
user_row = modify_dict(
    payload,
    exclude=["password"],
    include={"password_hash": passwords.hash(payload["password"])},
)
```

`include` wins over `data`, so it doubles as a "set or override" helper without mutating the source dict.

#### Where every other helper is documented

Every helper has its own recipe — this section is the quick map:

| Helper | Recipe |
| --- | --- |
| `PasswordUtils`, `JWTUtils` | [Authentication recipe](http.md#authentication) |
| `EmailUtils` | [Transactional email recipe](http.md#transactional-email) |
| `UploadUtils` | [File uploads recipe](http.md#file-uploads) |
| `DownloadUtils`, `build_content_disposition` | [Serving private files through the API](http.md#serving-private-files-through-the-api-downloadutils) |
| `LogUtils` + `configure_logging` | [Structured logging & request IDs recipe](logging.md) |
| `MetricsUtils` (CPU/memory/disk/GPU) | [System metrics recipe](metrics.md) |
| `CPF`, `CNPJ`, `CPFOrCNPJ`, `PhoneBR`, `is_valid_*`, `normalize_*`, `only_digits` | [CPF / CNPJ / phone](#cpf-cnpj-phone) |

