# Custom actions

Use to add buttons that run your code (`@admin_action`): show a secret once, ask for input before running, create through a service.

Part of the [Admin site](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/admin.md) recipe.

Beyond the 3 built-ins (activate / deactivate / delete), you register
**your own actions** — an async function decorated with `@admin_action`
and passed to `AdminModel(actions=[...])`. Each becomes an option in the
bulk-action dropdown, operating on the checked rows.

```python
from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminModel,
    EmailUtils,
    admin_action,
)

from src.admin import site
from src.core.settings import settings
from src.db.models import UserModel

mailer = EmailUtils(**settings.email_kwargs())


@admin_action(label="Send welcome")
async def send_welcome(ctx: AdminActionContext) -> AdminActionResult:
    """Runs on the selected rows; the message is shown on the list view."""
    users = await ctx.repository.list(filters={"id": ctx.ids})
    for user in users:
        await mailer.send(
            user.email,
            subject="Welcome",
            body=f"Hi {user.email}! Your account is ready.",
        )
    return AdminActionResult(f"Sent {len(users)} emails.")


site.register(AdminModel(model=UserModel, actions=[send_welcome]))
```

The handler receives an `AdminActionContext`:

| Field | What it is |
| --- | --- |
| `ids` | Identities of the checked rows. |
| `repository` | The model's `BaseRepository`, on the request session. |
| `db_session` | The DB session (for work beyond the repository). |
| `request` | The inbound request. |
| `session` | The authenticated admin session. |
| `principal` | The admin user row that triggered the action. |

Return an `AdminActionResult(message, category="success"|"error"|"warning")`
to flash a banner on the list view (or `None` for no banner). The
function stays **directly callable/testable** — the decorator only
attaches metadata. Use `name=` to pin the identifier (default: the
function name) and `dangerous=True` to mark a destructive action.

The panel **commits** the request session when the handler returns, with or
without `secrets` in the result, and rolls it back when the handler raises.
So the action may change the row straight on the session, with no commit of
its own:

```python
from tempest_fastapi_sdk import AdminActionContext, AdminActionResult, admin_action

from src.db.models import UserModel


@admin_action(label="Confirmar e-mail")
async def verify_email(ctx: AdminActionContext) -> AdminActionResult:
    """Mark the e-mail confirmed; the panel commits at the end."""
    for user_id in ctx.ids:
        user = await ctx.db_session.get(UserModel, user_id)
        if user is not None:
            user.is_active = True
    return AdminActionResult(f"{len(ctx.ids)} conta(s) confirmada(s).")
```

