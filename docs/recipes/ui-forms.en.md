# Forms from Pydantic schemas

The schema that validates the request already describes the form: names,
types, defaults, bounds, titles and descriptions. Writing that form twice
— once in HTML, once in Pydantic — is what this recipe deletes.

!!! tip "When to use"
    - You have a `*CreateSchema` / `*UpdateSchema` and need the screen
      that fills it.
    - You want real validation (Pydantic's) with per-field messages and
      the reader's input kept.
    - You do not want hand-written `<input>`s drifting from the schema.

## The whole round trip, in one file

```python
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, EmailStr, Field

from tempest_fastapi_sdk.ssr import html_response
from tempest_fastapi_sdk.ui.forms import form_for, parse_form

app: FastAPI = FastAPI()


class SignupSchema(BaseModel):
    """The signup payload — and the description of the form."""

    email: EmailStr
    full_name: str = Field(min_length=3, max_length=50, description="Full name")
    password: str = Field(min_length=8)


@app.get("/signup")
async def signup_form() -> Response:
    """Show the empty form."""
    return html_response(
        form_for(SignupSchema, action="/signup"),
        title="Sign up",
        stylesheets=["/static/app.css"],
    )


@app.post("/signup")
async def signup(request: Request) -> Response:
    """Validate the submission, re-rendering the screen when it fails."""
    result = await parse_form(SignupSchema, request)
    if not result.ok:
        return html_response(
            form_for(
                SignupSchema,
                action="/signup",
                values=result.values,
                errors=result.errors,
                form_errors=result.form_errors,
            ),
            title="Sign up",
            status_code=422,
            stylesheets=["/static/app.css"],
        )
    user = result.unwrap()
    return RedirectResponse(f"/welcome?email={user.email}", status_code=303)
```

Three calls:

1. **`form_for`** generates the `<form>` widget tree from the schema.
2. **`parse_form`** reads the body, fixes what HTML cannot express, and
   validates.
3. Failed? The same `form_for` takes `values=` and `errors=` from the
   result, and the screen comes back with the messages in place and the
   text preserved.

## What each type produces

The table is the complete rule, in evaluation order:

| Schema field | Control |
| --- | --- |
| `ui` override in `json_schema_extra` | whatever it names |
| `Enum` / `Literal` | `<select>` |
| `bool` | `<input type="checkbox">` |
| `int` | `number` with `step="1"` |
| `float` / `Decimal` | `number` with `step="any"` |
| `EmailStr` | `email` |
| `HttpUrl` / `AnyUrl` | `url` |
| `SecretStr`, or a name containing `password`/`senha` | `password` |
| `date` / `datetime` / `time` | `date` / `datetime-local` / `time` |
| `UUID` | `text` |
| `str` with `max_length > 255` | `<textarea>` |
| `str` | `text` |
| `list[...]` of enumerated values | `<select multiple>` |
| other `list[...]` | `<textarea>`, one value per line |

Schema bounds become native validation attributes, so the browser stops
the obvious before the round trip:

```python
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.ui.forms import fields_for


class ProductSchema(BaseModel):
    """A product with declared bounds."""

    name: str = Field(min_length=3, max_length=50)
    quantity: int = Field(ge=1, le=99)


specs = fields_for(ProductSchema)
assert specs[0].constraints == {"minlength": "3", "maxlength": "50"}
assert specs[1].constraints == {"min": "1", "max": "99", "step": "1"}
```

!!! warning "`gt` and `lt` become `min` and `max`"
    HTML only has inclusive bounds. A `Field(gt=0)` emits `min="0"`,
    a hint one step looser than the schema. Pydantic still rejects zero
    on submit — the real validation was never in the browser.

## The HTML is accessible by default

Each field renders like this:

```html
<div class="tui-field tui-field--invalid">
  <label class="tui-field__label" for="f-email">
    <span>Email</span><span class="tui-field__required" aria-hidden="true">*</span>
  </label>
  <input name="email" id="f-email" class="tui-field__control" required="required"
         aria-invalid="true" aria-describedby="f-email-error"
         autocomplete="email" type="email" />
  <p class="tui-field__error" id="f-email-error">already registered</p>
</div>
```

What you get for free: a `<label for>` bound to the control,
`aria-invalid` on the failing field, `aria-describedby` pointing at both
the hint and the message, `autocomplete` where the type implies one, and
the required asterisk marked `aria-hidden` (the real signal is
`required`).

Two forms on the same page? Give each its own `id_prefix` and the
`id`/`for` pairs stop colliding:

```python
from pydantic import BaseModel

from tempest_fastapi_sdk.ui.forms import form_for


class SearchSchema(BaseModel):
    """A search filter."""

    term: str


widget = form_for(SearchSchema, action="/search", method="get", id_prefix="search")
```

## Tuning a field

For what introspection cannot guess, declare it on the schema itself:

```python
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.ui.forms import form_for


class ArticleSchema(BaseModel):
    """An article carrying presentation hints on the schema."""

    title: str
    body: str = Field(
        default="",
        json_schema_extra={
            "ui": {
                "control": "textarea",
                "rows": 12,
                "label": "Body",
                "placeholder": "Write in Markdown…",
                "help_text": "Markdown accepted",
            },
        },
    )
    owner_id: str = Field(default="", json_schema_extra={"ui": {"hidden": True}})


widget = form_for(ArticleSchema, action="/articles", exclude=["owner_id"])
```

Keys accepted under `ui`: `control`, `input_type`, `label`,
`placeholder`, `help_text`, `autocomplete`, `rows`, `hidden`, `attrs`.

## When the schema is not enough: edit the specification

`form_for` is sugar over two steps. Split them when you want to patch the
generated form before rendering:

```python
from dataclasses import replace

from pydantic import BaseModel

from tempest_fastapi_sdk.ui.forms import form_spec_for, render_form


class ContactSchema(BaseModel):
    """A contact message."""

    email: str
    message: str


spec = form_spec_for(ContactSchema, action="/contact", submit_label="Send message")
spec = replace(
    spec,
    fields=[
        replace(field, placeholder="you@example.com") if field.name == "email" else field
        for field in spec.fields
    ],
)
widget = render_form(spec)
```

`FormSpec` and `FieldSpec` are frozen dataclasses: `replace()` returns an
altered copy and nothing mutates under you.

## Reading the submission

`parse_form` handles the three things HTML does differently from your
schema:

```python
from fastapi import Request
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.ui.forms import parse_form


class PreferencesSchema(BaseModel):
    """Account preferences."""

    newsletter: bool = True
    tags: list[str] = Field(default_factory=list)
    nickname: str | None = None


async def save(request: Request) -> str:
    """Read the form and return a summary."""
    result = await parse_form(PreferencesSchema, request)
    if not result.ok:
        return "invalid"
    return f"{result.unwrap().newsletter}"
```

- **An unchecked checkbox submits nothing** — the absent key means
  `False`, not "missing field".
- **A key the body never carried stays out of the payload**, so the
  schema default applies and a required field reports `Field required`
  against itself.
- **Empty text in an optional field becomes `None`**, not `""`.
- **`<select multiple>`** repeats the key; a list `textarea` sends lines.
  Both become the same `list`.

Values the server owns should never leave the browser's reach:

```python
from fastapi import Request
from pydantic import BaseModel

from tempest_fastapi_sdk.ui.forms import parse_form


class OrderSchema(BaseModel):
    """An order."""

    product: str
    owner_id: str


async def create(request: Request, current_user_id: str) -> str:
    """Read the order, ignoring any owner the form submitted."""
    result = await parse_form(
        OrderSchema,
        request,
        exclude=["owner_id"],
        extra={"owner_id": current_user_id},
    )
    return "ok" if result.ok else "invalid"
```

`exclude=` forbids reading that field from the body and `extra=` injects
the server-side value. Keys that do not belong to the schema (a CSRF
token, HTMX bookkeeping) are ignored either way.

To reword Pydantic's messages, pass `error_message`:

```python
from collections.abc import Mapping
from typing import Any

MESSAGES: dict[str, str] = {
    "string_too_short": "Too short.",
    "value_error": "Invalid value.",
    "missing": "This field is required.",
}


def translate(error: Mapping[str, Any]) -> str:
    """Map a raw Pydantic error to the message shown on screen."""
    return MESSAGES.get(str(error["type"]), str(error["msg"]))
```

## The look comes along

The classes a form emits (`tui-form`, `tui-field`, …) already have rules,
written against the design tokens:

```python
from tempest_fastapi_sdk.ui import app_stylesheet
from tempest_fastapi_sdk.ui.css import make_css_router

router = make_css_router(app_stylesheet())
```

`app_stylesheet()` includes the form and component rules. If you assemble
the sheet piece by piece, use `form_stylesheet()`. To plug into a design
system of your own, pass `classes=FormClasses(...)` to both `form_for`
and `form_stylesheet` — the names follow.

## Limits, measured

!!! danger "Two field kinds stop generation on purpose"
    - **A nested model** raises `UnsupportedFieldError`: it needs a form
      of its own, or an `exclude=` and a server-set value.
    - **A binary field (`bytes`)** raises too: an upload is `UploadFile`
      on the route, not a value coerced from a string.

    Failing loudly beats rendering a control that can never complete the
    round trip.

!!! info "Why not `tempest_core`'s `Input` / `Dropdown`"
    Measured against the HTML renderer, on `tempest-core` 0.18.0:
    `Form()` renders as `<div></div>` — no `action`, no `method`, not a
    `<form>` — and **none** of the controls renders a `name`, so a form
    built from them posts an empty body: a failure with no error message
    anywhere. Those widgets belong to the reactive client, not to SSR.

    The measurement changed shape in 0.18.0, and what upstream fixed is
    worth recording: up to 0.14.0 `Dropdown` and `TextArea` rendered an
    empty `<div>`, losing both the element type and the option list.
    Today the tags are right — `<input>`, `<textarea>`, and a `<select>`
    carrying its `<option>` list. What decides is still the missing
    `name`.

    So `ui.forms` emits the elements directly through the documented
    `tag`/`attrs` escape hatch. The measurement is pinned in
    `tests/ui/test_core_contract.py`.

## Recap

- The schema describes the form; `form_for` renders it and `parse_form`
  reads it back.
- A validation failure becomes per-field messages with the reader's input
  preserved — no extra server-side state.
- Schema bounds become native attributes; the real validation stays in
  Pydantic.
- Fine-tune through `json_schema_extra={"ui": {...}}` or by patching the
  `FormSpec` with `replace()`.
- `exclude=` + `extra=` keep server-owned values out of the browser's
  reach.

See also: [UI layer »](ui.md) and [Typed CSS »](ui-css.md).
