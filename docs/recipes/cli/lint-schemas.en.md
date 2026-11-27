# Schema metadata: `tempest lint-schemas`

Use to demand `title`, `description` and `examples` on the schema fields that reach `openapi.json`.

Part of the [CLI](../cli.md) recipe.

The client reads the `openapi.json`, and a field without `title`,
`description` and `examples` arrives there with a name and a type only. This
rule cannot come from `ast`: the metadata only exists once Pydantic built
the model. So it **imports** the schemas package (`src.schemas`, or
`app.schemas`) instead of reading the source like `lint-layers`:

```python
# src/schemas/order.py
from pydantic import Field
from tempest_fastapi_sdk import BaseSchema


class AddressSchema(BaseSchema):
    """Delivery address."""

    city: str = Field(title="City", description="Delivery city.")


class OrderCreateSchema(BaseSchema):
    """A new order."""

    total_cents: int
    address: AddressSchema = Field(title="Address", description="Where to deliver.")
```

`tempest lint-schemas` (with `src/schemas/__init__.py` exporting both; real
output, exit code `1`):

```text
src.schemas.order.AddressSchema.city: missing examples
src.schemas.order.OrderCreateSchema.total_cents: missing title, description, examples
2 schema field(s) without documentation.
```

- A **leaf** field (a value: `str`, `int`, an enum, `list[str]`) needs all
  three. A field **holding another schema** (`AddressSchema`,
  `list[ItemSchema]`, `ItemSchema | None`) needs only `title` and
  `description` — its examples are the nested schema's. That is why
  `address` passes.
- Each field is reported once, on the class declaring it, however many
  exported schemas inherit it. The fields `BaseResponseSchema` brings (`id`,
  `created_at`...) are already documented.
- A blank title and `examples=[]` count as missing. A package that does not
  import exits `2` with the traceback.

The same check runs **inside the suite**, which is how the `tempest new`
project enforces it on every `tempest check` — the scaffold already ships
this test:

```python
# tests/unit/architecture/test_schemas.py
import src.schemas
from tempest_fastapi_sdk.testing import assert_schema_metadata


def test_exported_schemas_carry_metadata() -> None:
    """Cenário: the schemas ``src.schemas`` exports.

    Esperado: every field carries ``title``, ``description`` and ``examples``.
    """
    assert_schema_metadata(src.schemas)
```