!!! warning "Before 0.366.0, an action without `secrets` was not committed"
    The commit happened only on the `secrets` branch: an action that wrote
    through the session showed the success flash and lost the write at the
    end of the request (#784).

Each option of the bulk-action select carries its own confirm prompt. A
plain action (and Activate/Deactivate) asks "Apply the selected action to the
checked rows?"; a `dangerous=True` action (and the built-in Delete) names the
action and warns there is no way back — `Run "Recusar e devolver" on the
checked rows? This cannot be undone.`. The label goes in a `data-confirm`
attribute escaped by the template, never interpolated into JavaScript, so
quotes or `<` in the label do not break the page.

## Show a secret once (`secrets=`)

Some actions **create a credential**: an API key, an integration token, a
temporary password. The operator has to see the value once, and the flash
cannot carry it: it comes back through the redirect's query string
(`?flash=...`), which puts the value in the browser history, the access
log and the next page's `Referer`.

Return it in `AdminActionResult(secrets=[...])`, as an `AdminSecret`:

```python
from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminModel,
    AdminSecret,
    admin_action,
    generate_opaque_token,
)

from src.admin import site
from src.db.models import ApiKeyModel, UserModel


@admin_action(label="Issue API key")
async def issue_api_key(ctx: AdminActionContext) -> AdminActionResult:
    """Create one key per selected user and show each one once."""
    users = await ctx.repository.list(filters={"id": ctx.ids})
    secrets: list[AdminSecret] = []
    for user in users:
        plaintext, digest = generate_opaque_token()
        ctx.db_session.add(ApiKeyModel(user_id=user.id, key_hash=digest))
        secrets.append(AdminSecret(f"Key for {user.email}", plaintext))
    return AdminActionResult(f"{len(secrets)} key(s) issued.", secrets=secrets)


site.register(AdminModel(model=UserModel, actions=[issue_api_key]))
```

With `secrets`, the panel **does not redirect**. The `POST`'s own response
is a page with the message and each value in a read-only field (focusing it
selects the text for copying), plus a button back to the list. What changes
compared to the flash:

- **Nothing reaches a URL or a header.** The value only exists in that
  response body.
- **`Cache-Control: no-store`** and `Pragma: no-cache`: neither the browser
  nor a proxy keeps a copy of the page. Reloading resubmits the `POST`,
  but the list form carries a single-use token and the resubmission does
  not run the action again (details in
  [Reloading does not issue again](#reloading-does-not-issue-again)).
- **`Referrer-Policy: no-referrer`**: the link back does not carry the
  action URL.
- **The request's session is committed before the page renders.** The
  `ctx.db_session.add(...)` above needs no commit of its own, and a secret
  on screen is never one whose row was rolled back.
- **`repr(AdminSecret)` leaves the value out**, so logging the result does
  not leak the secret.

!!! warning "Store only the digest"
    The panel shows the plaintext once, but storing it is your call.
    Persist the `digest` from `generate_opaque_token()` (SHA-256) and check
    an incoming key with `verify_opaque_token`. Put the digest column in
    `__secret_columns__`, so no panel screen displays it.

An action without `secrets` is unchanged: flashed on the list view, through
a redirect.

## Ask for input before running (`schema=`) *(v0.366.0+)*

Some actions need a value from the operator: the **reason** for an audited
access, an amount, a date. Pass a Pydantic model in `schema=` and the
handler receives `(data, ctx)`:

```python
from pydantic import BaseModel, Field

from tempest_fastapi_sdk import AdminActionContext, AdminActionResult, admin_action

from src.db.models import UserModel


class VerifyEmailInput(BaseModel):
    """What the operator fills in before the action runs."""

    reason: str = Field(min_length=10, max_length=500)


@admin_action(label="Confirmar e-mail", schema=VerifyEmailInput)
async def verify_email(
    data: VerifyEmailInput, ctx: AdminActionContext
) -> AdminActionResult:
    """Confirm the checked accounts and record the reason."""
    for user_id in ctx.ids:
        user = await ctx.db_session.get(UserModel, user_id)
        if user is not None:
            user.is_active = True
    return AdminActionResult(f"{len(ctx.ids)} confirmada(s): {data.reason}")
```

The flow, measured in `tests/admin/test_custom_action_flow.py`:

1. Picking the action on the list **runs nothing**: `POST /m/{slug}/bulk`
   answers `200` with the form generated from the schema (the same generator
   as `create_handler=`), the text `2 row(s) selected.`, the ids in hidden
   fields and a single-use nonce.
2. An invalid submission answers `400` with the field error
   (`String should have at least 10 characters`) and what was typed; the
   handler does not run and the nonce is not spent.
3. A valid submission runs the handler with the validated data and the ids,
   commits and returns to the list with the flash (`303`). Resubmitting the
   same form does not run it again: the nonce is spent, and the panel says
   `This form was already submitted; it did not run again.`.
4. An `AppException` from the handler rolls back and re-renders the form
   with the message and what was typed, with `400`.

An action without `schema=` is unchanged: it runs straight from the list.

## Create through a service (`create_handler=`)

An API key is not born from columns: the service generates the secret,
stores only the hash and shows the plaintext once. The column form would ask
for `key_hash` and `key_prefix` typed by hand — wrong. Moving the issuance to
a bulk action on the **users** list works, but whoever opens **Api Keys**
finds no button at all, and the action has no fields for name and expiry.

`create_handler` swaps the column form for a form generated from a
**Pydantic schema** and a handler that receives the validated schema:

```python
from uuid import UUID

from pydantic import BaseModel, Field
from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminCreateHandler,
    AdminModel,
    AdminSecret,
    AdminSite,
    generate_opaque_token,
)

from src.db.models import ApiKeyModel


class ApiKeyAdminCreateSchema(BaseModel):
    name: str = Field(title="Key name", min_length=3)
    expires_in_days: int | None = Field(default=None, title="Expires in (days)")
    user_id: UUID | None = Field(default=None, title="User")


async def issue_api_key(
    data: ApiKeyAdminCreateSchema, ctx: AdminActionContext
) -> AdminActionResult:
    plaintext, digest = generate_opaque_token()
    ctx.db_session.add(
        ApiKeyModel(name=data.name, key_hash=digest, user_id=data.user_id)
    )
    return AdminActionResult("Key issued", secrets=[AdminSecret(data.name, plaintext)])


site = AdminSite(title="Admin")
site.register(
    AdminModel(
        ApiKeyModel,
        autocomplete_fields=["user_id"],
        create_handler=AdminCreateHandler(
            schema=ApiKeyAdminCreateSchema,
            handler=issue_api_key,
            label="New API key",
        ),
    )
)
```

- The list button becomes **+ New API key** and opens the schema's form:
  `title` is the label, the type picks the control (an enum is a select,
  `int` a number, `SecretStr` a password), a field without a default is
  required. A field whose name is in `autocomplete_fields` uses the same FK
  search as the column form.
- A validation error re-renders with `400`, each field's message and what
  was typed. An `AppException` raised by the handler comes back as a form
  error, also keeping what was typed, and the session is rolled back.
- With `secrets`, the answer is the `POST`'s own response, as in the action
  above. Without them, the panel redirects to the list with the message.
- `create_handler` replaces the column form: `can_create=True` alongside it
  raises `ValueError` at construction.

### Reloading does not issue again

The form carries a single-use token, spent right before the handler runs. A
reload of the secret page resubmits the `POST`, and the panel answers `303`
to the list with the notice *This form was already submitted; it did not run
again.*, without calling the handler. Measured on Chromium: the reload
resubmitted the `POST`, landed on the list with the notice, and the table
held **one** key.

The same holds for the list's bulk actions, which now carry the token. A
`POST` without the token (a script, a template of your own) runs as before,
once per submission. The token is stored in the same backend as the session
revocations: with the default `InMemoryThrottleBackend` it holds per
process; behind more than one worker, pass the Redis client to
`AdminSessionRevocations` so the resubmission is refused on any of them.
