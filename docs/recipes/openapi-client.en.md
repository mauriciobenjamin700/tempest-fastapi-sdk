# Integration client from an OpenAPI spec

Integrating with a third party is, today, manual transcription: you open their
documentation, read field by field, write the equivalent Pydantic schema, choose
the Python name for each field (`createdAt` → `created_at`), wire the `alias` so
the payload still matches the wire — and then write another layer just to
assemble the HTTP calls.

The OpenAPI specification already describes all of it formally. So:

```bash
tempest openapi-client https://api.vendor.com/openapi.json --name vendor
```

```text
  + src/integrations/vendor/__init__.py
  + src/integrations/vendor/client.py
  + src/integrations/vendor/schemas.py
4 schema(s), 12 operation(s).
```

Done. Typed schemas with their metadata filled in, and a typed HTTP client on
top of them. 🚀

## Why this matters

Three problems with manual transcription:

1. **Cost proportional to the API's size.** 40 endpoints and 60 models is a
   whole afternoon of mechanical work.
2. **It gets things wrong, and it rots.** An optional field transcribed as
   required, a forgotten `alias`, an enum copied incompletely — all fail only at
   runtime, against the third party, often only in production.
3. **The documentation is lost.** The spec describes every field with a
   description, a format and an example. None of it survives transcription: the
   schema lands in your repository as a list of names and types.

Item 3 is what the generator attacks hardest: **every `Field` carries the
specification's `title` / `description` / `examples`**, so the generated module
_is_ the integration's documentation — and it outlives the third party changing
or retiring their docs site.

## What you get

```text
src/integrations/vendor/
├── __init__.py     re-exports the client and DEFAULT_BASE_URL
├── schemas.py      one class per component, metadata filled in
└── client.py       one async method per operation
```

!!! info "Why its own package instead of `src/schemas/`"
    A third-party integration is an **outbound adapter**, not your service's DTO
    layer. Dropping 60 generated schemas into `src/schemas/` would collide with
    the hand-written ones and pollute that package's `__init__.py`. A separate,
    fully-generated directory is safe to regenerate — nothing in it is ever
    hand-edited.

    Need it elsewhere? `--out src/vendor/vendor`.

### `schemas.py`

For this slice of specification:

```json
{
  "Customer": {
    "type": "object",
    "description": "A billable customer account.",
    "required": ["id", "emailAddress"],
    "properties": {
      "id": {"type": "string", "format": "uuid", "description": "Server-assigned id."},
      "emailAddress": {"type": "string", "format": "email", "title": "Email",
                       "description": "Primary contact email.", "example": "ana@example.com"},
      "createdAt": {"type": "string", "format": "date-time"},
      "tags": {"type": "array", "items": {"type": "string"}},
      "class": {"type": "string", "description": "Reserved-word field name."}
    }
  }
}
```

you get this:

```python
from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, EmailStr, Field

from tempest_fastapi_sdk import BaseSchema


class Customer(BaseSchema):
    """A billable customer account.

    Attributes:
        id (UUID): Server-assigned id.
        email_address (EmailStr): Primary contact email.
        created_at (datetime | None): Undocumented in the spec.
        tags (list[str]): Undocumented in the spec.
        class_ (str | None): Reserved-word field name.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(description="Server-assigned id.")
    email_address: EmailStr = Field(
        alias="emailAddress",
        title="Email",
        description="Primary contact email.",
        examples=["ana@example.com"],
    )
    created_at: datetime | None = Field(alias="createdAt", default=None)
    tags: list[str] = Field(default_factory=list)
    class_: str | None = Field(
        alias="class",
        description="Reserved-word field name.",
        default=None,
    )
```

Five things happening there:

- **Python names + `alias`.** `emailAddress` → `email_address`, with the wire
  name preserved. `populate_by_name=True` makes the schema accept **both** on
  input; `model_dump(by_alias=True)` gives back the wire shape.
- **Reserved word resolved.** `class` → `class_`, alias intact.
- **`format` becomes a rich type.** `uuid` → `UUID`, `date-time` → `datetime`,
  `email` → `EmailStr`.
