# SSR actions: confirmation, flash and redirect back

Every SSR panel repeats the same flow on each action:

1. the user clicks **Remove** and the browser asks "Remove bucket
   photos?";
2. the `POST` runs; success → `303` to the screen with "Bucket removed.";
3. a business rule fails → `303` **back to the screen it came from**, with
   "The bucket still has objects.";
4. the same exception, raised for the API, keeps answering the usual
   JSON.

Each step hides a security trap: inline script for the question (and a
name with quotes becomes code), the notice text in the URL (and a forged
link puts words on the screen), the `Referer` followed blindly (and the
panel becomes an open redirect). This recipe shows the SDK pieces that
already solve all three, so you rewrite none of them.

!!! tip "When to use this recipe"
    - Your service serves **HTML** with the [UI layer](ui.md) and has
      forms that change state.
    - You want to confirm a destructive action without writing JavaScript.
    - You want "the action failed → back to the screen with a notice"
      without assembling a cookie, the `Referer` and an exception handler
      by hand.

## The complete example

A bucket panel: list them, and remove only an empty bucket.

```python
from collections.abc import Sequence
from typing import ClassVar

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from tempest_core import Text, Widget

from tempest_fastapi_sdk import ConflictException, register_exception_handlers
from tempest_fastapi_sdk.ssr import (
    FlashMiddleware,
    flash,
    get_flashes,
    html_response,
    make_htmx_router,
    redirect_back,
    register_html_error_handlers,
)
from tempest_fastapi_sdk.ui import app_stylesheet
from tempest_fastapi_sdk.ui.components import Card, FlashMessage, FlashMessages
from tempest_fastapi_sdk.ui.css import make_css_router
from tempest_fastapi_sdk.ui.forms import form_for
from tempest_fastapi_sdk.ui.layout import Shell
from tempest_fastapi_sdk.ui.pages import ErrorPage, Page

BUCKETS: dict[str, int] = {"photos": 3, "drafts": 0}


class DeleteBucketSchema(BaseModel):
    """Removing has no field: the whole action is in the URL."""


class BasePage(Page):
    """The panel chrome: stylesheet, title and notices."""

    stylesheets: ClassVar[Sequence[str]] = ("/static/app.css",)
    title_suffix: ClassVar[str] = " · Panel"

    flashes: list[FlashMessage] = Field(default_factory=list)

    def shell(self, body: Widget) -> Widget:
        """Show the pending notices above the content."""
        return Shell(children=[FlashMessages(messages=self.flashes), body])


class AdminErrorPage(BasePage, ErrorPage):
    """The error page, with the panel chrome."""

    title_template: ClassVar[str] = "Error {status_code}"


class BucketsPage(BasePage):
    """The bucket listing, each with its remove button."""

    buckets: dict[str, int]

    def body(self) -> Widget:
        """One card per bucket."""
        return Card(
            title="Buckets",
            children=[
                Card(
                    title=name,
                    children=[
                        Text(content=f"{objects} objects", tag="p"),
                        form_for(
                            DeleteBucketSchema,
                            action=f"/admin/buckets/{name}/delete",
                            submit_label="Remove",
                            confirm=f"Remove bucket {name}?",
                            id_prefix=f"delete-{name}",
                        ),
                    ],
                )
                for name, objects in self.buckets.items()
            ],
        )


app: FastAPI = FastAPI()
app.add_middleware(FlashMiddleware, secret="replace-with-a-secret-from-settings")
app.include_router(make_htmx_router())
app.include_router(make_css_router(app_stylesheet()))
register_exception_handlers(app)
register_html_error_handlers(app, prefixes=["/admin"], error_page=AdminErrorPage)


@app.get("/admin/buckets")
async def list_buckets(request: Request) -> Response:
    """The screen: read the notices once and hand them to the page."""
    return html_response(
        BucketsPage(title="Buckets", buckets=BUCKETS, flashes=get_flashes(request)),
    )


@app.post("/admin/buckets/{name}/delete")
async def delete_bucket(request: Request, name: str) -> RedirectResponse:
    """The action: a rule failure is an exception; success is a notice."""
    if BUCKETS.get(name):
        raise ConflictException(f"Bucket {name} still has objects.")
    BUCKETS.pop(name, None)
    flash(request, f"Bucket {name} removed.", "success")
    return redirect_back(request, fallback="/admin/buckets", allowed_prefix="/admin")
```

Running this app with the `TestClient` (on `https://testserver`), the
output is:

```text
GET  /admin/buckets               200  <title>Buckets · Panel</title>
                                       <link rel="stylesheet" href="/static/app.css">
                                       <script src="/_ssr/confirm.js" defer></script>
                                       <form method="post" action="/admin/buckets/photos/delete"
                                             class="tui-form" data-confirm="Remove bucket photos?">
POST /admin/buckets/photos/delete 303  location: /admin/buckets   (set-cookie: tempest_flash=...)
GET  /admin/buckets               200  <div class="tui-alert tui-alert--error" role="alert">
                                       <p>Bucket photos still has objects.</p>
                                       (set-cookie: tempest_flash=""; Max-Age=0 — read, deleted)
POST /admin/buckets/drafts/delete 303  location: /admin/buckets
GET  /admin/buckets               200  <div class="tui-alert tui-alert--success" role="status">
                                       <p>Bucket drafts removed.</p>
GET  /admin/buckets/photos/delete 405  <title>Error 405 · Panel</title>
GET  /api/nothing                 404  application/json {"detail":"Not Found"}
```

No route passed `title=` or `stylesheets=`, and no line of JavaScript was
written.

## Piece by piece

### Confirm before removing

```python
from pydantic import BaseModel

from tempest_fastapi_sdk.ui.forms import form_for


class DeleteBucketSchema(BaseModel):
    """No fields."""


form = form_for(
    DeleteBucketSchema,
    action="/admin/buckets/photos/delete",
    submit_label="Remove",
    confirm="Remove bucket photos?",
)
```

`confirm=` puts `data-confirm="..."` on the `<form>`. `html_response` sees
the attribute in the document and includes `/_ssr/confirm.js`, served by
the same `make_htmx_router()` that already serves HTMX. The script
listens for `submit` on the whole document, reads the text with
`getAttribute` and calls `window.confirm`; on "Cancel", the submit does
not happen.

- **The text is data, never code.** It is an attribute value, escaped by
  the renderer, and the script reads it back — nothing is interpolated
  into JavaScript. `confirm=f"Remove {name}?"` with a name full of quotes
  is still just a question.
- **Without JavaScript, the form works.** The attribute is inert: the
  `POST` happens, just without the question.
- **CSP friendly.** The script comes from the application itself;
  `script-src 'self'` is enough, no `'unsafe-inline'`.

Outside a `form_for`, use `confirm()` — it returns the same attribute to
spread into `attrs=` of a button or a link:

```python
from tempest_core import Button, Text

from tempest_fastapi_sdk.ssr import confirm

button = Button(label="Remove everything", attrs=confirm("Remove every bucket?"))
link = Text(
    content="Sign out",
    tag="a",
    attrs={"href": "/logout", **confirm("End the session?")},
)
```

On a submit button, the **button's** question replaces the form's — you
can have "Save" without a question and "Remove" with one in the same
form.

!!! info "When the script lands on the page"
    `html_response(confirm=None)` (the default) includes the script when
    the document has `data-confirm` **or** when `htmx=True`: an HTMX swap
    can bring a guarded form into a page that had none, and the listener
    delegated on the document already covers the fragment that arrived.
    `confirm=True` forces it, `confirm=False` leaves it out.

!!! tip "An element that fires an HTMX request"
    For `hx-post`/`hx-delete`, prefer `htmx(confirm="...")`, which becomes
    `hx-confirm` and is read by HTMX itself.

### One-shot notices (flash)

```python
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from tempest_fastapi_sdk.ssr import FlashMiddleware, flash

app: FastAPI = FastAPI()
app.add_middleware(FlashMiddleware, secret="replace-with-a-secret-from-settings")


@app.post("/admin/buckets")
async def create_bucket(request: Request) -> RedirectResponse:
    flash(request, "Bucket created.", "success")
    return RedirectResponse("/admin/buckets", status_code=303)
```

- **`flash(request, text, variant)`** queues the notice. The variant is
  the `Alert` one: `"info"`, `"success"`, `"warning"` or `"error"`.
- **`FlashMiddleware`** writes the queue into a cookie on the response
  and, on the next request, decodes it — only when the HMAC-SHA256
  signature verifies and the cookie is younger than `max_age` seconds
  (300 by default).
- **`get_flashes(request)`** returns the pending notices and marks them
  read; the middleware deletes the cookie on that response, so reloading
  the page does not repeat the notice. A screen that does not call
  `get_flashes` leaves the cookie alone, and the notice waits for the next
  one that does.
- **`FlashMessages(messages=...)`** renders one `Alert` per notice. With
  none, the wrapper is empty and the SDK stylesheet hides it.

The text **never** goes through the URL, so a forged link cannot put words
on the screen; and a forged cookie fails the signature and is deleted.

