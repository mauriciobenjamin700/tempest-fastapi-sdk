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

And what is **not** represented — always with a line in the command's summary,
never silently:

| Construct | Reason |
| --- | --- |
| `not` | No Python equivalent |
| External `$ref` | Bundle the spec first (`redocly bundle`) |
| Swagger 2.0 | Convert to OpenAPI 3 (`swagger2openapi`) |
| `header` / `cookie` parameters | Pass them via `HTTPClient(default_headers=...)` |
| Non-JSON body/response (`multipart`, `octet-stream`) | Out of scope for this iteration |
| `type` with several concrete values | Not modelled |

!!! danger "It never guesses"
    The parser's contract: whatever it cannot represent as the spec wrote it
    becomes a line in the summary, and **each line says what was done
    instead** — became `Any`, was skipped, was synthesized:

    ```text
    1 construct(s) could not be modelled as written — each line says what was
    generated instead, and the ones with something to mark carry an
    `# openapi: unsupported` comment in the output:
      - 'header' parameter 'X-Trace' skipped (pass it via HTTPClient default_headers)
    ```

    A wrong schema that **looks** right is worse than a documented gap.

!!! check "The gap is marked in the file, not only in the terminal"
    The summary scrolls out of the terminal. Someone opening `schemas.py` six
    months from now and finding an `Any` needs the reason **right next to
    it**, so the generator writes a comment above the affected line:

    ```python
    # openapi: unsupported — `not` in ThingWeird rendered as Any (no Python
    #   equivalent)
    weird: Any | None = None
    ```

    It covers fields, methods (a `multipart` body, an unmodelled response) and
    synthesized parameters. It is greppable on purpose:
    `grep -rn "openapi: unsupported" src/integrations/` lists everything the
    integration lost. A gap with nothing in the file to mark — a dropped
    `header` parameter, say — stays summary-only, because there is no line to
    comment on.

## The generated code passes your gates

The emitter produces code that passes `ruff check` and `ruff format --check`
**before** any formatting pass — full annotations, Google-style docstrings on
every module/class/method, imports in isort order, and quotes in the style
`ruff format` normalizes to (double, save for the case explained
[just below](#the-specs-text-does-not-break-the-module)).

That is tested, not promised: the suite runs `ruff` against the raw output
(`--no-format`). It is worth knowing why — that one assertion caught an
un-imported `UUID`, an un-imported enum, an over-long docstring line and two
import-ordering mistakes that no assertion about the schemas' *shape* would have
noticed.

So `--no-format` (or a machine with no `ruff` installed) still yields a usable
package. The `ruff` pass the command runs by default is polish, not correctness.

### The spec's text does not break the module

The spec's prose ends up in the source: in a docstring, in a `title`, in a
`description`, in an enum value. And the third party writes whatever they like
there — quotes, an apostrophe, a backslash, a line break carried over from a
YAML block, a sentence too long for the line. Each of those has produced a
package that did not import, did not lint, or silently changed what the spec
said.

Here it is on a concrete case. This one property carries four traps at once —
quotes in the `title`, a `\#` in the description, text too long for the line,
and a wire name starting with a digit:

```json
{
  "Charge": {
    "type": "object",
    "required": ["reference"],
    "properties": {
      "reference": {
        "type": "string",
        "title": "The payer's \"reference\"",
        "description": "Encode the characters (%, \\#, /) before sending, because the gateway rejects the request and the error it answers with does not say which character was at fault."
      },
      "2fa": {"type": "boolean"}
    }
  }
}
```

And this comes out — code that passes `ruff check` and `ruff format --check`
with no formatting pass over it:

```python
from pydantic import ConfigDict, Field

from tempest_fastapi_sdk import BaseSchema


class Charge(BaseSchema):
    r"""Schema generated for Charge.

    Attributes:
        reference (str): Encode the characters (%, \#, /) before sending, because the
            gateway rejects the request and the error it answers with does not say which
            character was at fault.
        field_2fa (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    reference: str = Field(
        title='The payer\'s "reference"',
        description=(
            "Encode the characters (%, \\#, /) before sending, because the gateway "
            "rejects the request and the error it answers with does not say which "
            "character was at fault."
        ),
    )
    field_2fa: bool | None = Field(alias="2fa", default=None)
```