- **An optional collection is an empty list**, never `list[X] | None` — the
  project rule: "no matches" is an empty list, not a missing value.
- **Metadata filled in**, and **nothing invented**: a field with no
  `description` upstream gets none (the docstring says `Undocumented in the
  spec.`).

### `client.py`

```python
from tempest_fastapi_sdk import HTTPClient

from src.integrations.billing import Customer, CustomerStatus


class VendorClient:
    """Client for Billing API (version 2.1.0)."""

    def __init__(self, client: HTTPClient) -> None:
        """Initialize the client.

        Args:
            client (HTTPClient): The transport to issue requests through.
        """
        self._client: HTTPClient = client

    async def list_customers(
        self,
        *,
        page_size: int | None = None,
        status: CustomerStatus | None = None,
    ) -> list[Customer]:
        """List customers.

        Args:
            page_size (int | None): Rows per page. Omitted from the query when None.
            status (CustomerStatus | None): The status value. Omitted when None.

        Returns:
            list[Customer]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification
                documents 401.
        """
```

The client takes an [`HTTPClient`](http-client.md) **by injection** — it never
builds one. So the retry policy, backoff, circuit breaker, timeout and
credentials all stay yours:

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import HTTPClient

from src.core.settings import settings
from src.integrations.vendor import DEFAULT_BASE_URL, VendorClient

vendor_http: HTTPClient = HTTPClient(
    base_url=DEFAULT_BASE_URL,
    default_headers={"Authorization": f"Bearer {settings.VENDOR_TOKEN}"},
    timeout=15.0,
)
vendor: VendorClient = VendorClient(vendor_http)
```

And using it:

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient

from src.integrations.billing import CustomerStatus

vendor = HTTPClient(base_url="https://api.parceiro.com")


async def main() -> None:
    """Run this example."""
    customers = await vendor.list_customers(
        page_size=25,
        status=CustomerStatus.PAST_DUE,
    )
    for customer in customers:
        print(customer.email_address, customer.created_at)


asyncio.run(main())
```

!!! check "Testable without a network"
    Because the transport is injected, an `httpx.MockTransport` covers the whole
    integration in your tests:

    ```python
    import httpx
    from tempest_fastapi_sdk import HTTPClient

    from src.integrations.vendor import DEFAULT_BASE_URL, VendorClient


    def handler(request: httpx.Request) -> httpx.Response:
        """Answer the call without leaving the machine."""
        return httpx.Response(200, json=[])


    async def test_list() -> None:
        """Exercise the generated client against a fake transport."""
        http = HTTPClient(
            base_url=DEFAULT_BASE_URL,
            transport=httpx.MockTransport(handler),
        )
        async with http:
            assert await VendorClient(http).list_customers() == []
    ```

!!! warning "The generated client needs the `[http]` extra"
    `HTTPClient` raises `ImportError` without it.
    `uv add "tempest-fastapi-sdk[http]"`.

## Options

| Option | Effect |
| --- | --- |
| `<spec>` (argument) | URL (`http(s)://`) or path of the specification |
| `--name` / `-n` | Integration name — becomes the directory and the class prefix. Defaults to a slug of `info.title` |
| `--out` / `-o` | Destination. Defaults to `<src\|app>/integrations/<name>/` |
| `--header` / `-H` | Header for fetching the spec (`"Authorization: Bearer ..."`). Repeatable |
| `--path` / `-p` | Project root used to resolve the default destination |
| `--schemas-only` | Do not generate `client.py` |
| `--force` / `-f` | Overwrite what already exists |
| `--no-format` | Skip the `ruff format` pass over the result |

### A specification behind authentication

```bash
tempest openapi-client https://api.vendor.com/openapi.json \
    --name vendor \
    --header "Authorization: Bearer $VENDOR_TOKEN"
```

### A YAML specification

Works, but needs the `[openapi]` extra (PyYAML). JSON needs nothing beyond the
standard library.

```bash
uv add "tempest-fastapi-sdk[openapi]"
tempest openapi-client ./vendor/vendor.yaml --name vendor
```

Without the extra, the message says exactly that instead of raising a traceback.

### Refreshing when the third party versions their API

