# Campos validados (tipos prontos)

Em vez de repetir `Field(gt=0, ...)` em cada schema, o SDK traz tipos
`Annotated` com a regra de validação embutida — o campo passa a se
**autodescrever**. Seguem a mesma convenção `*Field` dos campos BR
(`CPFField`, `UFField`, ...): tudo que termina em `Field` é um tipo de
campo pronto pra schema. Valor inválido vira `ValidationError` → HTTP 422
pelo handler do SDK.

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CentsField, PercentField, SlugField


class ProductCreateSchema(BaseSchema):
    slug: SlugField              # kebab-case minúsculo
    price_cents: CentsField      # int >= 0 (dinheiro em centavos)
    discount: PercentField       # float em [0, 100]
```

## Inteiros

| Tipo | Regra | Uso |
| --- | --- | --- |
| `PositiveIntField` | `> 0` | quantidades, contagens, ids 1-based |
| `NonNegativeIntField` | `>= 0` | contadores que zeram |
| `CentsField` | `>= 0` | dinheiro em unidades menores (centavos) |
| `PortField` | `1..65535` | porta TCP/UDP |

!!! tip "Dinheiro em centavos"
    Guarde valores monetários como **inteiro de centavos** (`CentsField`)
    pra fugir do arredondamento de float binário. Divida por 100 só na
    borda de apresentação. Quando o contrato exige decimal exato (ex.:
    payload de gateway), use `PriceField`.

## Floats

| Tipo | Regra |
| --- | --- |
| `PositiveFloatField` | `> 0` |
| `NonNegativeFloatField` | `>= 0` |
| `PercentField` | `0..100` |
| `RatioField` | `0..1` (frações/probabilidades) |
| `LatitudeField` | `-90..90` |
| `LongitudeField` | `-180..180` |

## Decimais

`PriceField` é um `Decimal` não-negativo com 2 casas:

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import PriceField


class InvoiceSchema(BaseSchema):
    total: PriceField     # "9.99" -> Decimal("9.99"); "1.999" e "-1" rejeitados
```

## Strings

| Tipo | Regra |
| --- | --- |
| `NonEmptyStrField` | apara espaços e rejeita vazio (só-espaço também) |
| `SlugField` | kebab-case minúsculo (`meu-post-1`) |
| `HexColorField` | cor hex CSS (`#fff` ou `#abc123`) |

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import HexColorField, NonEmptyStrField, SlugField


class CategorySchema(BaseSchema):
    name: NonEmptyStrField     # "  Bebidas " -> "Bebidas"; "   " rejeitado
    slug: SlugField
    color: HexColorField
```

## Recap

- Tudo `Annotated[..., regra]`, com sufixo `*Field` — autodescritivo,
  sem repetir `Field(...)` no schema.
- Inteiros: `PositiveIntField`, `NonNegativeIntField`, `CentsField`, `PortField`.
- Floats: `PositiveFloatField`, `NonNegativeFloatField`, `PercentField`,
  `RatioField`, `LatitudeField`, `LongitudeField`.
- Decimal: `PriceField` (2 casas, `>= 0`).
- Strings: `NonEmptyStrField`, `SlugField`, `HexColorField`.
- Dinheiro: prefira `CentsField` (inteiro) pra guardar; `PriceField` no contrato decimal.
