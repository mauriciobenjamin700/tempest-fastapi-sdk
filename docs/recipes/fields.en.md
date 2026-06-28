# Validated fields (ready-made types)

Instead of repeating `Field(gt=0, ...)` on every schema, the SDK ships
`Annotated` types with the validation rule baked in — the field becomes
**self-describing**. They follow the same `*Field` convention as the BR
fields (`CPFField`, `UFField`, ...): anything ending in `Field` is a
ready-to-use schema field type. Invalid values raise `ValidationError` ->
HTTP 422 via the SDK exception handler.

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
    dodge binary float rounding. Divide by 100 only at the presentation
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

## Recap

- Everything is `Annotated[..., rule]` with a `*Field` suffix —
  self-describing, no repeated `Field(...)` in the schema.
- Integers: `PositiveIntField`, `NonNegativeIntField`, `CentsField`, `PortField`.
- Floats: `PositiveFloatField`, `NonNegativeFloatField`, `PercentField`,
  `RatioField`, `LatitudeField`, `LongitudeField`.
- Decimal: `PriceField` (2 places, `>= 0`).
- Strings: `NonEmptyStrField`, `SlugField`, `HexColorField`.
- Money: prefer `CentsField` (integer) for storage; `PriceField` for a decimal contract.
