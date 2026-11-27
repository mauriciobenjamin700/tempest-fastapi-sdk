# Metadado de schema: `tempest lint-schemas`

Use para cobrar `title`, `description` e `examples` nos campos dos schemas que chegam ao `openapi.json`.

Parte da receita [CLI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/cli.md).

O cliente lê o `openapi.json`, e um campo sem `title`, `description` e
`examples` chega lá só com nome e tipo. Essa regra não sai de `ast`: o
metadado só existe depois que o Pydantic montou o modelo. Por isso ela
**importa** o pacote de schemas (`src.schemas`, ou `app.schemas`), em vez de
ler o código como o `lint-layers`:

```python
# src/schemas/order.py
from pydantic import Field
from tempest_fastapi_sdk import BaseSchema


class AddressSchema(BaseSchema):
    """Endereço de entrega."""

    city: str = Field(title="Cidade", description="Cidade da entrega.")


class OrderCreateSchema(BaseSchema):
    """Pedido novo."""

    total_cents: int
    address: AddressSchema = Field(title="Endereço", description="Onde entregar.")
```

`tempest lint-schemas` (com `src/schemas/__init__.py` exportando os dois;
saída real, exit code `1`):

```text
src.schemas.order.AddressSchema.city: missing examples
src.schemas.order.OrderCreateSchema.total_cents: missing title, description, examples
2 schema field(s) without documentation.
```

- O campo **folha** (um valor: `str`, `int`, enum, `list[str]`) precisa dos
  três. O campo que **guarda outro schema** (`AddressSchema`,
  `list[ItemSchema]`, `ItemSchema | None`) precisa só de `title` e
  `description` — os exemplos são os do schema de dentro. Por isso o
  `address` passa.
- Cada campo sai uma vez, na classe que o declara, mesmo herdado por vários
  schemas exportados. Os campos que o `BaseResponseSchema` traz (`id`,
  `created_at`...) já vêm documentados.
- Título em branco e `examples=[]` contam como ausentes. Pacote que não
  importa sai com exit code `2` e o traceback.

O mesmo check roda **dentro da suíte**, que é como o projeto do
`tempest new` o cobra a cada `tempest check` — o scaffold já traz este
teste:

```python
# tests/unit/architecture/test_schemas.py
import src.schemas
from tempest_fastapi_sdk.testing import assert_schema_metadata


def test_exported_schemas_carry_metadata() -> None:
    """Cenário: os schemas que ``src.schemas`` exporta.

    Esperado: todo campo traz ``title``, ``description`` e ``examples``.
    """
    assert_schema_metadata(src.schemas)
```