The directory is **entirely generated**, so regenerating is safe:

```bash
tempest openapi-client https://api.vendor.com/openapi.json --name vendor --force
```

!!! tip "The diff is the integration's changelog"
    Running against an unchanged spec produces a **byte-for-byte identical**
    file (there is a test for it). So any line that shows up in `git diff` after
    a `--force` is a real change from the third party — a new field, a field
    that became required, an enum that gained a value.

## OpenAPI coverage

What the generator represents, stated:

| Construct | Handling |
| --- | --- |
| `type: object` + `properties` | Class inheriting `BaseSchema` |
| `required` | Field with no default; absent → `X \| None = None` |
| `string`/`integer`/`number`/`boolean` | `str`/`int`/`float`/`bool` |
| `format: date-time`/`date`/`time` | `datetime`/`date`/`time` |
| `format: uuid`/`email`/`binary`/`decimal` | `UUID`/`EmailStr`/`bytes`/`Decimal` |
| `type: array` | `list[T]`; not required → `Field(default_factory=list)` |
| String / integer `enum` | Subclass of `BaseStrEnum` / `BaseIntEnum` |
| Internal `$ref` | Reference to the generated class, dependency-ordered |
| `allOf` | Flattened into a single model |
| `oneOf` / `anyOf` | `A \| B`; with `discriminator`, `Annotated[..., Field(discriminator=...)]` |
| `nullable: true` (3.0) / `type: [x, "null"]` (3.1) | `X \| None` |
| `additionalProperties` | `dict[str, T]` |
| `minLength` / `maximum` / `pattern` / `minItems` / … | `Field` constraints |
| Recursive / mutually recursive | Deferred annotations + `model_rebuild()` at the end of the module |
| `path` and `query` parameters | Typed method arguments |
| `application/json` body and response | Generated schema |
| 204 / no-content response | `None` return |

And what is **not** represented — always as `Any` plus a note in the command's
summary, never silently:

| Construct | Reason |
| --- | --- |
| `not` | No Python equivalent |
| External `$ref` | Bundle the spec first (`redocly bundle`) |
| Swagger 2.0 | Convert to OpenAPI 3 (`swagger2openapi`) |
| `header` / `cookie` parameters | Pass them via `HTTPClient(default_headers=...)` |
| Non-JSON body/response (`multipart`, `octet-stream`) | Out of scope for this iteration |
| `type` with several concrete values | Not modelled |

!!! danger "It never guesses"
    The parser's contract: whatever it cannot represent becomes `Any`, gets a
    `# openapi: unsupported ...` comment, and shows up in the command's summary:

    ```text
    1 construct(s) could not be modelled (rendered as Any, marked in the output):
      - 'header' parameter 'X-Trace' skipped (pass it via HTTPClient default_headers)
    ```

    A wrong schema that **looks** right is worse than a documented gap.

## The generated code passes your gates

The emitter produces code that passes `ruff check` and `ruff format --check`
**before** any formatting pass — full annotations, double quotes, Google-style
docstrings on every module/class/method, imports in isort order.

That is tested, not promised: the suite runs `ruff` against the raw output
(`--no-format`). It is worth knowing why — that one assertion caught an
un-imported `UUID`, an un-imported enum, an over-long docstring line and two
import-ordering mistakes that no assertion about the schemas' *shape* would have
noticed.

So `--no-format` (or a machine with no `ruff` installed) still yields a usable
package. The `ruff` pass the command runs by default is polish, not correctness.

## Recap

1. **`tempest openapi-client <spec> --name X`** generates `src/integrations/x/`
   with `schemas.py` + `client.py`.
2. **Python names with an `alias`** for the wire name, and `populate_by_name` so
   both are accepted on input.
3. **The spec's metadata on every `Field`** — the generated module is the
   integration's documentation. Nothing is invented.
4. **The client takes an injected `HTTPClient`**, so retry / circuit breaker /
   credentials stay yours, and `httpx.MockTransport` tests everything offline.
5. **`--force` regenerates**, and since an unchanged spec yields an identical
   file, the diff shows exactly what the third party changed.
6. **What is unsupported becomes `Any` + a note**, never silence.
