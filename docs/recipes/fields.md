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

## Chave Pix

`PixKeyField` valida os **cinco tipos** de chave Pix do BACEN num campo só
e normaliza pra forma canônica. Chave inválida → `ValidationError` → 422.

| Tipo | Exemplo | Normaliza pra |
| --- | --- | --- |
| CPF | `529.982.247-25` | `52998224725` (dígitos) |
| CNPJ | `11.222.333/0001-81` | `11222333000181` (dígitos) |
| E-mail | `Ana@Example.com` | `ana@example.com` (minúsculo) |
| Telefone | `+5511999998888` | `+5511999998888` (E.164) |
| Aleatória (EVP) | `123e4567-e89b-12d3-a456-426614174000` | mesmo, minúsculo (UUID) |

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import PixKeyField


class PixTransferSchema(BaseSchema):
    to_key: PixKeyField      # aceita CPF/CNPJ/e-mail/telefone/aleatória
    amount_cents: CentsField
```

Precisa saber **qual** tipo a chave é (roteamento, UI, regra de negócio)?
Use os helpers — não exigem o campo:

```python
from tempest_fastapi_sdk.utils import PixKeyType, detect_pix_key_type, is_valid_pix_key


detect_pix_key_type("ana@example.com")   # -> PixKeyType.EMAIL
detect_pix_key_type("+5511999998888")    # -> PixKeyType.PHONE
detect_pix_key_type("chave-invalida")    # -> None
is_valid_pix_key("529.982.247-25")       # -> True
```

!!! info "Validação por forma + dígitos"
    A detecção é por formato: `@` → e-mail, `+` inicial → telefone E.164,
    UUID → aleatória; senão, dígitos + **dígitos verificadores** decidem
    entre CPF e CNPJ (um CPF com DV errado é rejeitado). O telefone segue
    E.164 brasileiro (`+55` + DDD + número, 10–11 dígitos após o `+55`).

## Exemplo completo (schema + rota + 422)

Os tipos são `Annotated` puros — funcionam em qualquer `BaseSchema` e o
FastAPI já converte o `ValidationError` em **422** pelo handler do SDK.
Programa completo:

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
    """Cria um produto. Payload inválido nunca chega aqui — vira 422."""
    return {"slug": payload.slug}
```

Um `POST` inválido (`price_cents: -5`, `slug: "Não Slug"`) recebe **422**
com o corpo que o handler do SDK padroniza:

```json
{
  "detail": [
    {"loc": ["body", "price_cents"], "msg": "Input should be greater than or equal to 0"},
    {"loc": ["body", "slug"], "msg": "String should match pattern ..."}
  ]
}
```

!!! tip "Opcional, default e lista"
    Compõem como qualquer tipo Pydantic: `CentsField | None = None`,
    `discount: PercentField = 0.0`, `list[SlugField]`. A regra continua
    valendo em cada elemento da lista.

## Compondo seus próprios campos

Todo `*Field` é só um `Annotated[tipo, Field(...)]`. Precisa de uma regra
que o SDK não traz? Monte a sua com o mesmo padrão — nada de mágica:

```python
from typing import Annotated

from pydantic import Field

# Quantidade em estoque: inteiro de 0 a 100000.
StockField = Annotated[int, Field(ge=0, le=100_000)]

# Sigla de 3 letras maiúsculas (ex.: moeda ISO-4217).
CurrencyCodeField = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class SkuSchema(BaseSchema):
    stock: StockField
    currency: CurrencyCodeField      # "BRL" ok; "brl" / "REAL" rejeitados
```

Prefira os tipos do SDK quando existir um equivalente (autodescrição +
consistência entre serviços); crie os seus só pras regras específicas do
domínio.

!!! warning "Pegadinhas comuns"
    - **`CentsField` vs `PriceField`** — guarde dinheiro como inteiro de
      centavos (`CentsField`) pra fugir do float binário; use `PriceField`
      (`Decimal`, 2 casas) só quando o **contrato** exige decimal.
    - **`PercentField` vs `RatioField`** — `Percent` é `0..100` (o humano
      digita "15"); `Ratio` é `0..1` (fração/probabilidade, "0.15"). Não
      misture os dois no mesmo fluxo.
    - **`NonEmptyStrField`** apara espaços **antes** de validar — `"  "`
      (só espaço) é rejeitado como vazio.

## Recap

- Tudo `Annotated[..., regra]`, com sufixo `*Field` — autodescritivo,
  sem repetir `Field(...)` no schema.
- Inteiros: `PositiveIntField`, `NonNegativeIntField`, `CentsField`, `PortField`.
- Floats: `PositiveFloatField`, `NonNegativeFloatField`, `PercentField`,
  `RatioField`, `LatitudeField`, `LongitudeField`.
- Decimal: `PriceField` (2 casas, `>= 0`).
- Strings: `NonEmptyStrField`, `SlugField`, `HexColorField`.
- BR: `CPFField`, `CNPJField`, `CPFOrCNPJField`, `PhoneBRField`, `CEPField`, `UFField`, `CityNameField` (veja [Helpers brasileiros](br-helpers.md)).
- Pix: `PixKeyField` (CPF/CNPJ/e-mail/telefone/aleatória) + `detect_pix_key_type` / `is_valid_pix_key`.
- Dinheiro: prefira `CentsField` (inteiro) pra guardar; `PriceField` no contrato decimal.
- Componha os seus com `Annotated[tipo, Field(...)]` pras regras do domínio.
