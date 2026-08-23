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
| `CPFField`, `CNPJField`, `CPFOrCNPJField`, `PhoneBRField` | `Annotated[str, AfterValidator(...)]` | Drop-in Pydantic field types — validate + normalize automatically. |

!!! info "`Field` suffix (since v0.76)"
    The field types now carry a `Field` suffix (`CPFField`, `CNPJField`, `CPFOrCNPJField`, `PhoneBRField`, `CEPField`) to make it obvious they are schema field types — like `UFField` / `CityNameField`. The old names (`CPF`, `CNPJ`, `CPFOrCNPJ`, `PhoneBR`, `CEP`) still work as **deprecated aliases**; prefer the new ones.

#### Schema usage

```python
from pydantic import EmailStr, Field

from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CPFOrCNPJField, PhoneBRField


class CustomerCreateSchema(BaseSchema):
    """Payload for POST /customers.

    `document` accepts CPF or CNPJ in masked or raw form and is
    stored digits-only after validation. `phone` is normalized the
    same way. Invalid values surface as a Pydantic `ValidationError`
    (HTTP 422 via the SDK exception handler).
    """

    name: str = Field(min_length=1, max_length=128)
    email: EmailStr
    document: CPFOrCNPJField
    phone: PhoneBRField
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
from src.schemas import CustomerCreateSchema


CustomerCreateSchema(...).document  # "52998224725"
CustomerCreateSchema(...).phone     # "5511988887777"
```

#### Manual validation (services, controllers, queue handlers)

```python
from tempest_fastapi_sdk.exceptions import ValidationException
from tempest_fastapi_sdk.utils import is_valid_cpf_cnpj, normalize_cpf_cnpj


def validate_document(raw_document: str) -> str:
    """Validate a CPF/CNPJ and return its canonical digits-only form.

    Args:
        raw_document (str): The document from the payload (masked or raw).

    Returns:
        str: The normalized, digits-only document.

    Raises:
        ValidationException: If the document is invalid.
    """
    if not is_valid_cpf_cnpj(raw_document):
        raise ValidationException(message="Invalid document")

    return normalize_cpf_cnpj(raw_document)
```

#### Filtering by stored digits

The normalizers strip masks before saving, so repository filters and unique constraints all work on the canonical digits-only form:

```python
import asyncio

from tempest_fastapi_sdk.utils import normalize_cpf_cnpj

from src.db.repositories import CustomerRepository

query = "529.982.247-25"

repo = CustomerRepository(session)

session = None  # provided by db.get_session_context() in your code


async def main() -> None:
    """Run this example."""
    await repo.get({"document": normalize_cpf_cnpj(query)})


asyncio.run(main())
```

---


## CEP (zipcode)


`CEPField` is an `Annotated[str, AfterValidator(normalize_cep)]` type — drop it into a Pydantic schema and inbound values are accepted as `"01310-100"` or `"01310100"`, normalized to 8 digits, and rejected (`ValidationError` → HTTP 422 envelope) when they don't match the shape. CEPs have no check digits, so validation is format-only.

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CEPField


class AddressCreateSchema(BaseSchema):
    cep: CEPField
    street: str
    number: str
```

Imperative variants: `is_valid_cep(value)`, `normalize_cep(value)`, plus `CEP_PATTERN` for raw regex use. Use them inside services / queue handlers where you don't want a Pydantic round-trip.


## States and municipalities

Every Brazilian app eventually needs a state/city `<select>`, or to validate that the UF and municipality in a payload actually exist. The SDK bundles that table — **27 states and 5606 municipalities** — so you don't have to call the IBGE API or version a JSON per service.

!!! info "Offline and dependency-free"
    The data lives in `tempest_fastapi_sdk/utils/data/br_locations.json` and is loaded on first use, then cached for the whole process. Zero network, nothing extra to install.

The UF is a `StrEnum`, and every state knows its official IBGE macro-region:

```python
from tempest_fastapi_sdk import UF, Region, list_states, get_state, states_by_region