!!! warning "Signed, not encrypted"
    Whoever holds the cookie can read the text (it is base64). That is a
    choice: the notice is meant for that person. Never put in a flash what
    they may not see.

??? note "Cookie technical details"
    - `HttpOnly`, `SameSite=Lax` (survives the top-level `303` after the
      form), `Secure` by default. On a development `http://` pass
      `secure=False`: a `Secure` cookie does not come back over plain HTTP.
    - The secret needs 16 characters or more; the signature uses a fixed
      context, so the same secret used elsewhere cannot produce a valid
      flash cookie.
    - Each notice is cut at `MAX_FLASH_MESSAGE_LENGTH` (500) characters,
      and the queue at `MAX_FLASH_COOKIE_BYTES` (3800) bytes: what does
      not fit drops the **oldest** first, so the latest notice always
      arrives.
    - `flash` and `get_flashes` without the middleware raise
      `RuntimeError`: a notice queued without it would be lost silently.

### Go back without an open redirect

```python
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from tempest_fastapi_sdk.ssr import redirect_back

app: FastAPI = FastAPI()


@app.post("/admin/buckets/{name}/delete")
async def delete_bucket(request: Request, name: str) -> RedirectResponse:
    return redirect_back(request, fallback="/admin/buckets", allowed_prefix="/admin")
```

`redirect_back` follows the `Referer` only when it names **this host** and
a path under `allowed_prefix`; the `Location` sent is always the
**relative** path, never the header as received. Everything else goes to
`fallback`:

| `Referer` | Result |
| --- | --- |
| `https://testserver/admin/buckets?page=2` | `/admin/buckets?page=2` |
| missing | `fallback` |
| `https://evil.example/admin` (another host) | `fallback` |
| `https://testserver@evil.example/admin` (user info) | `fallback` |
| `/admin/buckets` (no host) | `fallback` |
| `//evil.example/admin` (protocol-relative) | `fallback` |
| `javascript:...`, `data:...`, `ftp://...` | `fallback` |
| `https://testserver/public` (outside the prefix) | `fallback` |
| `https://testserver/administrator` (prefix without a boundary) | `fallback` |
| `https://testserver//evil.example/x`, `.../%2Fevil.example` | `fallback` |
| `https://testserver/admin/../public`, `.../%2e%2e/...`, `\` | `fallback` |

Every row is a case in `tests/ssr/test_redirects.py`. Need just the URL,
not the response? `back_url(request, allowed_prefix=...)` returns the
validated path or `None`.

### The exception becomes a page or a notice

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.ssr import FlashMiddleware, register_html_error_handlers

app: FastAPI = FastAPI()
app.add_middleware(FlashMiddleware, secret="replace-with-a-secret-from-settings")
register_exception_handlers(app)
register_html_error_handlers(app, prefixes=["/admin"])
```

`register_html_error_handlers` wraps the `AppException` and
`HTTPException` handlers already registered — which is why it comes
**after** `register_exception_handlers`. The JSON handler always runs
(logging, catalog localization, `on_server_error`), and only the shape of
the response changes:

| Request | Response |
| --- | --- |
| HTML route, `GET`/`HEAD` | `error_page` with the same status and the message |
| HTML route, any other method | `"error"` flash + `303` through `redirect_back` |
| anything else | the usual JSON, untouched |

- **An HTML route** matches one of the `prefixes` (`"/admin"` matches
  `/admin` and `/admin/...`, not `/administrator`) or carries one of the
  router `tags=`. An unknown path has no route, so only the prefix
  recognises it.
- **`error_page=`** takes the page class. Combine `ErrorPage` with your
  base page — `class AdminErrorPage(BasePage, ErrorPage)` — and it
  inherits the chrome, the stylesheet and the title suffix. The title
  comes from the `title_template` class attribute, `"Erro {status_code}"`
  by default — the example sets `"Error {status_code}"`.
- **`fallback=`** is the target when the `Referer` is refused; it
  defaults to the matched prefix.
- **Without `FlashMiddleware`** the notice would be lost, so the `POST`
  gets the error page instead of the redirect.

## Recap

- `form_for(..., confirm="...")` or `confirm("...")` asks before the
  action; the script is local, the text is data, and without JavaScript
  everything still works.
- `FlashMiddleware` + `flash` + `get_flashes` + `FlashMessages` carry a
  notice across the redirect in a signed, read-once cookie.
- `redirect_back` returns to the origin only when it is this host and
  this prefix, and always with a relative `Location`.
- `register_html_error_handlers` makes the same exception an error page, a
  notice with a redirect, or JSON, depending on who asked.
- `stylesheets`, `head` and `title_suffix` on the base page take those
  arguments off every route — see the [UI layer](ui.md).
