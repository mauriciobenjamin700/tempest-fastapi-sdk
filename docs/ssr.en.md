# SSR: typed Python pages rendered to HTML

Your FastAPI service, full-stack, **typed**, with no template language. 🚀

The SDK's SSR (Server-Side Rendering) layer lets you describe pages as
**typed Python components** and return them from a route already rendered
to HTML. No Jinja, no loose strings: the same type checker that covers
your schemas and services now covers your UI too.

!!! info "What you'll need"
    The SSR layer lives in the optional `[ssr]` extra, which pulls in the
    [`tempestweb`](https://pypi.org/project/tempestweb/) renderer (and,
    transitively, `tempest-core` with the typed widgets).

    ```bash
    pip install "tempest-fastapi-sdk[ssr]"
    ```

## Why this exists

In a traditional API you return JSON and a separate front-end draws the
screen. When all you need are server-driven pages (an internal panel, an
onboarding flow, a landing page), spinning up a whole SPA is dead weight.

The classic alternative — a templating engine — drops you out of the typed
world: the template is a string, the editor can't help you, and a field
renamed in the schema only breaks in production.

The SSR layer solves this by keeping **everything in typed Python**:

- You declare the page as a class (`Page`) with typed fields.
- You build the body with widgets (`Column`, `Row`, `Text`, `Button`, ...).
- You return `html_response(...)` from the route — and get an `HTMLResponse`.

## Minimal complete example

This is a complete, runnable program. Save it as `main.py`, install the
extra, and run it with `uvicorn main:app`.

```python
from tempest_core import Column, Text, Widget
from fastapi import FastAPI

from tempest_fastapi_sdk.ssr import Page, html_response

app: FastAPI = FastAPI()


class HomePage(Page):
    """The home page, with a single typed field."""

    user: str

    def body(self) -> Widget:
        """The page's main content."""
        return Column(
            tag="main",
            children=[
                Text(content=f"Hello, {self.user}!", tag="h1"),
                Text(content="Welcome to your typed app.", tag="p"),
            ],
        )


@app.get("/")
def home() -> object:
    """Render HomePage as a full HTML document."""
    return html_response(HomePage(title="Home", user="Ana"), title="Home")
```

Open `http://127.0.0.1:8000/` and you get a full HTML5 document with
`<!doctype html>`, `<title>Home</title>`, and a `<main>` body — all
generated from the classes above.

## Piece by piece

### The `Page` class

```python
class HomePage(Page):
    user: str

    def body(self) -> Widget:
        return Column(tag="main", children=[Text(content=f"Hello, {self.user}!")])
```

`Page` is a **`tempest_core` component** (a Pydantic model). That means the
page's data are **typed fields** — here, `user: str`. The inherited
`title: str` field feeds the document `<title>`.

You implement **`body()`**, which returns the widget tree for the main
content. It is the only required method.

!!! tip "Semantic tags"
    Every widget accepts `tag=` and `attrs=`. Use `tag="main"`,
    `tag="h1"`, `tag="nav"` to emit semantic HTML instead of the neutral
    defaults (`<div>` / `<span>`).

### The `html_response` function

```python
return html_response(HomePage(title="Home", user="Ana"), title="Home")
```

`html_response` renders the tree and returns a FastAPI `HTMLResponse`.
Its signature:

```python
def html_response(
    widget: Widget,
    *,
    title: str | None = None,
    status_code: int = 200,
    htmx: bool = False,
    document: bool = True,
    lang: str = "pt-BR",
) -> HTMLResponse: ...
```

- **`document=True`** (default) → full HTML5 document. Requires `title`
  (raises `ValueError` if `title is None`).
- **`document=False`** → bare HTML fragment (no `<!doctype>`), ideal for
  partial swaps with HTMX.
- **`status_code`** → passed through to the `HTMLResponse`.
- **`htmx=True`** → inject the HTMX `<script>` **served locally** (never
  from a CDN — see below).

!!! warning "`title` is required for documents"
    Calling `html_response(page)` with `document=True` (the default) and
    no `title` raises `ValueError`. For fragments (`document=False`),
    `title` is ignored.

## Shared layout with `shell()`

Every page usually shares the same header, navigation, and footer. Instead
of repeating it, override **`shell()`** on a base page and inherit through
normal Python inheritance.