# All 27 states, ordered by acronym.
states = list_states()
print(len(states))  # 27

# A single state (acronym in any case, or a UF member).
sp = get_state("sp")
print(sp.uf, sp.name, sp.region)        # SP São Paulo Sudeste
print(len(sp.cities), sp.cities[:2])    # 645 ['Adamantina', 'Adolfo']

# Grouping by region.
southeast = states_by_region(Region.SOUTHEAST)
print([state.uf.value for state in southeast])  # ['ES', 'MG', 'RJ', 'SP']
```

Each item is a `StateBR` (`uf: UF`, `name: str`, `region: Region`, `cities: list[str]`), ready to return straight from an endpoint.

### Validating UF and city in a schema

`UFField` accepts the acronym in any case (`"sp"`, `" RJ "`) and yields a `UF` member. `CityNameField` only trims whitespace — the cross-field "does this city exist in this UF" check is business logic, so it runs in the service with `is_valid_city` / `normalize_city`:

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import UFField, CityNameField


class AddressCreateSchema(BaseSchema):
    uf: UFField
    city: CityNameField
    street: str
    number: str
```

```python
from tempest_fastapi_sdk import UF, is_valid_city, normalize_city
from tempest_fastapi_sdk.exceptions import ValidationException


def validate_address(uf: UF, city: str) -> str:
    """Ensure the city belongs to the UF and return its canonical name.

    Args:
        uf (UF): The federative unit of the address.
        city (str): The city name coming from the payload.

    Returns:
        str: The municipality name in canonical case (e.g. "São Paulo").

    Raises:
        ValidationException: If the city does not exist in the given UF.
    """
    if not is_valid_city(uf, city):
        raise ValidationException(f"city {city!r} not found in {uf.value}")
    return normalize_city(uf, city)
```

!!! tip "City lookup ignores accents and case"
    `is_valid_city("SP", "sao paulo")` and `normalize_city("rj", "RIO DE JANEIRO")` both work — the comparison strips accents, case and surrounding whitespace. `normalize_city` always returns the canonical proper-case name (`"São Paulo"`, `"Rio de Janeiro"`).

### Choices ready for a frontend `<select>`

The same data serves **two roles**: validating input (the `UFField` /
`CityNameField` fields above) **and** feeding the frontend dropdowns. For
the latter, `uf_choices`, `region_choices` and `city_choices` return
`list[ChoiceBR]` — each item a `value` (what you store/submit) + `label`
(what the user sees), exactly the shape an `<option>` wants:

```python
from fastapi import APIRouter

from tempest_fastapi_sdk.utils import ChoiceBR, city_choices, region_choices, uf_choices

router = APIRouter(prefix="/api/locations", tags=["locations"])


@router.get("/states")
def list_uf_choices() -> list[ChoiceBR]:
    """UF choices: value = acronym, label = state name."""
    return uf_choices()


@router.get("/regions")
def list_region_choices() -> list[ChoiceBR]:
    """Choices for the 5 IBGE macro-regions."""
    return region_choices()


@router.get("/states/{uf}/cities")
def list_city_choices(uf: str) -> list[ChoiceBR]:
    """City choices for a UF: value = label = municipality name."""
    return city_choices(uf)
```

The `value` of `uf_choices()` is the acronym — the same value `UFField`
validates on the way back, so whatever the `<select>` submits drops
straight into your schema:

```python
from tempest_fastapi_sdk.utils import city_choices, region_choices, uf_choices


uf_choices()[0]          # ChoiceBR(value="AC", label="Acre")
region_choices()[0]      # ChoiceBR(value="Norte", label="Norte")
city_choices("sp")[0]    # ChoiceBR(value="Adamantina", label="Adamantina")
```

