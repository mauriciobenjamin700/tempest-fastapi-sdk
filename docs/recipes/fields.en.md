# Validated fields (ready-made types)

Instead of repeating `Field(gt=0, ...)` on every schema, the SDK ships
`Annotated` types with the validation rule baked in — the field becomes
**self-describing**. They follow the same `*Field` convention as the BR
fields (`CPFField`, `UFField`, …): anything ending in `Field` is a
ready-to-use schema field type. An invalid value becomes a
`ValidationError` → HTTP 422 via the SDK handler.

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CentsField, PercentField, SlugField


class ProductCreateSchema(BaseSchema):
    slug: SlugField              # lowercase kebab-case
    price_cents: CentsField      # int >= 0 (money in cents)
    discount: PercentField       # float in [0, 100]
```

## Integers

| Type | Rule | Use |
| --- | --- | --- |
| `PositiveIntField` | `> 0` | quantities, counts, 1-based ids |
| `NonNegativeIntField` | `>= 0` | counters that reset to zero |
| `CentsField` | `>= 0` | money in minor units (cents) |
| `PortField` | `1..65535` | TCP/UDP port |

!!! tip "Money in cents"
    Store monetary values as an **integer of cents** (`CentsField`) to
    avoid binary-float rounding. Divide by 100 only at the presentation
    edge. When the contract requires an exact decimal (e.g. a gateway
    payload), use `PriceField`.

## Floats

| Type | Rule |
| --- | --- |
| `PositiveFloatField` | `> 0` |
| `NonNegativeFloatField` | `>= 0` |
| `PercentField` | `0..100` |
| `RatioField` | `0..1` (fractions/probabilities) |
| `LatitudeField` | `-90..90` |
| `LongitudeField` | `-180..180` |

## Decimals

`PriceField` is a non-negative `Decimal` with 2 places:

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import PriceField


class InvoiceSchema(BaseSchema):
    total: PriceField     # "9.99" -> Decimal("9.99"); "1.999" and "-1" rejected
```

## Strings

| Type | Rule |
| --- | --- |
| `NonEmptyStrField` | trims whitespace and rejects empty (whitespace-only too) |
| `SlugField` | lowercase kebab-case (`my-post-1`) |
| `HexColorField` | CSS hex color (`#fff` or `#abc123`) |

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import HexColorField, NonEmptyStrField, SlugField


class CategorySchema(BaseSchema):
    name: NonEmptyStrField     # "  Drinks " -> "Drinks"; "   " rejected
    slug: SlugField
    color: HexColorField
```

## Locale

`LocaleField` is the language counterpart of `UFField`: an
`Annotated[Locale, ...]` that **normalizes the input** and yields a
[`Locale`](../reference.md) enum member. It accepts case/separator variants
(`"pt_BR"`, `"PT-BR"`) and the bare primary subtag (`"pt"` → `Locale.PT_BR`);
a tag outside the enum becomes a `422`.

```python
from tempest_fastapi_sdk import BaseSchema, Locale
from tempest_fastapi_sdk.utils import LocaleField


class ProfileUpdateSchema(BaseSchema):
    locale: LocaleField | None = None   # "pt_BR" -> Locale.PT_BR; "xx-YY" -> 422
```

!!! tip "Enum vs. Field"
    Use the **`Locale` enum** when you already hold the canonical tag
    (constants, internal logic). Use **`LocaleField`** on a request schema,
    where the input is client-supplied and worth normalizing — the same
    relationship `UF` has to `UFField`. To store the language on a table, see
    [`LocaleColumnMixin`](database.md#locale-the-users-preferred-language).

## Pix key

`PixKeyField` validates the **five** BACEN Pix key types in a single
field and normalizes to a canonical form. An invalid key →
`ValidationError` → 422.

| Type | Example | Normalizes to |
| --- | --- | --- |
| CPF | `529.982.247-25` | `52998224725` (digits) |
| CNPJ | `11.222.333/0001-81` | `11222333000181` (digits) |
| Email | `Ana@Example.com` | `ana@example.com` (lowercased) |
| Phone | `+5511999998888` | `+5511999998888` (E.164) |
| Random (EVP) | `123e4567-e89b-12d3-a456-426614174000` | same, lowercased (UUID) |

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import PixKeyField


class PixTransferSchema(BaseSchema):
    to_key: PixKeyField      # accepts CPF/CNPJ/email/phone/random
    amount_cents: CentsField
```

Need to know **which** type a key is (routing, UI, business rule)? Use the
helpers — they don't require the field:

```python
from tempest_fastapi_sdk.utils import PixKeyType, detect_pix_key_type, is_valid_pix_key


detect_pix_key_type("ana@example.com")   # -> PixKeyType.EMAIL
detect_pix_key_type("+5511999998888")    # -> PixKeyType.PHONE
detect_pix_key_type("bad-key")           # -> None
is_valid_pix_key("529.982.247-25")       # -> True
```

!!! info "Validation by shape + check digits"
    Detection is by shape: `@` → email, leading `+` → E.164 phone, UUID →
    random; otherwise digits + **check digits** decide between CPF and
    CNPJ (a CPF with wrong check digits is rejected). Phone follows
    Brazilian E.164 (`+55` + area + number, 10-11 digits after `+55`).

## Full example (schema + route + 422)

The types are plain `Annotated` — they work in any `BaseSchema` and
FastAPI turns the `ValidationError` into a **422** via the SDK handler.
Complete program:

```python
# src/schemas/product.py
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import (
    CentsField,
    NonEmptyStrField,
    PercentField,
    SlugField,
)


class ProductCreateSchema(BaseSchema):
    name: NonEmptyStrField
    slug: SlugField
    price_cents: CentsField
    discount_percent: PercentField = 0.0
```

```python
# src/api/routers/products.py
from fastapi import APIRouter

from src.schemas.product import ProductCreateSchema

router = APIRouter()


@router.post("/products")
async def create_product(payload: ProductCreateSchema) -> dict[str, str]:
    """Create a product. An invalid payload never reaches here — it 422s."""
    return {"slug": payload.slug}
```

An invalid `POST` (`price_cents: -5`, `slug: "Not A Slug"`) gets a **422**
with the body the SDK handler standardizes:

```json
{
  "detail": [
    {"loc": ["body", "price_cents"], "msg": "Input should be greater than or equal to 0"},
    {"loc": ["body", "slug"], "msg": "String should match pattern ..."}
  ]
}
```

!!! tip "Optional, default and list"
    They compose like any Pydantic type: `CentsField | None = None`,
    `discount: PercentField = 0.0`, `list[SlugField]`. The rule still
    applies to each list element.

## Composing your own

Every `*Field` is just an `Annotated[type, Field(...)]`. Need a rule the
SDK doesn't ship? Build yours with the same pattern — no magic:

```python
from typing import Annotated

from pydantic import Field

# Stock quantity: integer from 0 to 100000.
StockField = Annotated[int, Field(ge=0, le=100_000)]

# 3 uppercase letters (e.g. an ISO-4217 currency code).
CurrencyCodeField = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class SkuSchema(BaseSchema):
    stock: StockField
    currency: CurrencyCodeField      # "BRL" ok; "brl" / "REAL" rejected
```

Prefer the SDK types when an equivalent exists (self-description +
cross-service consistency); roll your own only for domain-specific rules.

!!! warning "Common gotchas"
    - **`CentsField` vs `PriceField`** — store money as an integer of
      cents (`CentsField`) to dodge binary float; use `PriceField`
      (`Decimal`, 2 places) only when the **contract** demands a decimal.
    - **`PercentField` vs `RatioField`** — `Percent` is `0..100` (a human
      types "15"); `Ratio` is `0..1` (fraction/probability, "0.15"). Don't
      mix the two in the same flow.
    - **`NonEmptyStrField`** trims **before** validating — `"  "`
      (whitespace-only) is rejected as empty.

## Recap

- Everything is `Annotated[..., rule]` with a `*Field` suffix —
  self-describing, no repeated `Field(...)` in the schema.
- Integers: `PositiveIntField`, `NonNegativeIntField`, `CentsField`, `PortField`.
- Floats: `PositiveFloatField`, `NonNegativeFloatField`, `PercentField`,
  `RatioField`, `LatitudeField`, `LongitudeField`.
- Decimal: `PriceField` (2 places, `>= 0`).
- Strings: `NonEmptyStrField`, `SlugField`, `HexColorField`.
- BR: `CPFField`, `CNPJField`, `CPFOrCNPJField`, `PhoneBRField`, `CEPField`, `UFField`, `CityNameField` (see [Brazilian helpers](br-helpers.md)).
- Pix: `PixKeyField` (CPF/CNPJ/email/phone/random) + `detect_pix_key_type` / `is_valid_pix_key`.
- Money: prefer `CentsField` (integer) to store; `PriceField` on the decimal contract.
- Compose your own with `Annotated[type, Field(...)]` for domain rules.