Four decisions in that output, none of them obvious:

1. **The docstring became `r"""`.** `\#` is not a Python escape: without the
   `r`, that is `W605` in the lint and a `SyntaxWarning` from 3.12 on.
2. **The `title` came out single-quoted**, despite the project's double-quote
   rule.
3. **The `description` was split into adjacent literals** rather than left on a
   long line.
4. **`2fa` became `field_2fa`** with `alias="2fa"` — covered in the
   [next section](#names-and-paths-the-spec-gets-wrong).

The middle two share one cause, and it is worth understanding:

!!! info "Why single quotes, when the project's rule is double"
    Because `ruff format` normalizes to whichever **escapes less**: text with
    more `"` than `'` comes out single-quoted. Emitting
    `title="The payer's \"reference\""` there is correct, readable code — that
    fails the consumer's `ruff format --check` on their very first run. The
    generator does not fight the formatter on the other side; it reproduces its
    rule.

!!! warning "`ruff format` never breaks a string"
    An over-long `description` **survives the format pass intact** and blows the
    consumer's `E501`. So the emitter splits — into **two or more** pieces,
    because a lone parenthesized literal is joined straight back onto the long
    line. Splitting into one piece is not splitting.

    The text returns character for character: concatenating the emitted literals
    reproduces the original description, whitespace included. Nothing is
    summarized or truncated.

Summing up what the emitter guarantees for any text the spec carries:

| In the spec | In the generated code |
| --- | --- |
| A backslash (`\#`, `\b`, `\x41`) | Escaped in the literal, and the docstring becomes `r"""` |
| Line break, tab, control character | `\n` / `\t` / `\xNN` in the literal |
| `"quotes"` in the text | A **single**-quoted literal |
| An over-long description | Split into two or more adjacent literals |
| An over-long enum value | Split the same way, and the member **name** is shortened — the value, never |

### Names and paths the spec gets wrong

The same tests cover the other side — when the spec names something Python will
not take, or describes a path that does not agree with the parameters it
declares:

| In the spec | In the generated code |
| --- | --- |
| A `2fa` property | `field_2fa` with `alias="2fa"` |
| `transaction` and `Transaction` together | `Transaction` and `Transaction2` |
| A `path` parameter the template never interpolates | Dropped, with a note |
| A placeholder no parameter declares | Synthesized as a required `str`, with a note |
| Path parameters out of order | Reordered by their position in the template |

!!! tip "The less obvious choices"
    The prefix is `field_`, not `_`: a leading underscore makes Pydantic treat
    the attribute as **private**, so the field would vanish from the model
    rather than merely be renamed. `Transaction_2` is not CapWords and fails the
    consumer's `N801`. A parameter the request never carries is worse than a
    missing one: the caller passes an identifier and it is silently dropped on
    the floor. And an undeclared placeholder **cannot** be skipped — the path is
    an f-string, so the module would reference a name that does not exist and
    would not even import.

!!! danger "Every one of these repairs shows up in the summary"
    Dropping one parameter and synthesizing another are decisions about the
    signature **you** are going to call, so they come out in the command's
    summary:

    ```text
    2 construct(s) could not be modelled (rendered as Any, marked in the output):
      - path parameter 'expand' of '/accounts/{accountId}' is declared but absent
        from the path template — skipped, since the value would never reach the request
      - path '/receipts/{receiptId}' interpolates 'receiptId', which no parameter
        declares — generated as a required str
    ```

    The synthesized parameter is marked in `client.py` too, above the method —
    the dropped one is not, because no line was left to comment on.

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
6. **The spec's prose does not break the module** — quotes, `\#`, line breaks
   and over-long text come out as valid literals that pass `ruff format --check`
   on your side, with the text intact.
7. **A name or path the spec gets wrong is repaired, never guessed** — and the
   repair shows up in the command's summary.
8. **What is unsupported becomes a line in the summary** and an
   `# openapi: unsupported` comment in the file, never silence.