!!! info "Why `ChoiceBR` instead of a tuple?"
    `ChoiceBR` is a Pydantic schema (`value: str`, `label: str`), so it
    serializes as `{"value": ..., "label": ...}` in JSON and shows up
    typed in OpenAPI/Swagger — no untyped "magic field". For the classic
    state→city case, the frontend calls `/states`, then `/states/{uf}/cities`
    once a UF is picked.

### Imperative variants

| Function | What it does |
| --- | --- |
| `is_valid_uf(value)` | `True` when the acronym exists (any case/whitespace). |
| `normalize_uf(value)` | Returns the `UF`; raises `ValueError` when invalid. |
| `cities_by_uf(uf)` | Sorted list of the state's municipalities. |
| `is_valid_city(uf, city)` | `True` when the city belongs to the UF (accent/case-insensitive). |
| `normalize_city(uf, city)` | Canonical municipality name; raises `ValueError` when unknown. |

!!! note "A states/cities endpoint for the frontend"
    For `<select>`s, prefer `uf_choices()` / `region_choices()` / `city_choices(uf)` (value/label shape). If you need the whole state with its city list, `list_states()` returns each `StateBR` with its `cities`. Since it's all in-memory, it never touches the database.

#### Recap

- `UF` (StrEnum, 27 acronyms) + `Region` (5 IBGE macro-regions).
- `StateBR` / `CityBR` for typed responses; `ChoiceBR` (`value`/`label`) for dropdowns.
- `list_states`, `get_state`, `cities_by_uf`, `states_by_region` to query the bundled table.
- `uf_choices`, `region_choices`, `city_choices` for frontend `<select>`s.
- `UFField` / `CityNameField` for schema fields; `is_valid_*` / `normalize_*` for imperative validation in the service.


## Money in real


Two directions, both in `tempest_fastapi_sdk.utils`, no extra needed.

**Reading** what a document printed, with `parse_currency_br`:

```python
from decimal import Decimal

from tempest_fastapi_sdk.utils import parse_currency_br

parse_currency_br("R$ 2.930,00")   # Decimal("2930.00")
parse_currency_br("2.930,00")      # Decimal("2930.00")
parse_currency_br("2,930.00")      # Decimal("2930.00") — US notation too
parse_currency_br("-R$ 0,01")      # Decimal("-0.01")
parse_currency_br("no price")      # None
```

Every service that ingests money written for humans needs this: a model
transcribing a PDF, an imported CSV, a scraped page. Sending that value
through `float` first is what silently moves the cent.

!!! warning "`None` is not zero"
    `None` means "the document printed no price"; `Decimal("0.00")` means
    "the document printed R$ 0,00". Collapsing the two loses the difference
    between a line with missing data and a line that is genuinely free.

!!! note "The lone-dot rule"
    The **last** separator present is the decimal one. The genuinely
    ambiguous case is a dot followed by exactly three digits: `"2.930"` is
    read as two thousand nine hundred and thirty, which is what the notation
    means in the documents this parses.

**Writing** for prose — a PDF, an e-mail, a page:

```python
from decimal import Decimal

from tempest_fastapi_sdk.utils import (
    format_currency_br,
    format_percent_br,
    format_quantity_br,
    quantize_money,
)

format_currency_br(Decimal("484365.84"))                # "R$ 484.365,84"
format_currency_br(Decimal("2930"), symbol=False)       # "2.930,00"
format_currency_br(Decimal("-0.01"))                    # "-R$ 0,01"
format_percent_br(Decimal("0.30"))                      # "30,00%"
format_percent_br(Decimal("0.2999998"), places=5)       # "29,99998%"
format_quantity_br(Decimal("1250"))                     # "1.250,00"
quantize_money(Decimal("1.005"))                        # Decimal("1.01")
```

None of it goes through `locale`, which is process-global, depends on
locales being generated in the container, and is not thread-safe.

!!! info "`quantize_money` rounds half up"
    Not `Decimal`'s default (banker's rounding). Brazilian accounting
    practice rounds half **away from zero**, and matching it is what lets a
    generated document reproduce a hand-built one cent for cent.