```python
from tempest_core import Column, Row, Text, Widget

from tempest_fastapi_sdk.ssr import Page, html_response


class BasePage(Page):
    """Shared layout: navigation bar + main area."""

    def shell(self, body: Widget) -> Widget:
        return Column(
            tag="body",
            children=[
                Row(tag="nav", children=[Text(content="MyApp")]),
                Column(tag="main", children=[body]),
            ],
        )


class DashboardPage(BasePage):
    """Inherits the navigation, defines only its own body."""

    def body(self) -> Widget:
        return Text(content="Dashboard", tag="h2")


class ReportsPage(BasePage):
    """Same navigation, different body."""

    def body(self) -> Widget:
        return Text(content="Reports", tag="h2")
```

`render()` (the component hook) already composes `shell(body())` for you —
**do not override `render()`**; override `body()` and, optionally,
`shell()`.

!!! note "How composition works"
    `Page.render()` returns `self.shell(self.body())`. The renderer expands
    components recursively, so a page is just another widget in the tree.

## HTMX served locally (no CDN)

For server-driven interactivity without writing JavaScript, the SDK bundles
HTMX 2.x **inside the package** and serves it from your own app — CSP-
friendly and offline-capable. No CDN.

Mount the router and flip `htmx=True`:

```python
from tempest_fastapi_sdk.ssr import make_htmx_router

app.include_router(make_htmx_router())  # serves GET /_ssr/htmx.js
```

When you call `html_response(page, title=..., htmx=True)`, the generated
document points at `/_ssr/htmx.js` (the same path served by the router),
never at `https://unpkg.com/...`.

### Recipe: server-driven counter with HTMX

A button that increments a counter on the server and swaps just a fragment —
no JavaScript. Complete program:

```python
from tempest_core import Button, Column, Text, Widget
from fastapi import FastAPI

from tempest_fastapi_sdk.ssr import Page, html_response, make_htmx_router

app: FastAPI = FastAPI()
app.include_router(make_htmx_router())

_count: int = 0


class CounterFragment(Page):
    """The fragment swapped on each click."""

    value: int

    def body(self) -> Widget:
        return Column(
            attrs={"id": "counter"},
            children=[
                Text(content=f"Total: {self.value}", tag="p"),
                Button(
                    label="Increment",
                    attrs={
                        "hx-post": "/increment",
                        "hx-target": "#counter",
                        "hx-swap": "outerHTML",
                    },
                ),
            ],
        )


class CounterPage(Page):
    """The full page that loads HTMX and shows the counter."""

    value: int

    def body(self) -> Widget:
        return CounterFragment(title="", value=self.value)


@app.get("/")
def index() -> object:
    """Full document, with the local HTMX loaded (htmx=True)."""
    return html_response(
        CounterPage(title="Counter", value=_count), title="Counter", htmx=True
    )


@app.post("/increment")
def increment() -> object:
    """Increment and return only the fragment (document=False)."""
    global _count
    _count += 1
    return html_response(CounterFragment(title="", value=_count), document=False)
```

How it works:

1. `GET /` returns the **full document** (`document=True`, `htmx=True`), so
   the local HTMX is loaded.
2. The button has `hx-post="/increment"` and `hx-swap="outerHTML"`.
3. `POST /increment` returns only the **fragment** (`document=False`), and
   HTMX swaps the `<div id="counter">` in place.

!!! check "Safe by default"
    All text is escaped on render. A `Text(content="<script>")` becomes
    `&lt;script&gt;` in the final HTML — no accidental injection.

## Recap

- **`Page`** — typed component; declare fields, implement `body()`,
  optionally override `shell()` for a shared layout. Do not override
  `render()`.
- **`html_response(widget, *, title, status_code, htmx, document, lang)`** —
  renders and returns an `HTMLResponse`. `document=True` requires `title`;
  `document=False` returns a fragment for HTMX swaps.
- **`make_htmx_router(prefix="/_ssr")`** — serves the bundled HTMX locally
  at `GET /_ssr/htmx.js`; combine with `htmx=True`.
- Everything lives in the `[ssr]` extra
  (`pip install "tempest-fastapi-sdk[ssr]"`), loaded on demand —
  `import tempest_fastapi_sdk` never requires the extra.