!!! tip "A spreadsheet cell takes a number, not text"
    These functions are for prose. In `.xlsx`, write the `Decimal` and let
    the mask present it — see
    [Spreadsheets](spreadsheets.en.md#numbers-not-strings).

For amounts already stored as integer cents (the `CentsField` convention),
`tempest_fastapi_sdk.pdf.format_cents` does the same and delegates here.


## Utility helpers (utcnow, to_utc, modify_dict)


Small stateless helpers from `tempest_fastapi_sdk.utils` that the SDK itself relies on and that show up across every service. Available without any extra.

| Helper | Signature | Purpose |
| --- | --- | --- |
| `utcnow()` | `() -> datetime` | Current time as a timezone-aware UTC datetime — the SDK uses this for `created_at` / `updated_at` defaults. |
| `to_utc(value)` | `(datetime) -> datetime` | Coerce naive datetimes to UTC (assumed UTC) and aware datetimes to UTC via `astimezone`. Used by `BaseResponseSchema` field validators. |
| `modify_dict(data, exclude=None, include=None)` | ``(dict, list[str] | None, dict | None) -> dict`` | Single-pass filter + merge. Drop sensitive keys before logging or merge computed fields when mapping payloads to ORM models. |

#### Timestamps the same way everywhere

`utcnow` is the canonical "now" for the SDK. Use it for soft-delete timestamps, JWT `iat` / `exp`, audit trails — anything where mixing naive and aware datetimes would burn you later.

```python
from datetime import datetime, timedelta

from fastapi import Request

from tempest_fastapi_sdk import to_utc, utcnow


now = utcnow()                      # timezone-aware UTC
expires_at = now + timedelta(hours=1)


async def parse_scheduled(request: Request) -> datetime:
    """Normalize whatever the caller gave you to a timezone-aware UTC datetime."""
    payload = await request.json()                              # request.json() is async
    incoming: str = payload["scheduled_for"]                    # naive or aware ISO-8601
    return to_utc(datetime.fromisoformat(incoming))
```

A naive datetime is tagged with UTC (not converted from local time) so it's predictable in headless workers and Docker containers where `time.timezone` is anyone's guess.

#### Drop sensitive keys before logging / mapping

`modify_dict` is the tiny utility that powers `BaseSchema.to_dict(exclude=..., include=...)` and `BaseModel.update_from_dict(...)`. Use it directly when you don't want to call into Pydantic round-trips:

```python
from tempest_fastapi_sdk import LogUtils, PasswordUtils, modify_dict

passwords = PasswordUtils()


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
| `CPFField`, `CNPJField`, `CPFOrCNPJField`, `PhoneBRField`, `CEPField`, `is_valid_*`, `normalize_*`, `only_digits` | [CPF / CNPJ / phone](#cpf-cnpj-phone) |
| `UF`, `Region`, `StateBR`, `CityBR`, `ChoiceBR`, `UFField`, `CityNameField`, `list_states`, `get_state`, `cities_by_uf`, `states_by_region`, `uf_choices`, `region_choices`, `city_choices`, `is_valid_uf`, `normalize_uf`, `is_valid_city`, `normalize_city` | [States and municipalities](#states-and-municipalities) |

## Recap

- `tempest_fastapi_sdk.utils.regex` ships the regex, the validator, the
  normalizer and the Pydantic type for CPF, CNPJ, phone and postcode — pure
  stdlib, no extra.
- The `Annotated` types (`CPFField`, `CEPField`, …) normalize on the way in, so
  the schema accepts `"013.100-000"` and your code only ever reads digits.
- States and municipalities come from a bundled table, so you validate a
  payload and build a `<select>` without calling an external service.
- Money goes both ways: a number into Brazilian-real text, and text back into
  whole cents.
- `utcnow`, `to_utc` and `modify_dict` are the stateless helpers the SDK itself
  uses — available with no extra.
